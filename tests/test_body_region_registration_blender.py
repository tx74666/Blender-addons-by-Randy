"""Native registration, persistent settings and placement Undo in a test scene.

Run only with --background --factory-startup --python-exit-code 1 --python.
No artist file is read or modified; saved files live in a temporary directory.
"""
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

import addon_utils
import bpy
from bpy.props import PointerProperty

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer as addon
from character_designer import body_region_ui, character_ground as placement, body_calibration
from test_character_ground_blender import activate, make_humanoid, mesh, reset_scene, raw_mesh


def enable():
    errors = []

    def failed(*_args):
        errors.append(traceback.format_exc())

    module = addon_utils.enable('character_designer', default_set=False, persistent=False, handle_error=failed)
    if module is None or errors:
        raise AssertionError('\n'.join(errors) or 'Character Designer enable returned None.')
    module._validate_registration_integrity()
    return module


def disable():
    if addon_utils.check('character_designer')[1]:
        addon_utils.disable('character_designer', default_set=False)


class RegionalRegistrationTests(unittest.TestCase):
    def setUp(self):
        disable()

    def tearDown(self):
        disable()
        # Failure injection intentionally bypasses addon_utils; remove only
        # this test's leftover full registration if the test exposed a failure.
        if hasattr(bpy.types.WindowManager, 'character_designer'):
            addon.unregister()

    def assert_new_registration(self, enabled):
        expected = (
            ('character_designer_body_region', body_region_ui.CharacterDesignerBodyRegionState),
            ('character_designer_ground', placement.CharacterDesignerGroundState),
        )
        for name, cls in expected:
            prop = bpy.types.Scene.bl_rna.properties.get(name)
            if enabled:
                self.assertIsNotNone(prop)
                self.assertEqual(prop.type, 'POINTER')
                self.assertEqual(prop.fixed_type, cls.bl_rna)
            else:
                self.assertIsNone(prop)
        for cls in (*body_region_ui.BODY_REGION_UI_CLASSES, *placement.CHARACTER_GROUND_CLASSES):
            registered = addon._registered_rna_class(cls)
            self.assertIs(registered, cls if enabled else None)

    def test_real_enable_disable_reenable_and_idempotent_register(self):
        enable()
        self.assert_new_registration(True)
        bpy.context.scene.character_designer_body_region.region = 'RIGHT_ARM'
        bpy.context.scene.character_designer_ground.distance = -.125
        addon.register()
        self.assert_new_registration(True)
        disable()
        self.assert_new_registration(False)
        enable()
        self.assert_new_registration(True)
        self.assertEqual(bpy.context.scene.character_designer_body_region.region, 'RIGHT_ARM')
        self.assertAlmostEqual(bpy.context.scene.character_designer_ground.distance, -.125)

    def test_scene_settings_survive_save_reopen_while_addon_is_disabled(self):
        enable()
        scene = bpy.data.scenes.new('Regional persistence test scene')
        scene.use_fake_user = True
        scene.character_designer_body_region.region = 'LEFT_LEG'
        scene.character_designer_ground.distance = -.271828
        scene_name = scene.name
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'regional_settings.blend')
            self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=path), {'FINISHED'})
            scene.character_designer_body_region.region = 'ARMS'
            scene.character_designer_ground.distance = 9.
            disable()
            self.assert_new_registration(False)
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=path), {'FINISHED'})
            enable()
            self.assert_new_registration(True)
            reopened = bpy.data.scenes[scene_name]
            self.assertEqual(reopened.character_designer_body_region.region, 'LEFT_LEG')
            self.assertAlmostEqual(reopened.character_designer_ground.distance, -.271828, places=6)

    def test_new_ground_class_registration_failure_rolls_back_and_can_enable_again(self):
        original = bpy.utils.register_class

        def injected(cls):
            if cls is placement.CharacterDesignerGroundState:
                raise RuntimeError('Injected new Ground class failure')
            return original(cls)

        with patch.object(bpy.utils, 'register_class', side_effect=injected):
            with self.assertRaisesRegex(RuntimeError, 'Injected new Ground class failure'):
                addon.register()
        self.assert_new_registration(False)
        self.assertFalse(hasattr(bpy.types.WindowManager, 'character_designer'))
        enable()
        self.assert_new_registration(True)

    def test_failure_after_scene_properties_rolls_them_back_and_can_enable_again(self):
        with patch.object(addon, 'register_mesh_mirror_runtime', side_effect=RuntimeError('Injected runtime failure')):
            with self.assertRaisesRegex(RuntimeError, 'Injected runtime failure'):
                addon.register()
        self.assert_new_registration(False)
        self.assertFalse(hasattr(bpy.types.WindowManager, 'character_designer'))
        enable()
        self.assert_new_registration(True)

    def test_integrity_rejects_a_missing_scene_placement_pointer(self):
        enable()
        del bpy.types.Scene.character_designer_ground
        try:
            with self.assertRaisesRegex(RuntimeError, 'character placement settings'):
                addon._validate_registration_integrity()
            with self.assertRaisesRegex(RuntimeError, 'partially registered'):
                addon.register()
        finally:
            bpy.types.Scene.character_designer_ground = PointerProperty(type=placement.CharacterDesignerGroundState)
        addon._validate_registration_integrity()

    def test_real_move_and_ground_operators_undo_redo_keep_native_rest(self):
        enable()
        reset_scene()
        rig = make_humanoid('Placement Undo Character')
        body = mesh('Placement Undo Body', .45, rig, rig)
        shoes = mesh('Placement Undo Shoes', .25, rig)
        activate(rig)
        bpy.context.preferences.edit.use_global_undo = True
        bpy.context.scene.character_designer_ground.distance = .2
        bpy.context.view_layer.update()
        rig_name, body_name, shoe_name = rig.name, body.name, shoes.name
        rest = body_calibration.native_rest(rig)
        raw = raw_mesh(body), raw_mesh(shoes)
        initial_z = rig.matrix_world.translation.z
        bpy.ops.ed.undo_push(message='Before character placement')
        self.assertEqual(bpy.ops.character_designer.move_character(), {'FINISHED'})
        bpy.ops.ed.undo_push(message='After character move')
        moved_z = bpy.data.objects[rig_name].matrix_world.translation.z
        self.assertAlmostEqual(moved_z, initial_z + .2, places=6)
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        self.assertAlmostEqual(bpy.data.objects[rig_name].matrix_world.translation.z, initial_z, places=6)
        self.assertEqual(body_calibration.native_rest(bpy.data.objects[rig_name]), rest)
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        self.assertAlmostEqual(bpy.data.objects[rig_name].matrix_world.translation.z, moved_z, places=6)
        self.assertEqual(body_calibration.native_rest(bpy.data.objects[rig_name]), rest)
        self.assertEqual(bpy.ops.character_designer.ground_character(), {'FINISHED'})
        bpy.ops.ed.undo_push(message='After character ground')
        grounded_z = bpy.data.objects[rig_name].matrix_world.translation.z
        self.assertAlmostEqual(grounded_z, -.25, places=6)
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        self.assertAlmostEqual(bpy.data.objects[rig_name].matrix_world.translation.z, moved_z, places=6)
        self.assertEqual(body_calibration.native_rest(bpy.data.objects[rig_name]), rest)
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        self.assertAlmostEqual(bpy.data.objects[rig_name].matrix_world.translation.z, grounded_z, places=6)
        self.assertEqual(body_calibration.native_rest(bpy.data.objects[rig_name]), rest)
        self.assertEqual((raw_mesh(bpy.data.objects[body_name]), raw_mesh(bpy.data.objects[shoe_name])), raw)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(RegionalRegistrationTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
