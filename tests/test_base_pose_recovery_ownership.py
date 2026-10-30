"""The opt-in matcher isolates pose/display to one actual Object owner."""
import ast
from contextlib import contextmanager
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


class MatchReached(Exception):
    pass


class RecoveryOwnership(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'addons/character_designer/control_pose_assets.py'
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        defs = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name in {'apply_channels', '_apply_channels'}]
        self.package = ModuleType('_pose_ownership_test')
        self.package.__path__ = []
        self.package.body_original_mode = ModuleType('_pose_ownership_test.body_original_mode')
        self.proof = Mock(return_value=False)
        self.package.animation_pose_test = NS(_pose_owner=self.proof)
        self.update = Mock(side_effect=MatchReached)
        self.globals = {'__package__': self.package.__name__,
                        'limb_ik': NS(_single_armature_user=self.proof),
                        'match': NS(_update=self.update),
                        '_validate_stable_ik_seed_metadata': lambda _: {}}
        exec(compile(ast.Module(body=defs, type_ignores=[]), str(source), 'exec'), self.globals)
        self.context = NS(mode='POSE')
        self.rig = NS(library=None, data=NS(library=None, users=7))
        modules = patch.dict(sys.modules, {self.package.__name__: self.package})
        modules.start()
        self.addCleanup(modules.stop)

    def test_default_rejects_extra_users_without_invoking_proof(self):
        with self.assertRaisesRegex(ValueError, 'single-user'):
            self.globals['_apply_channels'](self.context, self.rig, {})
        self.proof.assert_not_called()
        self.update.assert_not_called()

    def test_opt_in_rejects_unproven_refs_before_matching(self):
        with self.assertRaisesRegex(ValueError, 'single-user'):
            self.globals['_apply_channels'](self.context, self.rig, {}, _pose_only_owner=True)
        self.proof.assert_called_once_with(self.rig)
        self.update.assert_not_called()

    def test_opt_in_authenticates_again_on_every_call(self):
        self.proof.side_effect = [True, False]
        with self.assertRaises(MatchReached):
            self.globals['_apply_channels'](self.context, self.rig, {}, _pose_only_owner=True)
        with self.assertRaisesRegex(ValueError, 'single-user'):
            self.globals['_apply_channels'](self.context, self.rig, {}, _pose_only_owner=True)
        self.assertEqual(self.proof.call_count, 2)

    def test_opt_in_does_not_admit_linked_rigs_or_edit_mode(self):
        for target, field, value in ((self.rig, 'library', object()),
                                     (self.rig.data, 'library', object()),
                                     (self.context, 'mode', 'EDIT_ARMATURE')):
            old = getattr(target, field)
            setattr(target, field, value)
            with self.assertRaisesRegex(ValueError, 'single-user'):
                self.globals['_apply_channels'](self.context, self.rig, {}, _pose_only_owner=True)
            setattr(target, field, old)
        self.proof.assert_not_called()

    def test_option_requires_exact_boolean(self):
        for invalid in (None, 1, 'yes', object()):
            with self.assertRaisesRegex(ValueError, 'ownership option'):
                self.globals['_apply_channels'](self.context, self.rig, {}, _pose_only_owner=invalid)
        self.proof.assert_not_called()

    def test_public_wrapper_forwards_option_and_preserves_failure_refresh(self):
        refresh = Mock()
        @contextmanager
        def defer(*args, **kwargs):
            yield refresh
        self.package.forearm_twist = NS(defer_runtime=defer)
        delegate = Mock(side_effect=MatchReached)
        self.globals['_apply_channels'] = delegate
        with self.assertRaises(MatchReached):
            self.globals['apply_channels'](self.context, self.rig, {}, _pose_only_owner=True)
        self.assertIs(delegate.call_args.kwargs['_pose_only_owner'], True)
        self.assertIs(delegate.call_args.kwargs['refresh'], refresh)
        refresh.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
