"""Pure routing/rollback spies for Apply's protected Auto Key bridge."""
import ast
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/control_pose_assets.py'


class Values(dict):
    metadata = {'version': 1}


class ApplyDomainRoutingTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf8'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == 'CHARACTERDESIGNER_OT_apply_control_pose')
        function = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'execute')
        self.package = ModuleType('_apply_domain_test_package')
        self.package.__path__ = []
        self.body = NS(has_generated=Mock(return_value=True))
        self.pose = NS(active=Mock(return_value=True), _pose_owner=Mock(return_value=True))
        self.session = {'closed': False}
        self.blend = NS(begin=Mock(return_value=self.session), preview=Mock(), confirm=Mock(), cancel=Mock())
        self.package.body_setup, self.package.animation_pose_test = self.body, self.pose
        self.package.control_pose_blend = self.blend
        self.namespace = dict(__package__=self.package.__name__, __name__=self.package.__name__ + '.poses',
            ASSET_METADATA='metadata', _asset_channels=Mock(return_value=Values({'arm.L': {}})),
            _needs_control_matching=Mock(return_value=True), _apply_native=Mock(return_value={'FINISHED'}),
            apply_channels=Mock(), match=NS(_update=Mock()),
            bpy=NS(ops=NS(poselib=NS(apply_pose_asset=Mock(return_value={'FINISHED'})))))
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), self.namespace)
        self.rig = NS(data=NS(users=1), animation_data=NS(action=object()))
        self.context = NS(object=self.rig, asset=NS(local_id={'metadata': 'value'}),
            scene=NS(tool_settings=NS(use_keyframe_insert_auto=True), character_designer_animation_worklist=NS(items=[])))
        self.operator = NS(flipped=False, report=Mock())
        self.imports = patch.dict(sys.modules, {self.package.__name__: self.package})
        self.imports.start()
        self.addCleanup(self.imports.stop)

    def execute(self):
        return self.namespace['execute'](self.operator, self.context)

    def test_pose_test_autokey_uses_fixed_one_and_full_asset_region(self):
        self.assertEqual(self.execute(), {'FINISHED'})
        self.blend.begin.assert_called_once_with(self.context, rig=self.rig,
            values=self.namespace['_asset_channels'].return_value, metadata=Values.metadata,
            flipped=False, respect_selection=False)
        self.blend.preview.assert_called_once_with(self.context, self.session, 1.)
        self.blend.confirm.assert_called_once_with(self.context, self.session)
        self.namespace['apply_channels'].assert_not_called()
        self.namespace['_asset_channels'].assert_called_once()

    def test_actual_worklist_action_routes_and_source_can_refuse_before_preview(self):
        self.pose.active.return_value = False
        self.context.scene.character_designer_animation_worklist.items = [NS(
            rig=self.rig, source_action=self.rig.animation_data.action, custom_action=object())]
        self.blend.begin.side_effect = ValueError('Source is read-only')
        self.assertEqual(self.execute(), {'CANCELLED'})
        self.blend.preview.assert_not_called()
        self.namespace['apply_channels'].assert_not_called()
        self.assertIn('Source is read-only', self.operator.report.call_args.args[1])

    def test_native_destination_fallback_precedes_source_policy(self):
        self.namespace['_asset_channels'].return_value = {'finger.L': {}}
        self.namespace['_needs_control_matching'].return_value = False
        self.assertEqual(self.execute(), {'FINISHED'})
        self.namespace['_apply_native'].assert_called_once()
        self.blend.begin.assert_not_called()

    def test_control_authored_native_fallback_precedes_pose_owner(self):
        self.namespace['_asset_channels'].return_value = None
        self.assertEqual(self.execute(), {'FINISHED'})
        self.namespace['bpy'].ops.poselib.apply_pose_asset.assert_called_once_with(flipped=False)
        self.blend.begin.assert_not_called()

    def test_autokey_off_or_unowned_action_keeps_old_apply_path(self):
        for autokey, active in ((False, True), (True, False)):
            with self.subTest(autokey=autokey, pose_active=active):
                self.context.scene.tool_settings.use_keyframe_insert_auto = autokey
                self.pose.active.return_value = active
                self.assertEqual(self.execute(), {'FINISHED'})
        self.assertEqual(self.namespace['apply_channels'].call_count, 2)
        self.blend.begin.assert_not_called()

    def test_preview_failure_cancels_open_session_once(self):
        self.blend.preview.side_effect = RuntimeError('preview fault')
        self.assertEqual(self.execute(), {'CANCELLED'})
        self.blend.cancel.assert_called_once_with(self.context, self.session)

    def test_confirm_closed_failure_does_not_repeat_its_rollback(self):
        def fail(*_args):
            self.session['closed'] = True
            raise RuntimeError('key fault')
        self.blend.confirm.side_effect = fail
        self.assertEqual(self.execute(), {'CANCELLED'})
        self.blend.cancel.assert_not_called()

    def test_failed_cancel_reports_owned_recovery_limit(self):
        self.blend.preview.side_effect = RuntimeError('preview fault')
        self.blend.cancel.side_effect = RuntimeError('restore refusal')
        self.assertEqual(self.execute(), {'CANCELLED'})
        self.assertIn('owned recovery needs review', self.operator.report.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
