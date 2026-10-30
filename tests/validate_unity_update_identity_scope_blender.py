"""Small factory-Blender test of stored USER links and foreign NOP holders.

Run with tools/run_trusted_blender_validation.py, never against an artist file.
The selected PropertyGroup field declarations are compiled from product source;
the complete add-on is not imported or registered by this fixture.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback
import unittest
import uuid

import bpy
from bpy.props import CollectionProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / 'addons' / 'character_designer'
SOURCE = ADDON / 'unity_export.py'
KEY = 'character_designer_finger_bank'
RIG, PART, OWNER = ('character_designer_unity_update_' + name
                    for name in ('rig_id', 'part_id', 'part_rig_id'))
SOURCES = [SOURCE, ADDON / 'finger_definition_ui.py', ADDON / 'finger_bank_ui.py']
PINS = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in SOURCES}
REPORT = {'schema': 'x.unity-update-identity-native/1', 'pins': PINS,
          'build_hash': bpy.app.build_hash.decode(), 'version': bpy.app.version_string,
          'factory_startup': True, 'full_addon_imported': False,
          'artist_file_opened': False, 'scene_saved': False, 'preferences_saved': False,
          'unity_operated': False, 'checks': {}, 'errors': []}


class ExportError(ValueError):
    pass


def selected_group(path, name, fields, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
    declarations = [node for node in original.body if isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name) and node.target.id in fields]
    assert {node.target.id for node in declarations} == set(fields)
    node = ast.ClassDef(name=name, bases=[ast.Name(id='PropertyGroup', ctx=ast.Load())],
                        keywords=[], body=declarations, decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(path), 'exec'), namespace)
    return namespace[name]


def metadata(obj):
    bank = getattr(obj, KEY)
    return {'ids': dict(obj), 'data': obj.data.as_pointer(),
            'parent': obj.parent.as_pointer(), 'users': obj.users,
            'collections': [c.as_pointer() for c in obj.users_collection],
            'guides': [(slot.guide.source.as_pointer(), slot.guide.bend_source,
                        slot.guide.pending_source, slot.guide.record, slot.guide.bend_record,
                        slot.guide.pending) for slot in bank.slots]}


class NativeIdentityScope(unittest.TestCase):
    def setUp(self):
        self.objects, self.data, self.collections = [], [], []
        rig_data = bpy.data.armatures.new('X2_identity_fixture_rig_data')
        self.data.append((bpy.data.armatures, rig_data))
        self.rig = self.object('Rig', rig_data)
        self.rig[RIG] = '1' * 32
        self.body = self.mesh('Body')
        self.copy = self.mesh('PreservedCopy')
        for obj in (self.body, self.copy):
            obj[PART], obj[OWNER] = '2' * 32, '1' * 32
            obj.parent = self.rig
        bpy.context.scene.collection.objects.link(self.rig)
        bpy.context.scene.collection.objects.link(self.body)
        self.copy.modifiers.new('Armature', 'ARMATURE').object = self.rig
        self.copy.modifiers.new('Subdivision', 'SUBSURF')
        bank = getattr(self.copy, KEY)
        for index in range(10):
            guide = bank.slots.add().guide
            guide.source, guide.bend_source, guide.pending_source = self.copy, None, None
            guide.record, guide.bend_record, guide.pending = 'invalid-record:' + str(index), '{bad', 'stale'
        self.copy['unread_forearm_record'] = '{intentionally-invalid'
        self.inventory = [dict(object_name=self.body.name, renderer_path='Rig/Body', material_slots=[])]
        self.assertEqual(self.copy.users, 10)
        self.assertEqual(bpy.data.user_map(subset={self.copy}).get(self.copy), {self.copy})

    def object(self, name, data=None):
        obj = bpy.data.objects.new('X2_identity_fixture_' + name, data)
        self.objects.append(obj)
        return obj

    def mesh(self, name):
        data = bpy.data.meshes.new('X2_identity_fixture_' + name + '_mesh')
        self.data.append((bpy.data.meshes, data))
        return self.object(name, data)

    def tearDown(self):
        for obj in reversed(self.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection in self.collections:
            bpy.data.collections.remove(collection)
        for owner, data in reversed(self.data):
            owner.remove(data)

    def plan(self, meshes=None, inventory=None):
        return PRODUCT['_candidate_identity_plan'](self.rig, meshes or [self.body], inventory or self.inventory)

    def validate(self, plan):
        PRODUCT['_validate_candidate_ids'](plan)

    def allowed(self, obj=None):
        return PRODUCT['_inert_update_part_identity_carrier'](
            self.copy if obj is None else obj, self.rig, self.body, '1' * 32, '2' * 32)

    def foreign_driver(self):
        holder = self.object('ForeignDriver')
        holder['value'] = 0.0
        driver = holder.driver_add('["value"]').driver
        variable = driver.variables.new()
        variable.type = 'SINGLE_PROP'
        variable.targets[0].id = self.copy
        variable.targets[0].data_path = 'location.x'
        driver.expression = variable.name
        return holder

    def test_accounted_ten_USER_links_pass_both_stages_without_writes(self):
        before = metadata(self.copy), dict(self.body), dict(self.rig)
        plan = self.plan()
        self.assertEqual(plan['writes'], [])
        self.validate(plan)
        self.assertEqual((metadata(self.copy), dict(self.body), dict(self.rig)), before)
        self.assertEqual(len(getattr(self.copy, KEY).slots), 10)
        REPORT['native_user_evidence'] = {'users': self.copy.users,
            'holders': sorted(obj.name for obj in bpy.data.user_map(subset={self.copy})[self.copy]),
            'self_source_links': 10, 'fake_user': self.copy.use_fake_user,
            'extra_user': self.copy.use_extra_user}

    def test_foreign_driver_NOP_holder_does_not_increment_users_but_refuses(self):
        holder = self.foreign_driver()
        self.assertEqual(self.copy.users, 10)
        self.assertEqual(bpy.data.user_map(subset={self.copy})[self.copy], {self.copy, holder})
        with self.assertRaises(ExportError):
            self.plan()

    def test_late_driver_NOP_holder_is_freshly_refused(self):
        plan = self.plan()
        self.foreign_driver()
        with self.assertRaises(ExportError):
            self.validate(plan)

    def test_unaccounted_counted_self_property_is_refused(self):
        self.copy['unknown_self'] = self.copy
        self.assertEqual(self.copy.users, 11)
        self.assertEqual(bpy.data.user_map(subset={self.copy})[self.copy], {self.copy})
        self.assertFalse(self.allowed())

    def test_scene_membership_is_refused_in_both_stages(self):
        plan = self.plan()
        bpy.context.scene.collection.objects.link(self.copy)
        with self.assertRaises(ExportError):
            self.plan()
        with self.assertRaises(ExportError):
            self.validate(plan)

    def test_unscened_collection_membership_is_refused(self):
        collection = bpy.data.collections.new('X2_identity_fixture_unscened')
        self.collections.append(collection)
        collection.objects.link(self.copy)
        self.assertFalse(self.copy.users_scene)
        self.assertFalse(self.allowed())

    def test_second_actual_Mesh_binding_is_refused(self):
        other = self.object('OtherMeshBinding', self.copy.data)
        self.assertFalse(other.users_scene)
        self.assertFalse(self.allowed())

    def test_unscened_child_parent_NOP_link_is_refused(self):
        child = self.object('Child')
        child.parent = self.copy
        self.assertEqual(self.copy.users, 10)
        self.assertFalse(self.allowed())

    def test_fake_user_is_refused(self):
        self.copy.use_fake_user = True
        self.assertFalse(self.allowed())

    def test_nonself_guide_is_not_recovery_authority(self):
        getattr(self.copy, KEY).slots[0].guide.source = self.body
        self.assertFalse(self.allowed())

    def test_unknown_duplicate_and_duplicate_rig_still_refuse(self):
        plan = self.plan()
        unknown = self.mesh('UnknownDuplicate')
        unknown[PART], unknown[OWNER] = '2' * 32, '1' * 32
        with self.assertRaises(ExportError):
            self.validate(plan)
        del unknown[PART]
        unknown[RIG] = '1' * 32
        with self.assertRaisesRegex(ExportError, 'rig identities'):
            self.plan()

    def test_duplicate_sources_within_export_scope_still_refuse(self):
        inventory = self.inventory + [dict(object_name=self.copy.name,
            renderer_path='Rig/Copy', material_slots=[])]
        with self.assertRaisesRegex(ExportError, 'part identities'):
            self.plan([self.body, self.copy], inventory)

    def test_unset_root_is_not_created(self):
        other = self.mesh('UnsetRoot')
        other[PART], other[OWNER], other.parent = '2' * 32, '1' * 32, self.rig
        self.assertFalse(other.is_property_set(KEY))
        self.assertFalse(self.allowed(other))
        self.assertFalse(other.is_property_set(KEY))

    def test_unset_guide_is_not_created(self):
        slot = getattr(self.copy, KEY).slots.add()
        self.assertFalse(slot.is_property_set('guide'))
        self.assertTrue(self.allowed())
        self.assertFalse(slot.is_property_set('guide'))


def main():
    global PRODUCT
    args = sys.argv[sys.argv.index('--') + 1:]
    output, temporary = map(Path, args)
    assert not output.exists()
    temporary.mkdir(parents=True, exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(temporary)
    REPORT['isolation'] = {'config': os.environ['BLENDER_USER_CONFIG'],
        'TEMP': os.environ['TEMP'], 'TMP': os.environ['TMP'],
        'blender_temp': bpy.app.tempdir,
        'temporary_directory': bpy.context.preferences.filepaths.temporary_directory}
    assert Path(bpy.app.tempdir).resolve().is_relative_to(temporary.resolve())
    assert not hasattr(bpy.types.Object, KEY)
    initial_objects = {obj.as_pointer(): obj.name for obj in bpy.data.objects}
    namespace = dict(__name__=__name__, bpy=bpy, PropertyGroup=PropertyGroup,
        PointerProperty=PointerProperty, CollectionProperty=CollectionProperty, StringProperty=StringProperty)
    classes = [selected_group(ADDON / 'finger_definition_ui.py', 'CharacterDesignerFingerDefinition',
        ('source', 'bend_source', 'pending_source', 'record', 'bend_record', 'pending'), namespace),
        selected_group(ADDON / 'finger_bank_ui.py', 'CharacterDesignerFingerSlot', ('guide',), namespace),
        selected_group(ADDON / 'finger_bank_ui.py', 'CharacterDesignerFingerBank', ('slots',), namespace)]
    registered = []
    started = time.perf_counter()
    success = False
    try:
        for cls in classes:
            bpy.utils.register_class(cls)
            registered.append(cls)
        setattr(bpy.types.Object, KEY, PointerProperty(type=classes[-1]))
        names = {'_inert_update_part_identity_carrier', '_candidate_identity_plan', '_validate_candidate_ids'}
        nodes = [node for node in ast.parse(SOURCE.read_text(encoding='utf-8')).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in nodes} == names
        PRODUCT = dict(bpy=bpy, re=re, uuid=uuid, ExportError=ExportError,
            UPDATE_RIG_ID=RIG, UPDATE_PART_ID=PART, UPDATE_PART_OWNER=OWNER)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), PRODUCT)
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(NativeIdentityScope))
        REPORT.update(tests_run=result.testsRun,
            failures=[(str(case), error) for case, error in result.failures],
            test_errors=[(str(case), error) for case, error in result.errors])
        success = result.wasSuccessful()
    except Exception:
        REPORT['errors'].append(traceback.format_exc())
    finally:
        if hasattr(bpy.types.Object, KEY):
            delattr(bpy.types.Object, KEY)
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
        REPORT['checks']['factory_objects_preserved'] = initial_objects == {
            obj.as_pointer(): obj.name for obj in bpy.data.objects}
        REPORT['checks']['owned_RNA_removed'] = not hasattr(bpy.types.Object, KEY)
        REPORT['checks']['sources_unchanged'] = all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == pin
                                                   for path, pin in PINS.items())
        REPORT['seconds'] = time.perf_counter() - started
        REPORT['completed'] = success and all(REPORT['checks'].values()) and not REPORT['errors']
        output.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not REPORT['completed']:
        raise RuntimeError('Native identity fixture failed; inspect its preserved report.')


if __name__ == '__main__':
    main()
