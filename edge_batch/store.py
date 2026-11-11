from __future__ import annotations
import json,sqlite3,time
from contextlib import contextmanager
from pathlib import Path
from .common import TERMINAL,write

class Store:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'queue.sqlite3'
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, created REAL NOT NULL,
              state TEXT NOT NULL, settings TEXT NOT NULL, manifest TEXT NOT NULL, error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,source_id TEXT NOT NULL,
              title TEXT NOT NULL,status TEXT NOT NULL,attempt INTEGER NOT NULL DEFAULT 0,
              reference_id TEXT,result TEXT NOT NULL DEFAULT '{}',error TEXT NOT NULL DEFAULT '',updated REAL NOT NULL,
              UNIQUE(batch_id,source_id));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,batch_id TEXT,job_id TEXT,
              time REAL NOT NULL,message TEXT NOT NULL);
            ''')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def batch(self,bid):
        with self.connect() as db:row=db.execute('SELECT * FROM batches WHERE id=?',(bid,)).fetchone()
        if not row:raise ValueError('批次不存在。')
        value=dict(row);value['settings']=json.loads(value['settings']);value['manifest']=json.loads(value['manifest']);return value
    def batches(self):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT id,created,state,error FROM batches ORDER BY created DESC')]
    def create(self,bid,settings,manifest):
        with self.connect() as db:
            cur=db.execute('INSERT OR IGNORE INTO batches(id,created,state,settings,manifest) VALUES(?,?,?,?,?)',
                           (bid,time.time(),'imported',json.dumps(settings),json.dumps(manifest,ensure_ascii=False)))
        return bool(cur.rowcount)
    def state(self,bid,state,error=''):
        with self.connect() as db:db.execute('UPDATE batches SET state=?,error=? WHERE id=?',(state,error,bid))
    def event(self,bid,message,jid=None):
        with self.connect() as db:db.execute('INSERT INTO events(batch_id,job_id,time,message) VALUES(?,?,?,?)',(bid,jid,time.time(),message))
    def events(self,bid,after=0):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM events WHERE batch_id=? AND id>? ORDER BY id',(bid,after))]
    def add_job(self,jid,bid,source_id,title,status='pending',error=''):
        with self.connect() as db:db.execute('INSERT OR IGNORE INTO jobs(id,batch_id,source_id,title,status,error,updated) VALUES(?,?,?,?,?,?,?)',
                                            (jid,bid,source_id,title,status,error,time.time()))
    def jobs(self,bid):
        with self.connect() as db:rows=db.execute('SELECT * FROM jobs WHERE batch_id=? ORDER BY rowid',(bid,)).fetchall()
        values=[]
        for row in rows:
            value=dict(row);value['result']=json.loads(value['result']);values.append(value)
        return values
    def update(self,jid,**values):
        if not values or set(values)-{'status','attempt','reference_id','result','error','title'}:raise ValueError('Invalid queue update')
        if 'result' in values:values['result']=json.dumps(values['result'],ensure_ascii=False)
        values['updated']=time.time()
        with self.connect() as db:db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),jid))
    def recover(self,bid):
        """Read-only agent phases may restart. Native phases need reconciliation first."""
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='pending',error='上次运行中断，已恢复到待处理',updated=? WHERE batch_id=? AND status IN ('matching','planning')",(time.time(),bid))
            db.execute("UPDATE jobs SET status='pending_review',error='继续已保存成品的验收',updated=? WHERE batch_id=? AND status='reviewing'",(time.time(),bid))
    def retry(self,bid):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='pending',attempt=0,result='{}',error='',updated=? WHERE batch_id=? AND status IN ('failed','partial')",(time.time(),bid))
        self.state(bid,'paused')
