"""Execute the real importer routing/rollback with fake native IDs, not Blender.

Only the two actual function ASTs are loaded. No add-on, bpy, numpy, model
importer, solver, worker, or SDK is imported/executed by this test suite.
"""

import ast
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/animation_link_source.py'


class NativeID:
    def __init__(self, name, *, embedded=False, **fields):
        self.name, self.is_embedded_data = name, embedded
        self.properties = {}
        self.__dict__.update(fields)

    def __getitem__(self, key):
        return self.properties[key]

    def __setitem__(self, key, value):
        self.properties[key] = value

    def select_set(self, selected):
        self.selected = selected


class FakeContext:
    def __init__(self, scene):
        self.window = NS(scene=scene)
        self.preferences = NS(addons={'io_scene_fbx'})

    @property
    def scene(self):
        return self.window.scene

    @property
    def view_layer(self):
        return self.scene.view_layer

    @property
    def object(self):
        return self.view_layer.objects.active

    @property
    def selected_objects(self):
        return [obj for obj in self.scene.objects if obj.selected]


class ImportRiggingSetupRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.model = Path(self.temp.name) / 'actual-model.fbx'
        self.model.write_bytes(b'fake physical model bytes; no FBX parser is run')
        self.model_hash = hashlib.sha256(self.model.read_bytes()).hexdigest()
        self.calls, self.registry, self.created, self.removed = [], set(), set(), set()
        self.new_rig = None
        self.original_scene = self.make_scene('Artist Scene', original=True)
        self.original_rig = NativeID('Artist Rig', type='ARMATURE', mode='POSE', selected=True)
        self.original_rig.properties['artist_marker'] = 'unchanged'
        self.original_action = NativeID('Artist Action')
        self.image = NativeID('Existing Image', colorspace_settings=NS(name='sRGB'), alpha_mode='STRAIGHT')
        self.registry.update((self.original_rig, self.original_action, self.image))
        self.original_scene.objects.append(self.original_rig)
        self.original_scene.view_layer.objects.active = self.original_rig
        self.original_ids = set(self.registry)
        self.context = FakeContext(self.original_scene)
        self.packet = {'_sha256': 'a' * 64, 'clipName': 'Actual motion',
            'bones': [{'name': 'ActualCosha'}, {'name': 'Hips'}, {'name': 'Hand'}],
            'rigging': {'validated_capture_sentinel': True}}
        self.link = {'_manifest_path': str(Path(self.temp.name) / 'character_animation_link.json'),
            'targetGuid': 'b' * 32}
        self.saved = {'scene': self.original_scene, 'active': self.original_rig}
        self.mapping = object()
        self.mapping_error = self.setup_error = None
        self.model_tamper = self.link_tamper = False
        self.proof = {'schema': 'cdesigner.fresh-arm-rigging-setup/1', 'original_rest_unchanged': True}
        self.setup = Mock(side_effect=self.setup_controls)
        self.import_action = Mock(side_effect=self.base_import)
        self.bind = Mock(side_effect=lambda *_: self.calls.append('bind'))
        scenes = NS(new=lambda name: self.make_scene(name))
        collections = NS(new=self.make_collection)
        data = NS(scenes=scenes, collections=collections, images=[self.image],
            user_map=lambda: {value: set() for value in self.registry}, batch_remove=self.batch_remove)
        ops = NS(import_scene=NS(fbx=self.fbx_import), object=NS(mode_set=self.mode_set),
            preferences=NS(addon_enable=Mock(), addon_disable=Mock()))
        self.bpy = NS(data=data, ops=ops)
        ua = NS(PACKAGE_HASH_KEY='packet_hash', _mapping=self.strict_mapping,
            _set_playing=lambda *_: None)
        lengths = NS(BoneLengthError=ValueError, load=self.length_load, restore=self.length_restore)
        links = NS(load_link=self.link_read, _identity=lambda link: link['targetGuid'], bind_action=self.bind)
        self.package = '_isolated_import_routing_' + uuid.uuid4().hex
        package = ModuleType(self.package)
        package.__path__ = []
        setup_module = ModuleType(self.package + '.animation_rigging_setup')
        setup_module.prepare_fresh_arm_controls = self.setup
        modules = patch.dict(sys.modules, {self.package: package, setup_module.__name__: setup_module})
        modules.start()
        self.addCleanup(modules.stop)
        namespace = {'__package__': self.package, 'bpy': self.bpy,
            'unity_animation': ua, 'bone_rest_lengths': lengths, 'animation_link': links,
            're': re, 'json': json, '_error': ValueError, '_sha256': self.model_sha,
            '_validate_import_context': lambda _: None, '_context_state': lambda _: self.saved,
            '_restore_context': self.restore_context, '_import_action': self.import_action,
            'SourceResult': lambda *values: NS(rig=values[0], action=values[1], scene=values[4])}
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'), filename=str(SOURCE))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
            and node.name in {'_import_prepared_source', '_restore_image_settings'}]
        if len(functions) != 2:
            raise RuntimeError('The real Source importer functions were not found.')
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), 'exec'), namespace)
        self.run_import = namespace['_import_prepared_source']

    def register_new(self, value):
        self.registry.add(value)
        self.created.add(value)
        return value

    def make_scene(self, name, original=False):
        scene = NativeID(name, objects=[], render=NS(fps=30, fps_base=1.),
            unit_settings=NS(system='NONE', scale_length=1.),
            view_layer=NS(objects=NS(active=None), layer_collection=NS(children={}), update=lambda: None))
        def link(collection):
            scene.view_layer.layer_collection.children[collection.name] = collection
        scene.collection = NS(children=NS(link=link))
        if original:
            self.registry.add(scene)
        else:
            self.register_new(scene)
        return scene

    def make_collection(self, name):
        return self.register_new(NativeID(name))

    def fbx_import(self, **kwargs):
        self.calls.append('fbx')
        self.assertEqual(kwargs['filepath'], str(self.model))
        self.assertIs(kwargs['use_anim'], False)
        self.assertIs(kwargs['use_custom_props'], False)
        data = self.register_new(NativeID('Fresh Native Armature', bones={'Hips': object(), 'Hand': object()}))
        rig = self.register_new(NativeID('ActualCosha', type='ARMATURE', mode='OBJECT',
            selected=True, parent=None, data=data))
        self.new_rig = rig
        self.context.scene.objects.append(rig)
        self.context.view_layer.objects.active = rig
        self.image.colorspace_settings.name, self.image.alpha_mode = 'Non-Color', 'PREMUL'
        self.register_new(NativeID('New Embedded Child', embedded=True))
        if self.model_tamper:
            self.model.write_bytes(b'changed after the original physical model pin')
        return {'FINISHED'}

    def mode_set(self, **kwargs):
        self.context.object.mode = kwargs['mode']
        return {'FINISHED'}

    def length_load(self, *_):
        self.calls.append('rest_metadata_load')
        return None

    def length_restore(self, *_):
        self.calls.append('rest_metadata_restore')

    def model_sha(self, path):
        self.calls.append('model_sha')
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def strict_mapping(self, rig, packet, unit):
        self.calls.append('strict_mapping')
        self.assertIs(rig, self.new_rig)
        self.assertIs(packet, self.packet)
        self.assertEqual(unit, 1.)
        if self.mapping_error:
            raise self.mapping_error
        return self.mapping

    def setup_controls(self, ctx, rig, packet, mapping, **kwargs):
        self.calls.append('setup')
        self.assertIs(ctx, self.context)
        self.assertIs(rig, self.new_rig)
        self.assertIs(packet, self.packet)
        self.assertIs(mapping, self.mapping)
        self.assertEqual(kwargs['existing_ids'], self.original_ids)
        self.assertEqual(kwargs['model_file'], self.model)
        self.assertEqual(kwargs['model_sha256'], self.model_hash)
        self.register_new(NativeID('Fresh Owned Widget'))
        if self.setup_error:
            raise self.setup_error
        return self.proof

    def base_import(self, ctx, rig, packet, start, **kwargs):
        self.calls.append('base_import')
        action = self.register_new(NativeID('Fresh Base Action'))
        action['packet_hash'] = packet['_sha256']
        return NS(action=action, first_frame=start, last_frame=start + 10)

    def link_read(self, _):
        self.calls.append('final_link_read')
        return {**self.link, 'targetGuid': 'c' * 32} if self.link_tamper else self.link

    def restore_context(self, ctx, saved, *, playing):
        self.calls.append('restore_original_context')
        ctx.window.scene = saved['scene']
        ctx.view_layer.objects.active = saved['active']

    def batch_remove(self, *, ids):
        self.calls.append('cleanup_new_ids')
        self.removed.update(ids)
        self.registry.difference_update(ids)

    def invoke(self, *, native_timebase=True):
        return self.run_import(self.context, self.link, self.packet, 'ActualCosha',
            self.model, self.model_hash, start_frame=2., native_timebase=native_timebase)

    def assert_failed_cleanup_preserves_original(self):
        self.assertIs(self.context.scene, self.original_scene)
        self.assertIs(self.context.object, self.original_rig)
        self.assertTrue(self.original_ids <= self.registry)
        self.assertFalse(self.removed & self.original_ids)
        self.assertEqual(self.removed, {value for value in self.created if not value.is_embedded_data})
        self.assertEqual(self.original_rig.properties, {'artist_marker': 'unchanged'})
        self.assertEqual((self.image.colorspace_settings.name, self.image.alpha_mode), ('sRGB', 'STRAIGHT'))
        self.bind.assert_not_called()

    def test_native_rigging_rest_model_mapping_setup_base_and_link_order(self):
        result = self.invoke()
        self.assertEqual(self.calls, ['fbx', 'rest_metadata_load', 'rest_metadata_restore',
            'model_sha', 'strict_mapping', 'setup', 'base_import', 'model_sha',
            'final_link_read', 'bind', 'restore_original_context'])
        self.setup.assert_called_once()
        self.assertEqual(json.loads(result.rig['character_designer_fresh_arm_setup_v1']), self.proof)
        self.assertEqual(self.import_action.call_args.kwargs, {'native_timebase': True})
        self.assertIs(result.rig, self.new_rig)
        self.assertIsNot(result.rig, self.original_rig)
        self.assertIs(self.context.scene, result.scene)
        self.assertFalse(self.removed)
        self.assertTrue(self.original_ids <= self.registry)

    def test_mapping_rejection_never_builds_or_imports_base_and_cleans_only_new_ids(self):
        self.mapping_error = ValueError('actual strict mapping rejection')
        with self.assertRaisesRegex(ValueError, 'actual strict mapping rejection'):
            self.invoke()
        self.setup.assert_not_called()
        self.import_action.assert_not_called()
        self.assert_failed_cleanup_preserves_original()

    def test_setup_rejection_including_partial_new_widget_uses_outer_cleanup(self):
        self.setup_error = ValueError('Public ARM setup CANCELLED')
        with self.assertRaisesRegex(ValueError, 'Public ARM setup CANCELLED'):
            self.invoke()
        self.setup.assert_called_once()
        self.import_action.assert_not_called()
        self.assertIn('Fresh Owned Widget', {value.name for value in self.removed})
        self.assert_failed_cleanup_preserves_original()

    def test_actual_model_digest_tamper_rejects_before_mapping_or_setup(self):
        self.model_tamper = True
        with self.assertRaisesRegex(ValueError, 'model changed before fresh arm setup'):
            self.invoke()
        self.assertNotIn('strict_mapping', self.calls)
        self.setup.assert_not_called()
        self.import_action.assert_not_called()
        self.assert_failed_cleanup_preserves_original()

    def test_no_rigging_native_clock_route_retains_legacy_base_path(self):
        del self.packet['rigging']
        result = self.invoke()
        self.setup.assert_not_called()
        self.assertNotIn('strict_mapping', self.calls)
        self.assertNotIn('character_designer_fresh_arm_setup_v1', result.rig.properties)
        self.assertEqual(self.import_action.call_args.kwargs, {'native_timebase': True})
        self.bind.assert_called_once()

    def test_scene_clock_route_with_rigging_does_not_run_fresh_setup(self):
        result = self.invoke(native_timebase=False)
        self.setup.assert_not_called()
        self.assertNotIn('strict_mapping', self.calls)
        self.assertNotIn('character_designer_fresh_arm_setup_v1', result.rig.properties)
        self.assertEqual(self.import_action.call_args.kwargs, {})
        self.bind.assert_called_once()

    def test_final_link_identity_change_rejects_and_cleans_setup_and_base_ids(self):
        self.link_tamper = True
        with self.assertRaisesRegex(ValueError, 'Link source identity changed'):
            self.invoke()
        self.setup.assert_called_once()
        self.import_action.assert_called_once()
        self.assertIn('Fresh Base Action', {value.name for value in self.removed})
        self.assert_failed_cleanup_preserves_original()


if __name__ == '__main__':
    unittest.main()
