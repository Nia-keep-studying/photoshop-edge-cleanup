import copy,tempfile,threading,unittest
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from edge_batch.common import defaults,write,read,ProcessLock,BusyError,NativeUncertain,AgentError
from edge_batch.images import candidate,compile_plan,image_facts
from edge_batch.importer import create_batch
from edge_batch.pipeline import Pipeline,best_attempt,review_status,retry_exceptions
from edge_batch.native import NativeBackend

POLY=[[0,0],[1000,0],[1000,1000],[0,1000]]
def plan():
    return dict(title='测试挂牌',kind='trim',reference_matches=True,is_flat_artwork=True,remove_white_background=False,
                allow_edge_touching=False,frame_barriers=[],coordinate_system='normalized_1000',reference_observations=['无参考'],
                remove_rules=['去额外外围黑线'],preserve_rules=['保留内部字'],protect_regions=[],uncertain_regions=[],unresolved_notes=[],
                operations=[dict(method='white_edge',regions=[POLY],donor_regions=[],colour=[255,255,255],width_mode='auto',max_width=8,
                                 dark_threshold=235,chroma_limit=45,donor_tolerance=40,edge_only=True,reason='白底板黑线')])
def review(ok=True,preserved=True):
    return dict(status='pass' if ok else 'repair',reference_correct=True,subject_preserved=preserved,target_border_removed=ok,
                background_correct=True,shape_and_alpha_ok=preserved,reason='已检查' if ok else '有残边',
                issues=[] if ok else [dict(kind='residual',region=POLY,description='残边',suggestion='修补')],
                inspected_regions=[dict(region=POLY,finding='检查边界') for _ in range(3)])

class FakeAgent:
    reviews=[];calls=[]
    def __init__(self,*args):pass
    def call(self,stage,prompt,schema,images,folder):
        self.calls.append(stage)
        if stage=='制定区域方案':return plan()
        if stage=='执行前检查':return dict(safe_to_execute=True,reason='安全')
        if stage=='独立验收':return copy.deepcopy(self.reviews.pop(0) if self.reviews else review())
        raise AssertionError(stage)

class FakeNative:
    processes=0
    def __init__(self,workspace,settings):self.settings=settings
    def recover(self):pass
    prepare=NativeBackend.prepare
    def process(self,source,reference,plan,folder,artifact):
        FakeNative.processes+=1
        (folder/'fake.psd').write_bytes(b'fake-test-only')
        result=dict(png=str(folder/'candidate.png'),before=str(folder/'prepared.png'),psd=str(folder/'fake.psd'))
        write(folder/'native.json',result);return result

class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.inputs=self.root/'input';self.inputs.mkdir()
        im=Image.new('RGBA',(100,100));d=ImageDraw.Draw(im);d.rectangle((10,10,90,90),fill='white',outline='black',width=3);d.rectangle((40,40,60,60),fill='black');im.save(self.inputs/'one.png')
        self.settings=defaults();self.settings['photoshop_exe']=str(self.inputs/'one.png')
        FakeNative.processes=0;FakeAgent.calls=[];FakeAgent.reviews=[]
    def tearDown(self):self.temp.cleanup()
    def make(self):
        store,bid,_=create_batch(self.inputs,None,self.root/'output',self.settings)
        return store,bid,Pipeline(store,bid,agent_factory=FakeAgent,backend_factory=FakeNative,channel_lock=self.root/'ps.lock')
    def test_import_deduplicates_and_rejects_nested_output(self):
        store,bid,p=self.make();s,b,created=create_batch(self.inputs,None,store.root,self.settings)
        self.assertFalse(created);self.assertEqual(b,bid)
        with self.assertRaises(ValueError):create_batch(self.inputs,None,self.inputs/'out',self.settings)
        with self.assertRaises(ValueError):create_batch('',None,self.root/'out',self.settings)
        with self.assertRaises(ValueError):create_batch(self.inputs,None,'',self.settings)
    def test_preserves_alpha_interior_and_protected_edge(self):
        p=plan();p['protect_regions']=[[[0,0],[1000,0],[1000,140],[0,140]]]
        with Image.open(self.inputs/'one.png') as im:original=im.convert('RGBA')
        result,metrics=candidate(original,p,compile_plan(p,original.size))
        a=np.array(original);b=np.array(result)
        self.assertTrue(np.array_equal(a[:,:,3],b[:,:,3]));self.assertTrue(np.array_equal(a[:15],b[:15]));self.assertTrue(np.array_equal(a[40:61,40:61],b[40:61,40:61]))
        self.assertGreater(metrics[0]['target_pixels'],0);self.assertTrue(np.all(b[50,10,:3]==255))
    def test_completed_jobs_not_reprocessed(self):
        store,bid,p=self.make();p.run();calls=len(FakeAgent.calls);p.run()
        self.assertEqual(FakeNative.processes,1);self.assertEqual(len(FakeAgent.calls),calls);self.assertEqual(store.jobs(bid)[0]['status'],'passed')
    def test_repair_budget_keeps_safe_earlier_version(self):
        FakeAgent.reviews=[review(False),review(False,False),review(False,False)]
        store,bid,p=self.make();p.run();job=store.jobs(bid)[0]
        self.assertEqual(FakeNative.processes,3);self.assertEqual(job['status'],'partial');self.assertEqual(job['result']['selected_attempt'],0)
    def test_interrupted_review_reuses_native_output(self):
        store,bid,p=self.make();p.run();job=store.jobs(bid)[0];attempt=p.folder/'jobs/I0001/attempt-00'
        (attempt/'record.json').unlink();store.update(job['id'],status='reviewing',result={'active':str(attempt)})
        p.run();self.assertEqual(FakeNative.processes,1);self.assertEqual(store.jobs(bid)[0]['status'],'passed')
    def test_agent_outage_pauses_batch_and_resumes_saved_review(self):
        store,bid,p=self.make()
        class Outage(FakeAgent):
            def call(self,stage,*args):
                if stage=='独立验收':raise AgentError('连接中断')
                return super().call(stage,*args)
        p.agent=Outage()
        with self.assertRaises(AgentError):p.run()
        self.assertEqual(store.batch(bid)['state'],'blocked');self.assertEqual(FakeNative.processes,1)
        p.agent=FakeAgent();p.run();self.assertEqual(FakeNative.processes,1);self.assertEqual(store.jobs(bid)[0]['status'],'passed')
    def test_no_reference_without_authorization_fails_without_edit(self):
        self.settings['no_reference_default']=False;store,bid,p=self.make();p.run()
        self.assertEqual(FakeNative.processes,0);self.assertEqual(store.jobs(bid)[0]['status'],'failed')
    def test_pass_requires_flags_and_no_uncertainty(self):
        r=review();r['subject_preserved']=False;self.assertEqual(review_status(r,plan()),'failed')
        p=plan();p['unresolved_notes']=['未知'];self.assertEqual(review_status(review(),p),'partial')
    def test_live_queue_locked_against_retry(self):
        store,bid,p=self.make()
        with ProcessLock(store.root/'queue.lock'):
            with self.assertRaises(BusyError):retry_exceptions(store,bid)
    def test_missing_native_response_blocks_replay(self):
        backend=NativeBackend(self.root/'native',self.settings);write(backend.runtime/'jobs/old/request.json',{'op':'open_copy'})
        with self.assertRaises(NativeUncertain):backend.recover()
    def test_retry_archives_delivery_and_keeps_passed(self):
        FakeAgent.reviews=[review(False)]*3
        store,bid,p=self.make();p.run();old=Path(store.jobs(bid)[0]['result']['png']);self.assertTrue(old.exists())
        retry_exceptions(store,bid);self.assertFalse(old.exists());self.assertEqual(store.jobs(bid)[0]['status'],'pending')
        self.assertEqual(store.jobs(bid)[0]['result'],{})
        p.run();self.assertEqual(store.jobs(bid)[0]['status'],'passed')
    def test_pause_and_resume_skip_finished_job(self):
        Image.open(self.inputs/'one.png').save(self.inputs/'two.png')
        store,bid,p=self.make();original=p.deliver
        def pause_after(*args,**kw):original(*args,**kw);p.stop.set()
        p.deliver=pause_after;p.run();self.assertEqual(store.batch(bid)['state'],'paused');self.assertEqual(FakeNative.processes,1)
        p.deliver=original;p.stop.clear();p.run();self.assertEqual(FakeNative.processes,2)
    def test_pixel_facts_distinguish_baked_background(self):
        facts=image_facts(self.inputs/'one.png');self.assertEqual(facts['alpha_min'],0)
        im=Image.open(self.inputs/'one.png').convert('RGB');im.save(self.inputs/'opaque.png')
        self.assertEqual(image_facts(self.inputs/'opaque.png')['alpha_min'],255)
    def test_uncertain_matching_does_not_mutate(self):
        references=self.root/'refs';references.mkdir();Image.open(self.inputs/'one.png').save(references/'ref.png')
        class Uncertain(FakeAgent):
            def call(self,stage,*args):
                if stage=='匹配参考图':return dict(status='uncertain',reference_id='',reason='无法确认')
                return super().call(stage,*args)
        store,bid,_=create_batch(self.inputs,references,self.root/'output',self.settings)
        Pipeline(store,bid,agent_factory=Uncertain,backend_factory=FakeNative,channel_lock=self.root/'ps.lock').run()
        self.assertEqual(FakeNative.processes,0);self.assertEqual(store.jobs(bid)[0]['status'],'failed')
    def test_mixed_classification_excludes_reference_and_reports_unknown(self):
        Image.open(self.inputs/'one.png').save(self.inputs/'two.png');Image.open(self.inputs/'one.png').save(self.inputs/'three.png')
        self.settings['mixed']=True
        class Classifier(FakeAgent):
            def call(self,stage,*args):
                if stage=='区分素材与参考':return {'items':[dict(id='I0001',role='artwork',reason='平面图'),dict(id='I0002',role='unknown',reason='不确定'),dict(id='I0003',role='reference',reason='场景图')]}
                if stage=='匹配参考图':return dict(status='matched',reference_id='I0003',reason='图案对应')
                return super().call(stage,*args)
        store,bid,_=create_batch(self.inputs,None,self.root/'output',self.settings)
        p=Pipeline(store,bid,agent_factory=Classifier,backend_factory=FakeNative,channel_lock=self.root/'ps.lock');p.run()
        self.assertEqual(len(store.jobs(bid)),2);self.assertEqual(FakeNative.processes,1)
        self.assertTrue((p.folder/'delivery/failed/I0002/report.json').exists())

if __name__=='__main__':unittest.main()
