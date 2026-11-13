from __future__ import annotations
import math
from pathlib import Path
import numpy as np
from scipy import ndimage as ndi
from PIL import Image,ImageDraw,ImageFont
from .common import SKILL,write

def font(size):
    for path in ['C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/arial.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if Path(path).exists():return ImageFont.truetype(path,size)
    return ImageFont.load_default()

def rgba(path):
    with Image.open(path) as im:
        if im.width*im.height>24000000:raise ValueError('单张图片超过 2400 万像素，第一版暂不自动处理。')
        return im.convert('RGBA')

def image_facts(path):
    a=np.array(rgba(path));rgb=a[:,:,:3]
    frame=np.concatenate([rgb[0],rgb[-1],rgb[:,0],rgb[:,-1]])
    return {'alpha_min':int(a[:,:,3].min()),'alpha_max':int(a[:,:,3].max()),
            'transparent_fraction':round(float((a[:,:,3]<128).mean()),5),
            'frame_light_fraction':round(float((frame.min(1)>=246).mean()),5),
            'near_white_background_threshold':235}

def flatten(image,bg='#dddddd'):
    im=rgba(image) if isinstance(image,(Path,str)) else image.convert('RGBA')
    base=Image.new('RGBA',im.size,bg);base.alpha_composite(im);return base.convert('RGB')

def paste_fit(sheet,image,box,bg='#dddddd'):
    im=flatten(image,bg);x,y,w,h=box;im.thumbnail((w,h),Image.Resampling.LANCZOS)
    sheet.paste(im,(x+(w-im.width)//2,y+(h-im.height)//2))

def contacts(items,folder,prefix='catalog',per_page=9):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);paths=[]
    for page,start in enumerate(range(0,len(items),per_page),1):
        group=items[start:start+per_page];sheet=Image.new('RGB',(1200,math.ceil(len(group)/3)*360),'#f1f3f5');d=ImageDraw.Draw(sheet)
        for i,item in enumerate(group):
            x=i%3*400;y=i//3*360;d.text((x+12,y+8),item['id'],font=font(22),fill='#223344')
            paste_fit(sheet,item['snapshot'],(x+10,y+40,380,305))
        path=folder/f'{prefix}_{page}.png';sheet.save(path);paths.append(path)
    return paths

def coordinate_preview(path,dest):
    im=flatten(path);w,h=im.size;layer=Image.new('RGBA',im.size);d=ImageDraw.Draw(layer)
    size=max(13,round(min(w,h)/55))
    for n in range(100,1000,100):
        x=round(w*n/1000);y=round(h*n/1000)
        d.line((x,0,x,h),fill=(0,110,170,45),width=1);d.line((0,y,w,y),fill=(0,110,170,45),width=1)
        d.text((x+2,2),str(n),font=font(size),fill=(0,80,150,240),stroke_width=1,stroke_fill='white')
        d.text((2,y+2),str(n),font=font(size),fill=(0,80,150,240),stroke_width=1,stroke_fill='white')
    base=im.convert('RGBA');base.alpha_composite(layer);base.convert('RGB').save(dest);return Path(dest)

def pixel_polygons(polys,size):
    if not isinstance(polys,list) or len(polys)>100:raise ValueError('区域数量无效。')
    w,h=size;out=[]
    for poly in polys:
        if not isinstance(poly,list) or not 3<=len(poly)<=150:raise ValueError('区域需要 3 到 150 个顶点。')
        points=[]
        for p in poly:
            if not isinstance(p,list) or len(p)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) or not -100<=v<=1100 for v in p):raise ValueError('区域坐标必须为有限的 0..1000 数字，允许小幅越界后裁到画布。')
            # Vision models occasionally place a boundary point a few pixels
            # beyond the normalized canvas. Treat that as an off-canvas point
            # and clip it; reject large excursions instead of expanding scope.
            points.append([min(w-1,max(0,round(p[0]*w/1000))),min(h-1,max(0,round(p[1]*h/1000)))])
        out.append(points)
    return out

def raster(polys,size):
    im=Image.new('L',size);d=ImageDraw.Draw(im)
    for poly in polys:d.polygon([tuple(p) for p in poly],fill=255)
    return np.array(im)>0

def compile_plan(plan,size,allow_colour=True):
    if plan.get('edge_policy')!='transparent_remove':raise ValueError('旧的补色去边方案不可执行；请按抠成透明的新规则重新分析。')
    if plan.get('coordinate_system')!='normalized_1000':raise ValueError('未知坐标系统。')
    if plan['kind'] not in ('trim','export','unsupported'):raise ValueError('未知处理类型。')
    for field in ('reference_observations','remove_rules','preserve_rules'):
        if not isinstance(plan[field],list) or not plan[field] or any(not isinstance(s,str) or not s.strip() for s in plan[field]):raise ValueError('方案缺少观察或规则。')
    operations=plan['operations']
    if not isinstance(operations,list) or len(operations)>16:raise ValueError('每次最多 16 个固定修补操作。')
    if plan['kind']=='export' and operations:raise ValueError('透明导出方案不能包含去边操作。')
    if plan['kind']=='trim' and not operations and not plan['uncertain_regions']:raise ValueError('去边方案没有可执行操作。')
    # Export-only jobs do not execute any region operation. Ignore optional
    # annotations from the visual planner so a malformed unused polygon cannot
    # block a safe background separation and transparent export.
    if plan['kind']=='export' and not operations:
        protect=[];uncertain=[]
    else:
        protect=pixel_polygons(plan['protect_regions'],size);uncertain=pixel_polygons(plan['uncertain_regions'],size)
    if uncertain and not plan['unresolved_notes']:raise ValueError('未解决区域缺少原因。')
    compiled=[];remove=[]
    for operation in operations:
        op=dict(operation)
        if op['method'] not in ('erase_edge','clear_specks','local_fill','contract'):raise ValueError('外围黑边须抠成透明；不再接受 white_edge/sample_edge 补色代替去边。')
        if not allow_colour and op['method']=='local_fill':raise ValueError('本批次未授权内部局部补色。')
        if type(op['max_width']) is not int or not 1<=op['max_width']<=64:raise ValueError('边宽必须为 1..64 原图像素。')
        if op['width_mode'] not in ('auto','fixed'):raise ValueError('未知边宽模式。')
        for key in ('dark_threshold','chroma_limit','donor_tolerance'):
            if type(op[key]) is not int or not 0<=op[key]<=255:raise ValueError('颜色阈值必须为 0..255。')
        if not isinstance(op['colour'],list) or len(op['colour'])!=3 or any(type(v) is not int or not 0<=v<=255 for v in op['colour']):raise ValueError('RGB 颜色无效。')
        if type(op['edge_only']) is not bool:raise ValueError('边缘限制标记无效。')
        op['regions']=pixel_polygons(op['regions'],size);op['donor_regions']=pixel_polygons(op['donor_regions'],size)
        if not op['regions']:raise ValueError('修补操作没有范围。')
        if op['method']=='erase_edge' and (not op['edge_only'] or op['donor_regions']):raise ValueError('透明去边必须使用外缘限制，且不能使用补色取样区域。')
        if op['method']=='clear_specks' and (op['edge_only'] or op['donor_regions']):raise ValueError('背景噪点清理按连通分量限制，须 edge_only=false 且不取色。')
        if op['method']=='local_fill' and raster(op['regions'],size).mean()>.15:raise ValueError('局部补色区域过大，应拆分或使用外缘工具。')
        remove.extend(op['regions']);compiled.append(op)
    return {'operations':compiled,'remove_regions':remove,'protect_regions':protect,'uncertain_regions':uncertain}

def candidate(prepared,plan,compiled,simple_black_edge=False,simple_max_width=None):
    source=np.array(prepared);out=source.copy();rgb=source[:,:,:3].astype('float32');alpha=source[:,:,3];core=alpha>=128
    if compiled['operations'] and core.all():raise ValueError('输入仍是整幅不透明背景，无法可靠识别外轮廓，请检查背景分离。')
    depth=ndi.distance_transform_edt(core)
    protected=raster(compiled['protect_regions']+compiled['uncertain_regions'],prepared.size)
    metrics=[]
    for op in compiled['operations']:
        area=raster(op['regions'],prepared.size)&~protected&(alpha>0)
        mx=rgb.max(2);spread=np.ptp(rgb,axis=2)
        dark=(mx<op['dark_threshold'])&(spread<=op['chroma_limit'])&area
        width=op['max_width']
        if simple_black_edge and op['method']=='erase_edge':
            width=max(width,int(simple_max_width or width))
        if op['width_mode']=='auto' and op['edge_only']:
            reachable=ndi.binary_propagation(dark&(depth<=2),mask=dark&(depth<=width))
            if reachable.any():width=min(width,max(1,int(np.ceil(np.quantile(depth[reachable],.99)))+2))
        eligible=dark&(depth<=width) if op['edge_only'] else dark
        count=int(eligible.sum());note=''
        if op['method']=='erase_edge':
            if simple_black_edge:
                # Treat an exterior-connected near-white sticker halo as a
                # temporary background. Only dark pixels exposed through that
                # halo are eligible, so internal lines on a character or
                # letter remain enclosed and opaque.
                nearwhite=(rgb.min(2)>=245)&core
                background=(~core)|nearwhite
                seed=background.copy();seed[1:-1,1:-1]=False
                background=ndi.binary_propagation(seed,mask=background)
                exposed=dark & ndi.binary_dilation(background,iterations=1)
                eligible=ndi.binary_propagation(exposed,mask=dark)
                eligible &= ndi.distance_transform_edt(~background)<=width
                note='simple_exposed_outer_edge'
            else:
                # Delete only dark pixels connected to the actual exterior,
                # within the authorized band. Disconnected nearby lettering
                # stays intact.
                eligible=ndi.binary_propagation(eligible&(depth<=2),mask=eligible)
            count=int(eligible.sum());out[eligible,3]=0
        elif op['method']=='clear_specks':
            labels,_=ndi.label(out[:,:,3]>0,structure=np.ones((3,3)))
            sizes=np.bincount(labels.ravel());sizes[0]=0
            contained=np.bincount(labels[area],minlength=len(sizes))==sizes
            tiny=(sizes>0)&(sizes<=8)&contained
            if sizes.max():tiny[sizes.argmax()]=False
            eligible=tiny[labels];count=int(eligible.sum());out[eligible,3]=0
            note='Only fully contained detached components <=8 pixels; largest and protected components retained.'
        elif op['method']=='contract':
            eroded=ndi.minimum_filter(alpha,size=2*width+1,mode='nearest')
            out[:,:,3]=np.where(area,eroded,out[:,:,3]);count=int((out[:,:,3]!=source[:,:,3]).sum())
        elif np.any(eligible&(depth<=1)):
            raise ValueError('局部补色触及透明外轮廓；外围描边应使用 erase_edge 抠透。')
        elif op['donor_regions']:
            colour=np.array(op['colour'],dtype='float32')
            donors=raster(op['donor_regions'],prepared.size)&core&(depth>width+2)&(np.abs(rgb-colour).max(2)<=op['donor_tolerance'])
            if not donors.any():raise ValueError('指定范围内没有可靠的同材质取色像素。')
            distance,indices=ndi.distance_transform_edt(~donors,return_indices=True)
            weight=ndi.gaussian_filter(donors.astype('float32'),3)
            smoothed=np.stack([ndi.gaussian_filter(rgb[:,:,c]*donors,3)/np.maximum(weight,1e-9) for c in range(3)],axis=2)
            out[eligible,:3]=np.clip(np.rint(smoothed[indices[0][eligible],indices[1][eligible]]),0,255).astype('uint8')
            note='donor_distance_p95='+str(round(float(np.quantile(distance[eligible],.95)),1)) if count else ''
        else:out[eligible,:3]=op['colour']
        metrics.append({'method':op['method'],'width_used':width,'target_pixels':count,'note':note})
    allowed=raster(compiled['remove_regions'],prepared.size)&~protected
    out[~allowed]=source[~allowed]
    return Image.fromarray(out),metrics

def alpha_audit(before,after):
    a=np.array(before)[:,:,3];b=np.array(after)[:,:,3]
    return {'alpha_reduced_pixels':int((b<a).sum()),
            'fully_cleared_pixels':int(((a>0)&(b==0)).sum()),
            'alpha_increased_pixels':int((b>a).sum())}

def patch_assets(prepared,desired,folder):
    folder=Path(folder);a=np.array(prepared);b=np.array(desired)
    colour=(a[:,:,:3]!=b[:,:,:3]).any(2)&(b[:,:,3]>0)
    patch=desired.copy();patch.putalpha(Image.fromarray((colour*255).astype('uint8')));patch.save(folder/'patch.png')
    desired.save(folder/'candidate.png')
    return int(colour.sum())

def review_images(before,after,folder,compiled=None):
    folder=Path(folder);original=rgba(before);result=rgba(after);w,h=result.size
    sheet=Image.new('RGB',(1500,620),'#eeeeee');d=ImageDraw.Draw(sheet)
    for i,(bg,label) in enumerate([('white','白底'),('#dddddd','灰底'),('#343b46','深底')]):
        d.text((i*500+12,8),label,font=font(23),fill='#223344');paste_fit(sheet,result,(i*500+10,45,480,560),bg)
    whole=folder/'review_backgrounds.png';sheet.save(whole);paths=[whole]
    a=np.array(original)[:,:,3];b=np.array(result)[:,:,3]
    overlay=flatten(original);highlight=Image.new('RGBA',result.size,(224,48,130,0))
    highlight.putalpha(Image.fromarray(((b<a)*210).astype('uint8')));overlay=overlay.convert('RGBA');overlay.alpha_composite(highlight)
    sheet=Image.new('RGB',(1500,620),'#eeeeee');d=ImageDraw.Draw(sheet)
    for i,(im,label) in enumerate([(original.getchannel('A').convert('RGB'),'原 alpha：白实黑透'),(result.getchannel('A').convert('RGB'),'结果 alpha：白实黑透'),(overlay,'粉色：alpha 实际降低区域')]):
        d.text((i*500+12,8),label,font=font(21),fill='#223344');paste_fit(sheet,im,(i*500+10,45,480,560))
    evidence=folder/'alpha_evidence.png';sheet.save(evidence);paths.append(evidence)
    alpha=np.array(original)[:,:,3]>=128;edge=alpha&~ndi.binary_erosion(alpha)
    tile=max(160,math.ceil(max(w,h)/6));boxes=[]
    for y in range(0,h,tile):
        for x in range(0,w,tile):
            box=(max(0,x-10),max(0,y-10),min(w,x+tile+10),min(h,y+tile+10))
            if edge[box[1]:box[3],box[0]:box[2]].any():boxes.append(box)
    for op in (compiled or {}).get('operations',[]):
        if op['method']=='local_fill':
            for poly in op['regions']:
                xs,ys=zip(*poly);boxes.append((max(0,min(xs)-15),max(0,min(ys)-15),min(w,max(xs)+16),min(h,max(ys)+16)))
    boxes=list(dict.fromkeys(boxes))[:40]
    for page,start in enumerate(range(0,len(boxes),6),1):
        sheet=Image.new('RGB',(1200,1140),'#eeeeee');d=ImageDraw.Draw(sheet)
        for i,box in enumerate(boxes[start:start+6]):
            x=i%2*600;y=i//2*380;d.text((x+8,y+5),'crop='+str(list(box)),font=font(15),fill='#223344')
            d.text((x+8,y+27),'左：原图分离后    右：实际结果',font=font(16),fill='#445566')
            for col,im in enumerate([original,result]):paste_fit(sheet,im.crop(box),(x+col*300+8,y+53,284,315),'#cccccc')
        path=folder/f'edge_crops_{page}.png';sheet.save(path);paths.append(path)
    return paths

def comparison(source,result,reference,dest,title,status):
    sheet=Image.new('RGB',(1500,650),'#f1f3f5');d=ImageDraw.Draw(sheet)
    d.text((15,12),title+' · '+status,font=font(26),fill='#223344')
    for i,(label,path) in enumerate([('参考图',reference),('处理前（分离背景后）',source),('实际结果（灰底显示透明）',result)]):
        d.text((15+i*500,60),label,font=font(20),fill='#445566')
        if path:paste_fit(sheet,path,(15+i*500,100,470,525))
        else:d.text((15+i*500,270),'无匹配参考图',font=font(22),fill='#556677')
    sheet.save(dest)
