"""The reversible Original display must not block a complete Body operation."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'addons'), str(ROOT/'tests')]
import character_designer
from character_designer import body_setup, bone_display, body_detail_visuals
import test_body_setup_plan_blender as fixture


class DisplayLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def test_remove_from_original_view(self):
        rig, _body = fixture.fixture()
        body_setup.generate(bpy.context, rig)
        bone_display.show_native(bpy.context, rig, 'ORIGINAL')
        self.assertEqual(bone_display.view_mode(rig), 'ORIGINAL')
        result = body_setup.remove(bpy.context, rig)
        self.assertTrue(result['removed'])
        self.assertIsNone(bone_display.view_mode(rig))
        self.assertFalse(body_setup.has_generated(rig))

    def test_failed_update_restores_original_display(self):
        rig, _body = fixture.fixture()
        body_setup.generate(bpy.context, rig)
        body_detail_visuals.remove(bpy.context, rig)
        bone_display.show_native(bpy.context, rig, 'ORIGINAL')
        before = bone_display._snapshot(rig)
        view = rig.data[bone_display.VIEW_KEY]
        with patch.object(body_setup, '_add', side_effect=RuntimeError('Injected failure')):
            with self.assertRaisesRegex(RuntimeError, 'Injected failure'):
                body_setup.generate(bpy.context, rig)
        self.assertEqual(bone_display._snapshot(rig), before)
        self.assertEqual(rig.data[bone_display.VIEW_KEY], view)
        result = body_setup.generate(bpy.context, rig)
        self.assertEqual(result['created'], ['BODY_DETAIL'])
        self.assertIsNone(bone_display.view_mode(rig))


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DisplayLifecycle)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
