"""Captured copy ownership boundaries, without mocking native geometry."""
import types
import unittest

from test_skirt_surface_contract import load


class Key:
    def __init__(self, name, pointer):
        self.name, self.pointer = name, pointer
        self.library, self.override_library, self.users = None, None, 2

    def as_pointer(self): return self.pointer


class Mesh:
    def __init__(self, name, key):
        self.name, self.shape_keys, self.users = name, key, 0
        self.library, self.override_library = None, None


class CapturedKeyCleanup(unittest.TestCase):
    def setUp(self):
        self.s = load()
        self.artist = Key("Artist Key", 1)
        self.copied = Key("Copied Key", 2)
        self.original = Mesh("Artist Mesh", self.artist)
        self.mesh = Mesh("Copied Mesh", self.copied)
        self.keys = {key.name: key for key in (self.artist, self.copied)}
        self.references = {self.artist: {self.original}, self.copied: {self.mesh}}
        self.removed = []

        def remove(*, ids):
            self.removed.extend(ids)
            for block in ids:
                if isinstance(block, Key): self.keys.pop(block.name)

        self.s["bpy"].data = types.SimpleNamespace(
            shape_keys=types.SimpleNamespace(get=self.keys.get),
            user_map=lambda subset: {key: self.references.get(key, set()) for key in subset},
            batch_remove=remove)
        self.tx = self.s["_Transaction"].__new__(self.s["_Transaction"])
        self.tx.objects, self.tx.data, self.tx.copied_keys = [], [], []

    def copy(self, *, fail_assignment=False):
        original, mesh = self.original, self.mesh

        class Object:
            name = "Copied Object"
            def __init__(self): self._data, self.modifiers = original, []
            @property
            def data(self): return self._data
            @data.setter
            def data(self, value):
                if fail_assignment: raise RuntimeError("assignment failed")
                self._data = value

        clone = Object()
        original.copy = lambda: mesh
        source = types.SimpleNamespace(data=original, copy=lambda: clone)
        collection = types.SimpleNamespace(objects=types.SimpleNamespace(link=lambda obj: None))
        return self.tx.copy(source, "Copied Object", collection)

    def test_assignment_failure_captures_only_independent_key(self):
        with self.assertRaisesRegex(RuntimeError, "assignment failed"):
            self.copy(fail_assignment=True)
        self.assertEqual(self.tx.copied_keys, [(self.mesh, "Copied Key", 2)])
        self.assertNotIn(self.original, self.tx.data)
        self.assertEqual(self.removed, [])

    def test_detached_phantom_user_key_is_removed_by_exact_receipt(self):
        self.copy()
        self.mesh.shape_keys = None
        self.references[self.copied] = set()
        self.tx.rollback_copied_keys()
        self.assertEqual(self.removed, [self.copied])
        self.assertIs(self.keys["Artist Key"], self.artist)
        self.assertEqual(self.copied.users, 2)
        self.assertEqual(self.tx.copied_keys, [])

    def test_full_rollback_releases_object_and_mesh_before_detached_key(self):
        clone = self.copy()
        self.mesh.shape_keys = None
        self.references[self.copied] = set()
        objects = {clone.name: clone}
        self.s["bpy"].data.objects = types.SimpleNamespace(
            get=objects.get, remove=lambda obj, do_unlink: objects.pop(obj.name))
        self.tx.targets, self.tx.flags, self.tx.nodes, self.tx.collections = [], [], [], []
        self.tx.overlay, self.tx.raw, self.tx.source = None, None, {}
        self.s["skirt"].RECORD_KEY = "record"
        self.tx.rollback()
        self.assertEqual(objects, {})
        self.assertEqual(self.removed, [self.mesh, self.copied])
        self.assertIs(self.keys["Artist Key"], self.artist)

    def test_outside_user_and_replacement_are_preserved(self):
        self.copy()
        self.mesh.shape_keys = None
        self.references[self.copied] = {self.original}
        with self.assertRaisesRegex(ValueError, "rollback needs recovery"):
            self.tx.rollback_copied_keys()
        self.assertEqual(self.removed, [])
        self.references[self.copied] = set()
        replacement = Key("Copied Key", 3)
        self.keys["Copied Key"] = replacement
        with self.assertRaisesRegex(ValueError, "was preserved"):
            self.tx.rollback_copied_keys()
        self.assertIs(self.keys["Copied Key"], replacement)
        self.assertEqual(self.removed, [])

    def test_successful_clear_forgets_receipt_before_name_reuse(self):
        self.copy()
        self.keys.pop("Copied Key")
        self.mesh.shape_keys = None
        self.tx.forget_copied_key(self.mesh)
        replacement = Key("Copied Key", 3)
        self.keys["Copied Key"] = replacement
        self.tx.rollback_copied_keys()
        self.assertIs(self.keys["Copied Key"], replacement)
        self.assertEqual(self.removed, [])

    def test_shared_artist_key_is_never_captured_or_removed(self):
        self.mesh.shape_keys = self.artist
        with self.assertRaisesRegex(ValueError, "independent local Key"):
            self.copy()
        self.assertEqual(self.tx.copied_keys, [])
        self.tx.rollback_copied_keys()
        self.assertEqual(self.removed, [])
        self.assertIs(self.original.shape_keys, self.artist)


if __name__ == "__main__": unittest.main()
