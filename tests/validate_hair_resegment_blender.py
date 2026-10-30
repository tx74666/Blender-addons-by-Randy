"""Private native Hair re-segmentation acceptance on a complete frozen package.

Never opens artist data. The old bound mesh, Rig and Actions stay as comparison
objects; changed segment counts are sampled copies, not exact LBS equivalence.
Launch with tools/run_trusted_blender_validation.py --background and autoexec.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback
import unittest
from unittest.mock import patch

import bpy

REPO = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--projection', type=Path, required=True)
parser.add_argument('--projection-sha', required=True)
parser.add_argument('--report', type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
assert bpy.app.background and not bpy.data.filepath and not args.report.exists()
assert '--enable-autoexec' in sys.argv and '--disable-autoexec' not in sys.argv
assert sha(args.projection) == args.projection_sha
manifest = json.loads(args.projection.read_bytes())
PACKAGE = Path(manifest['source_root']).resolve()
assert all(sha(PACKAGE / name) == digest for name, digest in manifest['files'].items())
sys.path[:0] = [str(PACKAGE.parent), str(REPO / 'tests')]
import character_designer as cd
from character_designer import hair_resegment as service
from character_designer import hair_bones_binding as binding, hair_bones_rig as hair
from character_designer import hair_bones_variants as variants, animation_retarget as retarget
from character_designer import hair_strand_registry as registry, hair_motion_profiles as profiles
from test_hair_bones_binding_blender import scene_fixture
from test_hair_bones_rig_blender import activate
assert Path(service.__file__).resolve() == PACKAGE / 'hair_resegment.py'

METRICS = {}


def plain(value):
    if isinstance(value, bpy.types.ID):
        return ('ID', value.bl_rna.identifier, value.name_full)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, 'items'):
        return {str(k): plain(v) for k, v in value.items()}
    try:
        return tuple(plain(v) for v in value)
    except TypeError:
        return repr(value)


def curves(action):
    if action is None:
        return None
    if action.is_action_legacy:
        entries = [(None, None, None, c) for c in action.fcurves]
    else:
        entries = [(li, si, bag.slot_handle, c)
                   for li, layer in enumerate(action.layers)
                   for si, strip in enumerate(layer.strips)
                   for bag in strip.channelbags for c in bag.fcurves]
    return (action.name_full, bool(action.use_fake_user), plain(action),
        tuple((li, si, handle, c.data_path, c.array_index, c.extrapolation,
               tuple((tuple(k.co), tuple(k.handle_left), tuple(k.handle_right),
                      k.interpolation, k.handle_left_type, k.handle_right_type)
                     for k in c.keyframe_points),
               tuple((m.type, m.mute, m.show_expanded) for m in c.modifiers))
              for li, si, handle, c in entries))


def animation(owner):
    ad = owner.animation_data
    if ad is None:
        return None
    return (ad.action.name_full if ad.action else None, ad.action_slot_handle,
            ad.use_nla, ad.use_tweak_mode, ad.action_blend_type,
            ad.action_extrapolation, ad.action_influence,
            tuple((track.name, track.mute, track.is_solo, track.lock,
                tuple((strip.name, strip.type, strip.action.name_full if strip.action else None,
                       strip.action_slot_handle, strip.frame_start, strip.frame_end,
                       strip.action_frame_start, strip.action_frame_end, strip.scale,
                       strip.repeat, strip.influence, strip.blend_type, strip.extrapolation,
                       strip.mute, strip.use_animated_influence, strip.use_animated_time)
                      for strip in track.strips)) for track in ad.nla_tracks))


def mesh_author(obj):
    data = obj.data
    keys = data.shape_keys
    return dict(vertices=tuple((tuple(v.co), v.select, v.hide) for v in data.vertices),
        edges=tuple(tuple(e.vertices) for e in data.edges),
        faces=tuple((tuple(f.vertices), f.material_index, f.use_smooth) for f in data.polygons),
        uv=tuple((layer.name, tuple(tuple(x.uv) for x in layer.data)) for layer in data.uv_layers),
        keys=None if keys is None else tuple((key.name, key.value, key.mute,
             key.slider_min, key.slider_max, key.relative_key.name,
             tuple(tuple(v.co) for v in key.data)) for key in keys.key_blocks),
        object_animation=animation(obj), data_animation=animation(data),
        key_animation=animation(keys) if keys else None)


def weights(obj):
    names = {g.index: g.name for g in obj.vertex_groups}
    return tuple({names[w.group]: float(w.weight) for w in v.groups} for v in obj.data.vertices)


def rig_author(rig):
    return dict(properties=plain(rig), data_properties=plain(rig.data),
        basis=tuple(tuple(row) for row in rig.matrix_basis),
        parent=rig.parent.name_full if rig.parent else None,
        collections=tuple((c.name, c.parent.name if c.parent else None,
            c.is_visible, c.is_solo, tuple(b.name for b in c.bones), plain(c))
            for c in rig.data.collections_all),
        rest=tuple((b.name, b.parent.name if b.parent else None, b.use_connect,
            b.use_deform, b.inherit_scale, b.use_inherit_rotation, b.use_local_location,
            b.hide, b.hide_select, bool(b.select),
            tuple(tuple(row) for row in b.matrix_local), b.length, plain(b)) for b in rig.data.bones),
        pose=tuple((p.name, p.rotation_mode, tuple(p.location), tuple(p.rotation_euler),
            tuple(p.rotation_quaternion), tuple(p.rotation_axis_angle), tuple(p.scale), plain(p),
            tuple((c.name, c.type, c.mute, c.influence) for c in p.constraints)) for p in rig.pose.bones),
        animation=animation(rig))


def source_author(source, rig):
    return dict(mesh=mesh_author(source), weights=weights(source), properties=plain(source),
        groups=tuple((g.name, g.lock_weight) for g in source.vertex_groups),
        modifiers=tuple((m.name, m.type, m.show_viewport, m.show_render,
            m.object.name_full if m.type == 'ARMATURE' and m.object else None) for m in source.modifiers),
        parent=source.parent.name_full if source.parent else None,
        parent_inverse=tuple(tuple(row) for row in source.matrix_parent_inverse),
        basis=tuple(tuple(row) for row in source.matrix_basis), rig=rig_author(rig))


def ids():
    return {p.identifier: {item.as_pointer() for item in getattr(bpy.data, p.identifier)}
            for p in bpy.data.bl_rna.properties if p.type == 'COLLECTION'}


def context_state():
    return (bpy.context.mode, bpy.context.object.name_full if bpy.context.object else None,
        tuple(o.name_full for o in bpy.context.selected_objects),
        bpy.context.scene.frame_current, bpy.context.scene.frame_subframe,
        bpy.context.scene.tool_settings.use_keyframe_insert_auto)


def visibility():
    return (tuple((obj.as_pointer(), obj.hide_get(), obj.hide_viewport, obj.hide_render,
                   obj.hide_select) for obj in bpy.context.scene.objects),
            tuple((item.as_pointer(), item.hide_viewport, item.hide_render)
                  for item in bpy.data.collections))


def fixture(*, nla=False):
    source, plans, rig = scene_fixture()
    bound = binding.bind_hair(bpy.context, source, plans, bone_count=4, armature=rig)
    record = hair._read_records(source)
    assert all(len(c['bones']) == 4 for c in record['chains'])
    configured = registry.initialize(source)
    profiles.initialize(source, registry=configured)
    profiles.update(source, configured['strands'][0]['strand_id'], group='FRONT',
                    overrides={'recovery': .91, 'damping': .86, 'gravity': 1.4,
                               'stretch': .23}, registry=configured)
    # All changes below happen after binding; stale pre-bind paint is different.
    vertex = record['chains'][0]['vertices'][-1]
    first = record['chains'][0]['bones']
    for name in first:
        group = source.vertex_groups.get(name)
        if group:
            group.remove([vertex])
    source.vertex_groups[first[0]].add([vertex], .31, 'REPLACE')
    source.vertex_groups[first[-1]].add([vertex], .42, 'REPLACE')
    source.vertex_groups['Artist Mask'].lock_weight = True
    source.data.shape_keys.key_blocks['Artist Detail'].value = .35
    uv = source.data.uv_layers.new(name='Authored UV')
    for index, item in enumerate(uv.data):
        item.uv = ((index % 7) / 7., (index % 11) / 11.)
    action = bpy.data.actions.new('Authored Hair and Head')
    action.use_fake_user = True
    action.slots.new(id_type='OBJECT', name='Unused first slot')
    slot, bag = retarget._new_channelbag(action, rig)
    for name, axis, amplitude in [('Head', 0, .2), (first[0], 2, .32), (first[-1], 1, -.21)]:
        rig.pose.bones[name].rotation_mode = 'XYZ'
        retarget._write_curve(bag, rig.pose.bones[name].path_from_id('rotation_euler'),
                             axis, [1., 3.25, 8.], [0., amplitude, -.1])
    ad = rig.animation_data_create()
    ad.action, ad.action_slot = action, slot
    if nla:
        overlay = bpy.data.actions.new('Authored Hair NLA')
        overlay.use_fake_user = True
        overlay.slots.new(id_type='OBJECT', name='Unused NLA slot')
        overlay_slot, overlay_bag = retarget._new_channelbag(overlay, rig)
        retarget._write_curve(overlay_bag, rig.pose.bones[first[1]].path_from_id('rotation_euler'),
                             0, [1., 4., 8.], [.1, -.25, .2])
        track = ad.nla_tracks.new()
        track.name = 'Retained original NLA'
        strip = track.strips.new('Scaled sampled Hair', 1, overlay)
        strip.action_slot = overlay_slot
        strip.blend_type, strip.influence = 'ADD', .4
        strip.scale = 1.25
        ad.use_nla = True
    bpy.context.scene.frame_set(3, subframe=.25)
    rig.pose.bones['Arm'].rotation_mode = 'XYZ'
    rig.pose.bones['Arm'].rotation_euler.x = .17  # unkeyed author edit
    activate(rig, 'POSE')
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
    bpy.context.view_layer.update()
    return source, rig, record


def assert_result(case, source, record, result, count, before):
    output, rig = result['mesh'], result['armature']
    case.assertIsNot(output, source)
    case.assertEqual(source_author(source, before['rig_object']), before['author'])
    case.assertEqual(mesh_author(output), mesh_author(source))
    case.assertEqual(result['count'], count)
    new_record = hair._read_records(output)
    case.assertEqual(len(new_record['chains']), len(record['chains']))
    # Match by explicit saved signature, never by nearest geometry or row order.
    new_by_signature = {c['signature']: c for c in new_record['chains']}
    old_skin, new_skin = before['weights'], weights(output)
    max_total_error = 0.
    for chain in record['chains']:
        new = new_by_signature[chain['signature']]
        case.assertEqual(len(new['bones']), count)
        case.assertTrue(all(name in rig.pose.bones for name in new['bones']))
        for index, (old, current) in enumerate(zip(old_skin, new_skin)):
            previous_total = sum(old.get(name, 0.) for name in chain['bones'])
            new_total = sum(current.get(name, 0.) for name in new['bones'])
            max_total_error = max(max_total_error, abs(previous_total-new_total))
            case.assertLessEqual(abs(previous_total-new_total), 2e-6, (index, previous_total, new_total))
    for index, old in enumerate(old_skin):
        case.assertEqual(new_skin[index].get('Artist Mask', 0.), old.get('Artist Mask', 0.))
    case.assertTrue(output.vertex_groups['Artist Mask'].lock_weight)
    old_registry, new_registry = registry.read(source), registry.read(output)
    case.assertNotEqual(old_registry['source_uid'], new_registry['source_uid'])
    old_effective = {item['signature']: profiles.effective(source, item['strand_id'], registry=old_registry)
                     for item in old_registry['strands']}
    new_effective = {item['signature']: profiles.effective(output, item['strand_id'], registry=new_registry)
                     for item in new_registry['strands']}
    case.assertEqual(new_effective, old_effective)
    case.assertEqual(bpy.context.scene.frame_current, 3)
    case.assertEqual(bpy.context.scene.frame_subframe, .25)
    case.assertTrue(bpy.context.scene.tool_settings.use_keyframe_insert_auto)
    METRICS[case._testMethodName] = dict(count=count, strands=len(new_record['chains']),
        samples=plain(result['samples']), maximum_weight_total_error=max_total_error,
        service_report=plain({k: v for k, v in result.items() if k not in {'mesh','armature','collection'}}),
        exact_deformation_equivalence_claimed=False)
    return output, rig


def sampled_motion_metrics(case, source, original_rig, original_record, output, new_rig):
    old_frame = bpy.context.scene.frame_current, bpy.context.scene.frame_subframe
    new_record = hair._read_records(output)
    new_chains = {c['signature']: c for c in new_record['chains']}
    samples = []
    try:
        for frame in (1., 3.25, 4.5, 8.):
            bpy.context.scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
            bpy.context.view_layer.update()
            depsgraph = bpy.context.evaluated_depsgraph_get()
            old_eval, new_eval = (obj.evaluated_get(depsgraph) for obj in (original_rig, new_rig))
            root_old = old_eval.matrix_world @ old_eval.pose.bones[original_record['parent']].matrix
            root_new = new_eval.matrix_world @ new_eval.pose.bones[new_record['parent']].matrix
            root_error = max(abs(a-b) for ar,br in zip(root_old,root_new) for a,b in zip(ar,br))
            case.assertLessEqual(root_error, 3e-5)
            tip_error = max(((old_eval.matrix_world @ old_eval.pose.bones[c['bones'][-1]].tail)
                             - (new_eval.matrix_world @ new_eval.pose.bones[
                                 new_chains[c['signature']]['bones'][-1]].tail)).length
                            for c in original_record['chains'])
            # Domain endpoints are mandatory sample times. Intermediate frames
            # are measured approximations and intentionally not called exact.
            if frame in (1., 8.):
                case.assertLessEqual(tip_error, 3e-4)
            old_mesh_eval, new_mesh_eval = (obj.evaluated_get(depsgraph) for obj in (source, output))
            old_mesh, new_mesh = old_mesh_eval.to_mesh(), new_mesh_eval.to_mesh()
            try:
                case.assertEqual(len(old_mesh.vertices), len(new_mesh.vertices))
                vertex_error = max(((old_mesh_eval.matrix_world @ a.co)
                                    - (new_mesh_eval.matrix_world @ b.co)).length
                                   for a,b in zip(old_mesh.vertices,new_mesh.vertices))
            finally:
                old_mesh_eval.to_mesh_clear()
                new_mesh_eval.to_mesh_clear()
            case.assertTrue(all(math.isfinite(x) for x in (root_error,tip_error,vertex_error)))
            samples.append(dict(frame=frame, head_matrix_error=root_error,
                                terminal_point_error=tip_error, mesh_world_error=vertex_error))
    finally:
        bpy.context.scene.frame_set(old_frame[0], subframe=old_frame[1])
        bpy.context.view_layer.update()
    METRICS[case._testMethodName]['measured_sample_errors'] = samples
    METRICS[case._testMethodName]['intermediate_samples_exact_claimed'] = False


class NativeHairResegment(unittest.TestCase):
    def check_create(self, count, *, nla=False, activate_result=True):
        source, rig, record = fixture(nla=nla)
        before = dict(author=source_author(source, rig), rig_object=rig, weights=weights(source))
        old_actions = {a: curves(a) for a in bpy.data.actions}
        result = service.create(bpy.context, source, bone_count=count,
            frame_start=1., frame_end=8., activate=activate_result)
        output, result_rig = assert_result(self, source, record, result, count, before)
        for action, old in old_actions.items():
            self.assertEqual(curves(action), old)
        self.assertIsNot(result_rig.animation_data.action, rig.animation_data.action)
        values = [float(v) for c in retarget._curves(result_rig.animation_data.action,
                        result_rig.animation_data.action_slot)
                  for key in c.keyframe_points for v in key.co]
        self.assertTrue(values and all(math.isfinite(v) for v in values))
        sampled_motion_metrics(self, source, rig, record, output, result_rig)
        self.assertEqual(source_author(source, rig), before['author'])
        return source, rig, record, result, before

    def test_six_and_seven_preserve_postbind_paint_keys_rest_and_action_slot(self):
        self.check_create(6)
        self.check_create(7)

    def test_reduce_to_three_bakes_visible_nla_without_changing_old_stack(self):
        self.check_create(3, nla=True)

    def test_multiple_versions_source_switch_and_native_save_reopen(self):
        source, rig, record, first, before = self.check_create(6)
        variants.show_source(bpy.context, source)
        activate(rig, 'POSE')
        second = service.create(bpy.context, source, bone_count=7, frame_start=1., frame_end=8.)
        assert_result(self, source, record, second, 7, before)
        variants.show_variant(bpy.context, first['collection'])
        self.assertIs(bpy.context.object, first['armature'])
        variants.show_variant(bpy.context, second['collection'])
        self.assertIs(bpy.context.object, second['armature'])
        snapshot = source_author(source, rig)
        names = source.name, rig.name, first['collection'].name, second['collection'].name
        path = args.report.with_name(args.report.stem + '_saved.blend')
        self.assertFalse(path.exists())
        self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False), {'FINISHED'})
        self.assertEqual(bpy.ops.wm.open_mainfile(filepath=str(path), use_scripts=True), {'FINISHED'})
        source, rig = (bpy.data.objects[n] for n in names[:2])
        self.assertEqual(source_author(source, rig), snapshot)
        for name in names[2:]:
            variants.show_variant(bpy.context, bpy.data.collections[name])
        variants.show_source(bpy.context, source)
        self.assertEqual(source_author(source, rig), snapshot)

    def test_inactive_result_keeps_context_and_original_author_data(self):
        source, rig, record = fixture()
        before = source_author(source, rig), context_state()
        result = service.create(bpy.context, source, bone_count=6, frame_start=1., frame_end=8., activate=False)
        self.assertEqual(source_author(source, rig), before[0])
        self.assertEqual(context_state(), before[1])
        self.assertIsNotNone(result['mesh'])

    def test_reached_weight_action_publish_faults_restore_owned_ids_and_context(self):
        for name in ('_write_weights', '_write_action', '_publish'):
            with self.subTest(fault=name):
                source, rig, _record = fixture()
                before = source_author(source, rig), context_state(), ids(), visibility()
                original = getattr(service, name)
                reached = []
                def fail(*a, **kw):
                    value = original(*a, **kw)
                    reached.append(True)
                    raise RuntimeError('Injected reached ' + name)
                with patch.object(service, name, side_effect=fail):
                    with self.assertRaisesRegex((ValueError, RuntimeError), 'Injected reached ' + name):
                        service.create(bpy.context, source, bone_count=7, frame_start=1., frame_end=8.)
                self.assertTrue(reached, 'The requested fault stage was not reached.')
                self.assertEqual(source_author(source, rig), before[0])
                self.assertEqual(context_state(), before[1])
                self.assertEqual(ids(), before[2])
                self.assertEqual(visibility(), before[3])


result = None
registered = False
report = dict(schema='character-designer.hair-resegment-native/1', passed=False,
    projection=str(args.projection), projection_sha256=args.projection_sha,
    source_package=str(PACKAGE), harness_sha256=sha(__file__), metrics=METRICS,
    autoexec_enabled=True, artist_file_opened=False, preferences_saved=False,
    private_scene_save_reopen=True, errors=[], cleanup_errors=[],
    limits=['Native synthetic Hair fixture only; not artist/full character/FPS acceptance.',
            'Changed counts are sampled copies; no exact LBS/Bezier/subframe equivalence claim.'])
try:
    cd.register()
    registered = True
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeHairResegment))
except BaseException:
    report['errors'].append(traceback.format_exc())
finally:
    if registered:
        try:
            if bpy.context.object and bpy.context.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            cd.unregister()
        except BaseException:
            report['cleanup_errors'].append(traceback.format_exc())
    report.update(tests_run=result.testsRun if result else 0,
        failures=[(str(c), message) for c, message in result.failures] if result else [],
        test_errors=[(str(c), message) for c, message in result.errors] if result else [],
        source_unchanged=all(sha(PACKAGE/n) == value for n,value in manifest['files'].items()))
    report['passed'] = bool(result and result.wasSuccessful() and report['source_unchanged']
                            and not report['errors'] and not report['cleanup_errors'])
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf8')
if not report['passed']:
    raise RuntimeError('Hair re-segmentation native acceptance failed; original receipt retained.')
