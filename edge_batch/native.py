from __future__ import annotations
import os,sys,time,uuid
from pathlib import Path
import numpy as np
from PIL import Image
from .common import SKILL,NativeUncertain,read,write,digest,inside
from .images import compile_plan,candidate,patch_assets,rgba,alpha_audit

sys.path.insert(0,str(SKILL/'scripts'))
import ps_tool as ps
import reference_plan as rp

class NativeBackend:
    def __init__(self,workspace,settings):
        self.workspace=Path(workspace).resolve();self.runtime=self.workspace/'photoshop_agent/runtime';self.settings=settings
        self.pending=self.workspace/'native_intent.json'
        self.runtime.mkdir(parents=True,exist_ok=True)
        ps.WORKSPACE=self.workspace;ps.RUNTIME=self.runtime
        ps.EXE=Path(settings['photoshop_exe']) if settings['photoshop_exe'] else None
        if ps.EXE is None or not ps.EXE.is_file():raise ValueError('请先在设置中指定已安装的 Photoshop.exe。')
    def call(self,request):
        write(self.pending,{'state':'started','time':time.time(),'request':request})
        try:result=ps.execute(request)
        except (TimeoutError,FileExistsError) as exc:
            raise NativeUncertain('PS 指令结果尚未确认，暂停执行通道：'+str(exc)) from exc
        except Exception as exc:
            # No successful response means the in-flight native state must be
            # reconciled before launching another image, even after a Python error.
            raise NativeUncertain('PS 执行异常，需检查指令记录：'+str(exc)) from exc
        write(self.pending,{'state':'finished','time':time.time(),'request':request,'result':result})
        if not result.get('ok'):raise RuntimeError('PS 已返回失败：'+str(result.get('error',result)))
        return result
    def recover(self):
        """Reconcile prior completed native calls; never replay an unknown call."""
        lock=self.runtime/'execution.lock'
        pending=[]
        for request in self.runtime.glob('**/request.json'):
            if not (request.parent/'response.json').exists():pending.append(request)
        if pending:raise NativeUncertain('上次 PS 指令缺少响应，不能自动重放。请确认 PS 状态后重新继续；记录：'+str(pending[-1].parent))
        if lock.exists():
            # A lock with no outstanding native request can still be an active
            # Python preparation. Do not remove it while its owner is alive.
            try:pid=int(lock.read_text(encoding='utf-8'))
            except ValueError:raise NativeUncertain('PS 锁文件内容异常，请检查运行记录。')
            alive=False
            if os.name=='nt':
                import ctypes
                kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                kernel.OpenProcess.restype=ctypes.c_void_p;kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
                kernel.CloseHandle.argtypes=[ctypes.c_void_p]
                handle=kernel.OpenProcess(0x1000,False,pid)
                if handle:alive=True;kernel.CloseHandle(handle)
                elif ctypes.get_last_error()==5:alive=True
            else:
                try:os.kill(pid,0);alive=True
                except ProcessLookupError:pass
            if alive:raise NativeUncertain('另一个 PS 指令进程仍在运行。')
            if not inside(lock,self.workspace):raise NativeUncertain('锁路径越界。')
            lock.unlink()
        status=self.call({'op':'probe'});known={}
        for path in self.runtime.glob('sessions/*/jobs/*/request.json'):
            req=read(path)
            if req.get('owner_tag'):known[req['owner_tag']]=path.parent
        for document in status.get('managed_documents',[]):
            tag=document['name']
            if tag not in known:continue
            # Preserve a recovery snapshot, including any later user edits,
            # before closing a document that this workspace demonstrably owns.
            folder=self.runtime/'recovery'/uuid.uuid4().hex;folder.mkdir(parents=True)
            for operation in ('snapshot','close_session'):
                payload={'op':operation,'owner_tag':tag,'document_id':document['document_id'],
                         'psd_path':str(folder/'recovered.psd'),'png_path':str(folder/'recovered.png')}
                try:result=ps.invoke(payload,folder/operation)
                except Exception as exc:raise NativeUncertain('恢复托管文档失败：'+str(exc)) from exc
                if not result.get('ok'):raise NativeUncertain('恢复托管文档失败：'+str(result))
        return status
    def prepare(self,source,reference,plan,folder):
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        compiled=compile_plan(plan,rgba(source).size,self.settings['allow_colour_repair'])
        with Image.open(source) as original:
            prepared,kind=ps.prepare_source(original,plan['remove_white_background'],plan['allow_edge_touching'],plan['frame_barriers'])
        if prepared.getchannel('A').getbbox() is None:raise ValueError('背景分离后主体为空。')
        prepared.save(folder/'prepared.png');desired,metrics=candidate(prepared,plan,compiled)
        alpha_changes=alpha_audit(prepared,desired)
        if any(op['method']=='erase_edge' for op in compiled['operations']) and not alpha_changes['fully_cleared_pixels']:
            raise ValueError('透明去边没有清除任何目标像素，不能用颜色改变或空操作替代抠图。')
        coloured=patch_assets(prepared,desired,folder)
        request={'op':'set_plan','reference_observations':plan['reference_observations'],
                 'remove_rules':plan['remove_rules'],'preserve_rules':plan['preserve_rules'],
                 'remove_regions':compiled['remove_regions'],'protect_regions':compiled['protect_regions'],
                 'uncertain_regions':compiled['uncertain_regions'],'unresolved_notes':plan['unresolved_notes']}
        if reference:request['reference_path']=str(reference)
        else:request['no_reference_default']=self.settings['no_reference_default']
        fake={'revision':0,'width':prepared.width,'height':prepared.height,'history':[{'revision':0,'png_path':str(folder/'prepared.png')}]}
        scope=rp.create(request,fake,folder/'preflight',ps.shapes)
        audit=rp.audit(folder/'prepared.png',folder/'candidate.png',scope)
        if audit['changes_outside_edit_scope']:raise ValueError('修补预览超出计划范围。')
        artifact={'plan_request':request,'compiled':compiled,'input_kind':kind,'colour_pixels':coloured,
                  'metrics':metrics,'alpha_audit':alpha_changes,'preflight_scope':scope,'source_hash':digest(source),'reference_hash':digest(reference) if reference else None}
        write(folder/'prepared.json',artifact);return artifact
    def process(self,source,reference,plan,folder,artifact):
        folder=Path(folder);sid=None;uncertain=False
        if digest(source)!=artifact['source_hash'] or (reference and digest(reference)!=artifact['reference_hash']):raise ValueError('执行前输入副本发生变化。')
        # Normalize an RGB/JPEG input inside the workspace, never the user's file.
        source_png=folder/'source.png'
        rgba(source).save(source_png)
        try:
            opened=self.call({'op':'open_copy','source':str(source_png),'remove_white_background':plan['remove_white_background'],
                              'allow_edge_touching_artwork':plan['allow_edge_touching'],'frame_barriers':plan['frame_barriers']})
            sid=opened['session'];write(folder/'managed_session.json',opened)
            self.call(dict(artifact['plan_request'],session=sid))
            w,h=rgba(source_png).size
            if artifact['colour_pixels']:
                self.call({'op':'patch','session':sid,'patch_path':str(folder/'patch.png'),'regions':[[[0,0],[w,0],[w,h],[0,h]]]})
            state=read(self.runtime/'sessions'/sid/'session.json')
            current=np.array(rgba(state['png_path']))[:,:,3].astype('float32');target=np.array(rgba(folder/'candidate.png'))[:,:,3].astype('float32')
            if np.any(target>current):raise RuntimeError('PS 合成出现意外透明度丢失。')
            ratio=np.where(current>0,np.rint(255*target/np.maximum(current,1)),255).astype('uint8')
            if np.any((current>0)&(ratio<255)):
                Image.fromarray(ratio).save(folder/'alpha_ratio.png')
                self.call({'op':'mask','session':sid,'mask_path':str(folder/'alpha_ratio.png')})
            state=read(self.runtime/'sessions'/sid/'session.json')
            audit=rp.audit(opened['png_path'],state['png_path'],state['plan'])
            if audit['changes_outside_edit_scope']:raise RuntimeError('PS 实际导出发生越界修改。')
            actual=np.array(rgba(state['png_path']));expected=np.array(rgba(folder/'candidate.png'))
            visible=(actual[:,:,3]>0)|(expected[:,:,3]>0)
            changed=((actual[:,:,3]!=expected[:,:,3])|((actual[:,:,:3]!=expected[:,:,:3]).any(2)&visible))
            original=np.array(rgba(opened['png_path']))
            result={'png':state['png_path'],'psd':state['psd_path'],'before':opened['png_path'],'session':sid,
                    'scope_audit':audit,'candidate_difference_pixels':int(changed.sum()),
                    'alpha_audit':alpha_audit(Image.fromarray(original),Image.fromarray(actual)),
                    'opaque_pixels_lost':int(((original[:,:,3]>=128)&(actual[:,:,3]<128)).sum()),'closed':False}
            if result['candidate_difference_pixels']:raise RuntimeError('实际 PS 导出与预览不同，不能自动判为通过。')
        except NativeUncertain:
            uncertain=True;raise
        finally:
            if sid and not uncertain:
                try:self.call({'op':'close_session','session':sid,'discard_unsaved_changes':True})
                except Exception as exc:raise NativeUncertain('托管文档关闭结果不明确：'+str(exc)) from exc
        result['closed']=True;write(folder/'native.json',result);return result
