"""Pure numerical/control-flow checks for bounded native floor retries.

Only the named definitions are compiled from the production AST. No bpy,
mathutils, add-on registration, artist files or native graph are loaded. Tiny
matrix/vector doubles exercise dispatch and bounds; native Spline IK output,
rollback fidelity and saved results require the separate Blender proofs.
"""
import ast
from contextlib import contextmanager, nullcontext
import copy
import math
from pathlib import Path
import struct
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/character_ground.py'
DEFINITIONS = {
    '_GROUND_PRECISION', '_GROUND_NEIGHBOR_STEPS', '_ShapeChangeError',
    '_FloorPrecisionError', '_mesh_numerical_limit', '_nearby_floor_distances',
    '_spline_shape_dependency', '_verify', '_translate', 'ground',
}


class Vec:
    def __init__(self, values):
        self.values = tuple(values)

    def __iter__(self):
        return iter(self.values)

    def __add__(self, other):
        return Vec(a + b for a, b in zip(self, other))

    x, y, z = (property(lambda self, i=i: self.values[i]) for i in range(3))


class Mat:
    def __init__(self, rows=((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))):
        self.rows = rows

    def to_3x3(self):
        return self

    def inverted(self):
        if self.rows != Mat().rows:
            raise AssertionError('This test double inverts identity only.')
        return self

    def __matmul__(self, other):
        if isinstance(other, Mat):
            return Mat(tuple(tuple(sum(self.rows[r][k] * other.rows[k][c]
                                       for k in range(3)) for c in range(3)) for r in range(3)))
        return Vec(sum(a * b for a, b in zip(row, other)) for row in self.rows)

    @staticmethod
    def Translation(_values):
        return Mat()


class Item:
    def __init__(self, **fields):
        self.__dict__.update(fields)

    def get(self, name):
        return getattr(self, 'properties', {}).get(name)


@contextmanager
def subject():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    selected = []
    for node in tree.body:
        name = getattr(node, 'name', None)
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        if name in DEFINITIONS:
            selected.append(node)
    found = {getattr(node, 'name', None) if not isinstance(node, ast.Assign)
             else node.targets[0].id for node in selected}
    if found != DEFINITIONS:
        raise AssertionError(f'Missing production definitions: {DEFINITIONS - found}')
    package = ModuleType('_stable_floor_pure_subject')
    package.__path__ = []
    module = ModuleType(package.__name__ + '.ground')
    module.__package__ = package.__name__
    module.__dict__.update(math=math, struct=struct, Vector=Vec, Matrix=Mat)
    helpers = {}
    for name in ('floor_widgets', 'placement_offsets', 'body_setup', 'skirt_rig'):
        helper = ModuleType(package.__name__ + '.' + name)
        setattr(package, name, helper)
        helpers[helper.__name__] = helper
    package.floor_widgets.prepare = Mock(return_value={'display_baseline': object()})
    package.floor_widgets.apply = Mock()
    package.floor_widgets.verify = Mock(return_value={'count': 3})
    package.floor_widgets.restore = Mock()
    package.placement_offsets.prepare = Mock(return_value={'expected_channels': {}})
    package.placement_offsets.apply = Mock()
    package.placement_offsets.verify = Mock(return_value={'count': 0})
    package.placement_offsets.restore = Mock()
    package.body_setup._runtime_paused = nullcontext
    package.skirt_rig.RIG_KEY = 'owned_dress_rig'
    with patch.dict(sys.modules, {package.__name__: package, **helpers}):
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), module.__dict__)
        module.helpers = package
        yield module


def state_at(target=1., distance=.25):
    root = Item(name='Main', parent=None, matrix_basis=Mat(),
                location=Vec((0., 0., target - distance)), lock_location=(False,) * 3)
    mesh = Item(name='Dress', type='MESH', modifiers=[])
    channels = {'rotation_mode': 'XYZ', 'parent_inverse': (),
                'channels': {'location': tuple(root.location), 'scale': (1., 1., 1.)}}
    state = {'roots': [root], 'world': {root: Mat()}, 'channels': {root: channels},
             'objects': {root, mesh}, 'rigs': set(), 'internal': {},
             'points': {mesh: [0., 0., .25]}, 'visible': {mesh}, 'lowest': .25,
             'lowest_tolerance': .00012, 'mesh_proof': {}}
    return state, root, mesh


def word(value):
    return struct.unpack('!I', struct.pack('!f', value))[0]


class Base(unittest.TestCase):
    def setUp(self):
        manager = subject()
        self.m = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)


class NeighborTests(Base):
    def test_positive_target_alternates_nearest_float32_words_and_stops_at_eight(self):
        state, _root, _mesh = state_at(1.)
        rows = list(self.m._nearby_floor_distances(state, .25))
        self.assertEqual([step for _distance, step, _adjustment in rows],
                         [value for n in range(1, 9) for value in (n, -n)])
        self.assertEqual([word(1. + adjustment) for _d, _s, adjustment in rows],
                         [word(1.) + value for n in range(1, 9) for value in (n, -n)])
        self.assertEqual(len(rows), 16)

    def test_negative_target_orders_numerical_above_then_below(self):
        state, _root, _mesh = state_at(-1.)
        rows = list(self.m._nearby_floor_distances(state, .25))
        self.assertEqual([word(-1. + adjustment) for _d, _s, adjustment in rows],
                         [word(-1.) - value for n in range(1, 9) for value in (n, -n)])
        self.assertTrue(all(adjustment * step > 0 for _d, step, adjustment in rows))

    def test_zero_translation_has_no_alternatives(self):
        state, _root, _mesh = state_at()
        self.assertEqual(list(self.m._nearby_floor_distances(state, 0.)), [])

    def test_zero_target_deduplicates_signed_zero_and_keeps_finite_subnormals(self):
        state, _root, _mesh = state_at(0.)
        rows = list(self.m._nearby_floor_distances(state, .25))
        values = [adjustment for _d, _s, adjustment in rows]
        self.assertTrue(values)
        self.assertNotIn(0., values)
        self.assertEqual(len(values), len(set(values)))
        self.assertTrue(all(math.isfinite(value) for value in values))
        self.assertEqual(values[0], struct.unpack('!f', bytes.fromhex('00000001'))[0])
        self.assertTrue(any(value < 0 for value in values))

    def test_nonfinite_target_or_distance_has_no_alternatives(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                state, root, _mesh = state_at()
                state['channels'][root]['channels']['location'] = (0., 0., value)
                self.assertEqual(list(self.m._nearby_floor_distances(state, .25)), [])
                state, _root, _mesh = state_at()
                self.assertEqual(list(self.m._nearby_floor_distances(state, value)), [])

    def test_tighter_floor_precision_filters_large_float32_neighbors(self):
        state, _root, _mesh = state_at(8.)
        rows = list(self.m._nearby_floor_distances(state, .25))
        self.assertEqual([step for _d, step, _a in rows], [1, -1, -2])
        self.assertTrue(all(abs(adjustment) <= 1e-6 for _d, _s, adjustment in rows))
        self.assertTrue(all(distance == .25 + adjustment for distance, _s, adjustment in rows))
        state, _root, _mesh = state_at(32.)
        self.assertEqual(list(self.m._nearby_floor_distances(state, .25)), [])

    def test_only_one_unparented_root_can_retry(self):
        for count in (0, 2):
            state, root, _mesh = state_at()
            state['roots'] = [root] * count
            self.assertEqual(list(self.m._nearby_floor_distances(state, .25)), [])
        state, root, _mesh = state_at()
        root.parent = object()
        self.assertEqual(list(self.m._nearby_floor_distances(state, .25)), [])

    def test_world_vertical_delta_must_not_leak_into_local_xy(self):
        for axis in (0, 1):
            state, root, _mesh = state_at()
            rows = [list(row) for row in Mat().rows]
            rows[axis][2] = .01
            root.matrix_basis = Mat(tuple(tuple(row) for row in rows))
            self.assertEqual(list(self.m._nearby_floor_distances(state, .25)), [])

    def test_helper_does_not_write_original_channels_or_points(self):
        state, root, mesh = state_at()
        channels = copy.deepcopy(state['channels'][root])
        points = state['points'][mesh][:]
        list(self.m._nearby_floor_distances(state, .25))
        self.assertEqual(state['channels'][root], channels)
        self.assertEqual(state['points'][mesh], points)
        self.assertEqual(tuple(root.location), channels['channels']['location'])


class DependencyTests(Base):
    def fixture(self):
        target = Item(name='Guide')
        con = Item(type='SPLINE_IK', mute=False, influence=1., target=target)
        rig = Item(pose=Item(bones={'Manual': Item(constraints=[con])}))
        modifier = Item(type='ARMATURE', show_viewport=True, object=rig)
        mesh = Item(properties={'owned_dress_rig': rig}, modifiers={'Skin': modifier},
                    vertex_groups={'Def': Item(index=2)},
                    data=Item(vertices=[Item(groups=[Item(group=2, weight=1.)])]))
        record = {'modifier': 'Skin', 'chains': [{'curve': 'Guide', 'def': ['Def'], 'manual': ['Manual']}]}
        self.m.helpers.skirt_rig.read_record = Mock(return_value=record)
        self.m.bpy = SimpleNamespace(data=SimpleNamespace(objects={'Guide': target}))
        return {'rigs': {rig}, 'objects': {rig, target}}, mesh, modifier, con

    def test_actual_scoped_visible_spline_skin_is_eligible(self):
        state, mesh, _modifier, _con = self.fixture()
        self.assertTrue(self.m._spline_shape_dependency(state, mesh))

    def test_unrelated_or_inactive_dependencies_are_ineligible(self):
        variants = (('modifier', 'type', 'MIRROR'), ('modifier', 'show_viewport', False),
                    ('modifier', 'object', object()), ('con', 'type', 'IK'),
                    ('con', 'mute', True), ('con', 'influence', 0.),
                    ('con', 'influence', math.nan), ('con', 'target', object()))
        for owner, field, value in variants:
            with self.subTest(owner=owner, field=field, value=value):
                state, mesh, modifier, con = self.fixture()
                setattr(modifier if owner == 'modifier' else con, field, value)
                self.assertFalse(self.m._spline_shape_dependency(state, mesh))

    def test_registered_record_and_exact_skin_modifier_are_required(self):
        for change in ('no_record', 'no_rig', 'no_modifier', 'foreign_modifier_rig'):
            with self.subTest(change=change):
                state, mesh, modifier, _con = self.fixture()
                if change == 'no_record':
                    self.m.helpers.skirt_rig.read_record.return_value = None
                elif change == 'no_rig':
                    state['rigs'].clear()
                elif change == 'no_modifier':
                    mesh.modifiers.clear()
                else:
                    modifier.object = object()
                self.assertFalse(self.m._spline_shape_dependency(state, mesh))

    def test_curve_ownership_and_actual_positive_def_weights_are_required(self):
        for change in ('outside_curve', 'missing_def', 'zero_weight', 'foreign_weight'):
            with self.subTest(change=change):
                state, mesh, _modifier, con = self.fixture()
                if change == 'outside_curve':
                    state['objects'].remove(con.target)
                elif change == 'missing_def':
                    mesh.vertex_groups.clear()
                elif change == 'zero_weight':
                    mesh.data.vertices[0].groups[0].weight = 0.
                else:
                    mesh.data.vertices[0].groups[0].group = 99
                self.assertFalse(self.m._spline_shape_dependency(state, mesh))

    def test_invalid_record_is_not_silently_admitted_as_numerical_noise(self):
        state, mesh, _modifier, _con = self.fixture()
        self.m.helpers.skirt_rig.read_record.side_effect = ValueError('invalid authored record')
        with self.assertRaisesRegex(ValueError, 'invalid authored record'):
            self.m._spline_shape_dependency(state, mesh)


class GroundFlowTests(Base):
    def setUp(self):
        super().setUp()
        self.state, self.root, self.mesh = state_at(1., .25)
        self.m._prepare = Mock(return_value=self.state)
        self.m._spline_shape_dependency = Mock(return_value=True)
        self.m._verify = Mock(return_value=0.)
        self.ctx, self.rig = object(), object()

    def test_success_keeps_the_original_single_attempt(self):
        result = {'distance': -.25}
        self.m._translate = Mock(return_value=result)
        self.assertIs(self.m.ground(self.ctx, self.rig), result)
        self.assertEqual(self.m._translate.call_count, 1)
        self.assertNotIn('stable_floor', self.m._translate.call_args.kwargs)
        self.m._verify.assert_not_called()
        self.m._spline_shape_dependency.assert_not_called()

    def test_already_grounded_is_zero_distance_within_stricter_floor_precision(self):
        for value in (0., 9e-7, -9e-7, 1e-6, -1e-6):
            with self.subTest(value=value):
                self.state['lowest'] = value
                self.m._translate = Mock(return_value={})
                self.m.ground(self.ctx, self.rig)
                self.assertEqual(self.m._translate.call_args.args[2], 0.)
        self.state['lowest'] = 1.000001e-6
        self.m._translate = Mock(return_value={})
        self.m.ground(self.ctx, self.rig)
        self.assertEqual(self.m._translate.call_args.args[2], -1.000001e-6)

    def test_nonshape_errors_never_enter_fallback(self):
        for error in (ValueError('Rest changed'), ValueError('topology changed'),
                      RuntimeError('rollback incomplete'), self.m._FloorPrecisionError('floor')):
            with self.subTest(error=error):
                self.m._translate = Mock(side_effect=error)
                with self.assertRaises(type(error)) as caught:
                    self.m.ground(self.ctx, self.rig)
                self.assertIs(caught.exception, error)
                self.assertEqual(self.m._translate.call_count, 1)
        self.m._spline_shape_dependency.assert_not_called()
        self.m._verify.assert_not_called()

    def test_shape_failure_without_native_spline_dependency_is_reraised(self):
        original = self.m._ShapeChangeError(self.mesh, 'first failure')
        self.m._translate = Mock(side_effect=original)
        self.m._spline_shape_dependency.return_value = False
        with self.assertRaises(self.m._ShapeChangeError) as caught:
            self.m.ground(self.ctx, self.rig)
        self.assertIs(caught.exception, original)
        self.m._verify.assert_not_called()

    def test_each_retry_proves_rollback_first_and_reuses_original_baseline(self):
        original = self.m._ShapeChangeError(self.mesh, 'first failure')
        outcomes = [original, self.m._ShapeChangeError(self.mesh, 'next shape'),
                    self.m._FloorPrecisionError('too far from floor'), {'distance': -.2499999}]
        candidates = [(-.25 + n * 1e-8, n, n * 1e-8) for n in (1, -1, 2)]
        self.m._nearby_floor_distances = Mock(return_value=iter(candidates))
        before_channels = copy.deepcopy(self.state['channels'][self.root])
        before_points = self.state['points'][self.mesh][:]
        events = []

        def translate(_context, state, distance, **kwargs):
            self.assertIs(state, self.state)
            events.append(('attempt', kwargs.get('stable_floor', False)))
            value = outcomes.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        def verify(_context, state, distance):
            self.assertIs(state, self.state)
            self.assertEqual(distance, 0.)
            events.append(('rollback',))

        self.m._translate = Mock(side_effect=translate)
        self.m._verify.side_effect = verify
        result = self.m.ground(self.ctx, self.rig)
        self.assertEqual(events, [('attempt', False), ('rollback',), ('attempt', True),
                                  ('rollback',), ('attempt', True), ('rollback',), ('attempt', True)])
        self.m._prepare.assert_called_once_with(self.ctx, self.rig)
        self.assertEqual(self.state['channels'][self.root], before_channels)
        self.assertEqual(self.state['points'][self.mesh], before_points)
        calls = self.m._translate.call_args_list
        for call in calls[1:]:
            self.assertIs(call.kwargs['floor_state'], calls[0].kwargs['floor_state'])
            self.assertIs(call.kwargs['offset_state'], calls[0].kwargs['offset_state'])
        self.assertEqual(result['stable_floor']['planned_root_z_steps'], 2)
        self.assertEqual(result['stable_floor']['accepted_root_z'], self.root.location.z)
        self.assertTrue(result['stable_floor']['original_shape_limits_retained'])

    def test_failed_rollback_proof_stops_before_another_write(self):
        self.m._translate = Mock(side_effect=self.m._ShapeChangeError(self.mesh, 'shape'))
        self.m._verify.side_effect = ValueError('restored outputs differ')
        with self.assertRaisesRegex(ValueError, 'restored outputs differ'):
            self.m.ground(self.ctx, self.rig)
        self.assertEqual(self.m._translate.call_count, 1)

    def test_unexpected_retry_error_propagates_instead_of_searching_further(self):
        self.m._translate = Mock(side_effect=[self.m._ShapeChangeError(self.mesh, 'shape'),
                                             ValueError('Rest changed')])
        with self.assertRaisesRegex(ValueError, 'Rest changed'):
            self.m.ground(self.ctx, self.rig)
        self.assertEqual(self.m._translate.call_count, 2)

    def test_exhaustion_is_bounded_and_reraises_original_after_final_rollback_proof(self):
        original = self.m._ShapeChangeError(self.mesh, 'original')
        self.m._translate = Mock(side_effect=original)
        with self.assertRaises(self.m._ShapeChangeError) as caught:
            self.m.ground(self.ctx, self.rig)
        self.assertIs(caught.exception, original)
        self.assertEqual(self.m._translate.call_count, 17)
        self.assertEqual(self.m._verify.call_count, 17)
        self.m._verify.assert_called_with(self.ctx, self.state, 0.)

    def test_ineligible_hierarchy_exhausts_without_another_attempt(self):
        original = self.m._ShapeChangeError(self.mesh, 'original')
        self.root.parent = object()
        self.m._translate = Mock(side_effect=original)
        with self.assertRaises(self.m._ShapeChangeError) as caught:
            self.m.ground(self.ctx, self.rig)
        self.assertIs(caught.exception, original)
        self.assertEqual(self.m._translate.call_count, 1)
        self.m._verify.assert_called_once_with(self.ctx, self.state, 0.)


class StableTransactionTests(Base):
    def setUp(self):
        super().setUp()
        self.state, self.root, self.mesh = state_at()
        self.events = []
        self.ctx = SimpleNamespace(view_layer=SimpleNamespace(update=Mock()))
        self.m._tag_scope = Mock(side_effect=lambda _objects: self.events.append('tag'))
        self.m._channels = Mock(side_effect=lambda root: self.state['channels'][root])
        self.m._restore_roots = Mock(side_effect=self.restore)
        self.floor = self.m.helpers.floor_widgets.prepare.return_value
        self.offset = self.m.helpers.placement_offsets.prepare.return_value

    def restore(self, state):
        self.assertIs(state, self.state)
        self.root.location = Vec(state['channels'][self.root]['channels']['location'])

    def run_translate(self, values, *, stable=True):
        values = iter(values)

        def verify(_context, state, distance):
            self.events.append('verify')
            self.assertIs(state['points'], self.state['points'])
            self.assertEqual(distance, -.25)
            value = next(values)
            if isinstance(value, Exception):
                raise value
            return value

        self.m._verify = Mock(side_effect=verify)
        return self.m._translate(self.ctx, self.state, -.25, floor_state=self.floor,
                                 offset_state=self.offset, stable_floor=stable)

    def test_stable_attempt_retags_and_runs_full_original_verify_twice(self):
        result = self.run_translate([8e-8, 9e-8])
        self.assertEqual(self.events, ['tag', 'verify', 'tag', 'verify'])
        self.assertEqual(result['lowest_after'], 9e-8)
        self.m.helpers.floor_widgets.verify.assert_called_once_with(self.ctx, self.floor)
        self.m._restore_roots.assert_not_called()

    def test_first_floor_precision_failure_rolls_back_without_second_verify(self):
        before = tuple(self.root.location)
        with self.assertRaises(self.m._FloorPrecisionError):
            self.run_translate([1.000001e-6])
        self.assertEqual(self.m._verify.call_count, 1)
        self.assertEqual(tuple(self.root.location), before)
        self.m.helpers.floor_widgets.restore.assert_called_once_with(self.ctx, self.floor)
        self.m.helpers.placement_offsets.restore.assert_called_once_with(self.ctx, self.offset)

    def test_rebuilt_floor_precision_failure_also_rolls_back(self):
        with self.assertRaises(self.m._FloorPrecisionError):
            self.run_translate([0., -1.000001e-6])
        self.assertEqual(self.m._verify.call_count, 2)
        self.m._restore_roots.assert_called_once_with(self.state)

    def test_rebuilt_shape_refusal_is_never_suppressed(self):
        refusal = self.m._ShapeChangeError(self.mesh, 'second graph shape changed')
        with self.assertRaises(self.m._ShapeChangeError) as caught:
            self.run_translate([0., refusal])
        self.assertIs(caught.exception, refusal)
        self.m._restore_roots.assert_called_once_with(self.state)

    def test_exact_floor_bound_is_accepted_and_ordinary_move_keeps_its_contract(self):
        self.assertEqual(self.run_translate([1e-6, -1e-6])['lowest_after'], -1e-6)
        self.events.clear()
        self.assertEqual(self.run_translate([2e-5], stable=False)['lowest_after'], 2e-5)
        self.assertEqual(self.events, ['tag', 'verify'])


class OriginalShapeLimitTests(Base):
    def test_shape_limit_remains_size_based_and_translation_independent(self):
        self.assertEqual(self.m._mesh_numerical_limit([]), (0., 2e-5))
        points = [0., 0., 0., 3., 4., 0.]
        expected = (5., 2e-5 + 5e-4)
        self.assertEqual(self.m._mesh_numerical_limit(points), expected)
        moved = [value + (100. if index % 3 == 2 else 0.) for index, value in enumerate(points)]
        self.assertEqual(self.m._mesh_numerical_limit(moved), expected)

    def verify_points(self, extra, *, topology=False):
        mesh = Item(name='Dress')
        state = {'world': {}, 'channels': {}, 'roots': [], 'internal': {},
                 'points': {mesh: [0., 0., 0., 1., 0., 0.]},
                 'visible': {mesh}, 'lowest': 0.}
        ctx = SimpleNamespace(view_layer=SimpleNamespace(update=Mock()),
                              evaluated_depsgraph_get=Mock(return_value=object()))
        self.m._points = Mock(return_value=[0., 0., -.25, 1. + extra, 0., -.25]
                              + ([0., 0., 0.] if topology else []))
        return mesh, state, ctx

    def test_below_original_shape_limit_passes_without_relaxation(self):
        limit = 2e-5 + 1e-4
        mesh, state, ctx = self.verify_points(limit * .999)
        self.assertEqual(self.m._verify(ctx, state, -.25), -.25)
        self.assertEqual(state['mesh_proof'][mesh.name]['tolerance'], limit)

    def test_just_above_original_shape_limit_is_mesh_specific_refusal(self):
        limit = 2e-5 + 1e-4
        mesh, state, ctx = self.verify_points(limit * 1.001)
        with self.assertRaises(self.m._ShapeChangeError) as caught:
            self.m._verify(ctx, state, -.25)
        self.assertIs(caught.exception.mesh, mesh)
        self.assertIn('native limit', str(caught.exception))

    def test_topology_refusal_is_not_reclassified_as_retryable_shape_noise(self):
        _mesh, state, ctx = self.verify_points(0., topology=True)
        with self.assertRaises(ValueError) as caught:
            self.m._verify(ctx, state, -.25)
        self.assertNotIsInstance(caught.exception, self.m._ShapeChangeError)
        self.assertIn('topology', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
