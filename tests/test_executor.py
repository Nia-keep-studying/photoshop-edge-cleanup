"""Portable behavior checks. No Photoshop launch or private image fixtures."""
import base64,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageDraw

SCRIPTS=Path(__file__).resolve().parents[1]/'skills/photoshop-edge-cleanup/scripts'
sys.path.insert(0,str(SCRIPTS))
import ps_tool as ps
import reference_plan as rp

class ExecutorChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.runtime=self.root/'photoshop_agent/runtime';self.runtime.mkdir(parents=True)
        self.patcher=patch.object(ps,'RUNTIME',self.runtime);self.patcher.start();self.addCleanup(self.patcher.stop)

    def fixture(self):
        sid='abcde1234567';folder=self.runtime/'sessions'/sid;folder.mkdir(parents=True)
        source=folder/'source.png';Image.new('RGBA',(32,32),'blue').save(source)
        state={'session':sid,'owner_tag':'CE-'+sid,'source':str(source),'source_sha256':ps.sha(source),
               'width':32,'height':32,'revision':0,'document_id':1,'png_path':str(source),
               'history':[{'revision':0,'png_path':str(source)}]}
        ps.write(folder/'session.json',state)
        return sid,folder,source,state

    def request(self,source):
        return {'reference_path':str(source),'reference_observations':['synthetic matching reference'],
                'remove_rules':['test border'],'preserve_rules':['test interior'],
                'remove_regions':[[[0,0],[31,0],[31,31],[0,31]]]}

    def test_white_removal_keeps_enclosed_white_and_existing_alpha(self):
        im=Image.new('RGB',(80,80),'white');d=ImageDraw.Draw(im)
        d.rectangle((15,15,65,65),fill='black');d.rectangle((25,25,55,55),fill='white')
        out,_=ps.prepare_source(im,True)
        self.assertEqual(out.getpixel((0,0))[3],0);self.assertEqual(out.getpixel((40,40)),(255,255,255,255))
        out.putpixel((20,20),(55,44,33,90));again,_=ps.prepare_source(out,True)
        self.assertEqual(out.tobytes(),again.tobytes())
        with self.assertRaises(ValueError):ps.prepare_source(Image.new('RGB',(20,20),'green'),True)

    def test_frame_barrier_closes_clipped_white_keyline(self):
        im=Image.new('RGB',(40,40),'white');ImageDraw.Draw(im).rectangle((5,5,45,35),outline='black',width=2)
        leaking,_=ps.prepare_source(im,True,True)
        fixed,_=ps.prepare_source(im,True,True,[{'edge':'right','start':5,'end':35}])
        self.assertEqual(leaking.getpixel((20,20))[3],0)
        self.assertEqual(fixed.getpixel((20,20)),(255,255,255,255))
        self.assertEqual(fixed.getpixel((0,0))[3],0)
        self.assertEqual(im.tobytes(),fixed.convert('RGB').tobytes())

    def test_protection_and_uncertainty_override_removal(self):
        _,folder,source,state=self.fixture();req=self.request(source)
        req.update(protect_regions=[[[5,5],[10,5],[10,10],[5,10]]],uncertain_regions=[[[20,20],[25,20],[25,25],[20,25]]],unresolved_notes=['preserve unresolved fixture'])
        plan=rp.create(req,state,folder,ps.shapes);rp.verify(plan)
        with Image.open(plan['scope_path']) as scope:
            self.assertEqual([scope.getpixel(p) for p in [(0,0),(7,7),(22,22)]],[255,0,0])
        with Image.open(source) as im:changed=im.convert('RGBA')
        changed.putpixel((7,7),(255,0,0,255));changed.save(folder/'changed.png')
        self.assertEqual(rp.audit(source,folder/'changed.png',plan)['changes_outside_edit_scope'],1)

    def test_missing_plan_blocks_native_mutation(self):
        sid,_,_,_=self.fixture()
        with patch.object(ps,'invoke') as native:
            with self.assertRaises(ValueError):ps.execute({'op':'contract_mask','session':sid,'width':8})
            native.assert_not_called()

    def test_patch_is_clipped_before_native_call(self):
        sid,folder,source,_=self.fixture()
        ps.execute(dict(self.request(source),op='set_plan',session=sid))
        patch_file=folder/'patch.png';Image.new('RGBA',(32,32),'red').save(patch_file)
        def intercept(payload,job):
            with Image.open(payload['patch_path']) as im:
                self.assertEqual(im.getpixel((12,12)),(255,0,0,255))
                self.assertEqual(im.getpixel((0,0))[3],0)
                self.assertEqual(im.getpixel((31,31))[3],0)
            return {'ok':False,'error':'offline test intercept'}
        with patch.object(ps,'invoke',intercept):
            result=ps.execute({'op':'patch','session':sid,'patch_path':str(patch_file),'regions':[[[10,10],[16,10],[16,16],[10,16]]]})
        self.assertEqual(result['error'],'offline test intercept')

    def test_executable_must_be_configured_before_launch(self):
        with patch.object(ps,'EXE',None),patch.object(ps.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(ValueError,'PS_EDGE_PHOTOSHOP_EXE'):ps.invoke({'op':'probe'},self.root/'unused')
            launch.assert_not_called();self.assertFalse((self.root/'unused').exists())

    def test_no_reference_requires_explicit_default_and_limits_edge_band(self):
        _,folder,source,state=self.fixture();im=Image.new('RGBA',(32,32));ImageDraw.Draw(im).rectangle((5,5,26,26),fill='white');im.save(source)
        req=self.request(source);req.pop('reference_path');req['edge_limit']=3
        with self.assertRaises(ValueError):rp.create(req,state,folder,ps.shapes)
        req['no_reference_default']=True;plan=rp.create(req,state,folder,ps.shapes)
        with Image.open(plan['scope_path']) as scope:
            self.assertEqual(scope.getpixel((5,5)),255);self.assertEqual(scope.getpixel((16,16)),0)

    def test_stdio_uses_configured_workspace_and_returns_image(self):
        sid,_,_,_=self.fixture()
        requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05'}},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                  {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'photoshop_inspect','arguments':{'session':sid,'rect':[0,0,32,32],'background':'white'}}}]
        env=dict(os.environ,PS_EDGE_WORKSPACE=str(self.root),PS_EDGE_PHOTOSHOP_EXE='')
        proc=subprocess.run([sys.executable,'-B',str(SCRIPTS/'mcp_server.py')],input=''.join(json.dumps(r)+'\n' for r in requests),text=True,encoding='utf8',capture_output=True,timeout=15,check=True,env=env)
        messages=[json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual([m['id'] for m in messages],[1,2,3]);self.assertEqual(len(messages[1]['result']['tools']),8)
        result=messages[2]['result'];self.assertFalse(result['isError'])
        self.assertTrue(base64.b64decode(result['content'][1]['data']).startswith(b'\x89PNG'))

if __name__=='__main__':unittest.main(verbosity=2)
