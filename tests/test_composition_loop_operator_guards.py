"""Pure tests of the actual tool helpers; Blender/add-on code is never imported."""
import ast
import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'tools' / 'composition_timing_acceptance.py'
FUNCTIONS = {'require', '_loop_visibility', '_loop_rotation_state', '_loop_rotation_delta',
             '_loop_select', 'author_hold_loop'}


def actual_helpers():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS]
    if {node.name for node in nodes} != FUNCTIONS:
        raise AssertionError('The exact actual diagnostic helpers are required.')
    namespace = {'math': math}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace


class Bone:
    def __init__(self, name, collections=()):
        self.name, self.collections, self.hide_select = name, list(collections), False


class Pose:
    def __init__(self, native):
        self.native = native
        self.name, self.bone = native['name'], native['bone']
        self.lock_rotation = (False, False, False)

    def __eq__(self, other):
        return isinstance(other, Pose) and self.native is other.native

    @property
    def select(self):
        return self.native['select']

    @select.setter
    def select(self, value):
        self.native['select'] = value
        self.native['events'].append(('select', self.name, value))


class Poses:
    def __init__(self, names):
        self.native = {name: {'name': name, 'bone': Bone(name), 'select': False, 'events': []}
                       for name in names}

    def __iter__(self):
        return iter(Pose(native) for native in self.native.values())

    def __getitem__(self, name):
        return Pose(self.native[name])


class Track:
    def __init__(self, native):
        self.native, self.name, self.select = native, 'EDIT', True
        self.strips = native['strips']

    def __eq__(self, other):
        return isinstance(other, Track) and self.native is other.native


class DataBones:
    def __init__(self, poses):
        self.poses, self.active = poses, None

    def __getitem__(self, name):
        return self.poses.native[name]['bone']


class Action:
    def __init__(self, name):
        self.name = name


class Context:
    def __init__(self, rig, scene):
        self.scene, self.window = scene, NS(scene=scene)
        self.view_layer = NS(name='Layer', objects=NS(active=rig))
        self.pose_object, self.mode = rig, 'POSE'
        self.area, self.region, self.space_data = NS(type='VIEW_3D'), NS(type='WINDOW'), NS(type='VIEW_3D')
        self.rig = rig

    @property
    def active_pose_bone(self):
        active = self.rig.data.bones.active
        return self.rig.pose.bones[active.name] if active else None

    @property
    def selected_pose_bones(self):
        return getattr(self, 'selection_override', [pb for pb in self.rig.pose.bones if pb.select])


def selection_fixture():
    poses = Poses(('FK', 'OTHER'))
    rig = NS(name='QA Rig', pose=NS(bones=poses), mode='POSE', select_get=lambda: True)
    rig.data = NS(collections=NS(is_solo_active=False), bones=DataBones(poses))
    action = Action('Owned B')
    native = {'strips': [NS(action=action, action_slot_handle=7, select=True)]}
    track = Track(native)
    rig.animation_data = NS(use_tweak_mode=True, action=action, action_slot_handle=7,
                            nla_tracks=NS(active=Track(native)))
    scene = object()
    context = Context(rig, scene)
    session = {'scene': scene, 'view_layer': 'Layer', 'edit_target': 'B', 'solo': False,
               'edit_action': action, 'edit_slot': 7}
    backend = NS(_owned_tracks=lambda _rig, _session: {'EDIT': Track(native)},
                 _pose_selection_owner=lambda pb: pb)
    return context, rig, session, {'EDIT': track}, backend


class Basis(list):
    def __init__(self, quaternion):
        super().__init__(((1., 0., 0., 0.), (0., 1., 0., 0.),
                          (0., 0., 1., 0.), (0., 0., 0., 1.)))
        self.quaternion = quaternion

    def copy(self):
        return self

    def to_quaternion(self):
        return self.quaternion


class LoopOperatorGuards(unittest.TestCase):
    def setUp(self):
        self.actual = actual_helpers()

    def visibility(self, collections=(), solo=False, hidden=False):
        rig = NS(data=NS(collections=NS(is_solo_active=solo)))
        pb = NS(bone=Bone('FK', [NS(name=name, is_visible_effectively=visible)
                                for name, visible in collections]))
        return self.actual['_loop_visibility'](rig, pb, hidden)

    def test_unassigned_bone_requires_no_solo(self):
        self.assertTrue(self.visibility()['visible'])
        self.assertFalse(self.visibility(solo=True)['visible'])

    def test_effective_membership_and_pose_hide_both_required(self):
        self.assertFalse(self.visibility((('Hidden ancestor', False),))['visible'])
        self.assertTrue(self.visibility((('Hidden', False), ('Visible', True)), solo=True)['visible'])
        self.assertFalse(self.visibility((('Visible', True),), hidden=True)['visible'])

    def test_unknown_visibility_never_defaults_true_or_writes(self):
        collection = NS(name='Unknown', is_visible_effectively=1)
        rig = NS(data=NS(collections=NS(is_solo_active=False)))
        pb = NS(bone=Bone('FK', [collection]))
        before = copy.deepcopy(collection.__dict__)
        with self.assertRaises(AssertionError):
            self.actual['_loop_visibility'](rig, pb, False)
        self.assertEqual(collection.__dict__, before)

    def test_each_frame_reselects_fresh_native_equal_pose_and_track_wrappers(self):
        context, rig, session, tracks, backend = selection_fixture()
        self.assertIsNot(next(iter(rig.pose.bones)), rig.pose.bones['FK'])
        for _frame in range(2):
            rig.pose.bones['FK'].select = False
            rig.pose.bones['OTHER'].select = True
            diagnostic = {}
            chosen = self.actual['_loop_select'](context, rig, 'FK', session, tracks, backend,
                                                  lambda _pb: False, diagnostic)
            self.assertEqual(chosen, rig.pose.bones['FK'])
            self.assertEqual(diagnostic['selected_before'], ['OTHER'])
            self.assertEqual(diagnostic['selected_after'], ['FK'])
            self.assertEqual(diagnostic['active_bone'], 'FK')
            self.assertEqual(diagnostic['selected_pose_bones'], ['FK'])

    def test_actual_context_selection_must_match_native_owner_flags(self):
        context, rig, session, tracks, backend = selection_fixture()
        context.selection_override = []
        diagnostic = {}
        with self.assertRaisesRegex(AssertionError, 'selection/active'):
            self.actual['_loop_select'](context, rig, 'FK', session, tracks, backend,
                                        lambda _pb: False, diagnostic)
        self.assertEqual(diagnostic['selected_after'], ['FK'])
        self.assertEqual(diagnostic['selected_pose_bones'], [])

    def test_foreign_tweak_slot_and_context_fail_before_selection_writes(self):
        for change in ('slot', 'context', 'track', 'action'):
            with self.subTest(change=change):
                context, rig, session, tracks, backend = selection_fixture()
                if change == 'slot':
                    rig.animation_data.action_slot_handle = 8
                elif change == 'context':
                    context.region.type = 'CHANNELS'
                elif change == 'track':
                    rig.animation_data.nla_tracks.active = Track({'strips': tracks['EDIT'].strips})
                else:
                    rig.animation_data.action = Action('Owned B')
                diagnostic = {}
                with self.assertRaises(AssertionError):
                    self.actual['_loop_select'](context, rig, 'FK', session, tracks, backend,
                                                lambda _pb: False, diagnostic)
                self.assertTrue(diagnostic)
                self.assertFalse(any(native['events'] for native in rig.pose.bones.native.values()))

    def test_hidden_collection_fails_with_state_without_display_mutation(self):
        context, rig, session, tracks, backend = selection_fixture()
        collection = NS(name='Hidden', is_visible_effectively=False)
        rig.pose.bones.native['FK']['bone'].collections = [collection]
        diagnostic = {}
        with self.assertRaises(AssertionError):
            self.actual['_loop_select'](context, rig, 'FK', session, tracks, backend,
                                        lambda _pb: False, diagnostic)
        self.assertFalse(diagnostic['visibility']['visible'])
        self.assertFalse(collection.is_visible_effectively)
        self.assertFalse(any(native['events'] for native in rig.pose.bones.native.values()))

    def test_nonfinite_native_values_are_json_safe_and_rejected_for_motion(self):
        pb = NS(rotation_euler=(0., float('nan'), 0.), matrix_basis=Basis((1., 0., 0., 0.)))
        state = self.actual['_loop_rotation_state'](pb, 'rotation_euler')
        self.assertFalse(state['finite'])
        json.dumps(state, allow_nan=False)
        with self.assertRaises(AssertionError):
            self.actual['_loop_rotation_delta'](state, state)

    def test_native_basis_rotation_delta_rejects_unchanged_and_measures_rotation(self):
        before = self.actual['_loop_rotation_state'](
            NS(rotation_euler=(0., 0., 0.), matrix_basis=Basis((1., 0., 0., 0.))), 'rotation_euler')
        after = self.actual['_loop_rotation_state'](
            NS(rotation_euler=(.073, 0., 0.), matrix_basis=Basis((math.cos(.073 / 2), math.sin(.073 / 2), 0., 0.))),
            'rotation_euler')
        self.assertEqual(self.actual['_loop_rotation_delta'](before, before), 0.)
        self.assertAlmostEqual(self.actual['_loop_rotation_delta'](before, after), .073, places=10)

    def test_finite_values_cannot_hide_nonfinite_derived_rotation_normalization(self):
        state = {'valid': True, 'quaternion': [1e308, 0., 0., 0.]}
        with self.assertRaisesRegex(AssertionError, 'normalization'):
            self.actual['_loop_rotation_delta'](state, state)

    def test_actual_report_keeps_diagnostics_when_authoring_raises(self):
        rig = object()
        row = NS(rig=rig, item_id='Loop', new_animation=False, new_action_id='',
                 source_action='Source', source_slot=1, custom_action='Custom', custom_slot=1)
        saved = NS(items=[row], workspace_path='workspace', workspace_identity='identity',
                   model_file='model', model_sha256='sha')
        backend = NS(item_spec=lambda _row: {}, _prior=lambda _context, _rig: {},
                     _motion_hash=lambda _action, _slot: 'unchanged')
        captured = []
        class FakePath:
            def __init__(self, _value):
                pass
            def resolve(self):
                return self
            def is_dir(self):
                return True
            def __truediv__(self, _name):
                return self
            def is_relative_to(self, _directory):
                return True
            def __str__(self):
                return 'owned/report.json'
        def fail(_context, _row, _modules, _requested, *, diagnostics, checkpoint=None):
            diagnostics.update(control='FK', frames=[{'operator': {'poll': True, 'result': ['CANCELLED']}}])
            raise AssertionError('actual operator rejected')
        self.actual.update(validate_author_config=lambda value: value, Path=FakePath,
            _modules=lambda: {'animation_composition': backend, 'animation_worklist': NS(state=lambda _context: saved)},
            _row=lambda *_args: row, _sealed_loop=lambda _spec: None, _author_loop=fail,
            _write=lambda _path, report: captured.append(copy.deepcopy(report)),
            traceback=__import__('traceback'))
        config = {'evidenceDirectory': 'owned', 'holdLoopWorkItemId': 'actual',
                  'expectedModelSha256': 'actual', 'holdLoopRotationControl': None}
        with self.assertRaisesRegex(AssertionError, 'actual operator rejected'):
            self.actual['author_hold_loop'](object(), config)
        self.assertEqual(captured[0]['operator_diagnostics']['control'], 'FK')
        self.assertEqual(captured[0]['operator_diagnostics']['frames'][0]['operator']['result'], ['CANCELLED'])
        self.assertFalse(captured[0]['complete'])
        self.assertEqual(captured[0]['failure_phase'], 'author_actual_HoldLoop_Custom')


if __name__ == '__main__':
    unittest.main()
