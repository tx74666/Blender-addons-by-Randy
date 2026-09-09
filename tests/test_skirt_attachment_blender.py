"""Actual-pose reattachment, persistent restore, and failure isolation in Blender."""

import importlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("character_designer")
package.__path__ = [str(ROOT / "addons" / "character_designer")]
sys.modules["character_designer"] = package
service = importlib.import_module("character_designer.skirt_rig")
physics = importlib.import_module("character_designer.skirt_physics")


def rig_fixture(name, offset):
    data = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(rig)
    service._activate(bpy.context, rig, "EDIT")
    for name, x in (("Hips", 0.0), ("Other", 0.2)):
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = (x, 0, 1), (x, 0, 1.2)
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.location = offset
    rig.rotation_euler = (0.14, -0.09, 0.31)
    rig.scale = (1.1, 0.92, 1.05)
    return rig


def fixture():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sides, rows = 16, 6
    vertices = []
    for row in range(rows):
        fraction = row / (rows - 1)
        for col in range(sides):
            angle = math.tau * col / sides
            vertices.append(((0.35 + fraction * 0.3) * math.cos(angle),
                             (0.3 + fraction * 0.2) * math.sin(angle), 1.2 - fraction * 0.8))
    faces = [(row * sides + col, row * sides + (col + 1) % sides,
              (row + 1) * sides + (col + 1) % sides, (row + 1) * sides + col)
             for row in range(rows - 1) for col in range(sides)]
    mesh = bpy.data.meshes.new("Dress")
    mesh.from_pydata(vertices, [], faces)
    source = bpy.data.objects.new("Dress", mesh)
    bpy.context.collection.objects.link(source)
    source.location = (0.2, -0.1, 0.15)
    source.vertex_groups.new(name="Artist pin").add([1, 3], 0.42, "REPLACE")
    first = rig_fixture("First", (0.2, -0.4, 0.05))
    second = rig_fixture("Second", (1.2, 0.5, -0.25))
    record = service.build_skirt(bpy.context, source, armature=first)
    return source, source[service.RIG_KEY], first, second, record


def world_vertices(source):
    evaluated = source.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def weights(source):
    return [(group.name, [(v.index, entry.weight) for v in source.data.vertices for entry in v.groups
                          if entry.group == group.index]) for group in source.vertex_groups]


def action_state(rig):
    action = rig.animation_data.action
    curves = [curve for layer in action.layers for strip in layer.strips
              for bag in strip.channelbags for curve in bag.fcurves]
    return [(curve.data_path, curve.array_index, [(tuple(key.co), tuple(key.handle_left),
              tuple(key.handle_right), key.interpolation) for key in curve.keyframe_points]) for curve in curves]


def channels(rig):
    return (tuple(rig.location), tuple(rig.rotation_euler), tuple(rig.scale),
            tuple(rig.delta_location), tuple(rig.delta_rotation_euler), tuple(rig.delta_scale),
            [(bone.name, tuple(bone.location), tuple(bone.rotation_euler), tuple(bone.scale))
             for bone in rig.pose.bones])


class AttachmentTests(unittest.TestCase):
    def assert_matrix(self, a, b, tolerance=1e-5):
        self.assertLess(max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4)), tolerance)

    def test_update_preserves_posed_geometry_channels_and_follows_new_hips(self):
        for relative in (False, True):
            with self.subTest(relative_parent=relative):
                source, rig, first, second, record = fixture()
                second.data.bones["Hips"].use_relative_parent = relative
                control = rig.pose.bones[record["controls"]["hem"]]
                control.location.x = 0.03
                control.keyframe_insert("location", frame=1)
                control.location.x = 0.08
                control.keyframe_insert("location", frame=8)
                rig.keyframe_insert("location", frame=1)
                rig.location.x += 0.04
                rig.keyframe_insert("location", frame=8)
                rig.delta_location = (0.01, 0.02, -0.01)
                first.pose.bones["Hips"].rotation_mode = "XYZ"
                first.pose.bones["Hips"].rotation_euler = (0.07, 0.04, -0.12)
                second.pose.bones["Hips"].rotation_mode = "XYZ"
                second.pose.bones["Hips"].rotation_euler = (-0.08, 0.17, 0.09)
                bpy.context.scene.frame_set(4)
                before_world = rig.matrix_world.copy()
                before_vertices = world_vertices(source)
                before_channels, before_action, before_weights = channels(rig), action_state(rig), weights(source)
                count = len(bpy.data.objects)
                service.update_attachment(bpy.context, source, second)
                self.assert_matrix(rig.matrix_world, before_world)
                self.assertLess(max((a - b).length for a, b in zip(before_vertices, world_vertices(source))), 5e-5)
                self.assertEqual(channels(rig), before_channels)
                self.assertEqual(action_state(rig), before_action)
                self.assertEqual(weights(source), before_weights)
                self.assertEqual(len(bpy.data.objects), count)
                saved = source[service.ATTACHMENT_BACKUP_KEY]
                service.update_attachment(bpy.context, source, second)
                self.assertEqual(source[service.ATTACHMENT_BACKUP_KEY], saved)
                start = rig.matrix_world.translation.copy()
                second.pose.bones["Hips"].location.x += 0.2
                bpy.context.view_layer.update()
                expected = second.matrix_world.to_3x3() @ second.data.bones["Hips"].matrix_local.to_3x3().col[0] * 0.2
                self.assertLess(((rig.matrix_world.translation - start) - expected).length, 1e-5)

    def test_first_baseline_survives_repeat_save_reopen_and_restores_exact_space(self):
        source, rig, first, second, record = fixture()
        original_inverse = rig.matrix_parent_inverse.copy()
        original_record = json.loads(source[service.RECORD_KEY])["original"]
        service.update_attachment(bpy.context, source, second)
        saved = source[service.ATTACHMENT_BACKUP_KEY]
        service.update_attachment(bpy.context, source, second, "Other")
        self.assertEqual(source[service.ATTACHMENT_BACKUP_KEY], saved)
        first.name = "Renamed First"
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "attachment.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            source = bpy.data.objects["Dress"]
            rig = source[service.RIG_KEY]
            self.assertTrue(service.has_attachment_backup(source))
            first = bpy.data.objects["Renamed First"]
            first.pose.bones["Hips"].location.x = 0.4
            before = channels(rig)
            service.restore_attachment(bpy.context, source)
            self.assertIs(rig.parent, first)
            self.assertEqual(rig.parent_bone, "Hips")
            self.assert_matrix(rig.matrix_parent_inverse, original_inverse)
            self.assertEqual(channels(rig), before)
            self.assertFalse(service.has_attachment_backup(source))
            self.assertEqual(json.loads(source[service.RECORD_KEY])["original"], original_record)
            self.assertEqual(service.attachment_status(source)["character"], first)

    def test_live_status_ignores_stale_names_and_remove_keeps_original_baseline(self):
        source, rig, first, second, record = fixture()
        record["character"], record["parent_bone"] = "Stale", "Stale"
        service.write_record(source, record)
        state = service.attachment_status(source)
        self.assertIs(state["character"], first)
        self.assertEqual(state["parent_bone"], "Hips")
        self.assertTrue(state["attached"])
        service.update_attachment(bpy.context, source, second)
        service.remove_skirt(bpy.context, source)
        self.assertIsNone(source.parent)
        self.assertEqual(list(source.vertex_groups.keys()), ["Artist pin"])
        self.assertFalse(service.has_attachment_backup(source))
        self.assertNotIn(service.ATTACHMENT_PARENT_KEY, source)

    def test_invalid_changes_and_late_failures_leave_binding_unchanged(self):
        source, rig, first, second, record = fixture()
        before_world, before_record = rig.matrix_world.copy(), source[service.RECORD_KEY]
        for bone in ("Missing", "pelvis.L"):
            with self.assertRaises(service.SkirtRigError):
                service.update_attachment(bpy.context, source, second, bone)
        second.parent = rig
        with self.assertRaisesRegex(service.SkirtRigError, "cycle"):
            service.update_attachment(bpy.context, source, second)
        second.parent = None
        constraint = rig.constraints.new("COPY_LOCATION")
        with self.assertRaisesRegex(service.SkirtRigError, "constraints"):
            service.update_attachment(bpy.context, source, second)
        rig.constraints.remove(constraint)
        original_write = service._write_attachment_record
        def fail(*args):
            raise RuntimeError("Injected metadata failure")
        service._write_attachment_record = fail
        try:
            with self.assertRaisesRegex(service.SkirtRigError, "rolled back"):
                service.update_attachment(bpy.context, source, second)
        finally:
            service._write_attachment_record = original_write
        self.assertIs(rig.parent, first)
        self.assert_matrix(rig.matrix_world, before_world)
        self.assertEqual(source[service.RECORD_KEY], before_record)
        self.assertFalse(service.has_attachment_backup(source))
        service.update_attachment(bpy.context, source, second)
        saved = source[service.ATTACHMENT_BACKUP_KEY]
        service._write_attachment_record = fail
        try:
            with self.assertRaisesRegex(service.SkirtRigError, "rolled back"):
                service.restore_attachment(bpy.context, source)
        finally:
            service._write_attachment_record = original_write
        self.assertIs(rig.parent, second)
        self.assertEqual(source[service.ATTACHMENT_BACKUP_KEY], saved)

    def test_physics_update_is_noop_or_actionable_refusal(self):
        source, rig, first, second, record = fixture()
        bpy.context.scene.frame_set(1)
        physics.add_physics(bpy.context, source)
        before, count = source[service.RECORD_KEY], len(bpy.data.objects)
        service.update_attachment(bpy.context, source, first)
        with self.assertRaisesRegex(service.SkirtRigError, "Clearing the cache alone"):
            service.update_attachment(bpy.context, source, second)
        self.assertEqual(source[service.RECORD_KEY], before)
        self.assertEqual(len(bpy.data.objects), count)
        self.assertIs(rig.parent, first)
        self.assertFalse(service.has_attachment_backup(source))

    def test_shared_target_singular_bone_and_missing_previous_bone_are_safe(self):
        source, rig, first, second, record = fixture()
        duplicate = bpy.data.objects.new("Shared target", second.data)
        bpy.context.collection.objects.link(duplicate)
        with self.assertRaisesRegex(service.SkirtRigError, "unshared"):
            service.update_attachment(bpy.context, source, second)
        bpy.data.objects.remove(duplicate, do_unlink=True)
        second.pose.bones["Hips"].scale.x = 0.0
        with self.assertRaisesRegex(service.SkirtRigError, "zero-scale"):
            service.update_attachment(bpy.context, source, second)
        self.assertIs(rig.parent, first)
        self.assertFalse(service.has_attachment_backup(source))
        second.pose.bones["Hips"].scale.x = 1.0
        service.update_attachment(bpy.context, source, second)
        saved = source[service.ATTACHMENT_BACKUP_KEY]
        first.data.bones["Hips"].name = "Renamed bone"
        with self.assertRaisesRegex(service.SkirtRigError, "no bone named"):
            service.restore_attachment(bpy.context, source)
        self.assertIs(rig.parent, second)
        self.assertEqual(source[service.ATTACHMENT_BACKUP_KEY], saved)

    def test_original_unattached_rig_is_restored_without_extra_objects(self):
        source, rig, first, second, record = fixture()
        world = rig.matrix_world.copy()
        rig.parent = None
        rig.matrix_world = world
        bpy.context.view_layer.update()
        baseline = rig.matrix_world.copy()
        object_count = len(bpy.data.objects)
        service.update_attachment(bpy.context, source, second)
        service.restore_attachment(bpy.context, source)
        self.assertIsNone(rig.parent)
        self.assert_matrix(rig.matrix_world, baseline)
        self.assertEqual(len(bpy.data.objects), object_count)
        self.assertFalse(service.has_attachment_backup(source))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AttachmentTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
