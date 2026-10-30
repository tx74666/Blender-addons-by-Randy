"""Ephemeral RNA discovery reuse; no Blender import or passed-proof caching."""
import ast
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
from queue import Queue
from threading import Thread
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'
OWNER_KEY = 'character_designer_owner'


def load_scope():
    spec = importlib.util.spec_from_file_location('_native_scope_under_test', SOURCE / 'validation_native.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def node(file, name):
    return next(item for item in ast.parse((SOURCE / file).read_text(encoding='utf8')).body
                if isinstance(item, ast.FunctionDef) and item.name == name)


def function(file, name, namespace):
    exec(compile(ast.Module(body=[node(file, name)], type_ignores=[]), file, 'exec'), namespace)
    return namespace[name]


class Bone:
    def __init__(self, name, owner=None):
        self.name, self.owner, self.reads = name, owner, 0

    def get(self, key):
        self.assert_key(key)
        self.reads += 1
        if isinstance(self.owner, Exception):
            raise self.owner
        return self.owner

    @staticmethod
    def assert_key(key):
        if key != OWNER_KEY:
            raise AssertionError(key)


def rig(*owners):
    return SimpleNamespace(data=SimpleNamespace(bones=[Bone(str(i), owner) for i, owner in enumerate(owners)]),
                           as_pointer=lambda: 29)


class NativeScopeTests(unittest.TestCase):
    def setUp(self):
        self.scope = load_scope()

    def owned(self, armature, owner):
        return list(self.scope.iter_owned_bones(armature, OWNER_KEY, owner))

    def test_ordinary_reads_are_current_and_keep_original_short_circuit(self):
        arm = rig('torso_controls', RuntimeError('later bone must not be read'))
        self.assertTrue(any(True for _ in self.scope.iter_owned_bones(arm, OWNER_KEY, 'torso_controls')))
        self.assertEqual([bone.reads for bone in arm.data.bones], [1, 0])
        arm.data.bones[0].owner = 'eye_controls'
        self.assertEqual(next(self.scope.iter_owned_bones(arm, OWNER_KEY, 'eye_controls')).name, '0')
        self.assertEqual(arm.data.bones[0].reads, 2)

    def test_scope_reads_each_owner_once_and_preserves_native_order(self):
        arm = rig('limb_ik', 'eye_controls', 'limb_ik', None)
        with self.scope.read_scope():
            self.assertEqual([bone.name for bone in self.owned(arm, 'limb_ik')], ['0', '2'])
            self.assertEqual([bone.name for bone in self.owned(arm, 'eye_controls')], ['1'])
            self.assertEqual([bone.reads for bone in arm.data.bones], [1, 1, 1, 1])
        self.assertIsNone(self.scope._ACTIVE.get())
        with self.scope.read_scope():
            arm.data.bones.reverse()
            self.assertEqual([bone.name for bone in self.owned(arm, 'limb_ik')], ['2', '0'])
        self.assertEqual([bone.reads for bone in arm.data.bones], [2, 2, 2, 2])

    def test_six_validator_queries_share_one_complete_346_bone_owner_scan(self):
        owners = ('limb_ik', 'foot_controls', 'torso_controls', 'spine_ik_fk', 'eye_controls', 'root_control')
        arm = rig(*(owners[index % len(owners)] for index in range(346)))
        expected = {owner: self.owned(arm, owner) for owner in owners}
        self.assertEqual(sum(bone.reads for bone in arm.data.bones), 6 * 346)
        for bone in arm.data.bones:
            bone.reads = 0
        with self.scope.read_scope():
            for owner in owners:
                self.assertEqual(self.owned(arm, owner), expected[owner])
        self.assertEqual(sum(bone.reads for bone in arm.data.bones), 346)

    def test_unhashable_malformed_owners_keep_string_comparison_behavior(self):
        arm = rig([], {}, [1, 2], {'x': 1}, False, 7, 'eye_controls')
        expected = [bone for bone in arm.data.bones if bone.owner == 'eye_controls']
        for active in (False, True):
            if active:
                with self.scope.read_scope():
                    self.assertEqual(self.owned(arm, 'eye_controls'), expected)
            else:
                self.assertEqual(self.owned(arm, 'eye_controls'), expected)

    def test_native_owner_add_remove_rename_and_datablock_changes_are_fresh_next_call(self):
        arm = rig('eye_controls')
        with self.scope.read_scope():
            self.assertEqual([bone.name for bone in self.owned(arm, 'eye_controls')], ['0'])
        arm.data.bones[0].owner = 'root_control'
        arm.data.bones[0].name = 'renamed'
        arm.data.bones.append(Bone('new', 'eye_controls'))
        with self.scope.read_scope():
            self.assertEqual([bone.name for bone in self.owned(arm, 'root_control')], ['renamed'])
            self.assertEqual([bone.name for bone in self.owned(arm, 'eye_controls')], ['new'])
        arm.data = SimpleNamespace(bones=[Bone('replacement', 'eye_controls')])
        with self.scope.read_scope():
            self.assertEqual([bone.name for bone in self.owned(arm, 'eye_controls')], ['replacement'])

    def test_scope_exit_releases_borrowed_rna_on_success_and_exception(self):
        arm = rig('limb_ik')
        for fail in (False, True):
            try:
                with self.scope.read_scope():
                    self.owned(arm, 'limb_ik')
                    self.scope.strict_constraint_records(arm, lambda _arm: [(object(), object(), {})])
                    cache = self.scope._ACTIVE.get()
                    self.assertTrue(cache)
                    if fail:
                        raise ValueError('refused')
            except ValueError:
                pass
            self.assertEqual(cache, {})
            self.assertIsNone(self.scope._ACTIVE.get())

    def test_nested_scope_is_fresh_then_restores_outer_even_on_exception(self):
        arm = rig('limb_ik')
        with self.scope.read_scope():
            self.owned(arm, 'limb_ik')
            outer = self.scope._ACTIVE.get()
            with self.assertRaisesRegex(ValueError, 'nested'):
                with self.scope.read_scope():
                    self.owned(arm, 'limb_ik')
                    inner = self.scope._ACTIVE.get()
                    self.assertIsNot(inner, outer)
                    raise ValueError('nested')
            self.assertEqual(inner, {})
            self.assertIs(self.scope._ACTIVE.get(), outer)
            self.owned(arm, 'limb_ik')
            self.assertEqual(arm.data.bones[0].reads, 2)

    def test_decorator_recreates_scope_for_each_validation(self):
        arm = rig('limb_ik')
        @self.scope.read_scope()
        def validate():
            self.owned(arm, 'limb_ik')
            self.owned(arm, 'limb_ik')
            return self.scope._ACTIVE.get()
        first, second = validate(), validate()
        self.assertIsNot(first, second)
        self.assertEqual(first, {})
        self.assertEqual(second, {})
        self.assertEqual(arm.data.bones[0].reads, 2)

    def test_unrelated_thread_has_no_native_snapshot(self):
        arm = rig('limb_ik')
        observations = Queue()
        def worker():
            observations.put(self.scope._ACTIVE.get())
            self.owned(arm, 'limb_ik')
            self.owned(arm, 'limb_ik')
        with self.scope.read_scope():
            self.owned(arm, 'limb_ik')
            thread = Thread(target=worker)
            thread.start()
            thread.join()
            self.assertIsNone(observations.get_nowait())
            self.owned(arm, 'limb_ik')
        self.assertEqual(arm.data.bones[0].reads, 3)

    def test_successful_discovery_reused_with_fresh_list_and_no_error_cache(self):
        arm, calls = rig(), []
        record = (object(), object(), {'role': 'IK'})
        def discover(_arm):
            calls.append(_arm)
            return [record]
        with self.scope.read_scope():
            first = self.scope.strict_constraint_records(arm, discover)
            first.clear()
            self.assertEqual(self.scope.strict_constraint_records(arm, discover), [record])
            self.assertEqual(calls, [arm])
        self.scope.strict_constraint_records(arm, discover)
        self.assertEqual(len(calls), 2)
        failures = []
        def reject(_arm):
            failures.append(_arm)
            raise ValueError('wrong constraint type')
        with self.scope.read_scope():
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, 'wrong constraint type'):
                    self.scope.strict_constraint_records(arm, reject)
            self.assertEqual(self.scope._ACTIVE.get(), {})
        self.assertEqual(len(failures), 2)

    def test_scope_never_conflates_armatures_or_discovery_functions(self):
        first, second = rig(), rig()
        def a(arm): return [('a', arm)]
        def b(arm): return [('b', arm)]
        with self.scope.read_scope():
            self.assertEqual(self.scope.strict_constraint_records(first, a), [('a', first)])
            self.assertEqual(self.scope.strict_constraint_records(second, a), [('a', second)])
            self.assertEqual(self.scope.strict_constraint_records(first, b), [('b', first)])


class NativeValidatorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.scope = load_scope()

    def test_strict_discovery_body_retains_every_baseline83_check_in_order(self):
        old = deepcopy(node('limb_ik.py', '_scan_owned_constraint_records'))
        old.name = '_owned_constraint_records'
        actual = hashlib.sha256(ast.dump(old, include_attributes=False).encode()).hexdigest()
        # Authenticated original .83 scanner, not a second hand-rewritten scan.
        # Both hashes come from frozen .92's scanner renamed as above: 3.12
        # includes empty AST fields by default; 3.13 omits those same fields.
        self.assertIn(actual, {
            '9ee553064f6b7a11654763794a651d3990b2f8b68240fb525449b16b634a6a3b',
            '1d28fce570f989614db783fde03c849bef781caab954b6f812c2765cb28467c1',
        })

    def scanner(self):
        namespace = {'validation_native': self.scope, 'OWNER_VALUE': 'limb_ik', 'RIG_VERSION': 1,
                     'LimbIKError': ValueError, '_constraint_registry': lambda bone, strict: bone.registry,
                     '_constraint_name': lambda rig_id, role: f'{rig_id}:{role}'}
        function('limb_ik.py', '_scan_owned_constraint_records', namespace)
        return function('limb_ik.py', '_owned_constraint_records', namespace), namespace

    @staticmethod
    def arm():
        con = SimpleNamespace(name='rig:IK', type='IK')
        record = {'owner': 'limb_ik', 'version': 1, 'rig_id': 'rig', 'role': 'IK'}
        bone = SimpleNamespace(name='forearm', registry={'rig:IK': record}, constraints=[con])
        return SimpleNamespace(pose=SimpleNamespace(bones=[bone])), bone, con

    def test_current_registry_constraint_count_name_and_type_refuse_each_new_validation(self):
        discover, _namespace = self.scanner()
        for corrupt, message in (
                (lambda bone, con: bone.registry.update({'other': []}), 'invalid record'),
                (lambda bone, con: bone.registry['rig:IK'].update(owner=[]), 'ownership fingerprint'),
                (lambda bone, con: bone.constraints.clear(), 'missing or duplicated'),
                (lambda bone, con: bone.constraints.append(con), 'missing or duplicated'),
                (lambda bone, con: setattr(con, 'name', 'renamed'), 'missing or duplicated'),
                (lambda bone, con: setattr(con, 'type', 'COPY_ROTATION'), 'changed type')):
            arm, bone, con = self.arm()
            with self.scope.read_scope():
                self.assertEqual(len(discover(arm)), 1)
                self.assertEqual(len(discover(arm)), 1)
            corrupt(bone, con)
            with self.scope.read_scope():
                with self.assertRaisesRegex(ValueError, message):
                    discover(arm)
            self.assertIsNone(self.scope._ACTIVE.get())

    def test_strict_false_and_non_bool_arguments_keep_ordinary_discovery(self):
        discover, namespace = self.scanner()
        arm, bone, con = self.arm()
        calls = []
        original = namespace['_scan_owned_constraint_records']
        def count(*args, **kwargs):
            calls.append(kwargs.get('strict', True))
            return original(*args, **kwargs)
        namespace['_scan_owned_constraint_records'] = count
        with self.scope.read_scope():
            for strict in (False, 1, 'truthy', False, 1):
                discover(arm, strict=strict)
            discover(arm)
            discover(arm)
        self.assertEqual(calls, [False, 1, 'truthy', False, 1, True])

    def test_control_absence_and_orphan_refusals_preserve_owner_handling(self):
        modules = ('foot_controls', 'torso_controls', 'spine_ik_fk', 'eye_controls', 'root_control')
        owners = ('foot_controls', 'torso_controls', 'spine_ik_fk', 'eye_controls', 'root_control')
        for module, owner in zip(modules, owners):
            namespace = {'validation_native': self.scope, 'OWNER_KEY': OWNER_KEY, 'OWNER_VALUE': owner,
                         '_limb': lambda: SimpleNamespace(_validation_source_rest=lambda *_: None),
                         'get_record': lambda *_: None, 'records': lambda *_: {}, '_error': ValueError,
                         'SIDE_KEY': 'side', '_SUSPENDED': set()}
            validate_node = node(module + '.py', 'validate')
            # Torso's local import is irrelevant to the absent-record branch.
            validate_node.body = [item for item in validate_node.body if not isinstance(item, ast.ImportFrom)]
            exec(compile(ast.Module(body=[validate_node], type_ignores=[]), module, 'exec'), namespace)
            for active in (False, True):
                arm = rig([], {}, None, 'unrelated')
                if active:
                    with self.scope.read_scope():
                        namespace['validate'](arm)
                else:
                    namespace['validate'](arm)
                arm.data.bones.append(Bone('orphan', owner))
                # Foot additionally reads SIDE_KEY at its existing filter site.
                arm.data.bones[-1].get = lambda key, own=owner: own if key == OWNER_KEY else 'L'
                with self.assertRaises(ValueError):
                    if active:
                        with self.scope.read_scope():
                            namespace['validate'](arm)
                    else:
                        namespace['validate'](arm)


if __name__ == '__main__':
    unittest.main()
