from __future__ import annotations
import json,shutil,tempfile,threading,time,uuid
from pathlib import Path
from . import schemas
from .common import ProcessLock,NativeUncertain,AgentError,TERMINAL,VERSION,read,write,digest,inside
from .codex import CodexRunner,planner_prompt,review_prompt
from .images import contacts,coordinate_preview,rgba,comparison,review_images,pixel_polygons,image_facts
from .native import NativeBackend
from .store import Store

FLAGS=('reference_correct','subject_preserved','target_border_removed','removed_border_is_transparent','background_correct','shape_and_alpha_ok')

def review_status(review,plan):
    if len(review.get('inspected_regions',[]))<3:raise ValueError('验收未记录足够的实际检查位置。')
    for item in review['inspected_regions']+review.get('issues',[]):pixel_polygons([item['region']],(1001,1001))
    if review['status']=='pass' and all(review.get(k) is True for k in FLAGS) and not review['issues'] and not plan['unresolved_notes'] and not plan['uncertain_regions']:return 'passed'
    return 'partial' if review.get('subject_preserved') and review.get('shape_and_alpha_ok') and review.get('reference_correct') else 'failed'

def best_attempt(attempts):
    eligible=[a for a in attempts if a.get('native') and a.get('review')]
    # Never prefer a later damaged version just because its residual border is smaller.
    return max(eligible,key=lambda a:(a['status']=='passed',a['status']=='partial',sum(a['review'].get(k) is True for k in FLAGS),-len(a['review']['issues']),a['number']),default=None)

class Pipeline:
    def __init__(self,store,bid,stop=None,agent_factory=CodexRunner,backend_factory=NativeBackend,channel_lock=None):
        self.store=store;self.bid=bid;self.batch=store.batch(bid);self.settings=self.batch['settings']
        if self.batch['manifest'].get('version')!=VERSION:raise ValueError('此批次使用旧版补色去边规则。请重新导入生成透明去边批次，旧记录保留供对比。')
        self.folder=store.root/'batches'/bid;self.stop=stop or threading.Event()
        self.items={i['id']:i for i in self.batch['manifest']['items']}
        self.agent=agent_factory(self.settings,self.log);self.backend_factory=backend_factory
        self.channel_lock=Path(channel_lock) if channel_lock else Path(tempfile.gettempdir())/'photoshop-edge-cleanup'/'photoshop.lock'
    def log(self,message):self.store.event(self.bid,message)
    def call(self,stage,prompt,schema,images,parent):
        return self.agent.call(stage,prompt,schema,images,Path(parent)/('call-'+uuid.uuid4().hex))
    def classify(self):
        path=self.folder/'classification.json'
        if path.exists():roles=read(path)
        elif self.settings['mixed']:
            roles={}
            items=list(self.items.values())
            for start in range(0,len(items),9):
                group=items[start:start+9];pages=contacts(group,self.folder/f'classify-{start}')
                answer=self.call('区分素材与参考','按图片实际内容区分可编辑平面素材 artwork、实物/场景参考 reference、不明确 unknown。白背景不一定是平面图；有透视、挂件、投影时谨慎。每个 ID 必须恰好出现一次。数据：'+json.dumps([{k:i[k] for k in ('id','relative','size')} for i in group],ensure_ascii=False),schemas.CLASSIFY,pages,self.folder/'classify')
                got=answer['items']
                if len(got)!=len(group) or {r['id'] for r in got}!={i['id'] for i in group}:raise ValueError('分类结果遗漏或重复图片。')
                for r in got:
                    if r['role'] not in ('artwork','reference','unknown'):raise ValueError('分类角色不合法。')
                    roles[r['id']]=r
            write(path,roles)
        else:
            roles={i['id']:{'id':i['id'],'role':i['role'],'reason':'用户选择的输入目录'} for i in self.items.values()};write(path,roles)
        if set(roles)!=set(self.items):raise ValueError('分类记录与导入清单不一致。')
        for ident,role in roles.items():
            if role['role']=='reference':continue
            self.store.add_job(self.bid+'-'+ident,self.bid,ident,self.items[ident]['relative'],
                               'failed' if role['role']=='unknown' else 'pending',role['reason'] if role['role']=='unknown' else '')
        for job in self.store.jobs(self.bid):
            role=roles[job['source_id']]
            if role['role']=='unknown' and not job['result']:self.deliver(job,self.items[job['source_id']],None,[],role['reason'])
        self.references=[self.items[k] for k,r in roles.items() if r['role']=='reference']
        self.roles=roles
    def match(self,item,folder):
        path=folder/'match.json'
        if path.exists():answer=read(path)
        elif not self.references:answer={'status':'none','reference_id':'','reason':'本批次没有参考图'};write(path,answer)
        else:
            shortlist=[]
            for start in range(0,len(self.references),18):
                group=self.references[start:start+18];pages=contacts(group,folder/f'references-{start}')
                answer=self.call('匹配参考图','第一张为待处理平面图，后续为带 ID 的候选参考。仅当图案、文字、形状相符才 matched，返回其 ID；本组没有则 none；疑似但无法确认则 uncertain。不能只凭文件名相似。候选：'+json.dumps([{k:r[k] for k in ('id','relative')} for r in group],ensure_ascii=False),schemas.MATCH,[item['snapshot']]+pages,folder/'match-calls')
                if answer['status']=='uncertain':raise ValueError('参考匹配不确定：'+answer['reason'])
                if answer['status']=='matched':
                    if answer['reference_id'] not in {r['id'] for r in group}:raise ValueError('参考图 ID 不在候选中。')
                    shortlist.append(answer)
            if len(shortlist)>1:raise ValueError('存在多个匹配参考，不能自动选定处理标准。')
            answer=shortlist[0] if shortlist else {'status':'none','reference_id':'','reason':'视觉检查未找到匹配参考'};write(path,answer)
        if answer['status']=='matched':
            if answer['reference_id'] not in {r['id'] for r in self.references}:raise ValueError('已保存匹配记录失效。')
            return self.items[answer['reference_id']]
        if answer['status']!='none' or not self.settings['no_reference_default']:raise ValueError('缺少可靠参考，且未授权无参考默认规则。')
        return None
    def run(self):
        with ProcessLock(self.store.root/'queue.lock'),ProcessLock(self.channel_lock):
            try:
                pause_file=self.folder/'pause.request'
                if pause_file.exists():pause_file.unlink()
                self.store.state(self.bid,'running');self.log('开始/继续批次；先核对快照和 PS 上次指令。')
                for item in self.items.values():
                    if digest(item['snapshot'])!=item['sha256']:raise ValueError('输入快照发生变化：'+item['id'])
                self.native=self.backend_factory(self.folder/'native',self.settings);self.native.recover()
                self.store.recover(self.bid);self.classify()
                for job in self.store.jobs(self.bid):
                    if self.stop.is_set() or pause_file.exists():self.stop.set();break
                    if job['status'] in TERMINAL:continue
                    if self.roles[job['source_id']]['role']=='unknown':
                        self.deliver(job,self.items[job['source_id']],None,[],'素材类型仍不明确。');continue
                    if job['status']=='editing':
                        active=job['result'].get('active')
                        job['status']='pending_review' if active and (Path(active)/'native.json').exists() else 'pending'
                        self.store.update(job['id'],status=job['status'])
                    try:self.process_job(job)
                    except (NativeUncertain,AgentError):raise
                    except Exception as exc:
                        self.log('图片进入异常列表：'+job['title']+'；'+str(exc))
                        # A prior independently reviewed safe partial is still useful.
                        folder=self.folder/'jobs'/job['source_id'];attempts=self.load_attempts(folder)
                        self.deliver(job,self.items[job['source_id']],None,attempts,str(exc))
                    self.summarize()
                self.store.state(self.bid,'paused' if self.stop.is_set() else 'completed')
                self.log('已暂停，继续时跳过已完成图片。' if self.stop.is_set() else '本批次运行结束；通过与异常已分别归档。')
            except Exception as exc:
                self.store.state(self.bid,'blocked',str(exc));self.log('批次暂停：'+str(exc));raise
            finally:self.summarize()
    def load_attempts(self,folder):
        return [read(p) for p in sorted(Path(folder).glob('attempt-*/record.json'))]
    def process_job(self,job):
        item=self.items[job['source_id']];folder=self.folder/'jobs'/item['id'];folder.mkdir(parents=True,exist_ok=True)
        self.store.update(job['id'],status='matching');reference=self.match(item,folder)
        self.store.update(job['id'],reference_id=reference['id'] if reference else None)
        ref=reference['snapshot'] if reference else None
        meta={'source':{k:item[k] for k in ('id','relative','size','mode')},'reference':reference['relative'] if reference else None,
              'pixel_facts':image_facts(item['snapshot']),
              'no_reference_default':self.settings['no_reference_default'],'allow_colour_repair':self.settings['allow_colour_repair'],
              'acceptance':'没有多余黑边；不误删主体；有意设计线条保留。'}
        grid=folder/'source_coordinates.png';coordinate_preview(item['snapshot'],grid)
        attempts=self.load_attempts(folder);feedback=None
        for number in range(self.settings['max_repairs']+1):
            prior=next((a for a in attempts if a['number']==number),None)
            if prior and prior.get('review'):
                if prior['status']=='passed':break
                feedback=prior['review'];continue
            attempt=folder/f'attempt-{number:02d}'
            # Interrupted preparation may restart; actual completed native output is reused.
            saved_native=attempt/'native.json'
            if saved_native.exists():
                plan=read(attempt/'plan.json');artifact=read(attempt/'prepared.json');native=read(saved_native)
            else:
                if prior and prior.get('preflight_rejected'):
                    feedback={'reason':prior['error'],'previous_plan':prior['plan']};continue
                self.store.update(job['id'],status='planning',attempt=number,result={'active':str(attempt)})
                plan=None
                try:
                    plan=self.call('制定区域方案',planner_prompt(meta,feedback),schemas.PLAN,[item['snapshot']]+([ref] if ref else [])+[grid],attempt/'plan-calls')
                    write(attempt/'plan.json',plan)
                    if not plan['is_flat_artwork'] or plan['kind']=='unsupported':raise ValueError('输入不适合平面去边：'+'；'.join(plan['unresolved_notes']))
                    if reference and not plan['reference_matches']:raise ValueError('全尺寸参考与素材不匹配。')
                    artifact=self.native.prepare(item['snapshot'],ref,plan,attempt)
                    scope=artifact['preflight_scope']['effective_scope_path']
                    gate=self.call('执行前检查','依次查看原始图、参考（如有）、分离背景后、修补预览、范围图。核查主体是否误删/跨材质补色/修改保护内容。少量可补修残边允许进入独立验收；主体受损或范围错误则 safe_to_execute=false。方案：'+json.dumps(plan,ensure_ascii=False),schemas.PREFLIGHT,[item['snapshot']]+([ref] if ref else [])+[attempt/'prepared.png',attempt/'candidate.png',scope],attempt/'preflight-calls')
                    write(attempt/'preflight.json',gate)
                    if not gate['safe_to_execute']:raise ValueError('执行前检查未通过：'+gate['reason'])
                except AgentError:raise
                except Exception as exc:
                    feedback={'reason':str(exc),'previous_plan':locals().get('plan')}
                    record={'number':number,'status':'failed','preflight_rejected':True,'plan':locals().get('plan'),'error':str(exc)}
                    write(attempt/'record.json',record);attempts.append(record)
                    self.log('方案第 '+str(number+1)+' 次未通过：'+str(exc));continue
                self.store.update(job['id'],status='editing',attempt=number,result={'active':str(attempt)})
                native=self.native.process(item['snapshot'],ref,plan,attempt,artifact)
            self.store.update(job['id'],status='reviewing',attempt=number,title=plan['title'] or item['relative'],result={'active':str(attempt)})
            images=review_images(native['before'],native['png'],attempt,artifact['compiled'])
            review=self.call('独立验收',review_prompt(dict(meta,plan=plan,round=number,remaining_repairs=self.settings['max_repairs']-number,pixel_audit=native)),schemas.REVIEW,[item['snapshot']]+([ref] if ref else [])+images,attempt/'review-calls')
            status=review_status(review,plan);record={'number':number,'status':status,'plan':plan,'native':native,'review':review,'reference':ref}
            write(attempt/'record.json',record);attempts.append(record)
            self.log(item['id']+' 第 '+str(number+1)+' 轮：'+status+'；'+review['reason'])
            if status=='passed':break
            feedback=dict(review,previous_plan=plan)
        self.deliver(job,item,ref,attempts)
    def deliver(self,job,item,reference,attempts,error=''):
        best=best_attempt(attempts);status=best['status'] if best else 'failed'
        dest=self.folder/'delivery'/status/item['id'];dest.mkdir(parents=True,exist_ok=True)
        result={'attempts':len(attempts),'status':status,'review_type':'模型视觉初检，未经人工验收','error':error}
        if best:
            if status!='passed' and not result['error']:result['error']=best['review']['reason']
            result.update(selected_attempt=best['number'],review=best['review'],plan=best['plan'])
            reference=best.get('reference') or reference
            for kind in ('png','psd'):
                target=dest/('result.'+kind);shutil.copy2(best['native'][kind],target);result[kind]=str(target)
            comparison(best['native']['before'],best['native']['png'],reference,dest/'comparison.png',best['plan']['title'],{'passed':'自动初检通过','partial':'部分完成','failed':'异常结果，仅供排查'}[status])
            result['comparison']=str(dest/'comparison.png')
        else:
            shutil.copy2(item['snapshot'],dest/('original'+Path(item['snapshot']).suffix))
            result['error']=error or (attempts[-1].get('error','没有可靠结果') if attempts else '没有可靠结果')
        result['input_snapshot_sha256']=item['sha256']
        result['original_file_unchanged']=Path(item['original']).is_file() and digest(item['original'])==item['sha256']
        write(dest/'report.json',result)
        self.store.update(job['id'],status=status,result=result,error=result['error'])
    def summarize(self):
        jobs=self.store.jobs(self.bid);counts={key:sum(j['status']==key for j in jobs) for key in ('passed','partial','failed')}
        usage={'seconds':0,'input_tokens':0,'output_tokens':0,'cached_input_tokens':0,'calls':0}
        for path in self.folder.glob('**/usage.json'):
            record=read(path);usage['calls']+=1;usage['seconds']+=record.get('seconds',0)
            for k in ('input_tokens','output_tokens','cached_input_tokens'):usage[k]+=record.get('usage',{}).get(k,0)
        write(self.folder/'summary.json',{'batch':self.store.batch(self.bid),'counts':counts,'total':len(jobs),'jobs':jobs,'codex_usage':usage})

def retry_exceptions(store,bid):
    with ProcessLock(store.root/'queue.lock'):
        # Keep old attempts for audit, but start a fresh bounded repair budget.
        for job in store.jobs(bid):
            if job['status'] not in ('failed','partial'):continue
            folder=store.root/'batches'/bid/'jobs'/job['source_id']
            if folder.exists():
                archive=store.root/'batches'/bid/'retry_archive'/(job['source_id']+'-'+uuid.uuid4().hex)
                if not inside(folder,store.root) or not inside(archive,store.root):raise ValueError('归档路径越出结果目录。')
                archive.parent.mkdir(parents=True,exist_ok=True);folder.rename(archive)
            delivery=store.root/'batches'/bid/'delivery'/job['status']/job['source_id']
            if delivery.exists():
                archive=store.root/'batches'/bid/'retry_archive'/('delivery-'+job['source_id']+'-'+uuid.uuid4().hex)
                if not inside(delivery,store.root) or not inside(archive,store.root):raise ValueError('归档路径越出结果目录。')
                archive.parent.mkdir(parents=True,exist_ok=True);delivery.rename(archive)
        store.retry(bid)
