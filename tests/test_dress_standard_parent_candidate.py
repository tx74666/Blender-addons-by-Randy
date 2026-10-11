"""Actual private helper with fake RNA/ID endpoints; no native evaluation proof."""
import ast
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'addons/character_designer/dress_standard_parent_candidate.py'
spec = importlib.util.spec_from_file_location('private_standard_parent_test', SOURCE)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
MATRIX = tuple(tuple(float(i == j) for j in range(4)) for i in range(4))


class ID:
    sequence = 100

    def __init__(self, name, **fields):
        ID.sequence += 1
        self.pointer, self.name = ID.sequence, name
        self.library = self.override_library = None
        self.__dict__.update(fields)

    def as_pointer(self):
        return self.pointer


class Bones(list):
    def __getitem__(self, index):
        return next(bone for bone in self if bone.name == index) if isinstance(index, str) else super().__getitem__(index)


class Bone(ID):
    def __init__(self, name, parent=None):
        super().__init__(name, parent=parent, matrix_local=MATRIX, head_local=(0., 0., 0.),
                         tail_local=(0., 1., 0.), use_connect=False, use_deform=True,
                         inherit_scale='NONE', use_inherit_rotation=False, use_local_location=False)
        self.fault = None
        self.failures_left = 0
        self.mutate_before_error = False
        self.ignore = False
        self.flag_writes = []

    def __setattr__(self, name, value):
        if name in helper.FLAGS and 'flag_writes' in self.__dict__:
            self.flag_writes.append((name, value))
            if name == self.fault:
                if self.ignore:
                    return
                if self.failures_left:
                    if self.failures_left > 0:
                        self.failures_left -= 1
                    if self.mutate_before_error:
                        object.__setattr__(self, name, value)
                    raise RuntimeError('injected RNA setter failure')
        object.__setattr__(self, name, value)


def fixture(count=217):
    source_bones, copied_bones = Bones(), Bones()
    for index in range(count):
        name = 'Bone ' + str(index)
        source_bones.append(Bone(name, source_bones[-1] if source_bones else None))
        copied_bones.append(Bone(name, copied_bones[-1] if copied_bones else None))
    source_data = ID('Artist Data', bones=source_bones, users=1, animation_data=None)
    copied_data = ID('Export Data', bones=copied_bones, users=1, animation_data=None)
    fields = dict(type='ARMATURE', mode='OBJECT', parent=None, animation_data=None, constraints=[])
    source = ID('Artist', data=source_data, pose=NS(bones=[NS(constraints=[]) for _ in source_bones]), **fields)
    copied = ID('Export', data=copied_data, pose=NS(bones=[NS(constraints=[]) for _ in copied_bones]), **fields)
    original_ids = {'objects': {source.pointer}, 'armatures': {source_data.pointer}}
    registry = NS(objects={source.name: source, copied.name: copied},
                  armatures={source_data.name: source_data, copied_data.name: copied_data},
                  user_map=lambda **_: {copied_data: {copied}})
    return NS(bpy=NS(app=NS(background=True), data=registry), source=source, copied=copied,
              originals=original_ids, names=[bone.name for bone in copied_bones])


def run(f):
    return helper.standardize_export_copy(f.bpy, f.source, f.copied, f.originals, f.names)


class StandardParentTests(unittest.TestCase):
    def test_217_flags_only_and_artist_unchanged(self):
        f = fixture()
        original, before = helper._state(f.source), helper._state(f.copied)
        receipt = run(f)
        self.assertEqual(receipt['bone_count'], 217)
        self.assertEqual(len(receipt['original_flags']), 217)
        self.assertEqual(len(receipt['standard_flags']), 217)
        self.assertEqual(helper._state(f.source), original)
        self.assertTrue(all(not bone.flag_writes for bone in f.source.data.bones))
        self.assertEqual(helper._geometry(helper._state(f.copied)), helper._geometry(before))
        self.assertTrue(all(row['flags'] == helper.STANDARD for row in helper._state(f.copied).values()))
        for key in ('TRS_projection_verified', 'source_Manual_equivalent', 'FBX_verified',
                    'Unity_verified', 'export_authorized'):
            self.assertIs(receipt[key], False)

    def test_original_or_shared_native_id_refused_before_first_write(self):
        mutations = [lambda f: setattr(f, 'copied', f.source),
                     lambda f: setattr(f.copied, 'data', f.source.data),
                     lambda f: f.originals['objects'].add(f.copied.pointer),
                     lambda f: f.originals['armatures'].add(f.copied.data.pointer),
                     lambda f: setattr(f.copied.data.bones[0], 'pointer', f.source.data.bones[0].pointer)]
        for mutate in mutations:
            f = fixture(3); mutate(f)
            with self.subTest(mutate=mutate), self.assertRaises(helper.StandardParentCandidateError):
                run(f)
            self.assertTrue(all(not bone.flag_writes for bone in f.copied.data.bones))

    def test_unregistered_linked_dirty_execution_context_or_external_users_refused(self):
        mutations = [lambda f: setattr(f.bpy.app, 'background', False),
                     lambda f: f.bpy.data.objects.pop(f.copied.name),
                     lambda f: setattr(f.copied.data, 'library', object()),
                     lambda f: setattr(f.copied, 'mode', 'EDIT'),
                     lambda f: setattr(f.copied, 'parent', object()),
                     lambda f: setattr(f.copied, 'animation_data', object()),
                     lambda f: f.copied.pose.bones[0].constraints.append(object()),
                     lambda f: setattr(f.copied.data, 'users', 2),
                     lambda f: setattr(f.bpy.data, 'user_map', lambda **_: {f.copied.data: {f.source, f.copied}})]
        for mutate in mutations:
            f = fixture(3); mutate(f)
            with self.subTest(mutate=mutate), self.assertRaises(helper.StandardParentCandidateError):
                run(f)
            self.assertTrue(all(not bone.flag_writes for bone in f.copied.data.bones))

    def test_incomplete_inventory_changed_rest_or_parent_refused(self):
        for mutate in (lambda f: f.names.pop(), lambda f: f.names.append(f.names[0]),
                       lambda f: setattr(f.copied.data.bones[1], 'parent', None),
                       lambda f: setattr(f.copied.data.bones[1], 'matrix_local', ((2., 0., 0., 0.),) + MATRIX[1:])):
            f = fixture(3); mutate(f)
            with self.subTest(mutate=mutate), self.assertRaises(helper.StandardParentCandidateError):
                run(f)
            self.assertTrue(all(not bone.flag_writes for bone in f.copied.data.bones))

    def test_first_middle_last_each_setter_failure_restores_every_flag(self):
        for index in (0, 1, 2):
            for field in helper.FLAGS:
                for after_mutation in (False, True):
                    f = fixture(3)
                    original, before = helper._state(f.source), helper._state(f.copied)
                    failing = f.copied.data.bones[index]
                    failing.fault, failing.failures_left = field, 1
                    failing.mutate_before_error = after_mutation
                    with self.subTest(index=index, field=field, after=after_mutation):
                        with self.assertRaises(helper.StandardParentCandidateError) as error:
                            run(f)
                        self.assertEqual(error.exception.rollback_errors, ())
                        self.assertEqual(helper._state(f.copied), before)
                        self.assertEqual(helper._state(f.source), original)

    def test_head_tail_or_deform_mismatch_refused_before_any_flag_write(self):
        for field, value in (('head_local', (0., .1, 0.)), ('tail_local', (0., 2., 0.)),
                             ('use_deform', False)):
            f = fixture(3)
            setattr(f.copied.data.bones[1], field, value)
            with self.subTest(field=field), self.assertRaises(helper.StandardParentCandidateError):
                run(f)
            self.assertTrue(all(not bone.flag_writes for bone in f.copied.data.bones))

    def test_silent_write_failure_also_restores_flags(self):
        f = fixture(3); before = helper._state(f.copied)
        f.copied.data.bones[1].fault = 'use_local_location'
        f.copied.data.bones[1].ignore = True
        with self.assertRaises(helper.StandardParentCandidateError):
            run(f)
        self.assertEqual(helper._state(f.copied), before)

    def test_rollback_error_is_reported_separately(self):
        f = fixture(3)
        f.copied.data.bones[1].fault = 'inherit_scale'
        f.copied.data.bones[1].failures_left = -1
        with self.assertRaises(helper.StandardParentCandidateError) as error:
            run(f)
        self.assertTrue(error.exception.rollback_errors)
        self.assertIn('Bone 1.inherit_scale', str(error.exception))
        self.assertTrue(all(not bone.flag_writes for bone in f.source.data.bones))

    def test_filtered_source_parent_matches_nearest_retained_parent(self):
        f = fixture(3)
        removed = Bone('MCH', f.source.data.bones[0])
        f.source.data.bones[1].parent = removed
        f.source.data.bones.append(removed)
        self.assertEqual(run(f)['bone_count'], 3)

    def test_private_stdlib_import_and_existing_writer_gates_unchanged(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        self.assertEqual({alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names}, {'math'})
        self.assertFalse(any(isinstance(node, ast.ImportFrom) for node in tree.body))
        self.assertNotIn('register', {node.name for node in tree.body if isinstance(node, ast.FunctionDef)})
        worker = ast.parse((SOURCE.parent / 'animation_export_worker.py').read_text(encoding='utf-8'))
        tolerance = next(node.value.value for node in worker.body if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name) and target.id == 'MATRIX_TOLERANCE' for target in node.targets))
        self.assertEqual(tolerance, 2e-5)
        self.assertTrue(any(isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                            and node.left.id == 'error' and any(isinstance(v, ast.Constant) and v.value == 2e-4
                            for v in node.comparators) for node in ast.walk(worker)))


if __name__ == '__main__':
    unittest.main()
