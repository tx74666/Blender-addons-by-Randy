"""Disposable native refresh tests: normal, stale module, failed enable rollback."""
import sys
import types
from pathlib import Path
from unittest.mock import patch
import bpy
import addon_utils
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'addons'))
name='character_designer'
m=addon_utils.enable(name,default_set=False,persistent=True,refresh_handled=True)
assert m is not None
obj=bpy.context.active_object
obj['artist_record']='keep'
obj.shape_key_add(name='Basis')
k=obj.shape_key_add(name='Blink_L'); k.data[0].co.x+=.125
before=tuple(tuple(v.co) for v in k.data)
for scenario in ('normal','stale_module','enable_failure','retry'):
 old=m
 if scenario=='stale_module':
  shadow=types.ModuleType(name); shadow.__dict__.update(m.__dict__)
  shadow.__addon_enabled__=False
  sys.modules[name]=shadow
 if scenario=='enable_failure':
  with patch.object(addon_utils,'enable',return_value=None):
   old._reload_addon_deferred()
 else:
  old._reload_addon_deferred()
 m=sys.modules[name]
 m._validate_registration_integrity()
 assert m.__addon_enabled__
 assert bool(m.ADDON_REFRESH_LAST_ERROR)==(scenario=='enable_failure')
 assert obj['artist_record']=='keep' and before==tuple(tuple(v.co) for v in k.data)
 assert sum(getattr(h,'__module__','')==name+'.generated_names' for h in bpy.app.handlers.load_post)==1
 print('PASS',scenario)
addon_utils.disable(name,default_set=False)
