from __future__ import annotations
import os,shutil,threading,tkinter as tk
from pathlib import Path
from tkinter import ttk,filedialog,messagebox
from .common import defaults,read,write,ROOT
from .importer import create_batch
from .pipeline import Pipeline,retry_exceptions
from .store import Store

LABELS={'imported':'已导入','running':'运行中','paused':'已暂停','blocked':'暂停并需排查','completed':'已结束',
        'pending':'等待','pending_review':'等待验收','matching':'匹配参考','planning':'分析方案','editing':'PS 处理中','reviewing':'独立验收','passed':'自动初检通过','partial':'部分完成','failed':'异常'}

class App:
    def __init__(self,root,settings_path=None):
        self.root=root;root.title('Photoshop 批量去边 · 本地工作流');root.geometry('1050x780');root.minsize(900,680)
        self.path=Path(settings_path) if settings_path else Path(os.environ.get('LOCALAPPDATA',str(ROOT)))/'PhotoshopEdgeCleanup'/'settings.json'
        saved=read(self.path) if self.path.exists() else {};settings=defaults();settings.update(saved.get('settings',{}))
        if not settings['codex_exe'] or settings['codex_exe']=='codex':
            found=shutil.which('codex')
            if not found:
                matches=list((Path.home()/'AppData/Local/OpenAI/Codex/bin').glob('*/codex.exe'));found=str(max(matches,key=lambda p:p.stat().st_mtime)) if matches else ''
            if found:settings['codex_exe']=found
        self.values={k:tk.StringVar(value=saved.get(k,'')) for k in ('input','references','output')}
        self.config={k:(tk.BooleanVar(value=v) if isinstance(v,bool) else tk.StringVar(value=str(v))) for k,v in settings.items()}
        self.store=None;self.bid=None;self.worker=None;self.stop=threading.Event();self.event_id=0;self.closing=False;self.worker_error=None;self.import_result=None
        box=ttk.Frame(root,padding=16);box.pack(fill='both',expand=True)
        ttk.Label(box,text='导入素材 → 对照参考 → 自动修补 → 独立初检',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
        ttk.Label(box,text='逐张处理，最多补修两轮。结果分为“通过 / 部分完成 / 异常”；原始文件保留。').pack(anchor='w',pady=(4,12))
        form=ttk.Frame(box);form.pack(fill='x');form.columnconfigure(1,weight=1)
        for row,(key,label) in enumerate([('input','素材文件夹'),('references','参考文件夹（可空）'),('output','结果文件夹')]):
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w',padx=(0,12),pady=4)
            ttk.Entry(form,textvariable=self.values[key]).grid(row=row,column=1,sticky='ew')
            ttk.Button(form,text='选择',command=lambda k=key:self.browse(k)).grid(row=row,column=2,padx=(8,0))
        flags=ttk.Frame(box);flags.pack(fill='x',pady=6)
        for key,label in [('simple_black_edge','简化黑边模式'),('mixed','素材与参考混放'),('no_reference_default','无参考时去额外外边框'),('allow_colour_repair','允许主体内部局部补色')]:ttk.Checkbutton(flags,text=label,variable=self.config[key]).pack(side='left',padx=(0,15))
        settings_box=ttk.LabelFrame(box,text='运行设置',padding=8);settings_box.pack(fill='x',pady=6);settings_box.columnconfigure(1,weight=1)
        for row,(key,label) in enumerate([('photoshop_exe','Photoshop.exe'),('codex_exe','Codex CLI'),('model','模型（留空使用默认）'),('simple_max_width','简化模式最大黑边宽度')]):
            ttk.Label(settings_box,text=label).grid(row=row,column=0,sticky='w',padx=(0,12),pady=3)
            ttk.Entry(settings_box,textvariable=self.config[key]).grid(row=row,column=1,sticky='ew')
            if key in ('photoshop_exe','codex_exe'):ttk.Button(settings_box,text='选择',command=lambda k=key:self.browse_exe(k)).grid(row=row,column=2,padx=(8,0))
        bar=ttk.Frame(box);bar.pack(fill='x',pady=8);self.buttons=[]
        for label,fn in [('导入并开始',self.start),('载入已有批次',self.load),('继续选中批次',self.resume),('重试异常',self.retry)]:
            button=ttk.Button(bar,text=label,command=fn);button.pack(side='left',padx=(0,8));self.buttons.append(button)
        ttk.Button(bar,text='暂停（本张完成后）',command=self.pause).pack(side='left',padx=(0,8))
        ttk.Button(bar,text='打开结果',command=self.open_results).pack(side='left')
        self.batch_var=tk.StringVar();self.batch_box=ttk.Combobox(box,textvariable=self.batch_var,state='readonly');self.batch_box.pack(fill='x',pady=4);self.batch_box.bind('<<ComboboxSelected>>',self.select_batch)
        self.status=tk.StringVar(value='请选择素材与结果文件夹；首次使用需先登录 Codex CLI。');ttk.Label(box,textvariable=self.status).pack(anchor='w',pady=5)
        self.tree=ttk.Treeview(box,columns=('title','state','round','error'),show='headings',height=9)
        for key,label,width in [('title','图片',280),('state','状态',140),('round','补修轮次',80),('error','异常说明',400)]:self.tree.heading(key,text=label);self.tree.column(key,width=width)
        self.tree.pack(fill='both',expand=True);self.tree.bind('<Double-1>',self.open_comparison)
        self.logs=tk.Text(box,height=6,wrap='word',state='disabled');self.logs.pack(fill='x',pady=(8,0))
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(500,self.poll)
    def browse(self,key):
        value=filedialog.askdirectory(parent=self.root)
        if value:self.values[key].set(value)
    def browse_exe(self,key):
        value=filedialog.askopenfilename(parent=self.root,filetypes=[('程序','*.exe'),('所有文件','*')])
        if value:self.config[key].set(value)
    def settings(self):
        result={k:v.get() for k,v in self.config.items()}
        for k in ('max_repairs','agent_timeout','simple_max_width','simple_dark_threshold','simple_chroma_limit'):
            if k in result:result[k]=int(result[k])
        return result
    def save(self):write(self.path,dict({k:v.get() for k,v in self.values.items()},settings=self.settings()))
    def guarded(self,fn):
        try:fn()
        except Exception as exc:messagebox.showerror('未能开始',str(exc),parent=self.root)
    def start(self):
        def action():
            if self.worker and self.worker.is_alive():return
            self.save();args=(self.values['input'].get(),self.values['references'].get(),self.values['output'].get(),self.settings())
            self.stop.clear();self.worker_error=None;self.store=None;self.bid=None;self.status.set('正在导入并核对素材快照…')
            def work():
                try:self.import_result=create_batch(*args)
                except Exception as exc:self.worker_error=str(exc)
            self.worker=threading.Thread(target=work,daemon=False);self.worker.start()
            for button in self.buttons:button.configure(state='disabled')
            self.batch_box.configure(state='disabled')
        self.guarded(action)
    def load(self):
        def action():
            root=self.values['output'].get()
            if not root or not (Path(root)/'queue.sqlite3').exists():raise ValueError('请选择包含 queue.sqlite3 的已有结果文件夹。')
            self.store=Store(root);self.refresh_batches()
        self.guarded(action)
    def refresh_batches(self):
        batches=self.store.batches();self.batch_box['values']=[b['id']+' · '+LABELS.get(b['state'],b['state']) for b in batches]
        if not self.bid and batches:self.bid=batches[0]['id']
        for value in self.batch_box['values']:
            if value.startswith(self.bid or '!'):self.batch_var.set(value);break
        self.event_id=0;self.refresh_jobs()
    def select_batch(self,event=None):self.bid=self.batch_var.get().split(' · ')[0];self.event_id=0;self.refresh_jobs()
    def resume(self):self.guarded(self.run)
    def retry(self):
        def action():
            if not self.bid:raise ValueError('请先载入并选择批次。')
            retry_exceptions(self.store,self.bid);self.run()
        self.guarded(action)
    def run(self):
        if not self.bid:raise ValueError('请先导入或选择批次。')
        if self.worker and self.worker.is_alive():return
        self.stop.clear();self.worker_error=None
        pipeline=Pipeline(self.store,self.bid,self.stop)
        def work():
            try:pipeline.run()
            except Exception as exc:self.worker_error=str(exc)
        self.worker=threading.Thread(target=work,daemon=False);self.worker.start()
        for button in self.buttons:button.configure(state='disabled')
        self.batch_box.configure(state='disabled')
    def pause(self):
        self.stop.set()
        if self.store and self.bid:(self.store.root/'batches'/self.bid/'pause.request').write_text('pause',encoding='utf-8')
        self.status.set('将在当前图片完成后暂停；可稍后继续。')
    def refresh_jobs(self):
        if not self.store or not self.bid:return
        jobs=self.store.jobs(self.bid);selected=self.tree.selection();scroll=self.tree.yview()[0]
        for row in self.tree.get_children():self.tree.delete(row)
        for job in jobs:self.tree.insert('', 'end',iid=job['id'],values=(job['title'],LABELS.get(job['status'],job['status']),job['attempt'],job['error']))
        for row in selected:
            if self.tree.exists(row):self.tree.selection_add(row)
        self.tree.yview_moveto(scroll)
        batch=self.store.batch(self.bid);finished=sum(j['status'] in ('passed','partial','failed') for j in jobs)
        label=self.bid+' · '+LABELS.get(batch['state'],batch['state']);self.batch_var.set(label)
        self.batch_box['values']=[label if value.startswith(self.bid) else value for value in self.batch_box['values']]
        pause_pending=batch['state']=='running' and (self.stop.is_set() or (self.store.root/'batches'/self.bid/'pause.request').exists())
        state_label='等待本张完成后暂停' if pause_pending else LABELS.get(batch['state'],batch['state'])
        self.status.set(f"{state_label} · 已结束 {finished}/{len(jobs)} · "+batch['error'])
        events=self.store.events(self.bid,self.event_id)
        if events:
            self.logs.configure(state='normal')
            for event in events:self.logs.insert('end',event['message']+'\n');self.event_id=event['id']
            self.logs.see('end');self.logs.configure(state='disabled')
    def poll(self):
        if self.import_result and not self.worker.is_alive():
            self.store,self.bid,_=self.import_result;self.import_result=None;self.refresh_batches()
            if not self.closing and not self.stop.is_set():self.run()
        self.refresh_jobs()
        if not self.worker or not self.worker.is_alive():
            for button in self.buttons:button.configure(state='normal')
            self.batch_box.configure(state='readonly')
            if self.worker_error:
                error=self.worker_error;self.worker_error=None;messagebox.showerror('运行已暂停',error,parent=self.root)
            if self.closing:self.save();self.root.destroy();return
        self.root.after(1000,self.poll)
    def open_results(self):
        if self.store and self.bid:os.startfile(self.store.root/'batches'/self.bid)
    def open_comparison(self,event=None):
        selected=self.tree.selection()
        if self.store and selected:
            job=next(j for j in self.store.jobs(self.bid) if j['id']==selected[0]);path=job['result'].get('comparison')
            if path:os.startfile(path)
    def close(self):
        if self.worker and self.worker.is_alive():self.closing=True;self.pause();return
        self.save();self.root.destroy()

def launch(settings_path=None):
    root=tk.Tk();App(root,settings_path);root.mainloop()
