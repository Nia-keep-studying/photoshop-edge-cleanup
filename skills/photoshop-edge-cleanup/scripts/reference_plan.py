"""Image-specific reference decisions and enforceable edit scope, not a classifier."""
import json,uuid,hashlib,shutil
from pathlib import Path
from PIL import Image,ImageDraw,ImageChops,ImageFilter

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def nonempty_texts(value,name):
    if not isinstance(value,list) or not value or any(not isinstance(v,str) or not v.strip() for v in value):
        raise ValueError(name+' must contain explicit, nonempty reference-based observations/rules.')
    return value
def polygons(value,w,h,validate):
    if not isinstance(value,list):raise ValueError('Region groups must be arrays of polygons.')
    if value:validate({'regions':value},w,h,True)
    return value
def raster(regions,size):
    out=Image.new('L',size);draw=ImageDraw.Draw(out)
    for poly in regions:draw.polygon([tuple(p) for p in poly],fill=255)
    return out

def erode_square(mask,radius):
    # Repeated radius-1 erosion is the same square neighbourhood, with much
    # lower Pillow cost than a large rank-filter kernel on 1254px artwork.
    out=mask
    for _ in range(radius):out=out.filter(ImageFilter.MinFilter(3))
    return out

def create(request,state,folder,validate):
    if state['revision']!=0:
        raise ValueError('Set the image-specific reference plan on a fresh revision-0 copy before editing; reopen the original to change an edited plan.')
    ref=None
    if request.get('reference_path'):
        ref=Path(request['reference_path']).resolve(strict=True)
        if ref.suffix.lower() not in ('.png','.jpg','.jpeg','.webp'):raise ValueError('Reference must be an image.')
        with Image.open(ref) as im:im.verify()
    elif request.get('no_reference_default') is not True:
        raise ValueError('A reference is required unless the user explicitly authorized no-reference default edge removal.')
    w,h=state['width'],state['height'];pid=uuid.uuid4().hex[:12]
    plan={'plan_id':pid,'reference_path':str(ref) if ref else None,'reference_sha256':digest(ref) if ref else None,
      'decision_basis':'matched_reference' if ref else 'user_authorized_no_reference_default',
      'reference_observations':nonempty_texts(request.get('reference_observations'),'reference_observations'),
      'remove_rules':nonempty_texts(request.get('remove_rules'),'remove_rules'),
      'preserve_rules':nonempty_texts(request.get('preserve_rules'),'preserve_rules'),
      'remove_regions':polygons(request.get('remove_regions'),w,h,validate),
      'protect_regions':polygons(request.get('protect_regions',[]),w,h,validate),
      'uncertain_regions':polygons(request.get('uncertain_regions',[]),w,h,validate),
      'uncertainty_policy':'automatic_reinspect_then_preserve_and_report_without_blocking',
      'unresolved_notes':request.get('unresolved_notes',[])}
    if plan['uncertain_regions']:nonempty_texts(plan['unresolved_notes'],'unresolved_notes')
    folder=Path(folder)/'plans'/pid;folder.mkdir(parents=True)
    if ref:shutil.copy2(ref,folder/('reference'+ref.suffix.lower()))
    protect=raster(plan['protect_regions']+plan['uncertain_regions'],(w,h))
    scope=ImageChops.subtract(raster(plan['remove_regions'],(w,h)),protect)
    baseline=next(r for r in state['history'] if r['revision']==0)
    if 'edge_limit' in request:
        limit=request['edge_limit']
        if type(limit) is not int or not 1<=limit<=32:raise ValueError('edge_limit must be 1..32 pixels.')
        with Image.open(baseline['png_path']) as im:
            binary=im.convert('RGBA').getchannel('A').point(lambda a:255 if a>=128 else 0)
        # Follow the actual contour, including rounded corners. No rectangular
        # restoration corners, and no automatic erosion beyond canvas bounds.
        interior=erode_square(binary,limit)
        scope=ImageChops.multiply(scope,ImageChops.invert(interior));plan['edge_limit']=limit
    scope.save(folder/'edit_scope.png')
    rgba=Image.new('RGBA',(w,h),'white');rgba.putalpha(scope);rgba.save(folder/'edit_scope_rgba.png')
    with Image.open(baseline['png_path']) as im:
        base=Image.new('RGBA',(w,h),'#eeeeee');base.alpha_composite(im.convert('RGBA'))
    overlay=Image.new('RGBA',(w,h));draw=ImageDraw.Draw(overlay)
    for key,colour in [('remove_regions',(245,68,68)),('protect_regions',(0,143,180)),('uncertain_regions',(240,160,0))]:
        for poly in plan[key]:
            pts=[tuple(p) for p in poly];draw.polygon(pts,fill=colour+(42,));draw.line(pts+[pts[0]],fill=colour+(255,),width=3)
    base.alpha_composite(overlay);base.convert('RGB').save(folder/'plan_overlay.png')
    # Save the exact allowed mask separately from broad semantic polygons.
    flat=Image.new('RGBA',(w,h),'#eeeeee')
    with Image.open(baseline['png_path']) as im:
        rgba=im.convert('RGBA');flat.alpha_composite(rgba)
    display_mask=ImageChops.multiply(scope,rgba.getchannel('A').point(lambda a:255 if a else 0))
    tint=Image.new('RGBA',(w,h),(242,65,50,0));tint.putalpha(display_mask.point(lambda a:85 if a else 0));flat.alpha_composite(tint)
    flat.convert('RGB').save(folder/'effective_scope.png')
    plan.update(scope_path=str(folder/'edit_scope.png'),scope_rgba_path=str(folder/'edit_scope_rgba.png'),
      scope_sha256=digest(folder/'edit_scope.png'),scope_rgba_sha256=digest(folder/'edit_scope_rgba.png'),
      overlay_path=str(folder/'plan_overlay.png'),plan_path=str(folder/'plan.json'),
      effective_scope_path=str(folder/'effective_scope.png'),
      automatic_status='partial_plan' if plan['uncertain_regions'] else 'ready_to_edit')
    Path(plan['plan_path']).write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf8')
    return plan

def verify(plan):
    for path_key,hash_key in [('reference_path','reference_sha256'),('scope_path','scope_sha256'),('scope_rgba_path','scope_rgba_sha256')]:
        if plan.get(path_key) is not None and digest(plan[path_key])!=plan[hash_key]:raise ValueError('Reference or scope changed after analysis; create a fresh plan.')
    with Image.open(plan['scope_path']) as im:
        if not im.getbbox():raise ValueError('The plan permits no edits. Preserve this image and report its plan status.')

def audit(before_path,after_path,plan):
    with Image.open(before_path) as im:before=im.convert('RGBA')
    with Image.open(after_path) as im:after=im.convert('RGBA')
    diff=ImageChops.difference(before,after)
    rgb=ImageChops.lighter(ImageChops.lighter(diff.getchannel('R'),diff.getchannel('G')),diff.getchannel('B'))
    visible=ImageChops.lighter(before.getchannel('A'),after.getchannel('A')).point(lambda x:255 if x else 0)
    changed=ImageChops.lighter(ImageChops.multiply(rgb,visible),diff.getchannel('A')).point(lambda x:255 if x else 0)
    with Image.open(plan['scope_path']) as im:scope=im.convert('L')
    outside=ImageChops.multiply(changed,ImageChops.invert(scope))
    return {'plan_id':plan['plan_id'],'changed_pixels':changed.histogram()[255],
      'changes_outside_edit_scope':outside.histogram()[255],'outside_change_bbox':outside.getbbox(),
      'unresolved_regions':len(plan['uncertain_regions']),
      'visual_acceptance':'not_evaluated_by_pixel_audit'}
