"""Integration of per-finger Setup, whole-chain roll, paired rings and weights."""
import json
import math
import sys
import tempfile
import time
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector, Quaternion

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_bank_blender import fixture, select, capture_all, refused, bank, definition, layout, character_designer, C
from character_designer import finger_targets as targets, finger_workflow as work, finger_workflow_ui as ui


def bound_fixture():
    obj, chosen = fixture()
    capture_all(obj, chosen)
    bodies = json.loads(obj.character_designer_finger_bank.survey)['candidates']
    bpy.ops.object.mode_set(mode='OBJECT')
    rig = bpy.data.objects.new('MainRig', bpy.data.armatures.new('MainRig'))
    C.collection.objects.link(rig)
    obj.select_set(False); rig.select_set(True); C.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    for key, body in bodies.items():
        digit, side = key.split('.')
        root, tip = Vector(body['root']), Vector(body['tip'])
        previous = None
        count = 2 if digit == 'THUMB' else 3
        for i in range(count):
            b = rig.data.edit_bones.new(f'f_{digit.lower()}.{i+1:02d}.{side}')
            b.head, b.tail = root.lerp(tip, i/count), root.lerp(tip, (i+1)/count)
            b.parent, b.use_connect, b.roll = previous, False, .7
            previous = b
    extra = rig.data.edit_bones.new('OtherDeform')
    extra.head, extra.tail = (0, 0, 0), (0, 1, 0)
    bpy.ops.object.mode_set(mode='OBJECT')
    rig.select_set(False); obj.select_set(True); C.view_layer.objects.active = obj
    for bone in rig.data.bones: obj.vertex_groups.new(name=bone.name)
    obj.vertex_groups['OtherDeform'].add(list(range(len(obj.data.vertices))), .1, 'REPLACE')
    obj.vertex_groups['OtherDeform'].lock_weight = True
    for key, body in bodies.items():
        digit, side = key.split('.')
        obj.vertex_groups[f'f_{digit.lower()}.01.{side}'].add(body['vertices'], .9, 'REPLACE')
    obj.modifiers.new('Rig', 'ARMATURE').object = rig
    C.scene.character_designer_setup.rig, C.scene.character_designer_setup.body = rig, obj
    bpy.ops.object.mode_set(mode='EDIT')
    bank.select(C, 'INDEX', 'L')
    return obj, rig, chosen


def test_all_and_selected_complete_chains():
    obj, rig, chosen = bound_fixture()
    before = layout.fingerprint(obj)
    geometry = {b.name: (tuple(b.head_local), tuple(b.tail_local), b.parent.name if b.parent else '') for b in rig.data.bones}
    start = time.perf_counter()
    result = targets.calibrate(C)
    print('PERF_ALL', time.perf_counter()-start, result, flush=True)
    assert len(result['success']) == 5 and not result['skipped'] and not result['failed']
    assert result['success']['THUMB'] == 4
    assert C.edit_object == obj and layout.fingerprint(obj) == before
    assert geometry == {b.name: (tuple(b.head_local), tuple(b.tail_local), b.parent.name if b.parent else '') for b in rig.data.bones}
    for key, body in json.loads(obj.character_designer_finger_bank.survey)['candidates'].items():
        r = json.loads(obj.character_designer_finger_bank.slots[key].guide.record)
        n = Vector(r['basis']['normal'])
        for b in targets.resolve(obj, rig, key, targets.index(rig)):
            t = (b.tail_local-b.head_local).normalized()
            x = b.matrix_local.to_3x3().col[0]
            assert (Quaternion(x, .05) @ t-t).normalized().dot((-n+t*n.dot(t)).normalized()) > .99
    refused(lambda: targets.calibrate(C, selected=True))
    bpy.ops.object.mode_set(mode='OBJECT'); obj.select_set(False); rig.select_set(True); C.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    for bone in rig.data.edit_bones: bone.select = bone.select_head = bone.select_tail = False
    rig.data.edit_bones['f_middle.02.L'].select = True
    result = targets.calibrate(C, selected=True)
    assert result['success'] == {'MIDDLE': 6}, result
    assert C.edit_object == rig


def test_pair_roll_rollback_and_stale_chain():
    obj, rig, _ = bound_fixture()
    with targets.edit_rig(C, rig):
        old = {b.name: b.roll for b in rig.data.edit_bones}
    def fail(digit):
        if digit == 'INDEX': raise RuntimeError('injected second-side failure')
    result = targets.calibrate(C, after_pair=fail)
    assert 'INDEX' in result['failed'] and len(result['success']) == 4
    with targets.edit_rig(C, rig):
        assert all(abs(b.roll-old[b.name]) < 1e-6 for b in rig.data.edit_bones if 'index' in b.name)
        rig.data.edit_bones['f_middle.02.L'].tail.x += .02
    result = targets.calibrate(C)
    assert 'MIDDLE' in result['skipped']


def test_paired_topology_weights_idempotence_and_per_finger_settings():
    obj, rig, _ = bound_fixture()
    pair = work.prepare(C)
    before_count, source_keys = len(obj.data.vertices), len(obj.data.shape_keys.key_blocks)
    result = work.apply(C)
    print('PERF_GENERATE', result, flush=True)
    assert result['added'] > 0 and result['weighted'] > 0
    assert len(obj.data.shape_keys.key_blocks) == source_keys
    b = obj.character_designer_finger_bank
    assert all(not slot.error for slot in b.slots), [(s.name, s.error) for s in b.slots if s.error]
    first = layout.fingerprint(obj)
    result = work.apply(C)
    assert result['added'] == 0 and layout.fingerprint(obj) == first
    obj.update_from_editmode()
    generated = json.loads(obj.character_designer_finger_workflow.results)
    for key, data in generated.items():
        for ring in data['plan']['rings']:
            if ring['joint'] < 0: continue
            joint = pair.joints[ring['joint']]
            ratio = (joint.root_weight, joint.center_weight, joint.tip_weight)[ring['flank']+1]
            label = json.loads(pair.labels)[key+':'+str(ring['joint'])]
            for vi in ring['vertex_ids']:
                groups = {obj.vertex_groups[g.group].name: g.weight for g in obj.data.vertices[vi].groups}
                preserved = sum(v for n, v in groups.items() if n in {b.name for b in rig.data.bones if b.use_deform}-{label['A'], label['B']})
                assert abs(groups[label['A']]-(1-preserved)*ratio) < 1e-5
                assert abs(groups[label['B']]-(1-preserved)*(1-ratio)) < 1e-5
                assert abs(groups['OtherDeform']-.1) < 1e-5 and abs(groups['ArtistWeight']-.4) < 1e-5
    pair.joints[0].root_weight = .7
    result = work.apply(C, weights_only=True)
    print('PERF_WEIGHTS', result, flush=True)
    assert result['added'] == 0
    bank.select(C, 'MIDDLE', 'L')
    middle = work.prepare(C)
    assert middle != pair and abs(middle.joints[0].root_weight-.8) < 1e-6
    middle.joints[0].width = .02
    work.apply(C)
    bank.select(C, 'INDEX', 'L')
    assert work.settings(C)[2].joints[0].root_weight == pair.joints[0].root_weight
    work.apply(C)
    assert all(not slot.error for slot in b.slots)
    assert len(obj.data.vertices) > before_count
    assert len(targets.calibrate(C)['success']) == 5


def test_weight_lock_manual_edit_and_transaction_failures():
    obj, rig, _ = bound_fixture()
    pair = work.prepare(C)
    before = layout.fingerprint(obj)
    obj.vertex_groups['f_index.02.R'].lock_weight = True
    # Group locks also participate in the immutable data guard.
    refused(lambda: work.apply(C))
    assert len(obj.data.vertices) == len(obj.character_designer_finger_workflow.source.vertices)
    obj.vertex_groups['f_index.02.R'].lock_weight = False
    work.release(C); pair = work.prepare(C)
    before = layout.fingerprint(obj)
    records = {s.name: s.guide.record for s in obj.character_designer_finger_bank.slots}
    def fail(): raise RuntimeError('injected before commit completion')
    try: work.apply(C, after_commit=fail)
    except RuntimeError: pass
    else: raise AssertionError('Failure injection was ignored')
    assert layout.fingerprint(obj) == before
    assert records == {s.name: s.guide.record for s in obj.character_designer_finger_bank.slots}
    work.apply(C)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.vertex_groups['ArtistWeight'].add([0], .123, 'REPLACE')
    bpy.ops.object.mode_set(mode='EDIT')
    edited = layout.fingerprint(obj)
    refused(lambda: work.apply(C))
    assert layout.fingerprint(obj) == edited


def test_unbound_explicit_loop_and_asymmetric_spacing():
    obj, chosen = fixture(); capture_all(obj, chosen); bank.select(C, 'INDEX', 'L')
    bpy.ops.object.mode_set(mode='OBJECT')
    uv = obj.data.uv_layers.new(name='ArtistUV')
    for loop in obj.data.loops:
        p = obj.data.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (p.x, p.y)
    attr = obj.data.attributes.new('ArtistScalar', 'FLOAT', 'POINT')
    for i, value in enumerate(attr.data): value.value = obj.data.vertices[i].co.y
    obj.data.normals_split_custom_set([tuple(c.vector) for c in obj.data.corner_normals])
    bpy.ops.object.mode_set(mode='EDIT')
    pair = work.prepare(C); pair.auto_weight = False
    # One requested joint, center must be an existing closed ring.
    while len(pair.joints) > 1: pair.joints.remove(len(pair.joints)-1)
    body = bank.current_candidate(C)
    bm = bmesh.from_edit_mesh(obj.data); definition._index(bm)
    select(obj, [])
    row = body['rings'][2]
    for i, vi in enumerate(row): bm.edges.get((bm.verts[vi], bm.verts[row[(i+1) % len(row)]])).select_set(True)
    C.tool_settings.mesh_select_mode = (False, True, False)
    work.capture_joint(C)
    center_before = [tuple(bm.verts[i].co) for i in row]
    pair.joints[0].inner_spacing, pair.joints[0].outer_spacing = 1.3, .7
    result = work.apply(C)
    bm = bmesh.from_edit_mesh(obj.data); definition._index(bm)
    assert center_before == [tuple(bm.verts[i].co) for i in row]
    assert result['weighted'] == 0 and result['added'] == 32
    assert obj.data.has_custom_normals and obj.data.uv_layers.get('ArtistUV') and obj.data.attributes.get('ArtistScalar')
    original = layout.fingerprint(obj)
    assert work.apply(C)['added'] == 0 and layout.fingerprint(obj) == original


def test_save_reopen_and_idle_cache():
    obj, rig, _ = bound_fixture(); work.prepare(C); work.apply(C)
    name, counts = obj.name, len(obj.data.vertices)
    with tempfile.TemporaryDirectory(prefix='finger-workflow-') as folder:
        path = str(Path(folder)/'workflow.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path); bpy.ops.wm.open_mainfile(filepath=path)
        obj = bpy.data.objects[name]
        assert len(obj.data.vertices) == counts and work.apply(C)['added'] == 0
    from character_designer import finger_definition_ui as ui_def
    ui_def.redraw(); ui_def.cached_frame(C)
    old = definition._snapshot
    def forbidden(*args, **kwargs): raise AssertionError('Idle preview scanned the mesh')
    definition._snapshot = forbidden
    try:
        start = time.perf_counter()
        for _ in range(1000): ui_def.cached_frame(C)
        print('PERF_IDLE_1000_CACHE', time.perf_counter()-start, flush=True)
    finally: definition._snapshot = old


def test_multiple_selection_ambiguity_controllers_and_pose_guard():
    obj, rig, _ = bound_fixture()
    with targets.edit_rig(C, rig):
        for side in ('L', 'R'):
            parent = None
            for i in (1, 2, 3):
                original = rig.data.edit_bones[f'f_index.{i:02d}.{side}']
                other = rig.data.edit_bones.new(f'f_index_alt.{i:02d}.{side}')
                other.head, other.tail, other.parent = original.head, original.tail, parent
                parent = other
            controller = rig.data.edit_bones.new('ctrl_index.01.'+side)
            controller.head, controller.tail = (0, 0, 0), (0, 1, 0)
    result = targets.calibrate(C)
    assert 'INDEX' in result['skipped'] and 'Multiple candidate' in result['skipped']['INDEX']
    assert len(result['success']) == 4
    bpy.ops.object.mode_set(mode='OBJECT'); obj.select_set(False); rig.select_set(True); C.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    for b in rig.data.edit_bones: b.select = b.select_head = b.select_tail = False
    for name in ('f_index.02.L', 'f_index.01.R', 'f_middle.02.R'): rig.data.edit_bones[name].select = True
    result = targets.calibrate(C, selected=True)
    assert result['success'] == {'INDEX': 6, 'MIDDLE': 6}, result
    bpy.ops.object.mode_set(mode='POSE')
    rig.pose.bones['f_index.02.R'].rotation_mode = 'XYZ'
    rig.pose.bones['f_index.02.R'].rotation_euler.x = .2
    result = targets.calibrate(C)
    assert 'INDEX' in result['skipped'] and 'neutral' in result['skipped']['INDEX']


def test_missing_groups_topology_only_and_inactive_parameters():
    obj, rig, _ = bound_fixture()
    bpy.ops.object.mode_set(mode='OBJECT')
    for side in ('L', 'R'):
        for i in (2, 3): obj.vertex_groups.remove(obj.vertex_groups[f'f_index.{i:02d}.{side}'])
    bpy.ops.object.mode_set(mode='EDIT')
    pair = work.prepare(C)
    old_groups = list(obj.vertex_groups.keys())
    def fail(): raise RuntimeError('after missing groups creation')
    try: work.apply(C, after_commit=fail)
    except RuntimeError: pass
    else: raise AssertionError('Expected rollback')
    assert list(obj.vertex_groups.keys()) == old_groups
    work.apply(C)
    assert all(f'f_index.{i:02d}.{s}' in obj.vertex_groups for s in ('L', 'R') for i in (2, 3))
    pair.auto_weight = False
    before = layout.fingerprint(obj)
    refused(lambda: work.apply(C)); assert layout.fingerprint(obj) == before
    work.apply(C, weights_only=True)
    refused(lambda: work.apply(C))
    pair.auto_weight = True
    committed = json.loads(obj.character_designer_finger_workflow.results)['INDEX.L']['parameters']
    pair.joints[0].position = .38  # Not yet applied: another finger must not execute this.
    bank.select(C, 'MIDDLE', 'L'); work.prepare(C); work.apply(C)
    assert json.loads(obj.character_designer_finger_workflow.results)['INDEX.L']['parameters'] == committed


def test_reflection_plane_and_unavailable_opposite():
    from mathutils import Matrix
    obj, rig, _ = bound_fixture()
    # A shared world transform must not introduce an implicit world-X mirror.
    world = Matrix.Translation((2, 3, 4)) @ Matrix.Rotation(.8, 4, 'Z')
    obj.matrix_world, rig.matrix_world = world, world
    assert len(targets.calibrate(C)['success']) == 5
    # An inconsistent explicit mirror reference is not silently ignored.
    empty = bpy.data.objects.new('ArtistMirror', None); C.collection.objects.link(empty)
    empty.matrix_world = world @ Matrix.Translation((.4, 0, 0))
    mod = obj.modifiers.new('MirrorRef', 'MIRROR'); mod.mirror_object = empty
    result = targets.calibrate(C)
    assert not result['success'] and len(result['skipped']) == 5
    obj.modifiers.remove(mod)
    # Missing report entry is not evidence that an existing hand disappeared.
    report = json.loads(obj.character_designer_finger_bank.survey)
    report['candidates'].pop('INDEX.R')
    obj.character_designer_finger_bank.survey = json.dumps(report)
    result = targets.calibrate(C)
    assert 'INDEX' in result['skipped'] and 'Opposite' in result['skipped']['INDEX']


def test_proven_single_hand_and_invalid_loop():
    obj, rig, chosen = bound_fixture()
    bm = bmesh.from_edit_mesh(obj.data)
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.co.x < 0], context='VERTS')
    bmesh.update_edit_mesh(obj.data, destructive=True)
    with targets.edit_rig(C, rig):
        for b in list(rig.data.edit_bones):
            if b.name.endswith('.R'): rig.data.edit_bones.remove(b)
    b = obj.character_designer_finger_bank
    b.slots.clear(); b.survey = ''; b.active = ''
    select(obj, chosen['INDEX.L']); bank.capture(C)
    result = targets.calibrate(C)
    assert result['success'] == {'INDEX': 3}, result
    pair = work.prepare(C)
    refused(lambda: work.capture_joint(C))  # Longitudinal strip is not a center loop.
    before = layout.fingerprint(obj)
    pair.joints[1].position = pair.joints[0].position+.01
    refused(lambda: work.apply(C)); assert before == layout.fingerprint(obj)
    pair.joints[1].position = 2/3
    assert work.apply(C)['weighted'] == 48


def test_impossible_budget_and_locked_targets_are_atomic():
    obj, rig, _ = bound_fixture()
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.vertex_groups['OtherDeform'].add(list(range(len(obj.data.vertices))), .8, 'REPLACE')
    bpy.ops.object.mode_set(mode='EDIT')
    work.prepare(C)
    before = layout.fingerprint(obj)
    refused(lambda: work.apply(C)); assert layout.fingerprint(obj) == before
    obj.vertex_groups['f_index.02.R'].lock_weight = True
    work.release(C); work.prepare(C)
    before = layout.fingerprint(obj)
    refused(lambda: work.apply(C)); assert layout.fingerprint(obj) == before


def test_surface_conflict_retains_independent_record():
    obj, _, selected = bound_fixture()
    b = obj.character_designer_finger_bank
    r = json.loads(b.slots['INDEX.R'].guide.record)
    r['capture_source'] = 'INDEX.R'
    r['basis']['normal'] = [-v for v in r['basis']['normal']]
    b.slots['INDEX.R'].guide.record = json.dumps(r)
    saved = b.slots['INDEX.R'].guide.record
    select(obj, selected['INDEX.L']); bank.capture(C)
    assert b.slots['INDEX.R'].guide.record == saved
    assert 'conflict' in b.slots['INDEX.R'].error


if __name__ == '__main__':
    character_designer.register()
    for test in [v for k, v in list(globals().items()) if k.startswith('test_')]:
        test(); print('PASS', test.__name__, flush=True)
    print('FINGER_WORKFLOW_PASS', flush=True)
