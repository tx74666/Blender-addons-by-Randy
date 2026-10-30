"""Exact streaming animation proof with lazy large arrays, no Blender import."""
import ast
from array import array
import hashlib
import math
from pathlib import Path
import struct
import sys
import tracemalloc
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/hair_wiggle2_adapter.py'


class HairWiggleError(ValueError):
    pass


class Descriptor:
    _next = 100
    def __init__(self, name, properties):
        self.identifier, self.properties = name, properties
        Descriptor._next += 1
        self.pointer = Descriptor._next

    def as_pointer(self):
        return self.pointer


ENUM_CODES = {
    'interpolation': {'CONSTANT': 0, 'LINEAR': 1, 'BEZIER': 2, 'SINE': 12},
    'easing': {'AUTO': 0, 'EASE_IN': 1, 'EASE_OUT': 2, 'EASE_IN_OUT': 3},
    'type': {'KEYFRAME': 0, 'BREAKDOWN': 2, 'MOVING_HOLD': 4, 'EXTREME': 1},
    'handle_left_type': {'FREE': 0, 'ALIGNED': 3, 'VECTOR': 2, 'AUTO': 1, 'AUTO_CLAMPED': 4},
    'handle_right_type': {'FREE': 0, 'ALIGNED': 3, 'VECTOR': 2, 'AUTO': 1, 'AUTO_CLAMPED': 4},
}


def prop(name, kind, width=0, runtime=False):
    return NS(identifier=name, type=kind, is_array=bool(width), array_length=width,
              array_dimensions=(width, 0, 0) if width else (0, 0, 0), is_runtime=runtime,
              is_enum_flag=False, enum_items=[NS(identifier=k, value=v) for k, v in ENUM_CODES.get(name, {}).items()])


class Node:
    def __init__(self, descriptor, pointer, custom=None, **values):
        self.bl_rna, self.pointer, self.custom_values = descriptor, pointer, custom
        self.__dict__.update(values)

    def as_pointer(self):
        return self.pointer

    def keys(self):
        if self.custom_values is None:
            raise TypeError("bpy_struct.keys(): this type doesn't support IDProperties")
        return self.custom_values.keys()

    def __getitem__(self, key):
        return self.custom_values[key]

    def is_property_set(self, name):
        return name in self.__dict__


class ID(Node):
    library = None

    def __init__(self, descriptor, pointer, name, custom=None, **values):
        super().__init__(descriptor, pointer, custom={} if custom is None else custom, **values)
        self.name_full, self.name = name, name
        self.animation_data = values.get('animation_data')


class PropertyGroup(Node):
    pass


class Actions(list):
    def get(self, name):
        return next((action for action in self if action.name == name), None)


POINT = Descriptor('Keyframe', [prop('co', 'FLOAT', 2), prop('handle_left', 'FLOAT', 2),
    prop('handle_right', 'FLOAT', 2), prop('select_control_point', 'BOOLEAN'),
    prop('interpolation', 'ENUM'), prop('easing', 'ENUM'), prop('handle_left_type', 'ENUM'),
    prop('handle_right_type', 'ENUM'), prop('type', 'ENUM')])
CURVE = Descriptor('FCurve', [prop('data_path', 'STRING'), prop('array_index', 'INT'),
                            prop('keyframe_points', 'COLLECTION'), prop('modifiers', 'COLLECTION'),
                            prop('driver', 'POINTER')])
ACTION = Descriptor('Action', [prop('name', 'STRING'), prop('fcurves', 'COLLECTION'),
                             prop('duplicate_view', 'COLLECTION'), prop('asset_data', 'POINTER')])
OWNER = Descriptor('Object', [prop('name', 'STRING')])
ANIM = Descriptor('AnimData', [prop('action', 'POINTER'), prop('nla_tracks', 'COLLECTION'),
                              prop('drivers', 'COLLECTION')])
STRIP = Descriptor('NlaStrip', [prop('action', 'POINTER'), prop('scale', 'FLOAT')])
DRIVER = Descriptor('Driver', [prop('expression', 'STRING'), prop('target', 'POINTER')])


class Points:
    """A sized native-like collection: it never stores expanded point records."""
    def __init__(self, count):
        self.count, self.overrides, self.bulk_calls = count, {}, []
        self.bulk_overrides = {}

    def __len__(self):
        return self.count

    def __iter__(self):
        for index in range(self.count):
            yield self[index]

    def __getitem__(self, index):
        if not 0 <= index < self.count:
            raise IndexError(index)
        values = dict(co=(float(index), .25), handle_left=(float(index) - .2, .1),
                      handle_right=(float(index) + .2, .4), select_control_point=False,
                      interpolation='BEZIER', easing='AUTO', handle_left_type='AUTO',
                      handle_right_type='AUTO', type='KEYFRAME')
        values.update(self.overrides.get(index, {}))
        return Node(POINT, index + 1000, **values)

    def foreach_get(self, name, target):
        self.bulk_calls.append((name, len(target), target.itemsize))
        offset = 0
        for index, point in enumerate(self):
            value = getattr(point, name)
            if name in ENUM_CODES:
                value = self.bulk_overrides.get((name, index), ENUM_CODES[name][value])
            values = value if isinstance(value, tuple) else (value,)
            for number in values:
                target[offset] = number
                offset += 1


def load(count=40):
    points = Points(count)
    curve = Node(CURVE, 301, data_path='pose.bones["Head"].rotation_euler', array_index=0,
                 keyframe_points=points, modifiers=[], driver=None)
    metadata = Node(Descriptor('AssetMetaData', [prop('license', 'STRING')]), 302, license='Artist metadata')
    action = ID(ACTION, 201, 'Action', custom={'author_int64': 2 ** 60, 'signed_zero': 0.},
                fcurves=[curve], duplicate_view=[curve], asset_data=metadata)
    other = ID(ACTION, 202, 'Other Action', fcurves=[], duplicate_view=[])
    strip = Node(STRIP, 501, action=action, scale=1.)
    target = ID(OWNER, 401, 'Target')
    driver = Node(DRIVER, 502, expression='var', target=target)
    driver_curve = Node(CURVE, 504, data_path='location', array_index=0,
                        keyframe_points=[], modifiers=[], driver=driver)
    animation = Node(ANIM, 503, action=action, nla_tracks=[strip], drivers=[driver_curve])
    owner = ID(OWNER, 402, 'Rig', animation_data=animation)
    owner.type = 'ARMATURE'
    scene = ID(OWNER, 403, 'Scene')
    scene.objects = [owner]
    bpy = NS(types=NS(ID=ID, PropertyGroup=PropertyGroup), data=NS(actions=Actions([action, other])), context=NS(scene=scene))
    tree = ast.parse(SOURCE.read_text(encoding='utf8'))
    names = {'_AnimationHash', '_animation_proof', '_animation_same', '_fail'}
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    constants = [node for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id.startswith('_ANIMATION_') for target in node.targets)]
    ns = dict(bpy=bpy, hashlib=hashlib, struct=struct, array=array, sys=sys, HairWiggleError=HairWiggleError)
    exec(compile(ast.Module(body=constants + nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns, points, action, other, owner, strip, driver


class StreamingAnimationProofTests(unittest.TestCase):
    def test_stable_complete_token_and_per_pass_curve_memo(self):
        ns, points, action, other, owner, strip, driver = load()
        stats = {}
        proof = ns['_animation_proof'](diagnostics=stats)
        self.assertEqual(proof, ns['_animation_proof']())
        self.assertTrue(ns['_animation_same'](proof))
        self.assertGreater(stats['bulk_fields'], 0)
        self.assertGreater(stats['memo_hits'], 0)
        self.assertEqual(stats['enum_bulk_fields'], 5)
        self.assertEqual(stats['enum_scalar_validation_points'], 10)
        self.assertEqual(stats['max_buffer_bytes'], 40 * 2 * 8)
        self.assertNotIn('stats', proof)

    def test_large_lazy_arrays_have_bounded_peak_and_small_retained_token(self):
        ns, points, action, other, owner, strip, driver = load(20000)
        tracemalloc.start()
        try:
            stats = {}
            proof = ns['_animation_proof'](diagnostics=stats)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 8 * 1024 * 1024)
        self.assertLess(len(str(proof)), 1000)
        self.assertLessEqual(stats['max_buffer_bytes'], 8 * ns['_ANIMATION_MAX_BUFFER_ITEMS'])
        self.assertGreater(stats['bulk_fields'], 0)
        self.assertEqual(len(ns['_AnimationHash']().memo), 0)

    def test_bulk_and_scalar_fallback_have_identical_exact_encoding(self):
        ns, points, action, other, owner, strip, driver = load()
        fast = ns['_animation_proof']()
        ns['_ANIMATION_MAX_BUFFER_ITEMS'] = 1
        stats = {}
        slow = ns['_animation_proof'](diagnostics=stats)
        self.assertEqual(fast, slow)
        self.assertEqual(stats['bulk_fields'], 0)
        self.assertEqual(stats['max_buffer_bytes'], 0)

    def test_forced_scalar_proof_matches_numeric_and_enum_bulk(self):
        ns, points, action, other, owner, strip, driver = load()
        fast_stats, slow_stats = {}, {}
        fast = ns['_animation_proof'](diagnostics=fast_stats)
        slow = ns['_animation_proof'](force_scalar=True, diagnostics=slow_stats)
        self.assertEqual(fast, slow)
        self.assertEqual(fast_stats['enum_bulk_fields'], 5)
        self.assertEqual(slow_stats['enum_bulk_fields'], 0)
        self.assertEqual(slow_stats['bulk_fields'], 0)

    def test_unknown_enum_code_anywhere_in_buffer_refuses(self):
        ns, points, action, other, owner, strip, driver = load()
        points.bulk_overrides['interpolation', 19] = 999
        with self.assertRaisesRegex(HairWiggleError, 'Unknown native Keyframe enum value'):
            ns['_animation_proof']()

    def test_bulk_code_mapping_mismatch_uses_equivalent_full_fallback(self):
        ns, points, action, other, owner, strip, driver = load()
        expected = ns['_animation_proof'](force_scalar=True)
        points.bulk_overrides['interpolation', 0] = 1
        stats = {}
        actual = ns['_animation_proof'](diagnostics=stats)
        self.assertEqual(actual, expected)
        self.assertEqual(stats['enum_bulk_fields'], 4)

    def test_all_last_point_coordinates_handles_and_enums_are_exact(self):
        ns, points, action, other, owner, strip, driver = load()
        proof = ns['_animation_proof']()
        changes = [('co', (39., math.nextafter(.25, math.inf))),
                   ('handle_left', (38.8, math.nextafter(.1, math.inf))),
                   ('handle_right', (39.2, math.nextafter(.4, math.inf))),
                   ('interpolation', 'LINEAR'), ('easing', 'EASE_IN'),
                   ('handle_left_type', 'FREE'), ('handle_right_type', 'VECTOR'),
                   ('type', 'BREAKDOWN'), ('select_control_point', True)]
        for name, value in changes:
            with self.subTest(field=name):
                points.overrides[39] = {name: value}
                self.assertFalse(ns['_animation_same'](proof))
                points.overrides.clear()
                self.assertTrue(ns['_animation_same'](proof))

    def test_custom_int64_and_float_bits_are_not_quantized(self):
        ns, points, action, other, owner, strip, driver = load()
        proof = ns['_animation_proof']()
        action.custom_values['author_int64'] += 1
        self.assertFalse(ns['_animation_same'](proof))
        action.custom_values['author_int64'] -= 1
        action.custom_values['signed_zero'] = -0.
        self.assertFalse(ns['_animation_same'](proof))
        action.custom_values['signed_zero'] = 0.
        self.assertTrue(ns['_animation_same'](proof))

    def test_action_nla_driver_and_identity_changes_are_detected(self):
        ns, points, action, other, owner, strip, driver = load()
        proof = ns['_animation_proof']()
        for node, field, value in [(owner.animation_data, 'action', other),
                                   (strip, 'action', other), (driver, 'expression', 'var + 1'),
                                   (action.asset_data, 'license', 'Changed metadata'),
                                   (action, 'pointer', 999)]:
            with self.subTest(field=field):
                original = getattr(node, field)
                setattr(node, field, value)
                self.assertFalse(ns['_animation_same'](proof))
                setattr(node, field, original)
                self.assertTrue(ns['_animation_same'](proof))

    def test_same_named_local_and_linked_actions_are_members_by_exact_id(self):
        ns, points, action, other, owner, strip, driver = load()
        other.name = action.name
        other.name_full = action.name_full
        other.library = NS(as_pointer=lambda: 888)
        self.assertIs(ns['bpy'].data.actions.get(other.name), action)
        proof = ns['_animation_proof']()
        self.assertTrue(ns['_animation_same'](proof))

    def test_removed_same_named_action_still_refuses(self):
        ns, points, action, other, owner, strip, driver = load()
        other.name = action.name
        other.name_full = action.name_full
        other.library = NS(as_pointer=lambda: 888)
        proof = ns['_animation_proof']()
        ns['bpy'].data.actions.remove(other)
        self.assertFalse(ns['_animation_same'](proof))

    def test_replaced_retained_id_refuses_even_with_reused_name_and_pointer(self):
        ns, points, action, other, owner, strip, driver = load()
        proof = ns['_animation_proof']()
        replacement = ID(ACTION, action.pointer, action.name,
            fcurves=action.fcurves, duplicate_view=action.duplicate_view, asset_data=action.asset_data)
        ns['bpy'].data.actions[0] = replacement
        self.assertFalse(ns['_animation_same'](proof))

    def test_controls_scope_excludes_only_explicit_associations(self):
        ns, points, action, other, owner, strip, driver = load()
        proof = ns['_animation_proof'](include_associations=False)
        self.assertEqual(proof['owner_refs'], ())
        owner.animation_data.action = other
        self.assertTrue(ns['_animation_same'](proof))
        action.custom_values['author_int64'] += 1
        self.assertFalse(ns['_animation_same'](proof))

    def test_association_only_proof_never_reads_global_action_inventory(self):
        ns, points, action, other, owner, strip, driver = load()
        class NoActionInventory:
            @property
            def actions(self):
                raise AssertionError('Interactive association proof traversed the global Action library')
        ns['bpy'].data = NoActionInventory()
        proof = ns['_animation_proof'](include_actions=False)
        self.assertEqual(proof['action_refs'], ())
        self.assertEqual(proof['action_count'], 0)
        self.assertTrue(ns['_animation_same'](proof))

    def test_association_scope_preserves_key_edits_but_detects_binding_and_driver_changes(self):
        ns, points, action, other, owner, strip, driver = load()
        proof = ns['_animation_proof'](include_actions=False)
        points.overrides[19] = {'co': (19., .375)}
        self.assertTrue(ns['_animation_same'](proof))
        self.assertEqual(points.overrides[19], {'co': (19., .375)})
        for node, field, value in [(owner.animation_data, 'action', other),
                                   (strip, 'action', other), (driver, 'expression', 'var + .25')]:
            with self.subTest(field=field):
                original = getattr(node, field)
                setattr(node, field, value)
                self.assertFalse(ns['_animation_same'](proof))
                setattr(node, field, original)
                self.assertTrue(ns['_animation_same'](proof))

    def test_unknown_unset_registered_pointer_refuses_without_materialization(self):
        ns, points, action, other, owner, strip, driver = load()
        descriptor = Descriptor('Action', ACTION.properties + [prop('optional', 'POINTER', runtime=True)])
        action.bl_rna = descriptor
        class GuardedAction(ID):
            @property
            def optional(self):
                raise AssertionError('Proof materialized optional native storage')
        action.__class__ = GuardedAction
        with self.assertRaisesRegex(HairWiggleError, 'Action.optional'):
            ns['_animation_proof']()


if __name__ == '__main__':
    unittest.main()
