"""Execute actual UI routing/method AST without Blender or a live Unity project.

The worker, receiver announcement and WM are controlled boundaries. These tests
exercise the production functions, including modal dispatch, no-op/refusal before
worker creation, full-ledger checks and candidate-specific pending status. They
do not claim native layout, import, receiver application or drag-paint coverage.
"""

import ast
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/unity_export_ui.py'
NAMES = {'_model_target', '_existing_unity_project', '_full_update_ledger_guard',
         '_candidate_capabilities', '_selection_mode', '_prepare_update_route',
         '_poll_update_job', '_run_candidate_sync', 'CHARACTERDESIGNER_OT_unity_export',
         'CHARACTERDESIGNER_PT_unity_export'}


class FakeOperator:
    def report(self, level, text):
        self.reports.append((level, text))


class Layout:
    def __init__(self, parent=None):
        self.parent, self.enabled = parent, True
        self.log = parent.log if parent else []

    def row(self, **kwargs):
        return Layout(self)

    box = column = row

    def label(self, **kwargs):
        self.log.append(('label', kwargs.get('text', '')))

    def prop(self, owner, name, **kwargs):
        getattr(owner, name)

    def operator(self, name, **kwargs):
        enabled, ancestor = True, self
        while ancestor is not None:
            enabled = enabled and ancestor.enabled
            ancestor = ancestor.parent
        self.log.append(('operator', name, enabled))
        return SimpleNamespace()


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.body, self.dress = object(), object()
        self.rig = SimpleNamespace(name='CoshaRig')
        self.config = SimpleNamespace(asset_id='a' * 32, last_status='old status', last_report='old report',
                                      directory='/project/Assets/Cosha', show_materials=False)
        self.selection = {'eligible': (self.body, self.dress), 'meshes': (self.dress,), 'update_rig': False}
        self.job = {'candidate': True}
        self.result = {'candidate_path': '/project/.codex/character-update-inbox/candidate.json'}
        self.exporter = SimpleNamespace(
            _target=mock.Mock(return_value=(Path('/project/Assets/Cosha'), 'Cosha.fbx')),
            begin_export=mock.Mock(return_value={'candidate': False}),
            begin_candidate_export=mock.Mock(return_value=self.job),
            poll_export=mock.Mock(return_value={'filepath': '/project/Cosha.fbx'}),
            poll_candidate_export=mock.Mock(return_value=self.result),
            cancel_export=mock.Mock(), export_character=mock.Mock(return_value={'filepath': '/project/Cosha.fbx'}),
            _scope_references=mock.Mock(return_value=[]),
            _collection_scope=mock.Mock(return_value={'eligible': (self.body, self.dress)}),
            collect_character=mock.Mock(return_value={'objects': [self.body, self.dress], 'warnings': []}),
            export_running=mock.Mock(return_value=False))
        self.helper = SimpleNamespace(
            _io=lambda value: value,
            receiver_capabilities=mock.Mock(return_value={'supports_forearm_mesh': False}),
            require_full_update_allowed=mock.Mock())
        self.timer = object()
        wm = SimpleNamespace(event_timer_add=mock.Mock(return_value=self.timer),
                             event_timer_remove=mock.Mock(), modal_handler_add=mock.Mock())
        self.context = SimpleNamespace(mode='OBJECT', window_manager=wm, window=object())
        self.globals = {'Path': Path, 'Operator': FakeOperator, 'Panel': object, '_MODAL_EXPORTS': [],
                        'SIDEBAR_CATEGORY': 'Character Designer', 'UI_PAGE_MISC': 'MISC',
                        'active_ui_page': lambda context: 'MISC',
                        'character_setup': SimpleNamespace(settings=lambda context: SimpleNamespace(rig=self.rig, body=self.body)),
                        '_rig': lambda context: self.rig,
                        '_exporter': lambda: self.exporter, '_candidate_helpers': lambda: self.helper,
                        '_config': lambda context: (self.rig, self.config),
                        '_full_update_config': mock.Mock(return_value='full-scope-proxy'),
                        '_object_mode_required': mock.Mock(), '_redraw': mock.Mock(),
                        'update_selection': SimpleNamespace(
                            get_selection=mock.Mock(side_effect=lambda *args: self.selection),
                            draw_selection=mock.Mock(),
                            selection_counts=lambda *args: (len(self.selection['meshes']), len(self.selection['eligible']), False),
                            _read_settings=lambda rig: SimpleNamespace(update_rig=self.selection['update_rig']),
                            _selected=lambda choices, obj: obj in self.selection['meshes']),
                        'material_match_ui': SimpleNamespace(draw_matches=mock.Mock(), warning_matched=lambda *args, **kwargs: False),
                        '_simple_material_entries': lambda config: [], '_last_report': lambda config: None,
                        '_invalid_overrides': lambda *args, **kwargs: [], '_short': lambda text: text,
                        '_panel_warnings': lambda result: [], '_success_status': lambda warnings: 'Exported successfully',
                        'os': __import__('os'), 'time': SimpleNamespace(monotonic=mock.Mock(return_value=0), sleep=mock.Mock())}
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in NAMES]
        self.assertEqual({node.name for node in nodes}, NAMES)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.globals)
        # Use the real full-ledger helper separately below; normal routing stubs
        # only physical Unity-project discovery, not the ledger call itself.
        self.globals['_existing_unity_project'] = mock.Mock(return_value=Path('/project'))
        self.operator = self.globals['CHARACTERDESIGNER_OT_unity_export']()
        self.operator.reports = []

    def draw_panel(self):
        self.rig.character_designer_unity_export = self.config
        panel = self.globals['CHARACTERDESIGNER_PT_unity_export']()
        panel.layout = Layout()
        panel.draw(self.context)
        return panel.layout.log

    def export_enabled(self, log):
        return next(item[2] for item in log if item[:2] == ('operator', 'character_designer.unity_export'))

    def route(self):
        return self.globals['_prepare_update_route'](self.context, self.rig, self.config)

    def test_mesh_only_requires_valid_receiver_before_worker_or_timer(self):
        self.helper.receiver_capabilities.return_value = None
        self.assertEqual(self.operator.invoke(self.context, None), {'CANCELLED'})
        self.exporter.begin_export.assert_not_called()
        self.exporter.begin_candidate_export.assert_not_called()
        self.context.window_manager.event_timer_add.assert_not_called()
        self.helper.receiver_capabilities.side_effect = ValueError('incompatible receiver')
        self.assertEqual(self.operator.execute(self.context), {'CANCELLED'})
        self.exporter.export_character.assert_not_called()
        self.exporter.begin_candidate_export.assert_not_called()

    def test_rig_partial_is_refused_even_when_capability_claims_rig_support(self):
        self.selection['update_rig'] = True
        self.helper.receiver_capabilities.return_value = {'supports_rig': True}
        self.assertEqual(self.operator.invoke(self.context, None), {'CANCELLED'})
        self.helper.receiver_capabilities.assert_not_called()
        self.exporter.begin_export.assert_not_called()
        self.exporter.begin_candidate_export.assert_not_called()

    def test_none_is_noop_before_mode_validation_config_writes_and_workers(self):
        self.selection.update(meshes=(), update_rig=False)
        before = vars(self.config).copy()
        self.context.mode = 'EDIT_MESH'
        self.assertEqual(self.operator.invoke(self.context, None), {'FINISHED'})
        self.assertEqual(self.operator.execute(self.context), {'FINISHED'})
        self.assertEqual(vars(self.config), before)
        self.exporter.begin_export.assert_not_called()
        self.exporter.begin_candidate_export.assert_not_called()
        self.globals['_object_mode_required'].assert_not_called()
        self.helper.receiver_capabilities.assert_not_called()

    def test_full_route_keeps_proxy_and_checks_ledger_before_worker(self):
        self.selection.update(meshes=(self.body, self.dress), update_rig=True)
        self.assertEqual(self.operator.invoke(self.context, None), {'RUNNING_MODAL'})
        self.helper.require_full_update_allowed.assert_called_once_with(
            Path('/project/Assets/Cosha/Cosha.fbx'), asset_id='a' * 32, unity_project=Path('/project'))
        self.exporter.begin_export.assert_called_once_with(self.context, self.rig, 'full-scope-proxy')
        self.exporter.begin_candidate_export.assert_not_called()
        self.helper.receiver_capabilities.assert_not_called()

    def test_existing_ledger_blocks_full_before_proxy_or_any_worker(self):
        self.selection.update(meshes=(self.body, self.dress), update_rig=True)
        self.helper.require_full_update_allowed.side_effect = ValueError('accepted partial state exists')
        self.assertEqual(self.operator.invoke(self.context, None), {'CANCELLED'})
        self.assertEqual(self.operator.execute(self.context), {'CANCELLED'})
        self.globals['_full_update_config'].assert_not_called()
        self.exporter.begin_export.assert_not_called()
        self.exporter.export_character.assert_not_called()

    def test_outside_unity_full_route_preserves_legacy_export_compatibility(self):
        self.selection.update(meshes=(self.body, self.dress), update_rig=True)
        self.globals['_existing_unity_project'].return_value = None
        self.assertEqual(self.operator.execute(self.context), {'FINISHED'})
        self.helper.require_full_update_allowed.assert_not_called()
        self.exporter.export_character.assert_called_once_with(self.context, self.rig, 'full-scope-proxy')
        self.assertEqual(self.config.last_status, 'Exported successfully')

    def test_candidate_invoke_and_modal_dispatch_never_call_full_publisher(self):
        self.assertEqual(self.operator.invoke(self.context, None), {'RUNNING_MODAL'})
        self.exporter.begin_candidate_export.assert_called_once_with(
            self.context, self.rig, self.config, selection=self.selection)
        self.assertEqual(self.operator.modal(self.context, SimpleNamespace(type='MOUSEMOVE')), {'PASS_THROUGH'})
        self.assertEqual(self.operator.modal(self.context, SimpleNamespace(type='TIMER')), {'FINISHED'})
        self.exporter.poll_candidate_export.assert_called_once_with(self.job)
        self.exporter.begin_export.assert_not_called()
        self.exporter.poll_export.assert_not_called()
        self.assertEqual(self.config.last_status, 'Pending Unity review')
        self.assertEqual(self.config.last_report, self.result['candidate_path'])
        self.context.window_manager.event_timer_remove.assert_called_once_with(self.timer)
        self.assertNotIn(self.operator, self.globals['_MODAL_EXPORTS'])

    def test_candidate_result_without_filepath_reports_pending_not_accepted_export(self):
        self.operator._result(self.config, self.result)
        self.assertEqual(self.config.last_status, 'Pending Unity review')
        self.assertTrue(all(level == {'INFO'} for level, text in self.operator.reports))
        self.assertTrue(any('review' in text for level, text in self.operator.reports))
        self.assertFalse(any(text.startswith('Exported ') for level, text in self.operator.reports))

    def test_synchronous_candidate_polling_uses_current_selection_and_pending_status(self):
        self.exporter.poll_candidate_export.side_effect = [None, self.result]
        self.assertEqual(self.operator.execute(self.context), {'FINISHED'})
        self.assertEqual(self.exporter.poll_candidate_export.call_count, 2)
        self.exporter.export_character.assert_not_called()
        self.exporter.cancel_export.assert_not_called()
        self.assertEqual(self.config.last_status, 'Pending Unity review')

    def test_sync_candidate_timeout_cancels_private_job(self):
        self.exporter.poll_candidate_export.return_value = None
        self.globals['time'].monotonic.side_effect = [0, 601]
        self.assertEqual(self.operator.execute(self.context), {'CANCELLED'})
        self.exporter.cancel_export.assert_called_once_with(self.job)
        self.assertTrue(self.config.last_status.startswith('Export failed:'))

    def test_sync_candidate_poll_error_cancels_private_job(self):
        self.exporter.poll_candidate_export.side_effect = ValueError('changed base')
        self.assertEqual(self.operator.execute(self.context), {'CANCELLED'})
        self.exporter.cancel_export.assert_called_once_with(self.job)
        self.exporter.export_character.assert_not_called()

    def test_escape_cancels_candidate_and_removes_modal_timer(self):
        self.assertEqual(self.operator.invoke(self.context, None), {'RUNNING_MODAL'})
        self.assertEqual(self.operator.modal(self.context, SimpleNamespace(type='ESC', value='PRESS')), {'CANCELLED'})
        self.exporter.cancel_export.assert_called_once_with(self.job)
        self.context.window_manager.event_timer_remove.assert_called_once_with(self.timer)
        self.assertNotIn(self.operator, self.globals['_MODAL_EXPORTS'])

    def test_cleared_identity_cannot_bypass_full_guard_for_existing_formal_bundle(self):
        self.selection.update(meshes=(self.body, self.dress), update_rig=True)
        self.config.asset_id = ''
        self.helper._io = lambda value: SimpleNamespace(exists=lambda: True)
        self.assertEqual(self.operator.execute(self.context), {'CANCELLED'})
        self.exporter.export_character.assert_not_called()

    def test_panel_mesh_candidate_requires_capability_and_ready_notice_is_not_faked(self):
        self.helper.receiver_capabilities.return_value = None
        self.assertFalse(self.export_enabled(self.draw_panel()))
        self.assertFalse(self.globals['update_selection'].draw_selection.call_args.kwargs['pipeline_ready'])
        self.helper.receiver_capabilities.return_value = {'supports_forearm_mesh': False}
        self.assertTrue(self.export_enabled(self.draw_panel()))
        self.assertTrue(self.globals['update_selection'].draw_selection.call_args.kwargs['pipeline_ready'])

    def test_panel_body_candidate_warns_about_correction_review(self):
        self.selection['meshes'] = (self.body,)
        log = self.draw_panel()
        self.assertTrue(self.export_enabled(log))
        self.assertIn(('label', 'Body changes require correction review in Unity.'), log)

    def test_panel_full_ledger_and_partial_rig_paths_are_disabled(self):
        self.selection.update(meshes=(self.body, self.dress), update_rig=True)
        self.helper.require_full_update_allowed.side_effect = ValueError('accepted partial state exists')
        self.assertFalse(self.export_enabled(self.draw_panel()))
        self.selection['meshes'] = (self.dress,)
        self.assertFalse(self.export_enabled(self.draw_panel()))
        self.assertTrue(self.globals['update_selection'].draw_selection.call_args.kwargs['pipeline_ready'])

    def test_panel_full_export_stays_available_without_partial_receiver(self):
        self.selection.update(meshes=(self.body, self.dress), update_rig=True)
        self.helper.receiver_capabilities.return_value = None
        log = self.draw_panel()
        self.assertTrue(self.export_enabled(log))
        self.assertIn(('label', 'Full Mesh + Rig update available.'), log)

    def test_ready_receiver_notice_stays_true_for_full_and_empty_selection(self):
        self.selection.update(meshes=(self.body, self.dress), update_rig=True)
        self.draw_panel()
        self.assertTrue(self.globals['update_selection'].draw_selection.call_args.kwargs['pipeline_ready'])
        self.selection.update(meshes=(), update_rig=False)
        log = self.draw_panel()
        self.assertFalse(self.export_enabled(log))
        self.assertTrue(self.globals['update_selection'].draw_selection.call_args.kwargs['pipeline_ready'])
        self.assertIn(('label', 'Nothing selected; Unity is unchanged.'), log)

    def test_invalid_capability_does_not_block_legacy_full_export(self):
        self.selection.update(meshes=(self.body, self.dress), update_rig=True)
        self.helper.receiver_capabilities.side_effect = ValueError('invalid receiver announcement')
        self.assertTrue(self.export_enabled(self.draw_panel()))
        self.assertFalse(self.globals['update_selection'].draw_selection.call_args.kwargs['pipeline_ready'])

    def test_export_button_is_disabled_in_edit_mode_for_full_and_candidate(self):
        self.context.mode = 'EDIT_MESH'
        self.assertFalse(self.export_enabled(self.draw_panel()))
        self.selection.update(meshes=(self.body, self.dress), update_rig=True)
        self.assertFalse(self.export_enabled(self.draw_panel()))
        self.context.mode = 'POSE'
        self.assertTrue(self.export_enabled(self.draw_panel()))

    def test_nested_assets_folder_still_finds_actual_project_and_runs_full_ledger_guard(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == '_existing_unity_project')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), self.globals)
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / 'UnityProject'
            model = project / 'Assets/Clothes/Assets/Nested.fbx'
            model.parent.mkdir(parents=True)
            (project / 'ProjectSettings').mkdir()
            self.assertEqual(self.globals['_existing_unity_project'](model), project)
            self.exporter._target.return_value = (model.parent, model.name)
            self.helper.require_full_update_allowed.side_effect = ValueError('outer accepted partial state exists')
            self.selection.update(meshes=(self.body, self.dress), update_rig=True)
            self.assertEqual(self.operator.execute(self.context), {'CANCELLED'})
            self.helper.require_full_update_allowed.assert_called_once_with(model, asset_id='a' * 32, unity_project=project)
            self.exporter.export_character.assert_not_called()


if __name__ == '__main__':
    unittest.main()
