"""Local stdio MCP adapter for the same bounded Photoshop executor."""
import base64,json,sys
from pathlib import Path
from ps_tool import execute,RUNTIME,read

POINT={'type':'array','items':{'type':'number'},'minItems':2,'maxItems':2}
REGIONS={'type':'array','items':{'type':'array','items':POINT,'minItems':3}}
TEXTS={'type':'array','items':{'type':'string'},'minItems':1}
def obj(properties,required=()):return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}
SESSION={'type':'string','pattern':'^[0-9a-f]{12}$'}
FRAME_BARRIERS={'type':'array','items':obj({'edge':{'enum':['left','right','top','bottom']},'start':{'type':'integer','minimum':0},'end':{'type':'integer','minimum':1}},['edge','start','end'])}
TOOLS=[
 {'name':'photoshop_open_artwork','description':'Open a managed PNG working copy without modifying the source. Optional white removal; allow_edge_touching_artwork is only for visually confirmed flat-white artwork touching the canvas. frame_barriers closes explicitly inspected clipped white-keyline openings at canvas edges during flood separation, without drawing new content.','inputSchema':obj({'source':{'type':'string'},'remove_white_background':{'type':'boolean','default':False},'allow_edge_touching_artwork':{'type':'boolean','default':False},'frame_barriers':FRAME_BARRIERS},['source'])},
 {'name':'photoshop_set_plan','description':'Record image-specific rules BEFORE editing. Supply a matching reference, or no_reference_default=true ONLY when the user explicitly authorized that default. Protected/unresolved regions override removal. Optional edge_limit further restricts edits to an actual-contour band, protecting interiors and rounded corners.','inputSchema':obj({'session':SESSION,'reference_path':{'type':'string'},'no_reference_default':{'type':'boolean','default':False},'edge_limit':{'type':'integer','minimum':1,'maximum':32},'reference_observations':TEXTS,'remove_rules':TEXTS,'preserve_rules':TEXTS,'remove_regions':REGIONS,'protect_regions':REGIONS,'uncertain_regions':REGIONS,'unresolved_notes':{'type':'array','items':{'type':'string'}}},['session','reference_observations','remove_rules','preserve_rules','remove_regions'])},
 {'name':'photoshop_inspect','description':'Return an image of a saved revision at original-pixel coordinates, with optional enlargement and exact pixel samples. Cached preview; use snapshot to capture external manual changes.','inputSchema':obj({'session':SESSION,'revision':{'type':'integer'},'rect':{'type':'array','items':{'type':'integer'},'minItems':4,'maxItems':4},'scale':{'type':'number'},'background':{'enum':['checker','white','gray','dark']},'sample_points':{'type':'array','items':POINT}},['session'])},
 {'name':'photoshop_edit','description':'Apply an explicit mask, path, local colour repair, or native defringe to a managed document. Returns a new PSD/PNG checkpoint. Defringe alone does not remove an opaque black outline.','inputSchema':obj({'session':SESSION,'operation':{'enum':['contract_mask','paint_fringe','paint','erase','restore','mask','patch','native_defringe']},'width':{'type':'integer','minimum':1,'maximum':32},'regions':REGIONS,'keep_regions':REGIONS,'path':{'type':'array','items':obj({'anchor':POINT,'left':POINT,'right':POINT},['anchor'])},'feather':{'type':'number','minimum':0,'maximum':3},'colour':{'type':'array','items':{'type':'number','minimum':0,'maximum':255},'minItems':3,'maxItems':3},'opacity':{'type':'number','minimum':0,'maximum':100},'mask_path':{'type':'string'},'patch_path':{'type':'string'}},['session','operation'])},
 {'name':'photoshop_undo','description':'Restore a managed document from a prior immutable PSD checkpoint. Reopens only the tool-owned working document, never a user document.','inputSchema':obj({'session':SESSION,'checkpoint_revision':{'type':'integer','minimum':0}},['session','checkpoint_revision'])},
 {'name':'photoshop_snapshot','description':'Save the current managed Photoshop document to a new PSD checkpoint and transparent PNG. No output location outside the session can be supplied.','inputSchema':obj({'session':SESSION},['session'])},
 {'name':'photoshop_close','description':'Close only a managed tool-owned document after required outputs/checkpoints are saved, freeing scratch resources for the batch. Explicit discard_unsaved_changes=true is required; cached checkpoints remain.','inputSchema':obj({'session':SESSION,'discard_unsaved_changes':{'type':'boolean'}},['session','discard_unsaved_changes'])},
 {'name':'photoshop_status','description':'Read existing managed session metadata, or probe Photoshop script connectivity.','inputSchema':obj({'session':SESSION})}
]
for tool in TOOLS:
    tool['annotations']={'readOnlyHint':tool['name'] in ('photoshop_inspect','photoshop_status'),'destructiveHint':False,'openWorldHint':False}

def call(name,args):
    args=dict(args)
    schema=next((t['inputSchema'] for t in TOOLS if t['name']==name),None)
    if schema is None:raise ValueError('Unknown tool')
    if set(args)-set(schema['properties']):raise ValueError('Unknown tool arguments')
    if any(k not in args for k in schema['required']):raise ValueError('Missing required tool argument')
    if name=='photoshop_status' and args.get('session'):
        sid=args['session']
        if len(sid)!=12 or any(c not in '0123456789abcdef' for c in sid):raise ValueError('Invalid session')
        return read(RUNTIME/'sessions'/sid/'session.json')
    if name=='photoshop_open_artwork':args['op']='open_copy'
    elif name=='photoshop_set_plan':args['op']='set_plan'
    elif name=='photoshop_inspect':args['op']='inspect'
    elif name=='photoshop_edit':
        operation=args.pop('operation')
        if operation not in schema['properties']['operation']['enum']:raise ValueError('Unsupported edit operation')
        args['op']=operation
    elif name=='photoshop_undo':args['op']='undo'
    elif name=='photoshop_snapshot':args['op']='snapshot'
    elif name=='photoshop_close':args['op']='close_session'
    elif name=='photoshop_status':args['op']='probe'
    else:raise ValueError('Unknown tool')
    return execute(args)

def handle(message):
    method=message.get('method');params=message.get('params') or {}
    if method=='initialize':return {'protocolVersion':params.get('protocolVersion','2024-11-05'),'capabilities':{'tools':{}},'serverInfo':{'name':'photoshop-edge-local','version':'0.2.0'},'instructions':'Inspect the matching reference and raw artwork, then set an image-specific plan before editing managed copies. Black contours present in the reference may be intentional. Automatically reinspect uncertain regions; preserve and report anything still unresolved without blocking the batch for human input. Inspect original and revised edge crops. Tool completion and pixel-scope audits are not visual acceptance.'}
    if method=='ping':return {}
    if method=='tools/list':return {'tools':TOOLS}
    if method=='tools/call':
        try:
            result=call(params['name'],params.get('arguments') or {})
            content=[{'type':'text','text':json.dumps(result,ensure_ascii=False)}]
            if result.get('image_path'):
                content.append({'type':'image','mimeType':'image/png','data':base64.b64encode(Path(result['image_path']).read_bytes()).decode('ascii')})
            return {'content':content,'isError':result.get('ok') is False}
        except Exception as exc:return {'content':[{'type':'text','text':str(exc)}],'isError':True}
    if method.startswith('notifications/'):return None
    raise ValueError('Unsupported MCP method: '+str(method))

def main():
    # MCP stdio uses one JSON-RPC message per line. Keep stdout protocol-only.
    for line in sys.stdin:
        try:
            msg=json.loads(line);result=handle(msg)
            if 'id' in msg:print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result},ensure_ascii=False),flush=True)
        except Exception as exc:
            if 'msg' in locals() and 'id' in msg:print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'error':{'code':-32603,'message':str(exc)}}),flush=True)
if __name__=='__main__':main()
