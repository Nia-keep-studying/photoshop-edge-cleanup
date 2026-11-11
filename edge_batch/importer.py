from __future__ import annotations
import shutil
from pathlib import Path
from PIL import Image
from .common import VERSION,digest,inside,stable,validate_settings,write
from .store import Store

EXTENSIONS={'.png','.jpg','.jpeg','.webp'}

def scan(folder,role):
    folder=Path(folder).resolve(strict=True)
    if not folder.is_dir():raise ValueError('输入必须是文件夹。')
    result=[]
    for path in sorted(folder.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:continue
        if path.is_symlink():continue
        try:
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:size=list(im.size);mode=im.mode
            if size[0]*size[1]>24000000:raise ValueError('超过 2400 万像素')
        except Exception as exc:raise ValueError('无法读取图片 '+path.name+'：'+str(exc)) from exc
        result.append({'original':str(path),'relative':str(path.relative_to(folder)),'sha256':digest(path),'size':size,'mode':mode,'role':role})
    if not result:raise ValueError('文件夹中没有支持的图片。')
    return result

def create_batch(input_folder,reference_folder,output,settings):
    if not str(input_folder or '').strip() or not str(output or '').strip():raise ValueError('必须选择素材文件夹和结果文件夹。')
    settings=validate_settings(settings);source=Path(input_folder).resolve(strict=True);output=Path(output).resolve()
    references=Path(reference_folder).resolve(strict=True) if reference_folder else None
    if references==source:settings['mixed']=True;references=None
    for folder in [source]+([references] if references else []):
        if inside(output,folder) or inside(folder,output):raise ValueError('输出文件夹必须与素材/参考文件夹分开，不能互相包含。')
    if settings['mixed'] and references:raise ValueError('混合文件夹模式请把参考图放在同一输入目录，不另选参考文件夹。')
    items=scan(source,'unknown' if settings['mixed'] else 'artwork')
    if references:items+=scan(references,'reference')
    for i,item in enumerate(items,1):item['id']=f'I{i:04d}'
    fingerprint={'version':VERSION,'settings':settings,'items':[{k:v for k,v in item.items() if k!='snapshot'} for item in items]}
    bid='batch-'+stable(fingerprint)[:16];store=Store(output);folder=output/'batches'/bid
    existing={b['id'] for b in store.batches()}
    if bid in existing:return store,bid,False
    for item in items:
        dest=folder/'inputs'/(item['id']+Path(item['original']).suffix.lower());dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(item['original'],dest)
        if digest(dest)!=item['sha256']:raise ValueError('导入过程中素材发生变化，请重新导入。')
        item['snapshot']=str(dest)
    manifest={'input_folder':str(source),'reference_folder':str(references) if references else None,'items':items,'version':VERSION}
    write(folder/'manifest.json',manifest);write(folder/'settings.json',settings)
    return store,bid,store.create(bid,settings,manifest)
