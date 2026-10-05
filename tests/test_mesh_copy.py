"""Proved copied-Key cleanup, including Blender 5.1's phantom user count."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

PATH = Path(__file__).resolve().parents[1] / 'addons/character_designer/mesh_copy.py'


class Key:
    def __init__(self, name, pointer):
        self.name, self.pointer = name, pointer
        self.library = self.override_library = None
        self.users = 2
    def as_pointer(self): return self.pointer


class Mesh:
    def __init__(self, key):
        self.shape_keys = key
        self.users = 1
        self.library = self.override_library = None


class CopiedKeys(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(PATH.read_text(encoding='utf-8'))
        namespace = {}
        exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)],
                                type_ignores=[]), str(PATH), 'exec'), namespace)
        self.clear = namespace['clear_copied_shape_keys']
        self.original, self.copied = Key('Artist', 1), Key('Copy', 2)
        self.source = SimpleNamespace(data=Mesh(self.original))
        self.mesh = Mesh(self.copied)
        self.keys = {'Artist': self.original, 'Copy': self.copied}
        self.native = {self.copied: {self.mesh}}
        self.calls = []
        self.copy = SimpleNamespace(data=self.mesh, shape_key_clear=self.native_clear)
        namespace['bpy'] = SimpleNamespace(data=SimpleNamespace(
            shape_keys=SimpleNamespace(get=self.keys.get), user_map=lambda subset: self.native,
            batch_remove=self.remove))
    def native_clear(self):
        self.calls.append('clear')
        self.mesh.shape_keys = None
        self.copied.users = 1  # Native 5.1 does not decrement this extra count.
        self.native[self.copied] = set()
    def remove(self, ids):
        self.calls.append(('remove', ids))
        for key in ids: del self.keys[key.name]
    def assert_refused(self):
        with self.assertRaises(ValueError): self.clear(self.source, self.copy)
        self.assertEqual(self.calls, [])
        self.assertIs(self.source.data.shape_keys, self.original)
    def test_phantom_count_without_native_users_removed_exactly(self):
        self.clear(self.source, self.copy)
        self.assertEqual(self.calls, ['clear', ('remove', (self.copied,))])
        self.assertEqual(self.keys, {'Artist': self.original})
    def test_native_clear_already_deleted_id(self):
        self.copy.shape_key_clear = lambda: (self.native_clear(), self.keys.pop('Copy'))
        self.clear(self.source, self.copy)
        self.assertEqual(self.calls, ['clear'])
    def test_no_keys_does_nothing(self):
        self.mesh.shape_keys = None
        self.clear(self.source, self.copy)
        self.assertEqual(self.calls, [])
    def test_shared_source_mesh_refused(self):
        self.copy.data = self.source.data
        self.assert_refused()
    def test_shared_source_key_refused(self):
        self.mesh.shape_keys = self.original
        self.assert_refused()
    def test_outside_native_key_user_refused(self):
        self.native[self.copied].add(self.source.data)
        self.assert_refused()
    def test_multiuser_mesh_refused(self):
        self.mesh.users = 2
        self.assert_refused()
    def test_linked_key_refused(self):
        self.copied.library = object()
        self.assert_refused()
    def test_outside_user_after_clear_refused_without_delete(self):
        def clear():
            self.native_clear()
            self.native[self.copied].add(self.source.data)
        self.copy.shape_key_clear = clear
        with self.assertRaises(ValueError): self.clear(self.source, self.copy)
        self.assertEqual(self.calls, ['clear'])
        self.assertIs(self.keys['Copy'], self.copied)
    def test_same_name_replaced_id_after_clear_refused(self):
        def clear():
            self.native_clear()
            self.keys['Copy'] = Key('Copy', 99)
        self.copy.shape_key_clear = clear
        with self.assertRaises(ValueError): self.clear(self.source, self.copy)
        self.assertEqual(self.calls, ['clear'])
        self.assertEqual(self.keys['Copy'].as_pointer(), 99)


if __name__ == '__main__': unittest.main()
