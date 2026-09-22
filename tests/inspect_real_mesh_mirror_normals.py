"""Isolated replay of an exported edit selection; never saves the source file.

Pass -- <library.blend> <report.json>; the library must contain Cosha.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
from character_designer import mesh_mirror as mirror
from character_designer.topology_symmetry import _select_result_region

source, report_path = sys.argv[sys.argv.index('--') + 1:]
with bpy.data.libraries.load(source, link=False) as (available, requested):
    requested.objects = ['Cosha']
obj = requested.objects[0]
bpy.context.collection.objects.link(obj)
for item in bpy.context.selected_objects: item.select_set(False)
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
report = {'source': source, 'source_normal_type': obj.data.attributes['custom_normal'].data_type}
bpy.ops.object.mode_set(mode='EDIT')
plan = mirror.build_plan(bpy.context)
assert not plan.needs_choice
bpy.ops.object.mode_set(mode='OBJECT')
old = obj.data
before = mirror._fingerprint(obj)
report['before'] = dict(vertices=len(old.vertices), faces=len(old.polygons),
                       source_faces=len(plan.source_faces), target_faces=len(plan.target_faces),
                       keys=len(old.shape_keys.key_blocks), sharp=sum(e.use_edge_sharp for e in old.edges))
captured = {}
verify = mirror._verify_staged
def record(staging, old, plan, origins, mapping, expected):
    verify(staging, old, plan, origins, mapping, expected)
    captured.update(origins=origins, mapping=mapping, expected=expected)
with patch.object(mirror, '_verify_staged', record):
    mirror.apply_plan(plan, after_commit=lambda selection: _select_result_region(obj, selection))
assert obj.mode == 'EDIT'
bpy.ops.object.mode_set(mode='OBJECT')
verify(obj, old, plan, **captured)
matrix = plan.reflection.to_3x3().inverted().transposed()
errors = []
for i, (reflected, source_corner) in enumerate(captured['mapping']['CORNER']):
    wanted = old.corner_normals[source_corner].vector
    if reflected: wanted = (matrix @ wanted).normalized()
    errors.append((obj.data.corner_normals[i].vector - wanted).length)
report['after'] = dict(vertices=len(obj.data.vertices), faces=len(obj.data.polygons),
                      keys=len(obj.data.shape_keys.key_blocks), sharp=sum(e.use_edge_sharp for e in obj.data.edges),
                      max_normal_error=max(errors), normal_type=obj.data.attributes['custom_normal'].data_type)
after = mirror._fingerprint(obj)
bpy.ops.object.mode_set(mode='EDIT')
second = mirror.build_plan(bpy.context)
bpy.ops.object.mode_set(mode='OBJECT')
mirror.apply_plan(second, after_commit=lambda selection: _select_result_region(obj, selection))
bpy.ops.object.mode_set(mode='OBJECT')
report['repeat_identical'] = mirror._fingerprint(obj) == after
assert report['repeat_identical']
Path(report_path).write_text(json.dumps(report, indent=2), encoding='utf-8')
print('REAL_MIRROR_NORMALS_PASS', json.dumps(report), flush=True)
