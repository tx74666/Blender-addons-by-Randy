"""Isolated native Hair2/Dress acceptance; launch only through the trusted runner.

--addons-root selects the complete approved stage. --wiggle-file is the exact
installed 2.2.4 file. --report-json must name a fresh receipt. No artist file is
opened. Tiny fixture SaveCopy/reopen and history are intentionally native.
"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch

import addon_utils
import bpy

ROOT = Path(__file__).resolve().parents[1]
ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
parser = argparse.ArgumentParser()
parser.add_argument('--addons-root', type=Path, required=True)
parser.add_argument('--wiggle-file', type=Path, required=True)
parser.add_argument('--report-json', type=Path, required=True)
args = parser.parse_args(ARGS)
stage = args.addons_root.resolve(strict=True)
package_root = stage / 'character_designer'
sys.path[:0] = [str(stage), str(ROOT / 'tests')]
import character_designer as cd
from character_designer import hair_wiggle_adapter as facade
from character_designer import hair_wiggle2_adapter as hair
from character_designer import hair_bones_binding as binding
from character_designer import hair_strand_registry as registry
from character_designer import skirt_wiggle as skirt
from character_designer import skirt_rig
from character_designer import animation_physics as control
from character_designer import wiggle2_runtime as runtime
from test_hair_bones_binding_blender import scene_fixture
from test_hair_bones_rig_blender import activate
from test_skirt_topology_blender import frustum
from test_skirt_shared_rig_blender import mesh_content, rest_state, pose_channels

REPORT = {'schema': 'character-designer.hair-wiggle2-native/7', 'passed': False,
          'derived_from': {'script': 'test_hair_wiggle2_blender06.py', 'sha256': '600fa59c380725226b1735eeb69ed5e84f4593cc73076ce2f334bbb4e5cc7231'},
          'cases': [], 'errors': [], 'artist_operations': False, 'preferences_saved': False,
          'limits': ['Tiny synthetic shared-rig fixtures, not an artist model or FPS/collision quality measurement.'],
          'started_utc': datetime.now(timezone.utc).isoformat()}
BACKEND = None
REGISTERED = False


def require(value, message):
    if not value:
        raise AssertionError(message)


def pin(path):
    path = Path(path)
    return {'path': str(path.resolve()), 'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def staged_modules():
    paths = {}
    for name, module in tuple(sys.modules.items()):
        if name == 'character_designer' or name.startswith('character_designer.'):
            path = Path(module.__file__).resolve()
            require(path.is_relative_to(package_root), 'A product module escaped the selected stage: ' + name)
            paths[name] = str(path)
    return paths


def cleanup():
    global REGISTERED
    if hair._SESSION is not None:
        facade.stop_preview(reason='Native fixture cleanup.')
    skirt.unregister()
    if REGISTERED:
        cd.unregister()
        REGISTERED = False
    require(not runtime._LEASES, 'A native fixture leaked a shared runtime lease.')


def fixture():
    global REGISTERED
    cleanup()
    # The existing author-rich Hair fixture resets only this private process.
    source, plans, rig = scene_fixture()
    with skirt._native_writes(BACKEND):
        binding.bind_hair(bpy.context, source, plans, bone_count=4, armature=rig)
        registry.initialize(source)
        dress = frustum('Shared Dress', rows=6, sides=16)
        dress.shape_key_add(name='Basis')
        key = dress.shape_key_add(name='Artist fold')
        key.data[3].co.x += .009
        key.value = .32
        dress.vertex_groups.new(name='Artist mask').add([1, 2, 3], .27, 'REPLACE')
        skirt_rig.build_skirt(bpy.context, dress, chain_count=4, segment_count=3,
                              armature=rig, parent_bone='Head', shared=True)
    require(dress[skirt_rig.RIG_KEY] == rig, 'Fixture did not produce one shared rig.')
    bpy.context.scene.render.fps_base = 1.0
    bpy.context.scene.unit_settings.scale_length = 1.0
    bpy.context.scene.gravity = (0, 0, -9.81)
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 40
    bpy.context.scene.frame_set(8)
    cd.register()
    REGISTERED = True
    cd._validate_registration_integrity()
    bpy.context.scene.character_designer_setup.rig = rig
    activate(source)
    data = registry.read(source, validate=True)
    profiles = {item['strand_id']: dict(recovery=.4, damping=.3, gravity=4., stretch=0., depth=[])
                for item in data['strands']}
    bpy.context.view_layer.update()
    staged_modules()
    return source, dress, rig, data, profiles


def start_hair(source, data, profiles):
    return facade.start_preview(bpy.context, source, data, profiles,
                                [item['strand_id'] for item in data['strands']])


def start_skirt(dress):
    record = skirt_rig.read_record(dress)
    control.set_skirt_mode(bpy.context, dress.name, 'AUTOMATIC', expected_owner=record['owner'])


def hair_names(data):
    return tuple(name for item in data['strands'] for name in item['bones'])


def dress_names(dress):
    return tuple(name for item in skirt_rig.read_record(dress)['chains'] for name in item['phys'])


def points(source):
    evaluated = source.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return tuple(tuple(vertex.co) for vertex in mesh.vertices)
    finally:
        evaluated.to_mesh_clear()


def delta(first, second):
    return max((abs(a - b) for left, right in zip(first, second) for a, b in zip(left, right)), default=0.)


def author(source, dress, rig):
    return {'hair': mesh_content(source), 'dress': mesh_content(dress), 'rest': rest_state(rig),
            'actions': hair._animation_proof(),
            'hair_props': {k: facade._clone(source[k]) for k in source.keys() if k != hair._RECOVERY_KEY}}


def assert_author(before, source, dress, rig):
    require(mesh_content(source) == before['hair'], 'Hair mesh/weights/Keys changed.')
    require(mesh_content(dress) == before['dress'], 'Dress mesh/weights/Keys changed.')
    require(rest_state(rig) == before['rest'], 'Shared rig Rest changed.')
    require(hair._animation_same(before['actions']), 'Native Action data or animation associations changed.')
    require({k: facade._clone(source[k]) for k in source.keys() if k != hair._RECOVERY_KEY} == before['hair_props'],
            'Hair author metadata changed.')


def advance(count=4):
    for _ in range(count):
        bpy.context.scene.frame_set(bpy.context.scene.frame_current + 1)


def native_state(rig, names):
    return {'channels': pose_channels(rig, names),
            'raw': {name: {field: runtime._raw(rig.pose.bones[name], field)
                          for field in runtime._FLAGS} for name in names},
            'groups': {name: runtime._group(rig.pose.bones[name]) for name in names},
            'members': {item for item in runtime._members(bpy.context.scene) if item[1] in names}}


class HairWiggle2NativeTests(unittest.TestCase):
    def tearDown(self):
        cleanup()

    def test_01_hair_forward_pose_mesh_and_owned_stop(self):
        source, dress, rig, data, profiles = fixture()
        before = author(source, dress, rig)
        channels = pose_channels(rig, hair_names(data))
        initial_mesh = points(source)
        start_hair(source, data, profiles)
        advance()
        require(facade.status()['active'], 'Hair preview lost its session during forward playback.')
        require(pose_channels(rig, hair_names(data)) != channels, 'No real Hair bone movement was observed.')
        movement = delta(initial_mesh, points(source))
        require(movement > 1e-6, 'No real evaluated Hair mesh movement was observed.')
        frame = bpy.context.scene.frame_current
        facade.stop_preview(bpy.context, source=source)
        require(bpy.context.scene.frame_current == frame, 'Hair Stop rewound time.')
        require(pose_channels(rig, hair_names(data)) == channels, 'Hair Stop did not restore its owned pose channels.')
        require(hair._RECOVERY_KEY not in source, 'Completed Hair Stop left its recovery marker.')
        require(not any(rig.pose.bones[name].wiggle_head or rig.pose.bones[name].wiggle_tail
                        for name in hair_names(data)), 'Hair Stop left its own native simulation flags active.')
        assert_author(before, source, dress, rig)
        REPORT['cases'].append({'case': 'forward_hair_pose_and_evaluated_mesh', 'passed': True,
                                'mesh_max_delta': movement, 'frame_preserved': frame})

    def test_02_both_start_orders_and_stop_one_preserves_peer(self):
        for order in ('SKIRT_FIRST', 'HAIR_FIRST'):
            with self.subTest(order=order):
                source, dress, rig, data, profiles = fixture()
                before = author(source, dress, rig)
                if order == 'SKIRT_FIRST':
                    start_skirt(dress)
                    start_hair(source, data, profiles)
                else:
                    start_hair(source, data, profiles)
                    start_skirt(dress)
                require(runtime.has_component('HAIR') and runtime.has_component('SKIRT'), 'Both solver leases are not live.')
                first_hair, first_dress = points(source), points(dress)
                advance()
                require(delta(first_hair, points(source)) > 1e-6, 'Hair did not move while Dress ran.')
                require(delta(first_dress, points(dress)) > 1e-6, 'Dress did not move while Hair ran.')
                if order == 'SKIRT_FIRST':
                    peer = native_state(rig, dress_names(dress))
                    facade.stop_preview(bpy.context, source=source)
                    require(native_state(rig, dress_names(dress)) == peer, 'Hair Stop touched Dress state.')
                    require(skirt.status(bpy.context, dress)['running'], 'Hair Stop ended Dress motion.')
                    require(not any(rig.pose.bones[name].wiggle_head or rig.pose.bones[name].wiggle_tail
                                    for name in hair_names(data)), 'Hair Stop retained own native flags.')
                    peer_mesh = points(dress)
                    advance(2)
                    require(skirt.status(bpy.context, dress)['running'], 'Dress stopped on the next frame after Hair Stop.')
                    require(delta(peer_mesh, points(dress)) > 1e-6, 'Dress did not keep solving after Hair Stop.')
                else:
                    peer = native_state(rig, hair_names(data))
                    control.set_skirt_mode(bpy.context, dress.name, 'MANUAL',
                                           expected_owner=skirt_rig.read_record(dress)['owner'])
                    require(native_state(rig, hair_names(data)) == peer, 'Dress Stop touched Hair state.')
                    require(facade.status()['active'], 'Dress Stop ended Hair preview.')
                    require(not any(rig.pose.bones[name].wiggle_head or rig.pose.bones[name].wiggle_tail
                                    for name in dress_names(dress)), 'Dress Stop retained own native flags.')
                    peer_mesh = points(source)
                    advance(2)
                    require(facade.status()['active'], 'Hair stopped on the next frame after Dress Stop.')
                    require(delta(peer_mesh, points(source)) > 1e-6, 'Hair did not keep solving after Dress Stop.')
                assert_author(before, source, dress, rig)
                REPORT['cases'].append({'case': 'shared_rig_' + order, 'passed': True})

    def test_03_part_pause_resume_keeps_peer_solving(self):
        source, dress, rig, data, profiles = fixture()
        start_skirt(dress)
        start_hair(source, data, profiles)
        advance(2)
        frame = bpy.context.scene.frame_current
        peer = native_state(rig, dress_names(dress))
        facade.set_preview_paused(bpy.context, source, True)
        require(native_state(rig, dress_names(dress)) == peer, 'Hair Pause touched Dress at the switch.')
        held = pose_channels(rig, hair_names(data))
        moving = points(dress)
        advance(2)
        require(pose_channels(rig, hair_names(data)) == held, 'Paused Hair channels continued changing.')
        require(delta(moving, points(dress)) > 1e-6, 'Hair Pause stopped Dress solving.')
        facade.set_preview_paused(bpy.context, source, False)
        skirt.set_paused(bpy.context, dress, True)
        held = pose_channels(rig, dress_names(dress))
        moving = points(source)
        advance(2)
        require(pose_channels(rig, dress_names(dress)) == held, 'Paused Dress channels continued changing.')
        require(delta(moving, points(source)) > 1e-6, 'Dress Pause stopped Hair solving.')
        skirt.set_paused(bpy.context, dress, False)
        require(not rig.wiggle_freeze and not rig.wiggle_mute, 'A component used global rig freeze/mute.')
        REPORT['cases'].append({'case': 'independent_part_pause_resume', 'passed': True, 'start_frame': frame})

    def test_04_master_off_on_preserves_preference_and_author_inputs(self):
        source, dress, rig, data, profiles = fixture()
        before = author(source, dress, rig)
        start_skirt(dress)
        start_hair(source, data, profiles)
        advance(2)
        frame = bpy.context.scene.frame_current
        control.set_enabled(bpy.context, False)
        require(facade.status()['paused'] and skirt.status(bpy.context, dress)['paused'], 'Master Off did not pause both owners.')
        require(all(rig.pose.bones[n].wiggle_mute for n in hair_names(data) + dress_names(dress)), 'Owned native solve flags stayed enabled.')
        require(control._mode(dress, skirt_rig.read_record(dress)) == 'AUTOMATIC', 'Master Off changed Dress preference.')
        control.set_enabled(bpy.context, True)
        require(not facade.status()['paused'] and not skirt.status(bpy.context, dress)['paused'], 'Master On did not resume both owners.')
        require(bpy.context.scene.frame_current == frame, 'Master toggle moved the frame.')
        assert_author(before, source, dress, rig)
        REPORT['cases'].append({'case': 'master_off_on', 'passed': True})

    def test_05_unknown_callback_refuses_and_exact_legacy_guard_is_allowed(self):
        source, dress, rig, data, profiles = fixture()
        before = author(source, dress, rig)
        def unknown_pose_writer(scene, *args):
            rig.pose.bones[hair_names(data)[0]].location.x = .9
        bpy.app.handlers.frame_change_post.append(unknown_pose_writer)
        try:
            with self.assertRaisesRegex(ValueError, 'unverified frame callback'):
                start_hair(source, data, profiles)
            require(not runtime.has_component('HAIR'), 'Rejected unknown callback left a Hair lease.')
            assert_author(before, source, dress, rig)
        finally:
            bpy.app.handlers.frame_change_post.remove(unknown_pose_writer)
        added = facade._frame_guard not in bpy.app.handlers.frame_change_pre
        if added:
            bpy.app.handlers.frame_change_pre.append(facade._frame_guard)
        try:
            start_hair(source, data, profiles)
            advance(1)
            require(facade.status()['active'], 'Exact inert historical guard incorrectly refused Hair2.')
        finally:
            facade.stop_preview(bpy.context, source=source)
            if added and facade._frame_guard in bpy.app.handlers.frame_change_pre:
                bpy.app.handlers.frame_change_pre.remove(facade._frame_guard)
        REPORT['cases'].append({'case': 'unknown_refused_exact_legacy_allowed', 'passed': True})

    def test_06_real_configure_failure_rolls_back_only_hair(self):
        source, dress, rig, data, profiles = fixture()
        start_skirt(dress)
        before = author(source, dress, rig)
        peer = native_state(rig, dress_names(dress))
        channels = pose_channels(rig, hair_names(data))
        original = runtime.configure
        def fail_after_real_configure(*values):
            original(*values)
            raise RuntimeError('Injected after real owned Hair configure')
        with patch.object(runtime, 'configure', side_effect=fail_after_real_configure):
            with self.assertRaisesRegex(ValueError, 'Injected after real owned Hair configure'):
                start_hair(source, data, profiles)
        require(not facade.status()['active'] and not runtime.has_component('HAIR'), 'Failed start retained Hair simulation.')
        require(native_state(rig, dress_names(dress)) == peer, 'Hair start rollback touched Dress.')
        require(pose_channels(rig, hair_names(data)) == channels, 'Hair rollback lost owned original pose.')
        assert_author(before, source, dress, rig)
        REPORT['cases'].append({'case': 'real_configure_failure_rollback', 'passed': True})

    def test_07_savecopy_reopen_has_no_automatic_reconnect(self):
        source, dress, rig, data, profiles = fixture()
        start_skirt(dress)
        start_hair(source, data, profiles)
        advance(2)
        names, rig_name, hair_name, dress_name = hair_names(data) + dress_names(dress), rig.name, source.name, dress.name
        frame = bpy.context.scene.frame_current
        with tempfile.TemporaryDirectory(prefix='cd_hair2_saved_', dir=str(args.report_json.parent)) as folder:
            path = Path(folder) / 'tiny_shared_preview.blend'
            require('FINISHED' in bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True), 'Native SaveCopy failed.')
            require(not facade.status()['active'] and not runtime._LEASES, 'Save left an active transient lease.')
            require(hair._RECOVERY_KEY not in source, 'Save retained an active Hair marker.')
            require(bpy.context.scene.frame_current == frame, 'Save guard rewound the frame.')
            require('FINISHED' in bpy.ops.wm.open_mainfile(filepath=str(path), use_scripts=True), 'Native reopen failed.')
            rig = bpy.data.objects[rig_name]
            source = bpy.data.objects[hair_name]
            require(not any(rig.pose.bones[name].wiggle_head or rig.pose.bones[name].wiggle_tail for name in names),
                    'Saved/reopened transient preview auto resumed native solve flags.')
            require(not runtime._LEASES and not facade.status()['active'], 'Reopen reconnected a Python lease.')
            require(hair.preview_state(source)['stopped_confirmed'], 'Reopened Hair stopped state was not confirmed.')
        REPORT['cases'].append({'case': 'native_savecopy_reopen_no_reconnect', 'passed': True})

    def test_08_undo_redo_owned_residue_master_off_recovery(self):
        source, dress, rig, data, profiles = fixture()
        if not bpy.ops.ed.undo_push.poll():
            self.skipTest('Background context cannot poll undo_push; no GUI fallback.')
        bpy.context.preferences.edit.use_global_undo = True
        require('FINISHED' in bpy.ops.ed.undo_push(message='Hair2 author baseline'), 'Could not push baseline undo state.')
        start_skirt(dress)
        start_hair(source, data, profiles)
        names = (source.name, dress.name, rig.name)
        require('FINISHED' in bpy.ops.ed.undo_push(message='Hair2 shared preview'), 'Could not push preview undo state.')
        if not bpy.ops.ed.undo.poll():
            self.skipTest('Background context cannot poll Undo after two states; no GUI fallback.')
        require('FINISHED' in bpy.ops.ed.undo(), 'Native Undo failed.')
        if not bpy.ops.ed.redo.poll():
            self.skipTest('Background context cannot poll Redo; no GUI fallback.')
        require('FINISHED' in bpy.ops.ed.redo(), 'Native Redo failed.')
        source, dress, rig = (bpy.data.objects[name] for name in names)
        require(not runtime._LEASES and not facade.status()['active'], 'History automatically reconnected a lease.')
        state = hair.preview_state(source)
        require(state['pending_recovery'] and not state['stopped_confirmed'], 'History residue was falsely reported stopped.')
        # Unknown flag edits must still refuse before recovery setters.
        first = rig.pose.bones[hair_names(registry.read(source))[0]]
        old_mute = first.wiggle_mute
        before_raw = runtime._raw(first, 'wiggle_mute')
        marker = json.loads(source[hair._RECOVERY_KEY])
        probe = {'bone': first.name, 'before_rna': old_mute,
                 'before_idproperty': first.get('wiggle_mute', '__unset__'), 'before_raw': before_raw,
                 'expected': marker['expected'][first.name].get('wiggle_mute'),
                 'native_field_count': len(marker['expected'][first.name]),
                 'rna_field_list': sorted(marker['expected'][first.name])}
        REPORT['history_flag_injection'] = probe
        with runtime._writes(BACKEND):
            first.wiggle_mute = not old_mute
        probe.update(after_rna=first.wiggle_mute, after_raw=runtime._raw(first, 'wiggle_mute'),
                     after_idproperty=first.get('wiggle_mute', '__unset__'))
        try:
            require(first.wiggle_mute != old_mute, 'The foreign-flag injection did not change native RNA.')
            require(probe['after_raw'] != before_raw, 'The foreign-flag injection did not change the guarded native raw field.')
            with self.assertRaisesRegex(ValueError, 'changed outside'):
                hair.prepare_recovery(bpy.context, source)
            probe['guard_refused_verified_native_change'] = True
        finally:
            with runtime._writes(BACKEND):
                runtime._restore(first, 'wiggle_mute', before_raw)
            probe['restored_raw'] = runtime._raw(first, 'wiggle_mute')
            require(probe['restored_raw'] == before_raw, 'Fixture did not restore its injected native flag exactly.')
        control.set_enabled(bpy.context, False)
        require(hair.preview_state(source)['stopped_confirmed'], 'Master Off left provably owned Hair history flags solving.')
        require(skirt.preview_state(dress)['stopped_confirmed'], 'Master Off left provably owned Dress history flags solving.')
        require(hair._RECOVERY_KEY not in source, 'Explicit master recovery left the Hair marker.')
        require(not runtime._LEASES, 'Master history recovery automatically reconnected a lease.')
        REPORT['cases'].append({'case': 'native_undo_redo_explicit_master_off_recovery', 'passed': True})


    def test_09_scoped_error_stop_identity_and_resume_playback_rollback(self):
        source, dress, rig, data, profiles = fixture()
        start_skirt(dress)
        start_hair(source, data, profiles)
        facade.set_preview_paused(bpy.context, source, True)
        session = hair._SESSION
        before = runtime._live(session['lease'])
        marker_before = source[hair._RECOVERY_KEY]
        peer_before = native_state(rig, dress_names(dress))
        frame = bpy.context.scene.frame_current
        other = source.copy()
        other.name = source.name + ' unrelated source'
        try:
            with self.assertRaisesRegex(ValueError, 'another source'):
                facade.stop_preview(bpy.context, source=other)
            require(hair._SESSION is session, 'Wrong-source Stop changed the live Hair owner.')
            require(runtime._live(session['lease']) == before, 'Wrong-source Stop wrote native state.')
            with patch.object(hair, '_start_playback', side_effect=RuntimeError('Injected GUI playback refusal')):
                with self.assertRaisesRegex(RuntimeError, 'Injected GUI playback refusal'):
                    facade.resume_preview(bpy.context, source)
            require(runtime._live(session['lease']) == before, 'Resume playback failure did not restore native prior state.')
            require(session['physics_paused'] and source[hair._RECOVERY_KEY] == marker_before,
                    'Resume failure did not restore its pause/marker.')
            require(native_state(rig, dress_names(dress)) == peer_before, 'Resume failure touched Dress state.')
            require(bpy.context.scene.frame_current == frame, 'Resume failure moved the timeline.')
            with self.assertRaisesRegex(ValueError, 'existing Hair preview'):
                facade.start_preview(bpy.context, other, data, profiles, [item['strand_id'] for item in data['strands']])
            info = facade.status()
            require(info['source_pointer'] == source.as_pointer(), 'Rejected Start replaced the live source identity.')
            require(info['reason_scope'] == other.as_pointer(), 'Rejected Start reason was scoped to the old Hair source.')
            require(runtime._live(session['lease']) == before, 'Rejected Start changed live native prior state.')
        finally:
            bpy.data.objects.remove(other, do_unlink=True)
        REPORT['cases'].append({'case': 'three_narrow_identity_playback_boundaries', 'passed': True})


    def test_10_exact_streaming_native_arrays_metadata_drivers_and_associations(self):
        source, dress, rig, data, profiles = fixture()
        head = rig.pose.bones['Head']
        head.rotation_mode = 'XYZ'
        for frame in range(1, 33):
            head.rotation_euler.x = .04 * frame
            head.keyframe_insert(data_path='rotation_euler', frame=frame, group='Proof artist group')
        action = rig.animation_data.action
        action.name = 'Hair2 exact streaming author action'
        action['proof_int32'] = (2 ** 31 - 2)
        action.asset_mark()
        action.asset_data.description = 'Preserve artist metadata exactly'
        from character_designer import limb_ik
        curves = tuple(limb_ik._fcurves_for_action(action))
        require(curves and max(len(curve.keyframe_points) for curve in curves) >= 32,
                'Native proof fixture has no large-enough keyframe collection.')
        for curve in curves:
            for point in curve.keyframe_points:
                point.handle_left_type = point.handle_right_type = 'FREE'
        other_action = action.copy()
        other_action.name = 'Hair2 exact streaming alternate action'
        track = rig.animation_data.nla_tracks.new()
        track.name = 'Proof native NLA association'
        strip = track.strips.new('Proof native strip', 1, action)
        source['proof_driver_value'] = .5
        driver_curve = source.driver_add('["proof_driver_value"]')
        driver = driver_curve.driver
        variable = driver.variables.new()
        variable.name, variable.type = 'proof_var', 'SINGLE_PROP'
        variable.targets[0].id = rig
        variable.targets[0].data_path = 'location.x'
        driver.expression = 'proof_var'
        bpy.context.scene.frame_set(8)
        mixed = next(curve.keyframe_points[len(curve.keyframe_points) // 2]
                     for curve in curves if len(curve.keyframe_points) >= 32)
        mixed.interpolation, mixed.easing, mixed.type = 'SINE', 'EASE_OUT', 'MOVING_HOLD'
        mixed.handle_left_type, mixed.handle_right_type = 'VECTOR', 'AUTO_CLAMPED'
        stats = {}
        proof = hair._animation_proof(diagnostics=stats)
        scalar_stats = {}
        scalar_proof = hair._animation_proof(force_scalar=True, diagnostics=scalar_stats)
        require(proof == scalar_proof, 'Native numeric/enum bulk proof differs from complete scalar proof.')
        require(stats['enum_bulk_fields'] > 0, 'Native Keyframe enum int32 bulk path was not exercised.')
        require(stats['enum_scalar_validation_points'] == 2 * stats['enum_bulk_fields'],
                'Native enum bulk path did not authenticate two scalar identifiers per field.')
        require(scalar_stats['bulk_fields'] == scalar_stats['enum_bulk_fields'] == 0,
                'The forced scalar verification still used a native bulk getter.')
        require(proof == hair._animation_proof(), 'Unchanged native full proof was not stable.')
        require(stats['bulk_fields'] > 0 and stats['max_buffer_bytes'] > 0,
                'The real Blender numeric foreach_get path was not exercised.')
        require(stats['max_buffer_bytes'] <= 8000000, 'Native streaming allocated an oversized array buffer.')
        row = {'case': 'exact_streaming_native_rna_tampers', 'passed': False,
               'diagnostics': stats, 'scalar_diagnostics': scalar_stats,
               'bulk_scalar_equal': proof == scalar_proof, 'tampers': []}
        REPORT['cases'].append(row)
        point = next(curve.keyframe_points[-1] for curve in curves if len(curve.keyframe_points) >= 32)
        point_saved = [(item, tuple(item.co), tuple(item.handle_left), tuple(item.handle_right),
                        item.handle_left_type, item.handle_right_type, item.interpolation, item.easing, item.type)
                       for curve in curves for item in curve.keyframe_points]
        def restore_points():
            for item, co, left, right, left_type, right_type, interpolation, easing, point_type in point_saved:
                item.handle_left_type, item.handle_right_type = left_type, right_type
                item.interpolation, item.easing, item.type = interpolation, easing, point_type
                item.co, item.handle_left, item.handle_right = co, left, right
        def tamper(label, change, restore):
            entry = {'field': label, 'refused': False, 'restored': False}
            row['tampers'].append(entry)
            try:
                change()
                changed = hair._animation_proof()
                entry['refused'] = changed != proof
                entry['bulk_scalar_equal'] = changed == hair._animation_proof(force_scalar=True)
                require(entry['bulk_scalar_equal'], 'Native bulk/scalar proof disagreed on changed field: ' + label)
                require(entry['refused'], 'Streaming proof omitted native change: ' + label)
            finally:
                restore()
                entry['restored'] = hair._animation_same(proof)
                require(entry['restored'], 'Native fixture could not restore its exact proof after: ' + label)
        co, left, right = tuple(point.co), tuple(point.handle_left), tuple(point.handle_right)
        tamper('key.co', lambda: setattr(point, 'co', (co[0], co[1] + .125)), restore_points)
        tamper('key.handle_left', lambda: setattr(point, 'handle_left', (left[0], left[1] + .125)), restore_points)
        tamper('key.handle_right', lambda: setattr(point, 'handle_right', (right[0], right[1] + .125)), restore_points)
        for field, value in [('interpolation', 'LINEAR'), ('easing', 'EASE_IN'),
                             ('handle_left_type', 'VECTOR'), ('handle_right_type', 'ALIGNED'), ('type', 'BREAKDOWN')]:
            tamper('key.' + field, lambda field=field, value=value: setattr(point, field, value), restore_points)
        tamper('Action.custom_int32_boundary', lambda: action.__setitem__('proof_int32', (2 ** 31 - 1)),
               lambda: action.__setitem__('proof_int32', (2 ** 31 - 2)))
        tamper('Action.asset_metadata', lambda: setattr(action.asset_data, 'description', 'Changed native metadata'),
               lambda: setattr(action.asset_data, 'description', 'Preserve artist metadata exactly'))
        tamper('Driver.expression', lambda: setattr(driver, 'expression', 'proof_var + .125'),
               lambda: setattr(driver, 'expression', 'proof_var'))
        tamper('Driver.target_path', lambda: setattr(variable.targets[0], 'data_path', 'location.y'),
               lambda: setattr(variable.targets[0], 'data_path', 'location.x'))
        original_slot = getattr(rig.animation_data, 'action_slot', None)
        def restore_action():
            rig.animation_data.action = action
            if original_slot is not None:
                rig.animation_data.action_slot = original_slot
        tamper('AnimData.action_association', lambda: setattr(rig.animation_data, 'action', other_action), restore_action)
        strip_slot = getattr(strip, 'action_slot', None)
        def restore_nla():
            strip.action = action
            if strip_slot is not None:
                strip.action_slot = strip_slot
        tamper('NLA.strip_action_association', lambda: setattr(strip, 'action', other_action), restore_nla)
        row['passed'] = True


    def test_11_interactive_associations_only_and_exact_no_action_write_evidence(self):
        source, dress, rig, data, profiles = fixture()
        head = rig.pose.bones['Head']
        head.rotation_mode = 'XYZ'
        for frame in range(1, 17):
            head.rotation_euler.x = .03 * frame
            head.keyframe_insert(data_path='rotation_euler', frame=frame, group='Preview author anchor')
        action = rig.animation_data.action
        action.name = 'Bound preview author Action'
        unrelated = action.copy()
        unrelated.name = 'Unrelated author Action preserved during preview'
        bpy.context.scene.frame_set(8)
        from character_designer import limb_ik
        unrelated_curve = next(curve for curve in limb_ik._fcurves_for_action(unrelated) if curve.keyframe_points)
        unrelated_point = unrelated_curve.keyframe_points[-1]
        full_before = hair._animation_proof()
        original_proof = hair._animation_proof
        with patch.object(BACKEND.WiggleBake, 'execute', side_effect=AssertionError('Preview entered the Bake operator')) as bake:
            with patch.object(hair, '_animation_proof', wraps=original_proof) as proof_spy:
                start_hair(source, data, profiles)
                require(not hair._SESSION['actions']['include_actions'], 'Interactive snapshot requested the entire Action library.')
                require(hair._SESSION['actions']['action_count'] == 0 and not hair._SESSION['actions']['action_refs'],
                        'Interactive snapshot retained global Action contents/inventory.')
                advance(4)
                facade.set_preview_paused(bpy.context, source, True)
                facade.set_preview_paused(bpy.context, source, False)
                facade.stop_preview(bpy.context, source=source)
                requested = [call.kwargs.get('include_actions', True) for call in proof_spy.call_args_list]
                require(requested and not any(requested), 'An interactive Start/Pause/Stop scanned all Action contents.')
            bake.assert_not_called()
        require(hair._animation_same(full_before), 'Full native key/handle/metadata/association proof changed during preview.')
        # A deliberate external edit to an unrelated Action is preserved. It is
        # outside runtime preview scope, and full acceptance still detects it.
        start_hair(source, data, profiles)
        co = tuple(unrelated_point.co)
        unrelated_point.co = (co[0], co[1] + .125)
        require(hair._animation_same(hair._SESSION['actions']), 'Runtime falsely claimed to inspect unrelated Action keys.')
        require(not hair._animation_same(full_before), 'Complete acceptance failed to detect an external key edit.')
        expected_external = hair._animation_proof()
        facade.stop_preview(bpy.context, source=source)
        require(hair._animation_same(expected_external), 'Stop overwrote the deliberate external Action edit.')
        require(tuple(unrelated_point.co) == (co[0], co[1] + .125), 'Unrelated external key contents were restored/overwritten.')
        # Binding changes remain protected, before any pause mutation. Stop
        # releases only our solver/pose and preserves that external association.
        start_hair(source, data, profiles)
        session = hair._SESSION
        native_before = runtime._live(session['lease'])
        marker_before = source[hair._RECOVERY_KEY]
        rig.animation_data.action = unrelated
        with self.assertRaisesRegex(ValueError, 'associations changed'):
            facade.set_preview_paused(bpy.context, source, True)
        require(runtime._live(session['lease']) == native_before and source[hair._RECOVERY_KEY] == marker_before,
                'Association refusal changed owned native flags or its recovery marker.')
        with self.assertRaisesRegex(ValueError, 'associations changed'):
            facade.stop_preview(bpy.context, source=source)
        require(rig.animation_data.action == unrelated, 'Owned Stop overwrote the external Action binding.')
        require(not hair._SESSION and not runtime.has_component('HAIR'), 'Association Stop failed to release only its Hair lease.')
        # The hash-pinned external callbacks have no Action-writing API/target;
        # such calls live in the separately invoked Bake operator instead.
        backend_tree = ast.parse(Path(BACKEND.__file__).read_text(encoding='utf8'))
        functions = {node.name: node for node in backend_tree.body if isinstance(node, ast.FunctionDef)}
        operator_methods = {}
        for cls in backend_tree.body:
            if isinstance(cls, ast.ClassDef):
                identifier = next((value.value.value for value in cls.body if isinstance(value, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'bl_idname' for target in value.targets)
                    and isinstance(value.value, ast.Constant)), None)
                execute = next((node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'execute'), None)
                if identifier and execute:
                    operator_methods['bpy.ops.' + identifier] = execute
        pending, seen, forbidden = ['wiggle_pre', 'wiggle_post', 'reset_bone'], set(), []
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            node = functions.get(name) or operator_methods.get(name)
            require(node is not None, 'Unknown backend audit node: ' + name)
            for item in ast.walk(node):
                if isinstance(item, ast.Call):
                    called = ast.unparse(item.func)
                    if isinstance(item.func, ast.Name) and item.func.id in functions:
                        pending.append(item.func.id)
                    if called in operator_methods:
                        pending.append(called)
                    if any(word in called for word in ('keyframe_insert', 'keyframe_delete', '.bake')):
                        forbidden.append((name, called))
                elif isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                    for target in targets:
                        text = ast.unparse(target)
                        if any(word in text for word in ('.animation_data', '.keyframe_points', '.fcurves', '.actions')):
                            forbidden.append((name, text))
        require(not forbidden, 'Pinned preview callback callgraph contains Action writes: ' + str(forbidden))
        REPORT['cases'].append({'case': 'interactive_associations_only_no_action_writes', 'passed': True,
            'runtime_include_actions_requests': requested, 'full_native_action_proof_unchanged': True,
            'unrelated_external_key_edit_preserved': True, 'association_change_refused': True,
            'bake_operator_not_called': True, 'backend_callback_reachable_nodes': sorted(seen),
            'backend_forbidden_writer_sites': forbidden})


def main():
    global BACKEND
    output = args.report_json.resolve()
    require(not output.exists(), 'Native report already exists; preserve historical evidence.')
    output.parent.mkdir(parents=True, exist_ok=True)
    enabled_here = False
    result = None
    try:
        require(bpy.app.background, 'This harness only runs in an isolated background process.')
        manifest_path = stage / 'projection.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf8'))
        require(Path(manifest['source_root']).resolve() == package_root, 'Wrong projection root.')
        for name, expected in manifest['files'].items():
            require(pin(package_root / name)['sha256'] == expected, 'Staged source pin differs: ' + name)
        REPORT.update(script=pin(__file__), projection=pin(manifest_path), staged_files=len(manifest['files']),
                      blender=bpy.app.version_string, source_module=cd.__file__, background=bpy.app.background)
        path = args.wiggle_file.resolve(strict=True)
        require(hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest() == skirt.BACKEND_SHA256,
                'The external native Wiggle source is unverified.')
        sys.path.insert(0, str(path.parent))
        require('wiggle_2' not in sys.modules, 'Use a fresh isolated backend process.')
        BACKEND = addon_utils.enable('wiggle_2', default_set=False, persistent=False)
        enabled_here = True
        require(BACKEND is not None and Path(BACKEND.__file__).resolve() == path, 'Official Wiggle enable/path failed.')
        require(skirt._backend() is BACKEND, 'Original backend proof refused the native module.')
        REPORT['backend'] = pin(path)
        REPORT['modules'] = staged_modules()
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(HairWiggle2NativeTests))
        REPORT.update(tests_run=result.testsRun, success_count=result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
                      failures=[{'test': str(test), 'traceback': text} for test, text in result.failures],
                      errors=[{'test': str(test), 'traceback': text} for test, text in result.errors],
                      skipped=[{'test': str(test), 'reason': reason} for test, reason in result.skipped],
                      validation_complete=not result.skipped, passed=result.wasSuccessful() and not result.skipped)
    except Exception:
        REPORT['errors'].append({'stage': 'setup_or_cleanup', 'traceback': traceback.format_exc()})
    finally:
        try:
            cleanup()
            if enabled_here:
                addon_utils.disable('wiggle_2', default_set=False)
            REPORT['normal_unregister'] = True
        except Exception:
            REPORT['passed'] = False
            REPORT['cleanup_traceback'] = traceback.format_exc()
        REPORT['finished_utc'] = datetime.now(timezone.utc).isoformat()
        with output.open('x', encoding='utf8') as stream:
            json.dump(REPORT, stream, ensure_ascii=False, indent=2)
        print(json.dumps({'report': str(output), 'passed': REPORT['passed'], 'tests_run': REPORT.get('tests_run')}))
    if not REPORT['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
