"""Light failure-path tests for the actual worker, without importing Blender.

Fake native ID references model copied-Key phantom users and outside users.
The companion Blender fixture checks the same failures against native IDs.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


PATH = Path(__file__).resolve().parents[1] / 'addons/character_designer/unity_export_worker.py'


class IDs:
    def __init__(self):
        self.blocks = []

    def get(self, name):
        return next((block for block in self.blocks if block.name == name), None)

    def remove(self, block, **_kwargs):
        self.blocks.remove(block)


class ID:
    library = override_library = None

    def __init__(self, name):
        self.name = name

    def as_pointer(self):
        return id(self)


class Key(ID):
    users = 2  # The native phantom count does not prove outside references.

    def __init__(self, name):
        super().__init__(name)
        self.animation = 'Artist Action'


class Mesh(ID):
    def __init__(self, native, name, key):
        super().__init__(name)
        self.native, self.shape_keys = native, key

    @property
    def users(self):
        return sum(obj.data is self for obj in self.native.objects.blocks)

    def copy(self):
        self.native.fail('copy_mesh')
        key = self.shape_keys if self.native.failure == 'shared_key' else Key('Copied Key')
        if key is not self.shape_keys:
            self.native.shape_keys.blocks.append(key)
        data = Mesh(self.native, 'Copied Mesh', key)
        self.native.meshes.blocks.append(data)
        return data


class Items(list):
    def __init__(self, native, stage, items):
        super().__init__(items)
        self.native, self.stage = native, stage

    def remove(self, item):
        if self.native.failure == 'reused_cleared_name' and self.stage == 'strip_modifier':
            self.native.shape_keys.blocks.append(Key(self.native.removed_keys[-1].name))
            raise RuntimeError('Injected reused_cleared_name')
        self.native.fail(self.stage)
        super().remove(item)


class Object(ID):
    def __init__(self, native, name, data, *, copied=False):
        super().__init__(name)
        self.native, self._data, self.copied = native, data, copied
        self.properties = {'character_designer_owner': 'Artist'}
        self.modifiers = Items(native, 'strip_modifier', [SimpleNamespace(type='ARMATURE', show_viewport=False, name='Rig')])
        self.constraints = Items(native, 'strip_constraint', [SimpleNamespace()])

    @property
    def data(self):
        return self._data

    @data.setter
    def data(self, value):
        if self.copied:
            self.native.fail('assign_mesh')
        self._data = value

    def copy(self):
        self.native.fail('copy_object')
        duplicate = Object(self.native, 'Copied Object', self.data, copied=True)
        self.native.objects.blocks.append(duplicate)
        return duplicate

    def keys(self):
        return self.properties.keys()

    def __delitem__(self, name):
        del self.properties[name]

    def shape_key_clear(self):
        self.data.shape_keys = None


class Native:
    def __init__(self, failure):
        self.failure = failure
        self.objects, self.meshes, self.shape_keys = IDs(), IDs(), IDs()
        self.outside = {}
        self.clear_writes, self.removed_keys = [], []
        self.artist_key = Key('Artist Key')
        self.artist_mesh = Mesh(self, 'Artist Mesh', self.artist_key)
        self.source = Object(self, 'Artist Body', self.artist_mesh)
        self.objects.blocks.append(self.source)
        self.meshes.blocks.append(self.artist_mesh)
        self.shape_keys.blocks.append(self.artist_key)

    def fail(self, stage):
        if self.failure == stage:
            raise RuntimeError('Injected ' + stage)

    def user_map(self, subset):
        return {key: {data for data in self.meshes.blocks if data.shape_keys is key} | self.outside.get(key, set())
                for key in subset}

    def batch_remove(self, ids):
        for key in ids:
            self.shape_keys.remove(key)
            self.removed_keys.append(key)

    def link(self, _obj):
        self.fail('link_object')

    def clear_animation(self, block):
        self.fail('clear_animation')
        if isinstance(block, Key):
            self.clear_writes.append(block)
            block.animation = None

    def load_helper(self, module):
        self.fail('helper_import')

        def clear(_source, duplicate):
            self.fail('before_clear')
            key = duplicate.data.shape_keys
            duplicate.shape_key_clear()
            if self.failure == 'outside_key':
                self.outside[key] = {self.source}
            if self.failure == 'replaced_key':
                self.shape_keys.remove(key)
                self.shape_keys.blocks.append(Key(key.name))
            self.fail('after_detach')
            if self.failure in ('outside_key', 'replaced_key'):
                raise RuntimeError('Injected ' + self.failure)
            self.batch_remove((key,))
            self.fail('after_clear')

        module.clear_copied_shape_keys = clear

    def inventory(self):
        return tuple(tuple((block.name, block.as_pointer()) for block in collection.blocks)
                     for collection in (self.objects, self.meshes, self.shape_keys))


class EvaluationCleanup(unittest.TestCase):
    def runtime(self, native):
        tree = ast.parse(PATH.read_text(encoding='utf-8'))
        names = {'ExportError', '_dispose_evaluation_copy', '_bake_mesh'}
        declarations = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
        scope = {'__file__': str(PATH), 'Path': Path, 'SUPPORTED_MODIFIERS': set(),
                 'bpy': SimpleNamespace(data=native), '_shape_inputs': lambda *_: ([], []),
                 '_clear_animation': native.clear_animation, '_warn': lambda *_: None,
                 'importlib': SimpleNamespace(util=SimpleNamespace(
                     spec_from_file_location=lambda *_: SimpleNamespace(loader=SimpleNamespace(exec_module=native.load_helper)),
                     module_from_spec=lambda *_: SimpleNamespace()))}
        exec(compile(ast.Module(body=declarations, type_ignores=[]), str(PATH), 'exec'), scope)
        return scope

    def bake(self, native, scope):
        context = SimpleNamespace(scene=SimpleNamespace(collection=SimpleNamespace(objects=SimpleNamespace(link=native.link))))
        return scope['_bake_mesh'](context, native.source, [], [])

    def assert_artist(self, native):
        self.assertIs(native.source.data, native.artist_mesh)
        self.assertIs(native.artist_mesh.shape_keys, native.artist_key)
        self.assertEqual(native.artist_key.animation, 'Artist Action')
        self.assertNotIn(native.artist_key, native.clear_writes)
        self.assertNotIn(native.artist_key, native.removed_keys)

    def test_all_early_failures_restore_exact_object_mesh_key_inventory(self):
        stages = ('copy_object', 'copy_mesh', 'assign_mesh', 'link_object', 'clear_animation',
                  'helper_import', 'before_clear', 'after_detach', 'after_clear', 'strip_modifier', 'strip_constraint')
        for stage in stages:
            with self.subTest(stage=stage):
                native = Native(stage)
                scope, before = self.runtime(native), native.inventory()
                with self.assertRaisesRegex(RuntimeError, 'Injected ' + stage):
                    self.bake(native, scope)
                self.assertEqual(native.inventory(), before)
                self.assert_artist(native)

    def test_shared_artist_key_is_refused_before_animation_or_key_writes(self):
        native = Native('shared_key')
        scope, before = self.runtime(native), native.inventory()
        with self.assertRaisesRegex(scope['ExportError'], 'independent local Key'):
            self.bake(native, scope)
        self.assertEqual(native.inventory(), before)
        self.assert_artist(native)

    def test_outside_native_key_user_is_preserved_and_cleanup_error_reported(self):
        native = Native('outside_key')
        scope = self.runtime(native)
        with self.assertRaisesRegex(scope['ExportError'], 'cleanup needs recovery.*outside user'):
            self.bake(native, scope)
        self.assertEqual(native.objects.blocks, [native.source])
        self.assertEqual(native.meshes.blocks, [native.artist_mesh])
        self.assertEqual(len(native.shape_keys.blocks), 2)
        self.assertEqual(native.removed_keys, [])
        self.assert_artist(native)

    def test_same_name_replacement_key_is_preserved(self):
        native = Native('replaced_key')
        scope = self.runtime(native)
        with self.assertRaisesRegex(scope['ExportError'], 'cleanup needs recovery.*Key changed'):
            self.bake(native, scope)
        self.assertEqual(native.objects.blocks, [native.source])
        self.assertEqual(native.meshes.blocks, [native.artist_mesh])
        self.assertEqual(len(native.shape_keys.blocks), 2)
        self.assertEqual(native.removed_keys, [])
        self.assert_artist(native)

    def test_name_reuse_after_successful_clear_is_outside_completed_key_ownership(self):
        native = Native('reused_cleared_name')
        scope = self.runtime(native)
        with self.assertRaisesRegex(RuntimeError, 'Injected reused_cleared_name'):
            self.bake(native, scope)
        self.assertEqual(native.objects.blocks, [native.source])
        self.assertEqual(native.meshes.blocks, [native.artist_mesh])
        self.assertEqual(len(native.shape_keys.blocks), 2)
        replacement = native.shape_keys.get(native.removed_keys[0].name)
        self.assertIsNotNone(replacement)
        self.assertNotIn(replacement, native.removed_keys)
        self.assert_artist(native)


if __name__ == '__main__':
    unittest.main()
