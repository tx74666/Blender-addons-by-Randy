"""Read-only saved-character inventory for Unity animation compatibility work."""
import json
from pathlib import Path
import sys
import bpy

args = sys.argv[sys.argv.index('--')+1:]
bpy.ops.wm.open_mainfile(filepath=args[0], load_ui=False, use_scripts=False)

def props(owner):
    return {k: (v.name_full if isinstance(v, bpy.types.ID) else str(v)) for k, v in owner.items()}

def constraint(c):
    return {p: (getattr(c, p).name_full if isinstance(getattr(c, p), bpy.types.ID) else getattr(c, p))
            for p in ('name', 'type', 'target', 'subtarget', 'influence', 'mute', 'owner_space',
                      'target_space', 'mix_mode') if hasattr(c, p)}

report = {'file': args[0], 'scene_properties': props(bpy.context.scene), 'objects': []}
for o in bpy.data.objects:
    if o.type != 'ARMATURE': continue
    row = {'name': o.name, 'data': o.data.name, 'properties': props(o), 'data_properties': props(o.data),
           'constraints': [constraint(c) for c in o.constraints],
           'matrix': [list(r) for r in o.matrix_world], 'bones': []}
    for b in o.pose.bones:
        row['bones'].append({'name': b.name, 'parent': b.parent.name if b.parent else None,
                            'deform': b.bone.use_deform, 'connected': b.bone.use_connect,
                            'head': list(b.bone.head_local), 'tail': list(b.bone.tail_local),
                            'rest': [list(r) for r in b.bone.matrix_local],
                            'constraints': [constraint(c) for c in b.constraints], 'properties': props(b)})
    row['drivers'] = [{'path': f.data_path, 'expression': f.driver.expression,
                       'variables': [{'name': v.name, 'targets': [{'id': t.id.name_full if t.id else None,
                          'path': t.data_path, 'bone': t.bone_target} for t in v.targets]} for v in f.driver.variables]}
                      for f in o.animation_data.drivers] if o.animation_data else []
    report['objects'].append(row)
path = Path(args[1]); path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(report, indent=2), encoding='utf8')
print('ANIMATION_TARGET_INVENTORY', [(o['name'], o['data'], len(o['bones']), len(o['drivers']),
      sum(bool(b['constraints']) for b in o['bones']), list(o['properties'])) for o in report['objects']])
