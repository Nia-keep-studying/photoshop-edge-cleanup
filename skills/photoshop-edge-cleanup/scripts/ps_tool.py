"""Bounded Photoshop tools for Codex. No arbitrary JavaScript endpoint."""
from __future__ import annotations
import argparse,hashlib,json,math,os,shutil,subprocess,sys,time,uuid
from pathlib import Path
from PIL import Image,ImageDraw,ImageChops,ImageOps
import reference_plan

ROOT=Path(__file__).resolve().parent
WORKSPACE=Path(os.environ.get('PS_EDGE_WORKSPACE',str(Path.cwd()/'.ps-edge-workspace'))).expanduser().resolve()
RUNTIME=WORKSPACE/'photoshop_agent/runtime'
EXE=Path(os.environ['PS_EDGE_PHOTOSHOP_EXE']).expanduser() if os.environ.get('PS_EDGE_PHOTOSHOP_EXE') else None
ENGINE=ROOT/'engine.jsx'
OPERATIONS={'probe','open_copy','set_plan','close_session','inspect','snapshot','native_defringe','contract_mask','paint_fringe','erase','paint','mask','patch','restore','undo'}
MUTATIONS={'native_defringe','contract_mask','paint_fringe','erase','paint','mask','patch','restore','undo'}

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def write(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf8')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def finite(x):return isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x)
def point(p,w,h):
    if not isinstance(p,list) or len(p)!=2 or not all(finite(v) for v in p) or not (0<=p[0]<=w and 0<=p[1]<=h):raise ValueError('Points must be finite full-canvas pixel coordinates within the document.')
def shapes(req,w,h,required=False):
    if 'regions' in req and 'path' in req:raise ValueError('Supply polygons or a Bezier path, not both.')
    if 'regions' in req:
        if not isinstance(req['regions'],list) or not req['regions']:raise ValueError('At least one polygon is required.')
        for region in req['regions']:
            if not 3<=len(region)<=2000:raise ValueError('Polygon needs 3..2000 vertices.')
            for p in region:point(p,w,h)
    elif 'path' in req:
        if not 3<=len(req['path'])<=2000:raise ValueError('Bezier path needs 3..2000 anchors.')
        for p in req['path']:
            for k in ('anchor','left','right'):
                if k in p:point(p[k],w,h)
            if 'anchor' not in p:raise ValueError('Missing path anchor.')
    elif required:raise ValueError('An explicit local polygon or Bezier path is required.')
    if not finite(req.get('feather',0)) or not 0<=req.get('feather',0)<=3:raise ValueError('Feather must be 0..3 pixels.')

def invoke(payload,job,timeout=90):
    if EXE is None or not EXE.is_file():raise ValueError('Set PS_EDGE_PHOTOSHOP_EXE to the installed Photoshop.exe before invoking Photoshop.')
    job.mkdir(parents=True,exist_ok=False)
    response=job/'response.json';script=job/'job.jsx'
    # Only validated data is inserted as a JavaScript literal. No request can
    # supply code, an executable, output paths, or an engine path.
    # Some portable Photoshop 2019 builds reject $.evalFile(File(...)). Send
    # one self-contained UTF-8/BOM script instead of using a cross-file loader.
    script.write_text('#target photoshop\n'+ENGINE.read_text(encoding='utf8')+'\nPSEdgeRun('+json.dumps(payload,ensure_ascii=True)+','+json.dumps(str(response),ensure_ascii=True)+');\n',encoding='utf-8-sig')
    write(job/'request.json',payload)
    startup=None
    if os.name=='nt':
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    subprocess.Popen([str(EXE),str(script)],cwd=str(EXE.parent),startupinfo=startup,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if response.exists():
            try:return read(response)
            except (ValueError,PermissionError):pass
        time.sleep(.2)
    raise TimeoutError('Photoshop did not return a result. Do not retry a mutation blindly; inspect the app and snapshot the managed document first.')

def prepare_source(image,remove_white=False,allow_edge_touching=False,frame_barriers=None):
    rgba=image.convert('RGBA');kind='existing_alpha'
    if remove_white and rgba.getchannel('A').getextrema()[0]>=128:
        rgb=rgba.convert('RGB');w,h=rgb.size
        edge=[rgb.getpixel((x,0)) for x in range(w)]+[rgb.getpixel((x,h-1)) for x in range(w)]+[rgb.getpixel((0,y)) for y in range(h)]+[rgb.getpixel((w-1,y)) for y in range(h)]
        if sum(min(c)>=246 for c in edge)/len(edge)<(.80 if allow_edge_touching else .985):raise ValueError('Input is not flat-white artwork; do not automatically process a reference scene.')
        red,green,blue=rgb.split();white=ImageChops.darker(ImageChops.darker(red,green),blue).point(lambda v:255 if v>=235 else 0)
        # An explicitly observed clipped sticker may leave its white keyline
        # open on a canvas edge. Close only the specified flood-fill boundary;
        # original RGB pixels and canvas dimensions are never reconstructed.
        for barrier in frame_barriers or []:
            edge_name=barrier.get('edge');start=barrier.get('start');end=barrier.get('end')
            if edge_name not in ('left','right','top','bottom') or type(start) is not int or type(end) is not int:
                raise ValueError('Invalid explicit flood-fill frame barrier.')
            limit=h if edge_name in ('left','right') else w
            if not 0<=start<=end<limit:raise ValueError('Frame barrier is outside the canvas.')
            line=((0 if edge_name=='left' else w-1),start,(0 if edge_name=='left' else w-1),end) if edge_name in ('left','right') else (start,(0 if edge_name=='top' else h-1),end,(0 if edge_name=='top' else h-1))
            ImageDraw.Draw(white).line(line,fill=0)
        flood=ImageOps.expand(white,border=1,fill=255);ImageDraw.floodfill(flood,(0,0),128)
        keep=flood.crop((1,1,w+1,h+1)).point(lambda v:0 if v==128 else 255)
        rgba.putalpha(ImageChops.multiply(rgba.getchannel('A'),keep));kind='frame_connected_white_removed'
    return rgba,kind

def _execute(request):
    request=dict(request);op=request.get('op')
    if op not in OPERATIONS:raise ValueError('Operation is not allowlisted.')
    RUNTIME.mkdir(parents=True,exist_ok=True)
    if op=='probe':return invoke({'op':'probe'},RUNTIME/'probes'/uuid.uuid4().hex)
    if op=='open_copy':
        source=Path(request['source']).resolve(strict=True)
        if source.suffix.lower()!='.png':raise ValueError('This prototype accepts PNG artwork.')
        sid=uuid.uuid4().hex[:12];folder=RUNTIME/'sessions'/sid;folder.mkdir(parents=True,exist_ok=False)
        original_hash=sha(source);shutil.copy2(source,folder/'input_original.png')
        # White-background preparation is optional and frame-connected only.
        with Image.open(source) as im:
            rgba,kind=prepare_source(im,request.get('remove_white_background',False),request.get('allow_edge_touching_artwork',False),request.get('frame_barriers'))
            options={k:im.info[k] for k in ('icc_profile','dpi') if k in im.info}
            rgba.save(folder/'input_prepared.png',**options);w,h=rgba.size
        state={'session':sid,'owner_tag':'CE-'+sid,'source':str(source),'source_sha256':original_hash,'input_kind':kind,'width':w,'height':h,'revision':-1,'history':[]}
    else:
        sid=request.get('session','')
        if not isinstance(sid,str) or len(sid)!=12 or any(c not in '0123456789abcdef' for c in sid):raise ValueError('Invalid managed session id.')
        folder=RUNTIME/'sessions'/sid;state=read(folder/'session.json');w,h=state['width'],state['height']
        if sha(state['source'])!=state['source_sha256']:raise ValueError('The original source changed; stop for review.')
    if op=='set_plan':
        plan=reference_plan.create(request,state,folder,shapes)
        state['plan']=plan;write(folder/'session.json',state)
        return {'ok':True,'session':sid,'revision':state['revision'],'plan':plan,'image_path':plan['overlay_path'],
          'legend':{'red':'remove allowed','blue':'preserve','orange':'unresolved: preserve and continue'},'requires_human_confirmation':False}
    if op=='close_session':
        if request.get('discard_unsaved_changes') is not True:raise ValueError('Closing requires explicit discard_unsaved_changes=true; persisted checkpoints remain available.')
        result=invoke({'op':op,'owner_tag':state['owner_tag'],'document_id':state['document_id']},folder/'lifecycle'/uuid.uuid4().hex)
        if result.get('ok'):state['closed']=True;write(folder/'session.json',state)
        return dict(result,session=sid)
    plan=state.get('plan')
    if op in MUTATIONS-{'undo'}:
        if not plan:raise ValueError('Analyze the reference and set an image-specific plan before editing.')
        reference_plan.verify(plan)
    if op=='inspect':
        revision=request.get('revision',state['revision'])
        matches=[r for r in state['history'] if r['revision']==revision]
        if not matches:raise ValueError('Unknown revision.')
        rect=request.get('rect',[0,0,w,h])
        if len(rect)!=4 or not all(isinstance(v,int) for v in rect) or not (0<=rect[0]<rect[2]<=w and 0<=rect[1]<rect[3]<=h):raise ValueError('Rect is [left,top,right,bottom] in original canvas pixels.')
        scale=request.get('scale',1)
        if not finite(scale) or not .1<=scale<=8 or (rect[2]-rect[0])*(rect[3]-rect[1])*scale*scale>16000000:raise ValueError('Preview scale or output area is too large.')
        bg=request.get('background','checker')
        if bg not in ('checker','white','dark','gray'):raise ValueError('Unknown preview background.')
        with Image.open(matches[0]['png_path']) as im:
            rgba=im.convert('RGBA');tile=rgba.crop(rect)
            if bg=='checker':
                base=Image.new('RGBA',tile.size,'#eeeeee');dr=ImageDraw.Draw(base)
                for yy in range(0,tile.height,16):
                    for xx in range(0,tile.width,16):
                        if (xx//16+yy//16)%2:dr.rectangle((xx,yy,xx+15,yy+15),fill='#cccccc')
            else:base=Image.new('RGBA',tile.size,{'white':'white','dark':'#343b46','gray':'#dddddd'}[bg])
            base.alpha_composite(tile);image=base.convert('RGB').resize((round(tile.width*scale),round(tile.height*scale)),Image.Resampling.NEAREST if scale>1 else Image.Resampling.LANCZOS)
            samples=[]
            for pt in request.get('sample_points',[]):
                point(pt,w-1,h-1);xy=tuple(round(v) for v in pt);samples.append({'xy':xy,'rgba':rgba.getpixel(xy)})
        output=folder/'views'/f'{revision:03d}_{uuid.uuid4().hex[:8]}.png';output.parent.mkdir(exist_ok=True);image.save(output)
        return {'ok':True,'session':sid,'revision':revision,'image_path':str(output),'rect':rect,'scale':scale,'background':bg,'samples':samples,'source_unchanged':True}
    if op in {'erase','paint','paint_fringe','restore'}:shapes(request,w,h,True)
    if op in {'native_defringe','contract_mask','paint_fringe'}:
        if type(request.get('width')) is not int or not 1<=request['width']<=32:raise ValueError('Edge width must be an integer from 1 to 32.')
        shapes(request,w,h)
        if op=='contract_mask' and request.get('keep_regions'):shapes({'regions':request['keep_regions']},w,h,True)
    if op in {'paint','paint_fringe'}:
        colour=request.get('colour')
        if not isinstance(colour,list) or len(colour)!=3 or not all(finite(v) and 0<=v<=255 for v in colour):raise ValueError('RGB colour must have three values from 0 to 255.')
        if not finite(request.get('opacity',100)) or not 0<request.get('opacity',100)<=100:raise ValueError('Opacity must be greater than 0 and at most 100.')
    rev=state['revision']+1;token=uuid.uuid4().hex[:8]
    job=folder/'jobs'/f'{rev:03d}_{op}_{token}'
    payload={'op':op,'owner_tag':state['owner_tag'],'document_id':state.get('document_id'),'input_path':str(folder/'input_prepared.png'),
        'psd_path':str(job/'working.psd'),'png_path':str(job/'preview.png')}
    for k in ('regions','path','feather','colour','opacity','width','keep_regions'):
        if k in request:payload[k]=request[k]
    if op=='native_defringe':payload['previous_png']=state['png_path']
    if plan and op in MUTATIONS-{'undo'}:
        payload['scope_rgba']=plan['scope_rgba_path'];payload['previous_png']=state['png_path']
        # Preserve outside pixels with an inverse-scope alpha image. This avoids
        # attaching a second layer mask while PS 2019 has a mask channel active.
        with Image.open(state['png_path']) as im:
            outside=im.convert('RGBA');options={k:im.info[k] for k in ('icc_profile','dpi') if k in im.info}
        with Image.open(plan['scope_path']) as scope:
            outside.putalpha(ImageChops.multiply(outside.getchannel('A'),ImageChops.invert(scope.convert('L'))))
        outside_path=folder/f'asset_{token}_outside.png';outside.save(outside_path,**options)
        payload['preserved_outside_png']=str(outside_path)
    if op=='undo':
        index=request.get('checkpoint_revision')
        matches=[r for r in state['history'] if r['revision']==index]
        if not matches:raise ValueError('Unknown checkpoint revision.')
        payload['checkpoint_psd']=matches[0]['psd_path']
    if op in {'mask','patch'}:
        key='mask_path' if op=='mask' else 'patch_path';image_path=Path(request[key]).resolve(strict=True)
        if image_path.suffix.lower()!='.png':raise ValueError('Imported masks/patches must be PNG.')
        with Image.open(image_path) as im:
            if im.size!=(w,h):raise ValueError('Image must have exactly the original canvas dimensions.')
            if op=='mask':
                mask=im.convert('L');rgba=Image.new('RGBA',(w,h),'white');rgba.putalpha(mask)
            else:
                if not request.get('regions'):raise ValueError('Patch requires explicit allowed regions.')
                shapes(request,w,h,True);rgba=im.convert('RGBA');scope=Image.new('L',(w,h));dr=ImageDraw.Draw(scope)
                for region in request['regions']:dr.polygon([tuple(p) for p in region],fill=255)
                rgba.putalpha(ImageChops.multiply(rgba.getchannel('A'),scope))
            asset=folder/f'asset_{token}.png';rgba.save(asset)
        payload['mask_rgba' if op=='mask' else 'patch_path']=str(asset)
    result=invoke(payload,job)
    if not result.get('ok'):return dict(result,job=str(job),session=sid)
    if sha(state['source'])!=state['source_sha256']:raise RuntimeError('Source hash changed unexpectedly.')
    with Image.open(result['png_path']) as out:
        if out.size!=(w,h):raise RuntimeError('Photoshop changed the canvas dimensions.')
    scope_audit=None
    if plan:
        scope_audit=reference_plan.audit(state['png_path'],result['png_path'],plan)
        write(job/'scope_audit.json',scope_audit)
        if scope_audit['changes_outside_edit_scope']:
            return {'ok':False,'error':'Unexpected change outside the image-specific edit scope. Restore the last saved checkpoint before retrying.',
              'session':sid,'job':str(job),'scope_audit':scope_audit,'safe_to_retry':False}
    state.update(revision=rev,document_id=result['document_id'],psd_path=result['psd_path'],png_path=result['png_path'])
    entry={'revision':rev,'op':op,'history_index':result['history_index'],'job':str(job),'png_path':result['png_path'],'psd_path':result['psd_path']}
    if plan:entry.update(plan_id=plan['plan_id'],scope_audit=scope_audit)
    state['history'].append(entry);write(folder/'session.json',state)
    return dict(result,session=sid,revision=rev,source_unchanged=True,input_kind=state.get('input_kind'),job=str(job),scope_audit=scope_audit)

def execute(request):
    RUNTIME.mkdir(parents=True,exist_ok=True);lock=RUNTIME/'execution.lock';locked=False
    try:
        with lock.open('x',encoding='utf8') as f:f.write(str(os.getpid()))
        locked=True;return _execute(request)
    except FileExistsError:
        raise RuntimeError('Another Photoshop command is still running. Inspect its result before retrying.')
    finally:
        if locked:lock.unlink(missing_ok=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--request',type=Path,default=RUNTIME/'pending.json');parser.add_argument('--result',type=Path,default=RUNTIME/'last_result.json')
    args=parser.parse_args()
    if not args.result.resolve().is_relative_to(RUNTIME.resolve()):raise ValueError('Result files must stay inside the prototype runtime directory.')
    try:result=execute(read(args.request))
    except Exception as e:result={'ok':False,'error':str(e),'exception':type(e).__name__}
    write(args.result,result);print(json.dumps(result,ensure_ascii=False));return 0 if result.get('ok') else 1
if __name__=='__main__':raise SystemExit(main())
