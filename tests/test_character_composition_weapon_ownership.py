"""Read-only canonical weapon ownership helpers, native ID boundaries mocked."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

CANONICAL = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


class ID(dict):
    __hash__ = object.__hash__


class Collection(ID):
    def __init__(self):
        super().__init__()
        self.children, self.objects, self.users = [], [], 2


class Scene(ID):
    def __init__(self):
        super().__init__()
        self.collection = Collection()


class Object(ID):
    def __init__(self, collection):
        super().__init__()
        self.users_collection = [collection]


class Table(list):
    def __init__(self, *items):
        super().__init__(items)
        self.removed = []

    def remove(self, value, **_kwargs):
        self.removed.append(value)
        index = next(i for i, block in enumerate(self) if block is value)
        del self[index]

    def get(self, name):
        return next((block for block in self if getattr(block,'name',None) == name),None)


class WeaponOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.package = '_weapon_review_' + uuid.uuid4().hex
        package = ModuleType(self.package)
        package.__path__ = [str(CANONICAL)]
        controls = ModuleType(self.package + '.animation_composition_controls')
        controls._host = lambda _name: None
        def require(value, message):
            if not value:
                raise ValueError(message)
        controls._require = require
        contract = ModuleType(self.package + '.animation_composition_contract')
        self.bpy = ModuleType('bpy')
        self.bpy.types = SimpleNamespace(ID=ID, Collection=Collection, Scene=Scene, Object=Object)
        self.scene, self.other = Scene(), Scene()
        self.collection = Collection()
        self.obj = Object(self.collection)
        self.rig = ID()
        self.scene.collection.children.append(self.collection)
        self.collection.objects.append(self.obj)
        self.users = {self.obj: {self.rig, self.collection},
            self.collection: {self.rig, self.scene.collection}}
        self.bpy.data = SimpleNamespace(collections=Table(self.collection),
            scenes=Table(self.scene, self.other), objects=Table(self.obj), user_map=lambda: self.users)
        modules = patch.dict(sys.modules, {'bpy':self.bpy, self.package:package,
            controls.__name__:controls, contract.__name__:contract})
        modules.start(); self.addCleanup(modules.stop)
        self.weapon = self.load('animation_composition_weapon')
        self.owner = str(uuid.uuid4())
        self.collection[self.weapon.OWNER] = self.obj[self.weapon.OWNER] = self.owner
        self.rig[self.weapon.RECORD] = dict(schema=self.weapon.SCHEMA, owner=self.owner,
            collection=self.collection, scene=self.scene, scene_root=self.scene.collection,
            objects={'0':self.obj}, assets={})
        for key in self.weapon._ASSET_TABLES:
            setattr(self.bpy.data,key,Table())

    def load(self, short):
        name = self.package + '.' + short
        spec = importlib.util.spec_from_file_location(name, CANONICAL / (short + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        self.addCleanup(sys.modules.pop,name,None)
        spec.loader.exec_module(module)
        setattr(sys.modules[self.package],short,module)
        return module

    def reject_without_deleting(self):
        with self.assertRaises(ValueError):
            self.weapon.remove_context(None,self.rig)
        self.assertIn(self.weapon.RECORD,self.rig)
        self.assertFalse(self.bpy.data.objects.removed or self.bpy.data.collections.removed)

    def test_exact_native_objects_and_original_scene_link_are_accepted(self):
        record, objects = self.weapon.validate_owned(self.rig)
        self.assertIs(record,self.rig[self.weapon.RECORD])
        self.assertEqual(objects,(self.obj,))

    def test_relink_to_another_scene_same_user_count_rejects(self):
        self.scene.collection.children.clear()
        self.other.collection.children.append(self.collection)
        self.assertEqual(self.collection.users,2)
        self.reject_without_deleting()

    def test_additional_collection_parent_rejects(self):
        self.other.collection.children.append(self.collection)
        self.reject_without_deleting()

    def test_external_object_collection_link_rejects(self):
        self.obj.users_collection.append(self.other.collection)
        self.reject_without_deleting()

    def test_external_native_object_reference_rejects(self):
        self.users[self.obj].add(self.other)
        self.reject_without_deleting()

    def test_foreign_collection_reference_rejects_even_with_mocked_count(self):
        self.users[self.collection].add(self.other)
        self.reject_without_deleting()

    def test_zero_user_cleanup_uses_captured_new_assets_and_retains_shared(self):
        artist = SimpleNamespace(users=0)
        new = SimpleNamespace(users=0)
        shared = SimpleNamespace(users=1)
        self.bpy.data.meshes.extend([artist,new,shared])
        self.weapon._remove_unused_assets({'meshes':(new,shared)})
        self.assertEqual(self.bpy.data.meshes.removed,[new])
        self.assertEqual(self.bpy.data.meshes,[artist,shared])

    def test_owned_asset_native_pointer_and_tag_are_accepted(self):
        block = ID({self.weapon.OWNER:self.owner})
        block.name, block.users = 'Owned FBX Mesh', 1
        self.bpy.data.meshes.append(block)
        self.rig[self.weapon.RECORD]['assets']['meshes'] = {'0':block}
        self.weapon.validate_owned(self.rig)

    def test_existing_artist_zero_user_asset_in_cleanup_record_rejects(self):
        block = ID()
        block.name, block.users = 'Artist Mesh', 0
        self.bpy.data.meshes.append(block)
        self.rig[self.weapon.RECORD]['assets']['meshes'] = {'0':block}
        self.reject_without_deleting()
        self.assertFalse(self.bpy.data.meshes.removed)

    def test_foreign_owner_or_wrong_native_table_rejects_asset_deletion(self):
        block = ID({self.weapon.OWNER:'foreign-owner'})
        block.name, block.users = 'Shared Asset', 0
        self.bpy.data.meshes.append(block)
        self.rig[self.weapon.RECORD]['assets']['meshes'] = {'0':block}
        self.reject_without_deleting()
        block[self.weapon.OWNER] = self.owner
        self.rig[self.weapon.RECORD]['assets'] = {'materials':{'0':block}}
        self.reject_without_deleting()

    def test_missing_owner_and_absent_tags_cannot_claim_artist_zero_user_asset(self):
        self.rig[self.weapon.RECORD].pop('owner')
        self.collection.pop(self.weapon.OWNER)
        self.obj.pop(self.weapon.OWNER)
        artist = ID()
        artist.name, artist.users = 'Artist zero-user Mesh', 0
        self.bpy.data.meshes.append(artist)
        self.rig[self.weapon.RECORD]['assets']['meshes'] = {'0':artist}
        self.reject_without_deleting()
        self.assertFalse(self.bpy.data.meshes.removed)

    def unit_context(self):
        self.scene.frame_current, self.scene.frame_subframe = 17, .25
        self.scene.frame_start, self.scene.frame_end = 1, 120
        self.scene.render = SimpleNamespace(fps=30,fps_base=1.)
        self.scene.tool_settings = SimpleNamespace(use_keyframe_insert_auto=False)
        self.scene.unit_settings = SimpleNamespace(system='METRIC',scale_length=.01)
        self.scene.frame_set = Mock()
        layer = SimpleNamespace(objects=SimpleNamespace(active=None),update=Mock())
        return SimpleNamespace(scene=self.scene,view_layer=layer,object=None,selected_objects=[],
            window=SimpleNamespace(scene=self.scene,view_layer=layer))

    def test_context_captures_and_restores_exact_artist_units(self):
        context = self.unit_context()
        saved = self.weapon._context_capture(context)
        self.assertEqual((saved['unit_system'],saved['unit_scale']),('METRIC',.01))
        self.scene.unit_settings.system,self.scene.unit_settings.scale_length = 'IMPERIAL',100.
        self.weapon._restore_context(context,saved)
        self.assertEqual((self.scene.unit_settings.system,self.scene.unit_settings.scale_length),('METRIC',.01))
        self.scene.frame_set.assert_called_once_with(17,subframe=.25)

    def test_loading_unit_changes_reject_before_geometry(self):
        for field, value in (('system', 'IMPERIAL'), ('scale_length', 1.)):
            with self.subTest(field=field):
                context = self.unit_context()
                self.rig.pop(self.weapon.RECORD, None)
                self.rig['artist_marker'] = 'original'
                self.rig.data = SimpleNamespace()
                self.rig.pose = SimpleNamespace(bones={'hand': SimpleNamespace()})
                self.rig.animation_data = SimpleNamespace(action=None, action_slot=None, nla_tracks=[])
                before_objects = tuple(self.bpy.data.objects)
                self.bpy.data.collections.new = Mock()
                self.bpy.data.objects.new = Mock()
                self.bpy.data.meshes.new = Mock()
                controls = sys.modules[self.package + '.animation_composition_controls']
                controls.validate_scope = lambda *_args: None
                self.weapon._host = lambda _name: SimpleNamespace(_rest_state=lambda _data: {})
                def fields(*_args):
                    return {'units': {'system': self.scene.unit_settings.system,
                                     'scale_length': self.scene.unit_settings.scale_length}}
                def digest(value):
                    import hashlib
                    import json
                    return hashlib.sha256(json.dumps(value, sort_keys=True,
                        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
                def asset_snapshot():
                    previous = {key: set(getattr(self.bpy.data, key)) for key in self.weapon._ASSET_TABLES}
                    setattr(self.scene.unit_settings, field, value)
                    return previous
                self.weapon.contract.validate = lambda value: value
                self.weapon.contract.digest = digest
                export = ModuleType(self.package + '.animation_composition_export')
                export._artist_state = lambda *_args: digest(fields())
                export._action_hash = lambda *_args: 'state'
                mathutils = ModuleType('mathutils')
                mathutils.Matrix, mathutils.Vector = object, object
                with patch.dict(sys.modules, {export.__name__: export, 'mathutils': mathutils}), \
                     patch.object(self.weapon, 'validate_context', return_value=({'modelFile': 'actual fixture'}, [])), \
                     patch.object(self.weapon, '_weapon_identity', return_value='owned'), \
                     patch.object(self.weapon, '_artist_fields', side_effect=fields), \
                     patch.object(self.weapon, '_asset_snapshot', side_effect=asset_snapshot), \
                     patch.object(self.weapon, '_raw_surface') as measured:
                    with self.assertRaisesRegex(ValueError, 'changed the artist Scene units'):
                        self.weapon.add_context(context, self.rig,
                            {'weaponRigBonePath': 'Rig/Hand', 'gripTargets': []},
                            {'bones': [{'path': 'Rig/Hand'}]},
                            SimpleNamespace(mapping=SimpleNamespace(indices={'hand': 0})))
                    measured.assert_not_called()
                self.bpy.data.collections.new.assert_not_called()
                self.bpy.data.objects.new.assert_not_called()
                self.bpy.data.meshes.new.assert_not_called()
                self.assertEqual(tuple(self.bpy.data.objects), before_objects)
                self.assertFalse(self.bpy.data.objects.removed or self.bpy.data.collections.removed
                                 or self.bpy.data.meshes.removed)
                self.assertEqual(self.rig['artist_marker'], 'original')


if __name__ == '__main__':
    unittest.main()
