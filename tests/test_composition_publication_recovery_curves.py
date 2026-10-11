"""Pure actual-helper regressions for Blender FCurve driver-property admission."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'tools' / 'run_character_composition_publication_recovery.py'


def actual_functions():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    names = {'require', 'canonical', 'curve_rows'}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != names:
        raise RuntimeError('The exact recovery helper boundary changed.')
    namespace = {'json': json}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['curve_rows']


class RecoveryCurveDriverAdmission(unittest.TestCase):
    def setUp(self):
        self.curve_rows = actual_functions()
        self.curve = SimpleNamespace(data_path='pose.bones["f_index.01.L"].rotation_quaternion',
            array_index=0, group=None, modifiers=[], sampled_points=[], driver=None)
        self.reads = []
        def payload(curve):
            self.reads.append(curve)
            return {'path': curve.data_path, 'index': curve.array_index, 'value': 1.}
        self.backend = SimpleNamespace(_bag=lambda _action, _slot: SimpleNamespace(fcurves=[self.curve]),
            _curve_payload=payload)
        self.fp = SimpleNamespace(_curve_payload=payload)

    def test_native_curve_without_is_driver_property_is_accepted(self):
        self.assertFalse(hasattr(self.curve, 'is_driver'))
        payload, fingerprint = self.curve_rows(object(), 929201142, self.backend, self.fp)
        self.assertEqual(payload, fingerprint)
        self.assertEqual(len(self.reads), 2)

    def test_real_driver_object_rejects_before_curve_payload_read(self):
        self.curve.driver = object()
        with self.assertRaisesRegex(ValueError, 'drivers'):
            self.curve_rows(object(), 929201142, self.backend, self.fp)
        self.assertEqual(self.reads, [])

    def test_explicit_is_driver_true_rejects_before_curve_payload_read(self):
        self.curve.is_driver = True
        with self.assertRaisesRegex(ValueError, 'drivers'):
            self.curve_rows(object(), 929201142, self.backend, self.fp)
        self.assertEqual(self.reads, [])


if __name__ == '__main__':
    unittest.main()
