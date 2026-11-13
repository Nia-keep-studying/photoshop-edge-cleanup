from __future__ import annotations
import hashlib,json,os,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/'skills/photoshop-edge-cleanup'
VERSION='batch-v3.1-targeted-edge'
TERMINAL={'passed','partial','failed'}

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temp,path)
def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def stable(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
def inside(path,root):return Path(path).resolve().is_relative_to(Path(root).resolve())
def hidden_startup():
    if os.name!='nt':return None
    import subprocess
    info=subprocess.STARTUPINFO();info.dwFlags|=subprocess.STARTF_USESHOWWINDOW;info.wShowWindow=0
    return info

class BusyError(RuntimeError):pass
class NativeUncertain(RuntimeError):pass
class AgentError(RuntimeError):pass

class ProcessLock:
    """OS-held lock: process death releases it, unlike a PID marker file."""
    def __init__(self,path):self.path=Path(path);self.file=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=self.path.open('a+b');self.file.seek(0,2)
        if self.file.tell()==0:self.file.write(b'0');self.file.flush()
        self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except (OSError,IOError):
            self.file.close();self.file=None;raise BusyError('已有任务正在使用此输出目录。')
        return self
    def __exit__(self,*args):
        if self.file:
            self.file.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
            self.file.close()

def defaults():
    return {'photoshop_exe':os.environ.get('PS_EDGE_PHOTOSHOP_EXE',''),
            'codex_exe':'codex','model':'','max_repairs':2,'agent_timeout':600,
            'no_reference_default':True,'allow_colour_repair':True,'mixed':False}

def validate_settings(settings):
    out=defaults();out.update(settings)
    if type(out['max_repairs']) is not int or not 0<=out['max_repairs']<=2:raise ValueError('补修轮数必须为 0 到 2。')
    if type(out['agent_timeout']) is not int or not 60<=out['agent_timeout']<=1800:raise ValueError('Codex 超时必须为 60 到 1800 秒。')
    for key in ('no_reference_default','allow_colour_repair','mixed'):
        if type(out[key]) is not bool:raise ValueError(key+' 必须是布尔值。')
    for key in ('photoshop_exe','codex_exe','model'):
        if not isinstance(out[key],str):raise ValueError(key+' 必须是字符串。')
    return out
