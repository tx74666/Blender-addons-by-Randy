"""Export identity boundaries, using the real planning/revalidation functions."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/unity_export.py'
RIG, PART, OWNER = ('character_designer_unity_update_' + name
                    for name in ('rig_id', 'part_id', 'part_rig_id'))


class ExportError(ValueError):
    pass


def prop(kind, name):
    return SimpleNamespace(type=kind, fixed_type=SimpleNamespace(identifier=name))


class Group:
    def __init__(self, owner, schema, **values):
        self.id_data = owner
        self.bl_rna = SimpleNamespace(properties=schema)
        self.values = values
        self.reads = []

    def is_property_set(self, name):
        return name in self.values

    def __getattr__(self, name):
        self.reads.append(name)
        if name not in self.values:
            raise AssertionError('An unset or unrelated field getter was accessed: ' + name)
        return self.values[name]


class ID(dict):
    # Blender IDs compare by identity, not by their custom-property dictionaries.
    __hash__ = object.__hash__
    __eq__ = object.__eq__
    __ne__ = object.__ne__


class Data:
    def __init__(self):
        self.library = self.override_library = None
        self.is_editable = True


class Object(ID):
    def __init__(self, name, kind='MESH', data=None):
        super().__init__()
        self.name, self.type = name, kind
        self.data = data if data is not None else Data()
        self.library = self.override_library = None
        self.is_editable = True
        self.use_fake_user = self.use_extra_user = False
        self.users_scene, self.users_collection, self.children = [], [], []
        self.instance_type, self.instance_collection, self.parent = 'NONE', None, None
        self.material_slots = []
        self.users = 0
        self.bl_rna = SimpleNamespace(properties={})
        self.groups, self.group_reads = {}, []

    def is_property_set(self, name):
        return name in self.groups

    def __getattr__(self, name):
        self.group_reads.append(name)
        if name not in self.groups:
            raise AssertionError('Unset Object PropertyGroup getter accessed: ' + name)
        return self.groups[name]


class IdentityScopeTests(unittest.TestCase):
    def setUp(self):
        self.rig = Object('Rig', 'ARMATURE')
        self.rig[RIG] = '1' * 32
        self.body = Object('Body')
        self.body.update({PART: '2' * 32, OWNER: '1' * 32})
        self.copy = Object('Preserved copy')
        self.copy.update(self.body)
        self.copy.parent = self.rig
        self.copy.users = 10
        self.key = 'character_designer_finger_bank'
        self.copy.bl_rna.properties[self.key] = prop('POINTER', 'CharacterDesignerFingerBank')
        self.guides = []
        slots = []
        for _ in range(10):
            guide = Group(self.copy, {field: prop('POINTER', 'Object')
                for field in ('source', 'bend_source', 'pending_source')}, source=self.copy,
                bend_source=None, pending_source=None)
            self.guides.append(guide)
            slots.append(Group(self.copy, {'guide': prop('POINTER', 'CharacterDesignerFingerDefinition')}, guide=guide))
        self.bank = Group(self.copy, {'slots': prop('COLLECTION', 'CharacterDesignerFingerSlot')}, slots=slots)
        self.copy.groups[self.key] = self.bank
        self.objects = [self.rig, self.body, self.copy]
        self.holders = {self.copy}
        self.user_map_calls = 0

        def user_map(*, subset):
            self.assertEqual(subset, {self.copy})
            self.user_map_calls += 1
            return {self.copy: set(self.holders)}

        self.scope = dict(bpy=SimpleNamespace(data=SimpleNamespace(objects=self.objects, user_map=user_map)),
            re=re, uuid=uuid, ExportError=ExportError,
            UPDATE_RIG_ID=RIG, UPDATE_PART_ID=PART, UPDATE_PART_OWNER=OWNER)
        names = {'_inert_update_part_identity_carrier', '_candidate_identity_plan', '_validate_candidate_ids'}
        nodes = [n for n in ast.parse(SOURCE.read_text(encoding='utf-8')).body
                 if isinstance(n, ast.FunctionDef) and n.name in names]
        self.assertEqual({n.name for n in nodes}, names)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.scope)

    def allowed(self):
        return self.scope['_inert_update_part_identity_carrier'](
            self.copy, self.rig, self.body, '1' * 32, '2' * 32)

    def plan(self):
        return self.scope['_candidate_identity_plan'](self.rig, [self.body], [
            dict(object_name='Body', renderer_path='Rig/Body', material_slots=[])])

    def validate(self, plan):
        return self.scope['_validate_candidate_ids'](plan)

    def test_accounted_stored_self_refs_pass_both_stages_without_ID_writes(self):
        before = [(obj, dict(obj)) for obj in self.objects]
        plan = self.plan()
        self.assertEqual(plan['writes'], [])
        self.validate(plan)
        self.assertEqual(self.user_map_calls, 2)
        self.assertEqual([(obj, dict(obj)) for obj in self.objects], before)
        for guide in self.guides:
            self.assertEqual(set(guide.reads), {'source', 'bend_source', 'pending_source'})

    def test_foreign_nonrefcounting_holder_is_refused_with_users_unchanged(self):
        self.holders.add(Object('Foreign driver owner'))
        self.assertEqual(self.copy.users, 10)
        with self.assertRaises(ExportError):
            self.plan()

    def test_late_foreign_holder_is_freshly_refused_before_publication(self):
        plan = self.plan()
        self.holders.add(Object('Late foreign holder'))
        with self.assertRaises(ExportError):
            self.validate(plan)

    def test_scene_collection_children_and_instance_roles_are_refused(self):
        for name, value in [('users_scene', [object()]), ('users_collection', [object()]),
                            ('children', [Object('Unscened child')]),
                            ('instance_type', 'COLLECTION'), ('instance_collection', object())]:
            with self.subTest(name=name):
                old = getattr(self.copy, name)
                setattr(self.copy, name, value)
                self.assertFalse(self.allowed())
                setattr(self.copy, name, old)

    def test_another_actual_Mesh_binding_is_refused_even_when_unscened(self):
        self.objects.append(Object('Other Mesh binding', data=self.copy.data))
        self.assertFalse(self.allowed())

    def test_fake_extra_unknown_count_and_zero_count_remain_refused(self):
        for name, value in [('use_fake_user', True), ('use_extra_user', True), ('users', 11), ('users', 0)]:
            with self.subTest(name=name, value=value):
                old = getattr(self.copy, name)
                setattr(self.copy, name, value)
                self.assertFalse(self.allowed())
                setattr(self.copy, name, old)

    def test_unset_root_and_guide_do_not_create_default_groups(self):
        self.copy.groups.clear()
        self.assertFalse(self.allowed())
        self.assertEqual(self.copy.group_reads, [])
        self.copy.groups[self.key] = self.bank
        self.bank.values['slots'][0].values.clear()
        self.assertFalse(self.allowed())
        self.assertEqual(self.bank.values['slots'][0].reads, [])

    def test_foreign_schema_or_foreign_group_owner_is_refused(self):
        self.copy.bl_rna.properties[self.key] = prop('POINTER', 'UnknownBank')
        self.assertFalse(self.allowed())
        self.copy.bl_rna.properties[self.key] = prop('POINTER', 'CharacterDesignerFingerBank')
        self.guides[0].id_data = self.body
        self.assertFalse(self.allowed())

    def test_nonself_guide_pointer_is_not_history_authority(self):
        self.guides[0].values['source'] = self.body
        self.copy.users = 9
        self.assertFalse(self.allowed())

    def test_wrong_part_owner_parent_or_linked_data_is_refused(self):
        self.copy[OWNER] = '3' * 32
        self.assertFalse(self.allowed())
        self.copy[OWNER] = '1' * 32
        self.copy.parent = None
        self.assertFalse(self.allowed())
        self.copy.parent = self.rig
        self.copy.data.library = object()
        self.assertFalse(self.allowed())

    def test_new_unknown_duplicate_after_planning_is_still_refused(self):
        plan = self.plan()
        unknown = Object('Late unknown duplicate')
        unknown.update(self.body)
        self.objects.append(unknown)
        with self.assertRaises(ExportError):
            self.validate(plan)

    def test_duplicate_rig_guard_is_not_exempted(self):
        other = Object('Unscened duplicate rig', 'ARMATURE')
        other[RIG] = self.rig[RIG]
        self.objects.insert(0, other)
        with self.assertRaisesRegex(ExportError, 'rig identities'):
            self.plan()

    def test_selected_source_changes_are_still_refused(self):
        plan = self.plan()
        self.body[PART] = '4' * 32
        with self.assertRaisesRegex(ExportError, 'part changed'):
            self.validate(plan)


if __name__ == '__main__':
    unittest.main()
