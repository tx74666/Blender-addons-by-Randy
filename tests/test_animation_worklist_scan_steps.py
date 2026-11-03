"""Focused per-row scan ownership and final attached-route authentication."""

import ast
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import test_animation_worklist_collection as collection_fixture
import test_animation_worklist_ui as ui_fixture


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


def route_functions(fixture):
    """Keep production route checks; replace only native/filesystem boundaries."""
    module = fixture.worklist
    namespace = dict(module.__dict__, Path=Path, json=json, links=fixture.links,
                     bpy=NS(path=NS(abspath=lambda path: path)),
                     workspace=NS(route_identity=lambda clip: (clip['clipGuid'], clip['clipLocalId']),
                                  work_item_id=lambda clip: clip.get('workItemId', '')))
    tree = ast.parse((SOURCE / 'animation_worklist.py').read_bytes())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'_item_workspace', '_validate_route'}]
    exec(compile(ast.Module(body=functions, type_ignores=[]), 'animation_worklist.py', 'exec'), namespace)
    module._item_workspace = namespace['_item_workspace']
    module._validate_route = namespace['_validate_route']
    return namespace


class ScanSteps(unittest.TestCase):
    def setUp(self):
        self.f = collection_fixture.CollectionTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.worklist._item_workspace = lambda item: bool(getattr(item, 'workspace_path', ''))
        for name in ('run', 'idle'):
            item = self.f.make_item(name)
            item.link_identity = json.dumps(self.f.link)
            self.f.saved.items.append(item)
            self.f.catalog['clips'].append(self.f.clip(name))

    def test_prepare_reads_no_action_content_and_incomplete_until_last_row(self):
        f = self.f
        f.saved.scan_completed = True
        plan = f.collection.prepare_scan(f.context)
        self.assertFalse(f.saved.scan_completed)
        f.fingerprints.fingerprint_native.assert_not_called()
        self.assertFalse(f.collection.step_scan(f.context, plan))
        self.assertEqual(f.fingerprints.fingerprint_native.call_count, 1)
        self.assertFalse(f.saved.scan_completed)
        self.assertFalse(f.collection.step_scan(f.context, plan))
        self.assertTrue(f.collection.step_scan(f.context, plan))
        self.assertEqual(f.fingerprints.fingerprint_native.call_count, 3)
        self.assertTrue(f.saved.scan_completed)
        self.assertEqual(sum(plan['counts'].values()), 3)
        f.worklist.activate.assert_not_called()
        f.worklist.sync.assert_not_called()

    def test_replaced_unprocessed_row_refuses_before_reading_its_keys(self):
        f = self.f
        plan = f.collection.prepare_scan(f.context)
        f.collection.step_scan(f.context, plan)
        f.saved.items[1] = deepcopy(f.saved.items[1])
        with self.assertRaisesRegex(ValueError, 'row changed'):
            f.collection.step_scan(f.context, plan)
        self.assertEqual(f.fingerprints.fingerprint_native.call_count, 1)
        self.assertFalse(f.saved.scan_completed)

    def test_changed_completed_row_refuses_at_completion(self):
        f = self.f
        plan = f.collection.prepare_scan(f.context)
        f.collection.step_scan(f.context, plan)
        f.saved.items[0].custom_slot = 17
        f.collection.step_scan(f.context, plan)
        with self.assertRaisesRegex(ValueError, 'row changed'):
            f.collection.step_scan(f.context, plan)
        self.assertFalse(f.saved.scan_completed)

    def test_removed_row_or_replaced_scene_never_finishes_old_scan(self):
        f = self.f
        plan = f.collection.prepare_scan(f.context)
        f.saved.items.pop()
        with self.assertRaisesRegex(ValueError, 'list changed'):
            f.collection.step_scan(f.context, plan)
        self.assertFalse(f.saved.scan_completed)
        plan = f.collection.prepare_scan(f.context)
        other = NS(character_designer_animation_worklist=f.saved)
        f.window.scene = other
        with self.assertRaisesRegex(ValueError, 'list changed'):
            f.collection.step_scan(f.context, plan)
        self.assertFalse(f.saved.scan_completed)

    def test_each_new_row_authenticates_source_again(self):
        f = self.f
        plan = f.collection.prepare_scan(f.context)
        f.collection.step_scan(f.context, plan)
        f.worklist._check_baseline.side_effect = ValueError('Source file changed')
        f.collection.step_scan(f.context, plan)
        self.assertEqual(f.saved.items[1].scan_state, 'BLOCKED')
        self.assertEqual(f.saved.items[1].scan_reason, 'Source file changed')
        self.assertEqual(f.fingerprints.fingerprint_native.call_count, 1)

    def test_frame_subframe_fps_range_and_unit_changes_need_no_depsgraph_signal(self):
        f = self.f
        scene = f.context.scene
        scene.frame_current, scene.frame_subframe = 1, 0.0
        scene.frame_start, scene.frame_end = 1, 100
        scene.render = NS(fps=24, fps_base=1.0)
        scene.unit_settings = NS(scale_length=1.0)
        cases = ((scene, 'frame_current', 2), (scene, 'frame_subframe', .5),
                 (scene, 'frame_start', 0), (scene, 'frame_end', 120),
                 (scene.render, 'fps', 30), (scene.render, 'fps_base', 1.001),
                 (scene.unit_settings, 'scale_length', .01))
        for owner, field, changed in cases:
            with self.subTest(field=field):
                previous = getattr(owner, field)
                plan = f.collection.prepare_scan(f.context)
                f.collection.step_scan(f.context, plan)
                setattr(owner, field, changed)
                with self.assertRaisesRegex(ValueError, 'timing or frame changed'):
                    f.collection.step_scan(f.context, plan)
                self.assertFalse(f.saved.scan_completed)
                setattr(owner, field, previous)

    def test_synchronous_api_keeps_complete_counts(self):
        f = self.f
        counts = f.collection.scan_changes(f.context)
        self.assertEqual(sum(counts.values()), 3)
        self.assertEqual(f.fingerprints.fingerprint_native.call_count, 3)
        self.assertTrue(f.saved.scan_completed)

    def test_unchanged_rows_refresh_files_and_receipts_without_base_key_rescan(self):
        f = self.f
        f.publish_receipt()
        plan = f.collection.prepare_scan(f.context)
        self.assertFalse(f.collection.step_scan(f.context, plan))
        self.assertEqual(f.item.scan_state, 'UNCHANGED')
        self.assertEqual(len(plan['unchanged']), 1)
        self.assertFalse(f.collection.step_scan(f.context, plan))
        self.assertTrue(f.collection.step_scan(f.context, plan))
        self.assertEqual(f.fingerprints.fingerprint_native.call_count, 3)
        self.assertTrue(f.saved.scan_completed)

    def test_changed_link_publication_receipt_or_implementation_cancels_final_scan(self):
        f = self.f
        original_fbx = f.fbx.read_bytes()
        cases = ('link', 'publication', 'receipt_raw', 'source_receipt_raw', 'implementation')
        for case in cases:
            with self.subTest(case=case):
                f.fbx.write_bytes(original_fbx)
                f.link['revision'] = 1
                f.implementation['animation_export.py'] = '1' * 64
                f.item.source_baseline_receipt = ''
                f.publish_receipt()
                plan = f.collection.prepare_scan(f.context)
                f.collection.step_scan(f.context, plan)
                self.assertEqual(f.item.scan_state, 'UNCHANGED')
                if case == 'link':
                    f.link['revision'] = 2
                elif case == 'publication':
                    f.fbx.write_bytes(b'changed publication with old Link digest')
                elif case == 'receipt_raw':
                    f.item.last_synced_receipt += ' '
                elif case == 'source_receipt_raw':
                    f.item.source_baseline_receipt = 'changed unused source receipt'
                else:
                    f.implementation['animation_export.py'] = '2' * 64
                f.collection.step_scan(f.context, plan)
                with self.assertRaisesRegex(ValueError, 'changed during Scan'):
                    f.collection.step_scan(f.context, plan)
                self.assertFalse(f.saved.scan_completed)
                f.worklist.sync.assert_not_called()


class AttachedRoutes(unittest.TestCase):
    def setUp(self):
        self.f = collection_fixture.CollectionTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f = self.f
        self.namespace = route_functions(f)
        f.saved.target_guid = 'f' * 32
        f.item.workspace_path = str(f.folder / 'attached.workspace.json')
        f.item.workspace_identity = 'attached identity'
        f.item.model_file = str(f.folder / 'model.fbx')
        f.item.model_sha256 = '7' * 64
        f.catalog.update(targetGuid='f' * 32, modelFile=f.item.model_file, modelSha256=f.item.model_sha256)
        self.events = []
        f.links.load_link.side_effect = lambda path: self.events.append('link') or deepcopy(f.link)
        f.worklist._fresh_workspace.side_effect = lambda *args: self.events.append('workspace') or deepcopy(f.catalog)
        self.namespace['_fresh_workspace'] = f.worklist._fresh_workspace

    def test_attached_route_reads_workspace_once_after_link(self):
        f = self.f
        result = f.collection._current_link(f.saved, f.item, None)
        self.assertEqual(result, f.link)
        self.assertEqual(self.events, ['link', 'workspace'])
        f.worklist._fresh_workspace.assert_called_once_with(f.saved, f.item)

    def test_workspace_changed_while_link_is_read_is_still_rejected(self):
        f = self.f
        def changed_link(path):
            f.catalog['clips'][0]['clipLocalId'] = 999
            return deepcopy(f.link)
        f.links.load_link.side_effect = changed_link
        with self.assertRaisesRegex(ValueError, 'work item|workspace, route or model'):
            f.collection._current_link(f.saved, f.item, None)

    def test_missing_unprepared_manifest_model_and_wrong_target_still_refuse(self):
        f = self.f
        original = deepcopy(f.catalog)
        changes = (
            lambda: f.catalog.update(clips=[]),
            lambda: f.catalog['clips'][0].update(needsRebuild=True),
            lambda: f.catalog['clips'][0].update(linkManifest='elsewhere.json'),
            lambda: f.catalog.update(modelSha256='9' * 64),
            lambda: f.catalog.update(targetGuid='e' * 32),
        )
        for change in changes:
            f.catalog.clear()
            f.catalog.update(deepcopy(original))
            change()
            with self.assertRaises(ValueError):
                f.collection._current_link(f.saved, f.item, None)

    def test_unpaired_attached_identity_refuses(self):
        f = self.f
        for path, identity in (('', 'identity'), ('workspace.json', '')):
            f.item.workspace_path, f.item.workspace_identity = path, identity
            with self.assertRaisesRegex(ValueError, 'remain paired'):
                f.collection._current_link(f.saved, f.item, None)

    def test_saved_workspace_and_composition_keep_their_fresh_route(self):
        f = self.f
        f.item.workspace_path = f.item.workspace_identity = ''
        f.collection._current_link(f.saved, f.item, None)
        f.worklist._fresh_workspace.assert_called_once_with(f.saved, None)
        self.assertEqual(self.events, ['workspace', 'link'])
        f.worklist._fresh_workspace.reset_mock()
        f.item.composition_json = '{}'
        f.collection._current_link(f.saved, f.item, {'clips': []})
        f.worklist._fresh_workspace.assert_called_once_with(f.saved, f.item)


class ScanUI(unittest.TestCase):
    def setUp(self):
        self.f = ui_fixture.WorklistUI(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f = self.f
        self.callbacks = []
        sys.modules['bpy'].app.handlers = NS(depsgraph_update_post=self.callbacks)
        f.context.view_layer = NS(update=Mock())
        self.plan = dict(index=0, rows=(1, 2), done=False)
        f.collection.prepare_scan = Mock(return_value=self.plan)
        def step(context, plan):
            plan['index'] += 1
            context.scene.character_designer_animation_worklist.scan_completed = plan['index'] == 2
            return plan['index'] == 2
        f.collection.step_scan = Mock(side_effect=step)

    def test_cancel_between_rows_does_not_start_export(self):
        f = self.f
        f.ui.CHARACTERDESIGNER_OT_worklist_scan_changes().execute(f.context)
        self.assertEqual(f.ui._poll_scan(), .01)
        self.assertTrue(f.ui.scan_pending())
        self.assertFalse(f.state.scan_completed)
        f.ui.CHARACTERDESIGNER_OT_worklist_cancel_collection().execute(f.context)
        self.assertFalse(f.ui.scan_pending())
        f.timers.unregister.assert_called_once_with(f.ui._poll_scan)
        f.collection.begin_sync_changed.assert_not_called()
        f.collection.step_scan.assert_called_once()

    def test_scene_change_between_rows_stops_before_next_step(self):
        f = self.f
        f.ui.CHARACTERDESIGNER_OT_worklist_scan_changes().execute(f.context)
        f.ui._poll_scan()
        other = NS(character_designer_animation_worklist=NS(status='Other unchanged'))
        f.window.scene = other
        self.assertIsNone(f.ui._poll_scan())
        self.assertFalse(f.ui.scan_pending())
        self.assertEqual(other.character_designer_animation_worklist.status, 'Other unchanged')
        self.assertEqual(self.callbacks, [])
        f.collection.step_scan.assert_called_once()
        f.collection.begin_sync_changed.assert_not_called()

    def test_removed_window_between_rows_releases_temporary_guard(self):
        f = self.f
        f.ui.CHARACTERDESIGNER_OT_worklist_scan_changes().execute(f.context)
        self.assertEqual(f.ui._poll_scan(), .01)
        f.context.window_manager.windows = []
        self.assertIsNone(f.ui._poll_scan())
        self.assertFalse(f.ui.scan_pending())
        self.assertEqual(self.callbacks, [])
        f.collection.step_scan.assert_called_once()

    def test_external_depsgraph_edit_invalidates_completed_prefix_before_export(self):
        f = self.f
        f.ui.CHARACTERDESIGNER_OT_worklist_sync_changed().execute(f.context)
        self.assertEqual(f.ui._poll_scan(), .01)
        self.assertEqual(len(self.callbacks), 1)
        self.callbacks[0](f.context.scene, object())
        self.assertIsNone(f.ui._poll_scan())
        self.assertFalse(f.ui.scan_pending())
        self.assertFalse(f.state.scan_completed)
        self.assertEqual(self.callbacks, [])
        f.collection.step_scan.assert_called_once()
        f.collection.begin_sync_changed.assert_not_called()
        self.assertIn('inputs changed', f.state.status)

    def test_normal_steps_finish_before_export_and_release_only_owned_guard(self):
        f = self.f
        other = Mock()
        self.callbacks.append(other)
        f.ui.CHARACTERDESIGNER_OT_worklist_sync_changed().execute(f.context)
        self.assertEqual(f.ui._poll_scan(), .01)
        f.collection.begin_sync_changed.assert_not_called()
        self.assertIsNone(f.ui._poll_scan())
        self.assertTrue(f.state.scan_completed)
        f.collection.begin_sync_changed.assert_called_once_with(f.context, changed_only=True)
        self.assertEqual(self.callbacks, [other])

    def test_own_feedback_updates_do_not_invalidate_scan(self):
        f = self.f
        def updates():
            for callback in tuple(self.callbacks):
                callback(f.context.scene, object())
        f.context.view_layer.update.side_effect = updates
        f.ui.CHARACTERDESIGNER_OT_worklist_scan_changes().execute(f.context)
        self.assertEqual(f.ui._poll_scan(), .01)
        # A flush with no user work produces no external dependency updates.
        f.context.view_layer.update.side_effect = None
        self.assertIsNone(f.ui._poll_scan())
        self.assertTrue(f.state.scan_completed)
        self.assertEqual(self.callbacks, [])

    def test_cancel_and_lost_timer_remove_temporary_input_guard(self):
        f = self.f
        for lost_timer in (False, True):
            f.ui.CHARACTERDESIGNER_OT_worklist_scan_changes().execute(f.context)
            self.assertEqual(f.ui._poll_scan(), .01)
            self.assertEqual(len(self.callbacks), 1)
            if lost_timer:
                f.timers.is_registered.return_value = False
                self.assertFalse(f.ui.scan_pending())
            else:
                f.ui.CHARACTERDESIGNER_OT_worklist_cancel_collection().execute(f.context)
            self.assertEqual(self.callbacks, [])
            self.plan['index'] = 0

    def test_rest_draw_resolves_once_and_does_not_run_separate_admission(self):
        f = self.f
        with f.base_pose_modules() as (pose, _state):
            pose.natural_active = Mock(return_value=False)
            rest = sys.modules[f.ui.__package__ + '.animation_rest_pose']
            f.ui.draw_worklist(ui_fixture.Layout(), f.context)
            rest.get_rig.assert_called_once_with(f.context)
            rest.can_show.assert_not_called()
            rest.show_rest.assert_not_called()
            rest.show_pose.assert_not_called()

    def test_rest_refusal_keeps_buttons_hidden(self):
        f = self.f
        with f.base_pose_modules() as (pose, _state):
            pose.natural_active = Mock(return_value=False)
            rest = sys.modules[f.ui.__package__ + '.animation_rest_pose']
            rest.get_rig.side_effect = ValueError('Shared Armature data')
            layout = ui_fixture.Layout()
            f.ui.draw_worklist(layout, f.context)
            self.assertFalse(any(kind == 'operator' and value[0] == 'character_designer.worklist_pose_position'
                                 for kind, value, enabled in layout.records))


if __name__ == '__main__':
    unittest.main()
