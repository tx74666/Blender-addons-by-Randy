"""Focused registration-failure cleanup; no playback or performance exercise."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/playback_normal_bridge.py'


def subject():
    own, foreign = object(), object()
    handlers = [foreign, own, own]
    managed, active = {'known': object()}, {'active': object()}
    order = []
    namespace = dict(
        bpy=NS(app=NS(handlers=NS(load_post=handlers))),
        _HANDLERS=(('load_post', own),),
        _PLAYING={'scene': True}, _BLOCKED={'blocked'},
        _MANAGED=managed, _ACTIVE=active, _REGISTERED=True, _RECOVERY_PENDING=True,
        _recover_pending=Mock(side_effect=lambda: order.append('discover')),
        _off_all=Mock(side_effect=lambda: order.append('off') or True),
    )
    tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'unregister')
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace, handlers, own, foreign, managed, active, order


class RegistrationRollbackTests(unittest.TestCase):
    def test_default_unregister_still_discovers_pending_then_disables_known_owned(self):
        namespace, handlers, _own, foreign, managed, active, order = subject()
        namespace['unregister']()
        namespace['_recover_pending'].assert_called_once_with()
        namespace['_off_all'].assert_called_once_with()
        self.assertEqual(order, ['discover', 'off'])
        self.assertEqual(handlers, [foreign])
        self.assertFalse(namespace['_REGISTERED'])
        self.assertFalse(namespace['_RECOVERY_PENDING'])
        self.assertEqual(active, {})
        self.assertIn('known', managed)

    def test_fresh_failure_skips_discovery_but_disables_known_and_cleans_owned_hooks(self):
        namespace, handlers, _own, foreign, managed, active, order = subject()
        namespace['unregister'](recover_pending=False)
        namespace['_recover_pending'].assert_not_called()
        namespace['_off_all'].assert_called_once_with()
        self.assertEqual(order, ['off'])
        self.assertEqual(handlers, [foreign])
        self.assertFalse(namespace['_REGISTERED'])
        self.assertFalse(namespace['_RECOVERY_PENDING'])
        self.assertEqual(active, {})
        self.assertIn('known', managed)

    def test_known_owned_disable_failure_refuses_and_retains_guards_in_both_modes(self):
        for recover in (True, False):
            with self.subTest(recover=recover):
                namespace, handlers, own, foreign, managed, active, _order = subject()
                namespace['_off_all'] = Mock(return_value=False)
                before_active = dict(active)
                with self.assertRaisesRegex(RuntimeError, 'cleanup failed; guards remain registered'):
                    namespace['unregister'](recover_pending=recover)
                self.assertEqual(handlers, [foreign, own, own])
                self.assertTrue(namespace['_REGISTERED'])
                self.assertTrue(namespace['_RECOVERY_PENDING'])
                self.assertEqual(active, before_active)
                self.assertIn('known', managed)
                self.assertEqual(namespace['_recover_pending'].call_count, int(recover))

    def test_default_discovery_failure_is_not_silently_ignored(self):
        namespace, handlers, own, foreign, _managed, active, _order = subject()
        before_active = dict(active)
        namespace['_recover_pending'] = Mock(side_effect=AttributeError('restricted scene data'))
        with self.assertRaisesRegex(AttributeError, 'restricted scene data'):
            namespace['unregister']()
        namespace['_off_all'].assert_not_called()
        self.assertEqual(handlers, [foreign, own, own])
        self.assertTrue(namespace['_REGISTERED'])
        self.assertTrue(namespace['_RECOVERY_PENDING'])
        self.assertEqual(active, before_active)


if __name__ == '__main__':
    unittest.main()
