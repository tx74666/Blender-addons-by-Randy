"""Default Bend/section FK, safe optional-branch removal and legacy UI."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_calibration, body_setup, body_setup_removal,
    limb_ik, spine_ik_fk as spine, torso_controls as torso, torso_ui)
import test_body_calibration_blender as calibration
import test_body_setup_plan_blender as planning


class Layout:
    def __init__(self, events=None, enabled=True):
        self.events = events if events is not None else []
        self.enabled = enabled

    def row(self, **_kwargs):
        return Layout(self.events, self.enabled)

    column = box = row

    def label(self, **kwargs):
        self.events.append(('label', kwargs.get('text', '')))

    def operator(self, identifier, **kwargs):
        result = SimpleNamespace()
        self.events.append(('operator', identifier, kwargs.get('text', ''), self.enabled, result))
        return result


def draw(rig, advanced=False):
    limb_ik._settings(bpy.context).show_body_setup_advanced = advanced
    layout = Layout()
    torso_ui.CHARACTERDESIGNER_PT_torso_controls.draw(SimpleNamespace(layout=layout), bpy.context)
    return layout.events


def authored_mesh(obj):
    keys = obj.data.shape_keys
    return {
        'data': obj.data.as_pointer(), 'vertices': tuple(tuple(v.co) for v in obj.data.vertices),
        'edges': tuple(tuple(e.vertices) for e in obj.data.edges),
        'faces': tuple(tuple(p.vertices) for p in obj.data.polygons),
        'uv': tuple((layer.name, tuple(tuple(p.uv) for p in layer.data)) for layer in obj.data.uv_layers),
        'groups': tuple((g.name, g.index, g.lock_weight) for g in obj.vertex_groups),
        'weights': tuple(tuple((g.group, g.weight) for g in v.groups) for v in obj.data.vertices),
        'keys': tuple((key.name, key.as_pointer(), key.value, key.mute,
                       tuple(tuple(p.co) for p in key.data)) for key in keys.key_blocks),
        'modifiers': tuple((m.name, m.type, getattr(m, 'object', None)) for m in obj.modifiers),
        'parent': (obj.parent, obj.parent_type, obj.parent_bone),
    }


def fixture():
    rig, body = planning.fixture()
    rig.pose.bones['Hips'].matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    calibration.palms(rig)
    calibration.prepare(rig)
    chain = ('spine', 'Chest', 'UpperChest')
    coordinates, faces = [], []
    for name in chain:
        bone = rig.data.bones[name]
        center = (bone.head_local + bone.tail_local) * .5
        start = len(coordinates)
        coordinates.extend(center + Vector(offset) for offset in
                           ((-.025, -.04, 0), (.025, -.04, 0), (0, -.04, .025)))
        faces.append((start, start + 1, start + 2))
    mesh = bpy.data.meshes.new('Spine preservation mesh')
    mesh.from_pydata(coordinates, [], faces)
    obj = bpy.data.objects.new('Spine preservation mesh', mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.matrix_world = rig.matrix_world
    obj.modifiers.new('Artist armature', 'ARMATURE').object = rig
    for i, name in enumerate(chain):
        obj.vertex_groups.new(name=name).add([i * 3 + j for j in range(3)], 1., 'REPLACE')
    uv = mesh.uv_layers.new(name='Artist UV')
    for i, point in enumerate(uv.data):
        point.uv = (i / len(uv.data), (i % 3) / 3)
    obj.shape_key_add(name='Basis')
    smile = obj.shape_key_add(name='Smile_L')
    smile.data[0].co.x += .011
    smile.value = .35
    blink = obj.shape_key_add(name='Blink_R')
    blink.data[7].co.z -= .006
    blink.value = .2
    torso._update(bpy.context, rig)
    return rig, body, obj


def matrices(rig, names):
    torso._update(bpy.context, rig)
    return {name: rig.pose.bones[name].matrix.copy() for name in names}


def matrix_error(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4))


class DefaultSpineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def test_generate_and_update_use_only_bend_and_section_fk(self):
        rig, body, mesh = fixture()
        rest = body_calibration.native_rest(rig)
        authored = authored_mesh(mesh)
        surfaces = body_setup_removal._bound_surfaces(bpy.context, rig)
        result = body_setup.generate(bpy.context, rig)
        self.assertIn('TORSO', result['created'])
        self.assertNotIn('SPINE', result['created'])
        self.assertIsNone(spine.get_record(rig))
        self.assertFalse(any(bone.get(limb_ik.OWNER_KEY) == spine.OWNER_VALUE for bone in rig.data.bones))
        record = torso.validate(rig)
        body_calibration.verify_rest(rig, rest)
        self.assertEqual(authored_mesh(mesh), authored)
        self.assertLess(body_setup_removal._check_surfaces(bpy.context, rig, surfaces), 1e-4)
        controls = draw(rig)
        operators = [event for event in controls if event[0] == 'operator']
        self.assertEqual([event[2] for event in operators], ['Bend Spine', *record['sources']])
        self.assertTrue(all(event[1] == 'character_designer.torso_controls' and event[3] for event in operators))
        # Prove the simple graph actually deforms the native spine and skin.
        before = matrices(rig, record['sources'])
        rig.pose.bones[record['bend']].rotation_euler = (.18, .04, -.03)
        shared = matrices(rig, record['sources'])
        self.assertTrue(all(matrix_error(before[name], shared[name]) > .005 for name in before))
        rig.pose.bones[record['controls'][record['sources'][1]]].rotation_euler.y += .12
        refined = matrices(rig, record['sources'])
        self.assertLess(matrix_error(shared[record['sources'][0]], refined[record['sources'][0]]), 2e-6)
        self.assertGreater(matrix_error(shared[record['sources'][1]], refined[record['sources'][1]]), .01)
        with self.assertRaises(ValueError):
            body_setup_removal._check_surfaces(bpy.context, rig, surfaces)
        posed = body_setup_removal._bound_surfaces(bpy.context, rig)
        names = set(rig.data.bones.keys())
        updated = body_setup.generate(bpy.context, rig)
        self.assertFalse(updated['created'])
        self.assertEqual(set(rig.data.bones.keys()), names)
        self.assertIsNone(spine.get_record(rig))
        self.assertLess(body_setup_removal._check_surfaces(bpy.context, rig, posed), 1e-4)
        self.assertEqual(authored_mesh(mesh), authored)
        body_calibration.verify_rest(rig, rest)

    def test_explicit_optional_removal_keeps_pose_data_and_is_not_readded(self):
        rig, body, mesh = fixture()
        body_setup.generate(bpy.context, rig)
        record = spine.build(bpy.context, rig)
        spine.switch(bpy.context, rig, 'IK')
        rig.pose.bones[record['chest']].location.y -= .02
        rig.pose.bones[record['shape']].rotation_euler.x += .08
        desired = matrices(rig, body_calibration.native_rest(rig))
        rest = body_calibration.native_rest(rig)
        authored = authored_mesh(mesh)
        surfaces = body_setup_removal._bound_surfaces(bpy.context, rig)
        raw = rig.data[spine.RECORD_KEY]
        # Update must not delete or replace an artist's optional IK branch.
        update = body_setup.generate(bpy.context, rig)
        self.assertIn('SPINE', update['reused'])
        self.assertEqual(rig.data[spine.RECORD_KEY], raw)
        spine._verify_pose(rig, desired)
        result = spine.remove(bpy.context, rig)
        self.assertTrue(result['pose_preserved'])
        self.assertIsNone(spine.get_record(rig))
        spine._verify_pose(rig, desired)
        body_calibration.verify_rest(rig, rest)
        self.assertEqual(authored_mesh(mesh), authored)
        self.assertLess(body_setup_removal._check_surfaces(bpy.context, rig, surfaces), 1e-4)
        self.assertIsNotNone(torso.validate(rig))
        update = body_setup.generate(bpy.context, rig)
        self.assertNotIn('SPINE', update['created'])
        self.assertIsNone(spine.get_record(rig))
        spine._verify_pose(rig, desired)
        self.assertLess(body_setup_removal._check_surfaces(bpy.context, rig, surfaces), 1e-4)

    def test_legacy_ik_ui_is_read_only_and_animated_branch_is_not_deleted(self):
        rig, body, mesh = fixture()
        body_setup.generate(bpy.context, rig)
        record = spine.build(bpy.context, rig)
        spine.switch(bpy.context, rig, 'IK')
        desired = matrices(rig, body_calibration.native_rest(rig))
        raw = rig.data[spine.RECORD_KEY]
        before = planning.states(rig, body)
        daily = draw(rig)
        daily_buttons = [event for event in daily if event[0] == 'operator']
        self.assertTrue(all(event[1] == 'character_designer.torso_controls' and not event[3] for event in daily_buttons))
        advanced = draw(rig, True)
        self.assertTrue(any(event[0] == 'operator' and event[1] == 'character_designer.spine_ik_fk'
                            and event[2] == 'FK' and event[3] for event in advanced))
        self.assertEqual(planning.states(rig, body), before)
        self.assertEqual(rig.data[spine.RECORD_KEY], raw)
        limb_ik._settings(bpy.context).show_body_setup_advanced = False
        rig.pose.bones[record['chest']].keyframe_insert('location', frame=1)
        protected = planning.states(rig, body)
        action = rig.animation_data.action.as_pointer()
        with self.assertRaisesRegex(limb_ik.LimbIKError, 'animation'):
            spine.remove(bpy.context, rig)
        self.assertEqual(planning.states(rig, body), protected)
        self.assertEqual(rig.animation_data.action.as_pointer(), action)
        self.assertEqual(rig.data[spine.RECORD_KEY], raw)
        self.assertEqual(spine.validate(rig), json.loads(raw))
        spine._verify_pose(rig, desired)


if __name__ == '__main__':
    tests = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    suite = (unittest.defaultTestLoader.loadTestsFromNames(tests, sys.modules[__name__]) if tests else
             unittest.defaultTestLoader.loadTestsFromTestCase(DefaultSpineTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('BODY_SPINE_DEFAULT_PASSED', result.testsRun, flush=True)
