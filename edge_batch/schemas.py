"""Strict machine-readable contracts. Coordinates are normalized to 0..1000."""
def obj(properties):return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
def array(items):return {'type':'array','items':items}
STRING={'type':'string'}
NUMBER={'type':'number'}
INTEGER={'type':'integer'}
BOOL={'type':'boolean'}
TEXTS=array(STRING)
POINT=array(NUMBER)
POLYGONS=array(array(POINT))
CLASSIFY=obj({'items':array(obj({'id':STRING,'role':{'type':'string','enum':['artwork','reference','unknown']},'reason':STRING}))})
MATCH=obj({'status':{'type':'string','enum':['matched','none','uncertain']},'reference_id':STRING,'reason':STRING})
PREFLIGHT=obj({'safe_to_execute':BOOL,'reason':STRING})
OPERATION=obj({'method':{'type':'string','enum':['erase_edge','clear_specks','local_fill','contract']},
               'regions':POLYGONS,'donor_regions':POLYGONS,'colour':array(INTEGER),
               'width_mode':{'type':'string','enum':['auto','fixed']},'max_width':INTEGER,
               'dark_threshold':INTEGER,'chroma_limit':INTEGER,'donor_tolerance':INTEGER,
               'edge_only':BOOL,'reason':STRING})
PLAN=obj({'title':STRING,'kind':{'type':'string','enum':['trim','export','unsupported']},
          'edge_policy':{'type':'string','enum':['transparent_remove']},
          'reference_matches':BOOL,'reference_mode':{'type':'string','enum':['flat_artwork','scene_product','none']},
          'is_flat_artwork':BOOL,'remove_white_background':BOOL,
          'allow_edge_touching':BOOL,'frame_barriers':array(obj({'edge':{'type':'string','enum':['left','right','top','bottom']},'start':INTEGER,'end':INTEGER})),
          'coordinate_system':{'type':'string','enum':['normalized_1000']},
          'reference_observations':TEXTS,'remove_rules':TEXTS,'preserve_rules':TEXTS,
          'protect_regions':POLYGONS,'uncertain_regions':POLYGONS,'unresolved_notes':TEXTS,
          'operations':array(OPERATION)})
REVIEW=obj({'status':{'type':'string','enum':['pass','repair','partial','fail']},
            'reference_correct':BOOL,'subject_preserved':BOOL,'target_border_removed':BOOL,
            'removed_border_is_transparent':BOOL,
            'background_correct':BOOL,'shape_and_alpha_ok':BOOL,'reason':STRING,
            'issues':array(obj({'kind':{'type':'string','enum':['residual','overdelete','colour_seam','background','reference','uncertain']},
                                'region':array(POINT),'description':STRING,'suggestion':STRING})),
            'inspected_regions':array(obj({'region':array(POINT),'finding':STRING}))})
