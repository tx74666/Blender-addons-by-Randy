"""Pure ordering/rollback checks; native move semantics are checked separately."""
import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/bone_collections.py'
GROUP_KEY = 'character_designer_simple_bone_group'
HAIR_KEY = 'character_designer_hair_bones_owner'
DRESS_KEY = 'character_designer_skirt_owner'
SHARED_KEY = 'character_designer_shared_skirts_v1'
SOURCE_KEY = 'character_designer_skirt_source'
RIG_KEY = 'character_designer_skirt_armature'


class Group:
    def __init__(self, name, **properties):
        self.name, self.properties = name, properties
        self.parent = None
        self.members = ('authored bone',)
        self.visible, self.solo, self.expanded = True, False, True

    def get(self, key, default=None):
        return self.properties.get(key, default)

    @property
    def bones(self):
        raise AssertionError('Ordering must not scan bones')


class Roots(list):
    def __init__(self, values):
        super().__init__(values)
        self.active = values[0] if values else None
        self.calls = 0
        self.fail_at = None
        self.fail_after = False
        self.silent = False
        self.disturb_active = False

    def move(self, old, new):
        self.calls += 1
        fail = self.calls == self.fail_at
        if fail and not self.fail_after:
            raise RuntimeError('injected move failure')
        if not self.silent:
            self.insert(new, self.pop(old))
        if self.disturb_active:
            self.active = self[0]
        if fail:
            raise RuntimeError('injected move failure after mutation')


class Rig:
    type, mode, library, is_editable = 'ARMATURE', 'POSE', None, True

    def __init__(self, groups):
        self.properties = {}
        self.data = NS(collections=Roots(groups), library=None)

    def get(self, key, default=None):
        return self.properties.get(key, default)

    @property
    def pose(self):
        raise AssertionError('Ordering must not read the pose')


class Source:
    type = 'MESH'

    def __init__(self, rig, owner):
        self.properties = {DRESS_KEY: owner, RIG_KEY: rig}

    def get(self, key, default=None):
        return self.properties.get(key, default)


def managed(name, role):
    return Group(name, **{GROUP_KEY: role})


def state(rig, groups):
    return tuple((group, group.name, group.parent, group.members, group.visible,
                  group.solo, group.expanded, dict(group.properties)) for group in groups), rig.data.collections.active


class CollectionOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
        node = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == 'sort_managed_collections')
        cls.module = ModuleType('_collection_order_test')
        cls.module.__package__, cls.module.__path__ = cls.module.__name__, []
        cls.module.GROUP_KEY = GROUP_KEY
        hair, skirt = ModuleType(cls.module.__name__ + '.hair_bones_rig'), ModuleType(cls.module.__name__ + '.skirt_rig')
        hair.OWNER_KEY, hair.OWNER_VALUE = HAIR_KEY, 'hair_bones_v1'
        skirt.OWNER_KEY, skirt.SHARED_SOURCES_KEY = DRESS_KEY, SHARED_KEY
        skirt.SOURCE_KEY, skirt.RIG_KEY = SOURCE_KEY, RIG_KEY
        cls.modules = {cls.module.__name__: cls.module, hair.__name__: hair, skirt.__name__: skirt}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), cls.module.__dict__)

    def setUp(self):
        self.patcher = patch.dict(sys.modules, self.modules)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def sort(self, rig):
        return self.module.sort_managed_collections(rig)

    def test_new_hair_goes_before_original_without_body_profile(self):
        original, artist = managed('Original', 'Original'), Group('Artist')
        hair = Group('My Hair Controls', **{HAIR_KEY: 'hair_bones_v1'})
        rig = Rig([original, artist, hair])
        before = state(rig, [original, artist, hair])
        self.assertGreater(self.sort(rig), 0)
        self.assertEqual(list(rig.data.collections), [hair, artist, original])
        self.assertEqual(state(rig, [original, artist, hair]), before)

    def test_artist_roots_keep_relative_order_and_managed_slots(self):
        original, body, hair = managed('Original', 'Original'), managed('Body', 'Body'), managed('Hair', 'Hair')
        artist_a, artist_b, unowned_dress = Group('Artist A'), Group('Artist B'), Group('Dress')
        dress_a, dress_b = Group('Dress.001', **{DRESS_KEY: 'owner_a'}), Group('Dress.002', **{DRESS_KEY: 'owner_b'})
        groups = [original, artist_a, dress_a, hair, artist_b, body, dress_b, unowned_dress]
        rig = Rig(groups)
        rig.properties[SHARED_KEY] = {'owner_a': Source(rig, 'owner_a'), 'owner_b': Source(rig, 'owner_b')}
        before = state(rig, groups)
        self.sort(rig)
        self.assertEqual(list(rig.data.collections), [body, artist_a, hair, dress_a, artist_b,
                                                      dress_b, unowned_dress, original])
        self.assertEqual(state(rig, groups), before)

    def test_plain_names_and_unregistered_dress_owner_are_untouched(self):
        groups = [Group('Original'), Group('Hair'), Group('Body'), Group('Dress', **{DRESS_KEY: 'unknown'})]
        rig = Rig(groups)
        self.assertEqual(self.sort(rig), 0)
        self.assertEqual(list(rig.data.collections), groups)
        self.assertEqual(rig.data.collections.calls, 0)

    def test_unrelated_source_rig_or_owner_is_not_claimed(self):
        original, body = managed('Original', 'Original'), managed('Body', 'Body')
        dress = Group('Dress', **{DRESS_KEY: 'owner'})
        other = Rig([])
        rig = Rig([original, dress, body])
        rig.properties[SHARED_KEY] = {'owner': Source(other, 'owner')}
        self.sort(rig)
        self.assertEqual(list(rig.data.collections), [body, dress, original])
        rig.data.collections[:] = [original, dress, body]
        rig.properties[SHARED_KEY] = {'owner': Source(rig, 'different owner')}
        self.sort(rig)
        self.assertEqual(list(rig.data.collections), [body, dress, original])

    def test_standalone_dress_requires_back_reference(self):
        artist, dress = Group('Artist'), Group('Dress', **{DRESS_KEY: 'owner'})
        body = managed('Body', 'Body')
        rig = Rig([dress, artist, body])
        rig.properties[DRESS_KEY] = 'owner'
        rig.properties[SOURCE_KEY] = Source(rig, 'owner')
        self.sort(rig)
        self.assertEqual(list(rig.data.collections), [body, artist, dress])

    def test_nested_artist_group_and_active_child_are_preserved(self):
        original, body, artist = managed('Original', 'Original'), managed('Body', 'Body'), Group('Artist')
        child = managed('Nested Hair', 'Hair')
        child.parent = artist
        artist.members = ('first', 'second')
        artist.visible, artist.solo, artist.expanded = False, True, False
        rig = Rig([original, artist, body])
        rig.data.collections.active = child
        rig.data.collections.disturb_active = True
        before = state(rig, [original, artist, body, child])
        self.sort(rig)
        self.assertEqual(list(rig.data.collections), [body, artist, original])
        self.assertEqual(state(rig, [original, artist, body, child]), before)

    def test_legacy_animation_marker_and_renamed_original_are_owned(self):
        original, body = managed('Rest Bones', 'Original'), managed('Animation', 'Animation')
        rig = Rig([original, body])
        self.sort(rig)
        self.assertEqual(list(rig.data.collections), [body, original])

    def test_stable_order_is_noop_even_with_active_none(self):
        body, original = managed('Body', 'Body'), managed('Original', 'Original')
        rig = Rig([body, original])
        rig.data.collections.active = None
        self.assertEqual(self.sort(rig), 0)
        self.assertEqual(self.sort(rig), 0)
        self.assertEqual(rig.data.collections.calls, 0)
        self.assertIsNone(rig.data.collections.active)

    def test_second_call_does_not_write(self):
        original, hair, body = managed('Original', 'Original'), managed('Hair', 'Hair'), managed('Body', 'Body')
        rig = Rig([original, hair, body])
        self.sort(rig)
        calls = rig.data.collections.calls
        self.assertEqual(self.sort(rig), 0)
        self.assertEqual(rig.data.collections.calls, calls)

    def test_conflicting_marker_refuses_before_writes(self):
        confused = managed('Confused', 'Body')
        confused.properties[HAIR_KEY] = 'hair_bones_v1'
        rig = Rig([managed('Original', 'Original'), confused])
        before = list(rig.data.collections)
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.sort(rig)
        self.assertEqual(list(rig.data.collections), before)
        self.assertEqual(rig.data.collections.calls, 0)

    def test_duplicate_singleton_role_refuses_before_writes(self):
        for role in ('Body', 'Hair', 'Original'):
            with self.subTest(role=role):
                rig = Rig([managed('First', role), managed('Second', role)])
                with self.assertRaisesRegex(ValueError, 'ambiguous'):
                    self.sort(rig)
                self.assertEqual(rig.data.collections.calls, 0)

    def test_fault_before_and_after_mutation_rolls_back_roots_and_active(self):
        for after in (False, True):
            with self.subTest(after=after):
                original, hair, artist, body = managed('Original', 'Original'), managed('Hair', 'Hair'), Group('Artist'), managed('Body', 'Body')
                groups = [original, hair, artist, body]
                rig = Rig(groups)
                rig.data.collections.active = artist
                rig.data.collections.disturb_active = True
                rig.data.collections.fail_at, rig.data.collections.fail_after = 2, after
                before = state(rig, groups)
                with self.assertRaisesRegex(RuntimeError, 'injected move failure'):
                    self.sort(rig)
                self.assertEqual(list(rig.data.collections), groups)
                self.assertEqual(state(rig, groups), before)

    def test_native_silent_failure_is_detected(self):
        original, body = managed('Original', 'Original'), managed('Body', 'Body')
        rig = Rig([original, body])
        rig.data.collections.silent = True
        with self.assertRaisesRegex(RuntimeError, 'requested root order'):
            self.sort(rig)
        self.assertEqual(list(rig.data.collections), [original, body])

    def test_edit_or_linked_rig_refuses_before_moves(self):
        for field, value in (('mode', 'EDIT'), ('library', object()), ('is_editable', False)):
            with self.subTest(field=field):
                rig = Rig([managed('Original', 'Original'), managed('Body', 'Body')])
                setattr(rig, field, value)
                with self.assertRaises(ValueError):
                    self.sort(rig)
                self.assertEqual(rig.data.collections.calls, 0)


if __name__ == '__main__':
    unittest.main()
