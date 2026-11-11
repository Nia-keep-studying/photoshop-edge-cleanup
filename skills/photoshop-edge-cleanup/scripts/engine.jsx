/* Photoshop CC 2019 ExtendScript execution adapter. Invoked only by ps_tool.py. */
function PSEdgeRun(req, responsePath) {
    function json(v) {
        if (v === null || typeof v === 'undefined') return 'null';
        if (typeof v === 'boolean' || typeof v === 'number') return String(v);
        if (typeof v === 'string') return '"' + v.replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/\r/g,'\\r').replace(/\n/g,'\\n').replace(/\t/g,'\\t') + '"';
        var a=[],k,i;
        if (v instanceof Array) { for(i=0;i<v.length;i++) a.push(json(v[i])); return '['+a.join(',')+']'; }
        for(k in v) if(v.hasOwnProperty(k)) a.push(json(k)+':'+json(v[k]));
        return '{'+a.join(',')+'}';
    }
    function write(v) {
        var f=new File(responsePath); f.encoding='UTF8';
        if(!f.open('w')) throw new Error('Cannot write response');
        f.write(json(v));f.close();
    }
    function owned() {
        for(var i=0;i<app.documents.length;i++) {
            var d=app.documents[i];
            if(d.id===req.document_id && d.name.indexOf(req.owner_tag)===0) {app.activeDocument=d;return d;}
        }
        throw new Error('Managed Photoshop document is not open. Refusing to use the active document.');
    }
    function describe(d) {
        var layers=[];
        for(var i=0;i<d.layers.length;i++) layers.push({id:d.layers[i].id,name:d.layers[i].name,visible:d.layers[i].visible});
        return {document_id:d.id,name:d.name,width:d.width.as('px'),height:d.height.as('px'),layers:layers,active_layer_id:d.activeLayer.id,history_index:d.historyStates.length-1};
    }
    function save(d) {
        // Some 2019 DOM setters select/show a layer as a side effect. Resolve
        // the immutable backup by name again immediately before rendering.
        d.layers.getByName('Original — keep').visible=false;
        var psd=new PhotoshopSaveOptions(); psd.layers=true;psd.embedColorProfile=true;
        d.saveAs(new File(req.psd_path),psd,true,Extension.LOWERCASE);
        var png=new PNGSaveOptions();png.interlaced=false;
        d.saveAs(new File(req.png_path),png,true,Extension.LOWERCASE);
    }
    function stamp(d) {
        var tmp=d.duplicate(req.owner_tag+'-temporary',true);
        // With only one visible masked layer, duplicate(mergeLayersOnly=true)
        // can retain its layer mask in PS 2019. Merge with a transparent layer
        // to bake the visible composite before adding a new independent mask.
        tmp.artLayers.add();tmp.mergeVisibleLayers();
        var layer=tmp.activeLayer.duplicate(d,ElementPlacement.PLACEATBEGINNING);
        tmp.close(SaveOptions.DONOTSAVECHANGES);app.activeDocument=d;
        for(var i=0;i<d.layers.length;i++) d.layers[i].visible=(d.layers[i].id===layer.id);
        layer.name='Working — '+req.op;d.activeLayer=layer;return layer;
    }
    function selectRegions(d,regions) {
        d.selection.deselect();
        for(var i=0;i<regions.length;i++) d.selection.select(regions[i],i===0?SelectionType.REPLACE:SelectionType.EXTEND,req.feather||0,true);
    }
    function selectPath(d,points) {
        var p=[],i;
        for(i=0;i<points.length;i++) {
            var v=points[i],info=new PathPointInfo();
            info.anchor=v.anchor;info.leftDirection=v.left||v.anchor;info.rightDirection=v.right||v.anchor;
            info.kind=(v.left||v.right)?PointKind.SMOOTHPOINT:PointKind.CORNERPOINT;p.push(info);
        }
        var sub=new SubPathInfo();sub.closed=true;sub.operation=ShapeOperation.SHAPEADD;sub.entireSubPath=p;
        var path=d.pathItems.add(req.owner_tag+'-path',[sub]);
        path.makeSelection(req.feather||0,true,SelectionType.REPLACE);path.remove();
    }
    function selection(d) {if(req.path) selectPath(d,req.path);else selectRegions(d,req.regions);}
    function revealSelection() {
        var de=new ActionDescriptor(),re=new ActionReference();
        de.putClass(charIDToTypeID('Nw  '),charIDToTypeID('Chnl'));
        re.putEnumerated(charIDToTypeID('Chnl'),charIDToTypeID('Chnl'),charIDToTypeID('Msk '));
        de.putReference(charIDToTypeID('At  '),re);
        de.putEnumerated(charIDToTypeID('Usng'),charIDToTypeID('UsrM'),charIDToTypeID('RvlS'));
        executeAction(charIDToTypeID('Mk  '),de,DialogModes.NO);
    }
    function transparency() {
        stage='load_layer_transparency';
        var de=new ActionDescriptor(),re=new ActionReference(),to=new ActionReference();
        re.putProperty(charIDToTypeID('Chnl'),charIDToTypeID('fsel'));de.putReference(charIDToTypeID('null'),re);
        to.putEnumerated(charIDToTypeID('Chnl'),charIDToTypeID('Chnl'),charIDToTypeID('Trsp'));de.putReference(charIDToTypeID('T   '),to);
        executeAction(charIDToTypeID('setd'),de,DialogModes.NO);
    }
    function binaryTransparency(d) {
        transparency();stage='binary_alpha';var alpha=d.channels.add();alpha.name=req.owner_tag+'-temporary-alpha';
        d.selection.store(alpha);d.selection.deselect();d.activeChannels=[alpha];
        var threshold=new ActionDescriptor();threshold.putInteger(charIDToTypeID('Lvl '),128);
        executeAction(charIDToTypeID('Thrs'),threshold,DialogModes.NO);
        d.selection.load(alpha,SelectionType.REPLACE);alpha.remove();d.activeChannels=d.componentChannels;
    }
    function importLayer(d,file,name) {
        var temp=app.open(new File(file));
        if(temp.width.as('px')!==d.width.as('px') || temp.height.as('px')!==d.height.as('px')) {
            temp.close(SaveOptions.DONOTSAVECHANGES);throw new Error('Imported image must match the full canvas dimensions.');
        }
        var layer=temp.activeLayer.duplicate(d,ElementPlacement.PLACEATBEGINNING);
        temp.close(SaveOptions.DONOTSAVECHANGES);app.activeDocument=d;layer.name=name;d.activeLayer=layer;return layer;
    }
    function restrictToScope(d) {
        // Both halves must be masked: leaving an unmasked backdrop would
        // restore black pixels wherever the candidate made them transparent.
        var candidate=stamp(d);candidate.name='Scoped result — '+req.op;
        var prior=importLayer(d,req.preserved_outside_png,'Preserved outside edit scope');
        prior.move(candidate,ElementPlacement.PLACEAFTER);
        var scope=importLayer(d,req.scope_rgba,'Reference edit scope');
        transparency();scope.visible=false;d.activeLayer=candidate;stage='scope_candidate_mask';revealSelection();
        d.selection.deselect();d.activeChannels=d.componentChannels;d.activeLayer=candidate;
    }
    var oldDialogs=app.displayDialogs,oldUnits=app.preferences.rulerUnits,d=null,before=null,initialIds=[],stage='operation';
    for(var z=0;z<app.documents.length;z++) initialIds.push(app.documents[z].id);
    try {
        app.displayDialogs=DialogModes.NO;app.preferences.rulerUnits=Units.PIXELS;
        if(req.op==='probe') {
            var managed=[];
            for(var n=0;n<app.documents.length;n++) if(/^CE-[0-9a-f]{12}$/.test(app.documents[n].name)) {
                var md=app.documents[n];managed.push({document_id:md.id,name:md.name,mode:String(md.mode),bits:String(md.bitsPerChannel),active_layer:md.activeLayer.name,is_background:md.activeLayer.isBackgroundLayer});
            }
            write({ok:true,version:app.version,documents:app.documents.length,managed_documents:managed,adapter:'extendscript-file-argument'});return;
        }
        if(req.op==='close_session') {d=owned();var closedId=d.id;d.close(SaveOptions.DONOTSAVECHANGES);write({ok:true,closed:true,document_id:closedId});return;}
        if(req.op==='open_copy') {
            var input=app.open(new File(req.input_path));d=input.duplicate(req.owner_tag);
            input.close(SaveOptions.DONOTSAVECHANGES);app.activeDocument=d;
            var base=d.activeLayer;base.name='Working';
            var original=base.duplicate();original.name='Original — keep';original.allLocked=true;original.visible=false;
            d.activeLayer=base;
        } else {
            d=owned();before=d.activeHistoryState;
            if(req.op==='native_defringe') {
                var previous=d.activeLayer,working=stamp(d);
                var de=new ActionDescriptor();de.putInteger(charIDToTypeID('Wdth'),req.width);
                executeAction(charIDToTypeID('Dfrg'),de,DialogModes.NO);
                if(req.regions || req.path) {
                    selection(d);revealSelection();
                    // The previous visible composite lives in the immediately older
                    // snapshot layers, but may consist of several clipped layers.
                    for(var i=1;i<d.layers.length;i++) {
                        if(d.layers[i].name.indexOf('Original')!==0) d.layers[i].visible=false;
                    }
                    // Import the previous exported full-canvas snapshot as backdrop.
                    var back=importLayer(d,req.previous_png,'Before local defringe');
                    back.move(working,ElementPlacement.PLACEAFTER);d.activeLayer=working;
                    d.selection.deselect();
                }
            } else if(req.op==='erase') {
                stamp(d);selection(d);d.selection.clear();d.selection.deselect();
            } else if(req.op==='contract_mask') {
                stamp(d);binaryTransparency(d);d.selection.contract(req.width);
                if(req.keep_regions) {
                    for(var k=0;k<req.keep_regions.length;k++) d.selection.select(req.keep_regions[k],SelectionType.EXTEND,0,true);
                }
                if(req.feather) d.selection.feather(req.feather);
                revealSelection();d.selection.deselect();
            } else if(req.op==='paint_fringe') {
                stamp(d);binaryTransparency(d);d.selection.contract(req.width);d.selection.invert();
                var ring=d.channels.add();ring.name=req.owner_tag+'-temporary-edge';d.selection.store(ring);
                selection(d);d.selection.load(ring,SelectionType.INTERSECT);ring.remove();
                if(req.feather) d.selection.feather(req.feather);
                var fill=d.artLayers.add();fill.name='Local fringe colour repair';fill.grouped=true;
                var edgeColour=new SolidColor();edgeColour.rgb.red=req.colour[0];edgeColour.rgb.green=req.colour[1];edgeColour.rgb.blue=req.colour[2];
                d.selection.fill(edgeColour,ColorBlendMode.NORMAL,req.opacity||100,false);d.selection.deselect();
            } else if(req.op==='paint') {
                stamp(d);var paint=d.artLayers.add();paint.name='Local colour repair';paint.grouped=true;
                selection(d);var colour=new SolidColor();colour.rgb.red=req.colour[0];colour.rgb.green=req.colour[1];colour.rgb.blue=req.colour[2];
                d.selection.fill(colour,ColorBlendMode.NORMAL,req.opacity||100,false);d.selection.deselect();
            } else if(req.op==='mask') {
                var target=stamp(d),ml=importLayer(d,req.mask_rgba,'Mask source');transparency();ml.visible=false;
                d.activeLayer=target;revealSelection();d.selection.deselect();
            } else if(req.op==='patch') {
                stamp(d);importLayer(d,req.patch_path,'Local pixel repair');
            } else if(req.op==='restore') {
                stamp(d);var src=d.layers.getByName('Original — keep');var restore=src.duplicate();
                restore.allLocked=false;restore.visible=true;restore.move(d.layers[0],ElementPlacement.PLACEBEFORE);
                restore.name='Restored source region';d.activeLayer=restore;selection(d);revealSelection();d.selection.deselect();
            } else if(req.op==='undo') {
                // PSD checkpoints survive history pruning and branching. Never
                // address a mutable history-state index from an earlier job.
                var checkpoint=app.open(new File(req.checkpoint_psd));
                var replacement=checkpoint.duplicate(req.owner_tag);
                checkpoint.close(SaveOptions.DONOTSAVECHANGES);
                d.close(SaveOptions.DONOTSAVECHANGES);d=replacement;before=null;app.activeDocument=d;
            } else if(req.op!=='snapshot') throw new Error('Unsupported operation: '+req.op);
        }
        if(req.scope_rgba) restrictToScope(d);
        save(d);var result=describe(d);result.ok=true;result.version=app.version;
        result.psd_path=req.psd_path;result.png_path=req.png_path;
        write(result);
    } catch(e) {
        var diagnostic=null;
        try {if(d) diagnostic={mode:String(d.mode),bits:String(d.bitsPerChannel),active_layer:d.activeLayer.name,is_background:d.activeLayer.isBackgroundLayer};}catch(ignored) {}
        if(d && before) {try {d.activeHistoryState=before;}catch(ignore) {}}
        write({ok:false,error:String(e.message||e),line:e.line||null,op:req.op,stage:stage,diagnostic:diagnostic});
    } finally {
        app.displayDialogs=oldDialogs;app.preferences.rulerUnits=oldUnits;
    }
}
