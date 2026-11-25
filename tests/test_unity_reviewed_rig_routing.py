"""Exercise production reviewed-Rig dispatch without Blender or real inbox writes.

Native source definitions, receiver authority and identities are controlled
boundaries here. The dedicated pure transport and native definition reader have
their own fixtures. All payload copies in this file remain in a tempfile tree.
"""
import ast
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest import mock
import uuid

import test_unity_update_export_routing as old_routes

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = '_reviewed_rig_routing_fixture'


class NativeObject(dict):
    __hash__ = object.__hash__
    __eq__ = object.__eq__
    __ne__ = object.__ne__
    def __init__(self, name, kind):
        self.name, self.type = name, kind


class ReviewedRigUITests(unittest.TestCase):
    route = old_routes.RoutingTests.route
    draw_panel = old_routes.RoutingTests.draw_panel
    export_enabled = old_routes.RoutingTests.export_enabled

    def setUp(self):
        old_routes.RoutingTests.setUp(self)
        self.hair = NativeObject('Hair', 'MESH')
        self.hair['character_designer_hair_bones_armature'] = types.SimpleNamespace(type='ARMATURE')
        for key in ('bones', 'strands', 'motion'):
            self.hair['character_designer_hair_' + ('bones_v1' if key == 'bones' else key + '_v1')] = '{}'
        self.selection.update(meshes=(self.hair,), eligible=(self.body, self.hair), update_rig=True)
        self.caps = mock.Mock(return_value=None)
        self.globals['_rig_capabilities'] = self.caps
        self.globals['_rig_display_capabilities'] = self.caps
        self.globals['_reviewed_rig_route'] = mock.Mock(return_value='REVIEWED_RIG')

    def test_exact_rig_hair_requires_dedicated_gate_before_worker(self):
        self.assertEqual(self.operator.invoke(self.context, None), {'CANCELLED'})
        self.caps.assert_called_once()
        self.exporter.begin_candidate_export.assert_not_called()
        self.exporter.begin_export.assert_not_called()
        self.context.window_manager.event_timer_add.assert_not_called()

    def test_ready_reviewed_rig_uses_candidate_dispatch_and_semantic_status(self):
        self.caps.return_value = {'supports_hair_structure': True}
        self.result['reviewed_rig'] = True
        self.assertEqual(self.operator.invoke(self.context, None), {'RUNNING_MODAL'})
        self.exporter.begin_candidate_export.assert_called_once_with(
            self.context, self.rig, self.config, selection=self.selection)
        self.assertEqual(self.operator.modal(self.context, types.SimpleNamespace(type='TIMER')), {'FINISHED'})
        self.exporter.begin_export.assert_not_called()
        self.exporter.export_character.assert_not_called()
        self.assertIn('Reviewed Rig', self.config.last_status)

    def test_extra_requested_mesh_is_refused_without_capability_fallback(self):
        self.selection.update(meshes=(self.hair, self.dress), eligible=(self.body, self.hair, self.dress))
        self.caps.return_value = {'supports_hair_structure': True}
        self.assertEqual(self.operator.execute(self.context), {'CANCELLED'})
        self.caps.assert_not_called()
        self.exporter.begin_candidate_export.assert_not_called()
        self.exporter.export_character.assert_not_called()

    def test_exact_request_button_names_reviewed_action_and_gate_is_closed(self):
        log = self.draw_panel()
        self.assertFalse(self.export_enabled(log))
        self.assertTrue(any('review' in text.lower() for kind, *tail in log
                            if kind == 'label' for text in tail))


class ReviewedRigExporterTests(unittest.TestCase):
    def setUp(self):
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(ROOT / 'addons/character_designer')]
        self.patch = mock.patch.dict(sys.modules, {PACKAGE: package})
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.base = importlib.import_module(PACKAGE + '.unity_update_candidate')
        self.reviewed = types.ModuleType(PACKAGE + '.unity_rig_update')
        self.reviewed.read_capabilities = mock.Mock(return_value=None)
        self.reviewed.capture_plan = mock.Mock()
        self.reviewed.revalidate_plan = mock.Mock()
        self.reviewed.publish_candidate = mock.Mock(return_value=Path('candidate.json'))
        self.choices = types.ModuleType(PACKAGE + '.unity_update_selection_ui')
        self.guard = types.ModuleType(PACKAGE + '.dress_export_guard')
        self.guard.reject_model_sources = mock.Mock()
        sys.modules[self.reviewed.__name__] = self.reviewed
        sys.modules[self.choices.__name__] = self.choices
        sys.modules[self.guard.__name__] = self.guard
        self.temp = tempfile.TemporaryDirectory(prefix='reviewed-rig-route-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rig, self.body, self.hair = (NativeObject(name, kind) for name, kind in
            (('Rig', 'ARMATURE'), ('Body', 'MESH'), ('Hair', 'MESH')))
        self.hair.update(character_designer_hair_bones_armature=self.rig,
            character_designer_hair_bones_v1='{}', character_designer_hair_strands_v1='{}',
            character_designer_hair_motion_v1='{}')
        self.selection = {'update_rig': True, 'meshes': (self.hair,), 'eligible': (self.body, self.hair)}
        self.choices.get_selection = mock.Mock(side_effect=lambda *args: self.selection)
        self.context = types.SimpleNamespace(mode='POSE')
        self.config = types.SimpleNamespace(asset_id='a' * 32)
        parts = [{'object_name': name, 'part_id': str(index) * 32,
            'renderer_path': name, 'material_slots': []} for index, name in enumerate(('Body', 'Hair'), 1)]
        self.identities = {'rig_id': 'b' * 32, 'parts': parts}
        self.snapshot = {'native_definition': 'unchanged'}
        self.g = {'__package__': PACKAGE, 'Path': Path, 'tempfile': tempfile, 'time': time, 'uuid': uuid,
            'ExportError': ValueError, 'bpy': types.SimpleNamespace(context=self.context),
            '_ACTIVE_JOB': None, '_target': mock.Mock(return_value=(self.root, 'Character.fbx')),
            '_scope_references': mock.Mock(return_value=()),
            'character_setup': types.SimpleNamespace(settings=lambda *args: None),
            '_collection_scope': mock.Mock(return_value={'eligible': (self.body, self.hair)}),
            '_candidate_full_config': mock.Mock(), 'collect_character': mock.Mock(
                return_value={'objects': (self.rig, self.body, self.hair)}),
            '_model_body': mock.Mock(return_value=self.body),
            '_candidate_renderer_inventory': mock.Mock(return_value=[{key: part[key] for key in
                ('renderer_path', 'object_name', 'material_slots')} for part in parts]),
            '_candidate_identity_plan': mock.Mock(return_value=self.identities),
            '_validate_candidate_ids': mock.Mock(), '_capture_hair_motion': mock.Mock(
                return_value={'source': 'Hair', 'registry': {'source_uid': 'c' * 32}}),
            'rig_source_snapshot': mock.Mock(side_effect=lambda *args: dict(self.snapshot)),
            '_dispose': mock.Mock(side_effect=lambda job: job['temporary'].cleanup()),
            'report_messages': mock.Mock(return_value=([], []))}
        tree = ast.parse((ROOT / 'addons/character_designer/unity_export.py').read_text(encoding='utf8'))
        names = {'rig_candidate_source', '_begin_reviewed_rig_candidate', '_poll_reviewed_rig_candidate'}
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in nodes}, names)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'unity_export.py', 'exec'), self.g)

    def begin(self):
        return self.g['_begin_reviewed_rig_candidate'](self.context, self.rig, self.config, self.selection)

    def ready(self):
        self.reviewed.read_capabilities.return_value = {'supports_hair_structure': True}
        sealed = self.root / 'sealed'; sealed.mkdir()
        model = sealed / 'Character.fbx'; model.write_bytes(b'NATIVE-REVIEWED-IMMUTABLE-FBX')
        manifest = sealed / 'Character.cdesigner.json'
        manifest.write_text(json.dumps({'files': {model.name: hashlib.sha256(model.read_bytes()).hexdigest()}}))
        self.plan = types.SimpleNamespace(bundle_manifest=manifest,
            candidate_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
        self.reviewed.capture_plan.return_value = self.plan
        self.base.capture_base = mock.Mock(return_value=object())

    def test_disabled_current_announcement_stops_before_native_inventory_and_writes(self):
        with self.assertRaisesRegex(ValueError, 'native-verified'):
            self.begin()
        self.g['collect_character'].assert_not_called()
        self.g['rig_source_snapshot'].assert_not_called()
        self.reviewed.capture_plan.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_wrong_owner_and_extra_request_refuse_before_disk_authority(self):
        self.hair['character_designer_hair_bones_armature'] = NativeObject('Other', 'ARMATURE')
        with self.assertRaisesRegex(ValueError, 'exactly Rig'):
            self.begin()
        self.hair['character_designer_hair_bones_armature'] = self.rig
        self.selection['meshes'] = (self.hair, self.body)
        with self.assertRaisesRegex(ValueError, 'Extra Mesh'):
            self.begin()
        self.reviewed.read_capabilities.assert_not_called()

    def test_reviewed_payload_is_copied_byte_exact_without_worker_or_identity_write(self):
        self.ready()
        before = self.plan.bundle_manifest.parent.joinpath('Character.fbx').read_bytes()
        job = self.begin(); self.addCleanup(job['temporary'].cleanup)
        self.assertIsNone(job['process'])
        self.assertEqual(job['mesh_ids'], [part['part_id'] for part in self.identities['parts']])
        def verify_publish(plan, **kwargs):
            self.assertEqual((kwargs['stage'] / 'Character.fbx').read_bytes(), before)
            self.assertEqual((kwargs['stage'] / 'Character.cdesigner.json').read_bytes(), plan.bundle_manifest.read_bytes())
            return Path('candidate.json')
        self.reviewed.publish_candidate.side_effect = verify_publish
        result = self.g['_poll_reviewed_rig_candidate'](job)
        self.assertTrue(result['reviewed_rig'])
        self.assertEqual(self.plan.bundle_manifest.parent.joinpath('Character.fbx').read_bytes(), before)
        self.assertEqual(self.reviewed.revalidate_plan.call_count, 2)
        self.g['_dispose'].assert_called_once_with(job)

    def test_changed_definition_or_selection_refuses_before_publication(self):
        self.ready(); job = self.begin(); self.addCleanup(job['temporary'].cleanup)
        self.reviewed.revalidate_plan.side_effect = ValueError('Source definition changed')
        with self.assertRaisesRegex(ValueError, 'Source definition changed'):
            self.g['_poll_reviewed_rig_candidate'](job)
        self.reviewed.publish_candidate.assert_not_called()
        self.g['_dispose'].assert_called_once_with(job)


if __name__ == '__main__':
    unittest.main()
