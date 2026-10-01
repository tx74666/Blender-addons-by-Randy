"""Four-sample exact-motion integration check on a disposable current-X session.

blender --background --factory-startup --python this.py -- --real-blend X.blend
    --report controls-report.json

Opens, never saves, the input file. The synthetic packet comes from this file's
native bind skeleton; it proves control routing/recovery, not Unity export or
retarget compatibility. Other armatures are neither accepted nor manipulated.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import traceback

import bpy
from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import unity_animation as ua
from character_designer import unity_animation_controls as controls
from character_designer import unity_export_worker


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def flat(matrix):
    return [float(value) for row in matrix for value in row]


def difference(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4))


def rig_content_hash(rig, meshes):
    """Stream original rest/weights/mesh/Shape Key content, not evaluated skin."""
    digest = hashlib.sha256()
    def add(value):
        digest.update(repr(value).encode('utf-8'))
        digest.update(b'\n')
    add(ua._rest_state(rig.data))
    for obj in sorted(meshes, key=lambda item: item.name):
        add((obj.name, obj.data.name, [(group.index, group.name) for group in obj.vertex_groups]))
        for vertex in obj.data.vertices:
            add((tuple(vertex.co), [(group.group, group.weight) for group in vertex.groups]))
        for polygon in obj.data.polygons:
            add(tuple(polygon.vertices))
        if obj.data.shape_keys:
            for key in obj.data.shape_keys.key_blocks:
                add((key.name, key.value, key.mute, key.slider_min, key.slider_max,
                     key.relative_key.name if key.relative_key else None, key.vertex_group))
                for point in key.data:
                    add(tuple(point.co))
    return digest.hexdigest()


def graph_state(rig):
    def fields(owner):
        result = []
        for prop in owner.bl_rna.properties:
            if prop.identifier == 'rna_type' or prop.is_readonly or prop.type == 'COLLECTION':
                continue
            value = getattr(owner, prop.identifier)
            if isinstance(value, bpy.types.ID):
                value = value.name_full
            elif getattr(prop, 'is_array', False):
                value = tuple(value)
            result.append((prop.identifier, repr(value)))
        return result
    payload = [(pb.name, [(con.name, con.type, fields(con)) for con in pb.constraints]) for pb in rig.pose.bones]
    for curve in rig.animation_data.drivers if rig.animation_data else ():
        payload.append((curve.data_path, curve.array_index, curve.mute, curve.driver.type, curve.driver.expression,
                        [(var.name, var.type, [fields(binding) for binding in var.targets])
                         for var in curve.driver.variables], [fields(modifier) for modifier in curve.modifiers],
                        [(tuple(point.co), point.interpolation) for point in curve.keyframe_points]))
    return payload


def graph_hash(rig):
    return hashlib.sha256(repr(graph_state(rig)).encode()).hexdigest()


def graph_changes(expected, actual):
    """Report exact property paths without weakening the graph invariant."""
    changes = []
    def walk(before, after, path):
        if isinstance(before, (list, tuple)) and isinstance(after, (list, tuple)):
            if len(before) != len(after):
                changes.append({'path': path + '.length', 'before': len(before), 'after': len(after)})
            for index, (left, right) in enumerate(zip(before, after)):
                walk(left, right, path + '[' + str(index) + ']')
        elif before != after:
            changes.append({'path': path, 'before': before, 'after': after})
    walk(expected, actual, 'graph')
    return changes


def assert_graph(rig, expected, report, phase):
    actual = graph_state(rig)
    changes = graph_changes(expected, actual)
    if changes:
        report['graph_failure_phase'] = phase
        report['graph_differences'] = changes
        report['graph_expected'] = expected
        report['graph_actual'] = actual
        raise AssertionError('Rig graph changed during ' + phase + ': ' + repr(changes[:8]))


def data_blocks():
    return {name: {value.as_pointer() for value in getattr(bpy.data, name)}
            for name in ('objects', 'armatures', 'actions')}


def nla_state(rig):
    ad = rig.animation_data
    return [(track.name, track.mute, track.is_solo,
             [(strip.name, strip.action.as_pointer() if strip.action else None,
               strip.frame_start, strip.frame_end, strip.action_frame_start, strip.action_frame_end,
               strip.mute, strip.blend_type, strip.influence, strip.repeat, strip.scale)
              for strip in track.strips]) for track in ad.nla_tracks] if ad else []


def assert_snapshot(rig, plan, before):
    after = ua._snapshot(bpy.context, rig, plan)
    for key in before:
        if key != 'pose':
            assert before[key] == after[key], (key, before[key], after[key])
            continue
        for name, values in before['pose'].items():
            for prop, value in values.items():
                actual = after['pose'][name][prop]
                if isinstance(value, str):
                    assert value == actual, (name, prop)
                else:
                    assert max(abs(a - b) for a, b in zip(value, actual)) < 2.0e-6, (name, prop, value, actual)


def packet(rig, names):
    """Known matrices independently constructed without the preview baker."""
    unit = bpy.context.scene.unit_settings.scale_length
    conversion = (Matrix.Translation((0.27, -0.18, 0.09))
                  @ Quaternion((0, 0, 1), 0.23).to_matrix().to_4x4()
                  @ Matrix.Diagonal((-1.0 / unit, 1.0 / unit, 1.0 / unit, 1.0)))
    inverse = conversion.inverted()
    ordered = sorted(names, key=lambda name: (len(rig.data.bones[name].parent_recursive), name))
    indices = {name: index for index, name in enumerate(ordered)}
    parents = {}
    for name in ordered:
        parent = rig.data.bones[name].parent
        while parent and parent.name not in names:
            parent = parent.parent
        parents[name] = parent.name if parent else None
    result = {'schema': ua.SCHEMA, 'targetName': rig.name, 'clipName': 'Owned Controls Exact Fixture',
              'units': 'metres', 'coordinate': 'unity-lh-y-up', 'matrixLayout': 'row-major',
              'sampleRate': 4, 'duration': 0.75, 'loopTime': False, 'bones': [], 'frames': []}
    rests = {name: rig.matrix_world @ rig.data.bones[name].matrix_local for name in ordered}
    # A distinct right-side rest rotation exercises deformation-matrix transfer;
    # incoming FBX bone-axis frames need not match Blender's native bone axes.
    offsets = {name: Quaternion((0, 1, 0), 0.11 + index * 0.001).to_matrix().to_4x4()
               for index, name in enumerate(ordered)}
    for name in ordered:
        result['bones'].append({'name': name, 'path': name,
                                'parent': indices[parents[name]] if parents[name] else -1,
                                'restSource': 'bindpose',
                                'rest': flat(inverse @ rests[name] @ offsets[name])})
    expected = []
    for sample in range(4):
        posed = {}
        for name in ordered:
            bone = rig.data.bones[name]
            basis = Matrix.Identity(4)
            if sample >= 2:
                angles = {'spine': (0.08, (1, 0, 0)), 'Chest': (-0.06, (0, 0, 1)),
                          'forearm.L': (0.32, (1, 0, 0)), 'forearm.R': (-0.24, (1, 0, 0)),
                          'shin.L': (0.27, (1, 0, 0)), 'shin.R': (-0.18, (1, 0, 0)),
                          'foot.L': (-0.11, (1, 0, 0)), 'toe.L': (0.12, (1, 0, 0)),
                          'eye.L': (0.13, (0, 0, 1)), 'eye.R': (-0.1, (1, 0, 0))}
                if name in angles:
                    angle, axis = angles[name]
                    basis = Quaternion(axis, angle * (sample - 1)).to_matrix().to_4x4()
                if sample == 3 and name == 'forearm.L':
                    basis.translation = Vector((0.001, 0.002, -0.001))
            parent_name = parents[name]
            if bone.parent:
                parent_pose = (posed[parent_name] @ rig.data.bones[parent_name].matrix_local.inverted()
                               @ bone.parent.matrix_local) if parent_name else bone.parent.matrix_local
                kwargs = {'parent_matrix': parent_pose, 'parent_matrix_local': bone.parent.matrix_local}
            else:
                kwargs = {}
            posed[name] = bone.convert_local_to_pose(basis, bone.matrix_local, **kwargs)
        if sample:
            rigid = Matrix.Translation((0.018 * sample, -0.012 * sample, 0.005 * sample)) @ Quaternion(
                (0, 0, 1), 0.07 * sample).to_matrix().to_4x4()
            posed = {name: rigid @ matrix for name, matrix in posed.items()}
        worlds = {name: rig.matrix_world @ matrix for name, matrix in posed.items()}
        expected.append(worlds)
        result['frames'].append({'time': sample * 0.25, 'root': flat(Matrix.Identity(4)),
                                 'poses': [{'matrix': flat(inverse @ worlds[name] @ offsets[name])}
                                           for name in ordered]})
    return result, expected


def seed_previous_animation(rig, plan):
    """Exercise restoration with real Action slot, unmuted NLA and nonzero controls."""
    ad = rig.animation_data_create()
    action = bpy.data.actions.new('Controls recovery fixture previous Action')
    slot, bag = ua._new_channelbag(action, rig)
    bend = next(name for name in plan.neutral if name.startswith('CTRL_torso'))
    ua._write_curve(bag, rig.pose.bones[bend].path_from_id('location'), 0, [1.0, 40.0], [0.001, 0.001])
    ad.action, ad.action_slot = action, slot
    nla_action = action.copy()
    nla_action.name = 'Controls recovery fixture NLA'
    track = ad.nla_tracks.new()
    strip = track.strips.new('Recovery fixture strip', 1, nla_action)
    strip.blend_type, strip.influence = 'ADD', 0.2
    track.mute = False
    ad.use_nla = True
    ad.action_blend_type, ad.action_influence, ad.action_extrapolation = 'ADD', 0.65, 'HOLD'
    for (name, prop), value in plan.switches.items():
        rig.pose.bones[name][prop] = 1.08 if prop == 'uniform_scale' else 0.35
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = 24, 1.001
    scene.frame_start, scene.frame_end = 5, 90
    scene.frame_preview_start, scene.frame_preview_end = 9, 75
    scene.use_preview_range = True
    scene.tool_settings.use_keyframe_insert_auto = True
    scene.frame_set(13, subframe=0.25)
    rig.update_tag(refresh={'OBJECT'})
    bpy.context.view_layer.update()
    return action, slot.handle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--real-blend', required=True)
    parser.add_argument('--report')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    source_path = Path(args.real_blend).resolve()
    before_file = file_hash(source_path)
    report = {'source': str(source_path), 'source_sha256': before_file, 'passed': False,
              'scope': 'Four synthetic exact same-character samples through current Cosha controls; not a Unity roundtrip or rendered skin quality claim.'}
    try:
        bpy.ops.wm.open_mainfile(filepath=str(source_path), use_scripts=False, load_ui=False)
        rig = bpy.data.objects.get('CoshaRig')
        assert rig is not None and rig.type == 'ARMATURE', 'Expected the current CoshaRig.'
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        rig.hide_set(False)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
        names = {bone.name for bone in rig.data.bones if not unity_export_worker._is_control(bone)}
        plan = controls.build_plan(rig, names)
        assert plan is not None and len(plan.eyes) == 2 and len(plan.redirects) >= 5, 'Expected current complete Cosha controls.'
        assert not names & plan.control_names
        meshes = [obj for obj in bpy.data.objects if obj.type == 'MESH'
                  and any(modifier.type == 'ARMATURE' and modifier.object == rig for modifier in obj.modifiers)]
        original_data = rig.data
        previous, slot = seed_previous_animation(rig, plan)
        before = ua._snapshot(bpy.context, rig, plan)
        before_content = rig_content_hash(rig, meshes)
        original_nla = nla_state(rig)
        before_graph = graph_state(rig)
        blocks = data_blocks()
        data, expected = packet(rig, names)
        with tempfile.TemporaryDirectory(prefix='cd_owned_controls_') as directory:
            path = Path(directory) / 'same-character.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            result = ua.import_test_action(bpy.context, rig, path, start_frame=7)
            fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
            assert result.sample_count == 4
            assert not rig.animation_data.use_nla and nla_state(rig) == original_nla
            maximum = 0.0
            for index in (0, 1, 2, 3, 2, 0):
                ua._set_frame(bpy.context.scene, 7 + data['frames'][index]['time'] * fps)
                bpy.context.view_layer.update()
                evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
                for name, desired in expected[index].items():
                    error = difference(rig.matrix_world @ evaluated.pose.bones[name].matrix, desired)
                    maximum = max(maximum, error)
                    assert error < 3.0e-4, (name, index, error)

            # Version 2 must reject a truncated recovery record before touching
            # any switch; otherwise Restore could leave the original rig in FK.
            assert result.action[ua.VERSION_KEY] == 2
            saved_session = result.action[ua.SESSION_KEY]
            live_snapshot = ua._snapshot(bpy.context, rig, plan)
            live_data = rig.data
            live_blocks = data_blocks()
            for fault in ('whole_control_field', 'one_control_switch'):
                corrupted = json.loads(saved_session)
                if fault == 'whole_control_field':
                    del corrupted['control_properties']
                else:
                    name = next(iter(corrupted['control_properties']))
                    prop = next(iter(corrupted['control_properties'][name]))
                    del corrupted['control_properties'][name][prop]
                result.action[ua.SESSION_KEY] = json.dumps(corrupted)
                try:
                    try:
                        ua.restore_preview(bpy.context, rig)
                        raise AssertionError('Incomplete control recovery record was accepted: ' + fault)
                    except ua.UnityAnimationError:
                        pass
                    assert ua.active_preview(rig) is result.action
                    assert rig.animation_data.action is result.action and rig.data is live_data
                    assert data_blocks() == live_blocks
                    assert_snapshot(rig, plan, live_snapshot)
                finally:
                    result.action[ua.SESSION_KEY] = saved_session
            ua.restore_preview(bpy.context, rig)
            assert rig.data is original_data and ua.active_preview(rig) is None
            assert rig.animation_data.action is previous and rig.animation_data.action_slot.handle == slot
            assert nla_state(rig) == original_nla
            assert_snapshot(rig, plan, before)
            assert_graph(rig, before_graph, report, 'normal_restore')
            assert rig_content_hash(rig, meshes) == before_content
            after_blocks = data_blocks()
            assert after_blocks['objects'] == blocks['objects'] and after_blocks['armatures'] == blocks['armatures']
            assert after_blocks['actions'] == blocks['actions'] | {result.action.as_pointer()}

            # A foreign native-bone relation must fail before any Action or copy exists.
            constraint_selection = {value.name: value.active for value in rig.pose.bones['hand.L'].constraints}
            constraint = rig.pose.bones['hand.L'].constraints.new('COPY_ROTATION')
            constraint.name = 'Unrecognized test relation'
            constraint.target, constraint.subtarget = rig, 'hand.R'
            failed_blocks = data_blocks()
            foreign_graph = graph_state(rig)
            try:
                try:
                    ua.import_test_action(bpy.context, rig, path)
                    raise AssertionError('The foreign constraint was accepted.')
                except ua.UnityAnimationError:
                    pass
                assert constraint in rig.pose.bones['hand.L'].constraints.values()
                assert data_blocks() == failed_blocks and ua.active_preview(rig) is None
                assert_snapshot(rig, plan, before)
                assert_graph(rig, foreign_graph, report, 'unknown_constraint_rejection')
            finally:
                rig.pose.bones['hand.L'].constraints.remove(constraint)
                # Adding a constraint makes it the UI-active entry and removing
                # it does not restore the previous entry. Undo our own fixture
                # effect; keep active-state equality in the graph assertion.
                for value in rig.pose.bones['hand.L'].constraints:
                    value.active = constraint_selection[value.name]

            before_mismatch_graph = graph_state(rig)
            report['foreign_fixture_removal_graph_differences'] = graph_changes(before_graph, before_mismatch_graph)

            # Mismatching one bind position must never invoke a retarget fallback.
            mismatch = json.loads(json.dumps(data))
            next(bone for bone in mismatch['bones'] if bone['name'] == 'hand.L')['rest'][3] += 0.04
            mismatch_path = Path(directory) / 'mismatched-rest.json'
            mismatch_path.write_text(json.dumps(mismatch), encoding='utf-8')
            failed_blocks = data_blocks()
            try:
                ua.import_test_action(bpy.context, rig, mismatch_path)
                raise AssertionError('A changed bind skeleton was accepted.')
            except ua.UnityAnimationError:
                pass
            assert data_blocks() == failed_blocks and rig.data is original_data
            assert_snapshot(rig, plan, before)
            assert_graph(rig, before_mismatch_graph, report, 'bind_mismatch_rejection')
            assert_graph(rig, before_graph, report, 'post_fixture_baseline')
            assert rig_content_hash(rig, meshes) == before_content
            report.update(passed=True, samples=4, mapped_native_bones=len(names), controls=len(plan.control_names),
                          approved_drivers=len(plan.driver_keys), eye_controls=len(plan.eyes),
                          maximum_world_matrix_error=maximum, restore_action_slot_nla_properties=True,
                          original_data_geometry_weights_shape_keys=True, temporary_objects_cleaned=True,
                          truncated_control_restore_rejected_atomically=True,
                          unknown_constraint_rejected_atomically=True, mismatched_bind_rejected_atomically=True)
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        report['input_file_unchanged'] = file_hash(source_path) == before_file
        report['passed'] = report['passed'] and report['input_file_unchanged']
        if args.report:
            report_path = Path(args.report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        summary = {key: value for key, value in report.items() if key not in {'graph_expected', 'graph_actual'}}
        print('UNITY_ANIMATION_CONTROLS_RESULT ' + json.dumps(summary))
    if not report['passed']:
        raise AssertionError(report.get('error', 'Input file changed.'))


if __name__ == '__main__':
    main()
