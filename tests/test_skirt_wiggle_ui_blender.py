"""Owned Dress display and Original round trips on an approved add-on projection.

Run only in factory-startup background Blender. No artist file is opened.
  blender --background --factory-startup --disable-autoexec --python-exit-code 1
    --python tests/test_skirt_wiggle_ui_blender.py --
    --addons-root <approved-projection/addons> --report-json <result.json>
The projection path is installed on sys.path before importing the add-on.
External Wiggle is unnecessary: these checks inspect native display and the
existing pose-preserving Original transaction, without starting simulation.
"""

import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--addons-root", type=Path, default=ROOT / "addons")
parser.add_argument("--report-json", type=Path)
arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ARGS, _unknown = parser.parse_known_args(arguments)
sys.path.insert(0, str(ARGS.addons_root.resolve()))
MEASUREMENTS = []

import bpy
import character_designer
from character_designer import (
    bone_collections, bone_display, body_original_mode, skirt_rig,
    skirt_wiggle_ui as ui,
)


def matrix_values(matrix):
    return tuple(tuple(row) for row in matrix)


def channels(rig, names=None):
    return {bone.name: (bone.rotation_mode, tuple(bone.location),
                        tuple(bone.rotation_euler), tuple(bone.rotation_quaternion),
                        tuple(bone.rotation_axis_angle), tuple(bone.scale))
            for bone in rig.pose.bones if names is None or bone.name in names}


def evaluated_vertices(source):
    evaluated = source.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return tuple(tuple(evaluated.matrix_world @ vertex.co) for vertex in mesh.vertices)
    finally:
        evaluated.to_mesh_clear()


def evaluated_pose(rig, names):
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {name: matrix_values(rig.matrix_world @ evaluated.pose.bones[name].matrix)
            for name in names}


def mesh_data(source):
    keys = source.data.shape_keys
    return (tuple(tuple(vertex.co) for vertex in source.data.vertices),
            tuple(tuple(edge.vertices) for edge in source.data.edges),
            tuple(tuple(face.vertices) for face in source.data.polygons),
            tuple((group.name, group.index, group.lock_weight) for group in source.vertex_groups),
            tuple(tuple(sorted((item.group, item.weight) for item in vertex.groups))
                  for vertex in source.data.vertices),
            tuple((key.name, key.value, tuple(tuple(point.co) for point in key.data))
                  for key in keys.key_blocks) if keys else ())


def rest_state(rig):
    return {bone.name: (bone.parent.name if bone.parent else None, bone.use_connect,
                        bone.use_deform, matrix_values(bone.matrix_local), bone.length)
            for bone in rig.data.bones}


def action_state(rig):
    animation = rig.animation_data
    return (None if animation is None else animation.action,
            tuple((track.name, track.mute, tuple((strip.name, strip.type, strip.action)
                                               for strip in track.strips))
                  for track in animation.nla_tracks) if animation else (),
            tuple(sorted(action.name for action in bpy.data.actions)))


def make_rig(name):
    data = bpy.data.armatures.new(name + " Skeleton")
    rig = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(rig)
    skirt_rig._activate(bpy.context, rig, "EDIT")
    for title, head, tail, parent in (
            ("Hips", (0, 0, 2), (0, 0, 2.2), None),
            ("Body", (0, 0, 2.2), (0, 0, 2.7), "Hips"),
            ("Hair", (0, 0, 2.7), (.1, 0, 2.9), "Body")):
        bone = data.edit_bones.new(title)
        bone.head, bone.tail = head, tail
        if parent:
            bone.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    for title, names in (("Body", ("Hips", "Body")), ("Hair", ("Hair",)),
                         ("Original", ("Hips", "Body", "Hair"))):
        group = data.collections.new(title)
        group[bone_collections.GROUP_KEY] = title
        for bone_name in names:
            group.assign(data.bones[bone_name])
    rig.pose.bones["Body"].rotation_mode = "XYZ"
    rig.pose.bones["Body"].rotation_euler.y = .021
    rig.pose.bones["Hair"].rotation_mode = "XYZ"
    rig.pose.bones["Hair"].rotation_euler.z = -.017
    return rig


def skin(name, rig, bone, height):
    mesh = bpy.data.meshes.new(name + " Mesh")
    mesh.from_pydata(((0, 0, height), (.1, 0, height), (.1, 0, height + .2),
                      (0, 0, height + .2)), (), ((0, 1, 2, 3),))
    source = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(source)
    source.vertex_groups.new(name=bone).add((0, 1, 2, 3), 1.0, "REPLACE")
    source.modifiers.new("Artist Skin", "ARMATURE").object = rig
    source.shape_key_add(name="Basis")
    key = source.shape_key_add(name="Artist Shape")
    key.data[0].co.x += .015
    key.value = .27
    return source


def fixture(shared):
    main = make_rig("Display Main")
    body, hair = skin("Display Body", main, "Body", 2.2), skin("Display Hair", main, "Hair", 2.7)
    vertices, faces = [], []
    rows, sides = 8, 16
    for row in range(rows):
        fraction = row / (rows - 1)
        for column in range(sides):
            angle = math.tau * column / sides
            vertices.append(((.5 + .5 * fraction) * math.cos(angle),
                             (.4 + .4 * fraction) * math.sin(angle), 2 - fraction))
    for row in range(rows - 1):
        for column in range(sides):
            following = (column + 1) % sides
            faces.append((row * sides + column, (row + 1) * sides + column,
                          (row + 1) * sides + following, row * sides + following))
    mesh = bpy.data.meshes.new("Display Dress Mesh")
    mesh.from_pydata(vertices, [], faces)
    source = bpy.data.objects.new("Display Dress", mesh)
    bpy.context.collection.objects.link(source)
    record = skirt_rig.build_skirt(bpy.context, source, chain_count=4, segment_count=3,
                                   armature=main, parent_bone="Hips", shared=shared)
    rig = source[skirt_rig.RIG_KEY]
    source.shape_key_add(name="Basis")
    key = source.shape_key_add(name="Artist Fold")
    key.data[19].co.x += .012
    key.value = .23
    waist, _rig, _path = skirt_rig.physics_control(source)
    waist["physics_influence"] = .65
    rig.pose.bones[record["controls"]["hem"]].location.x = .025
    rig.pose.bones[record["controls"]["hem"]].rotation_euler.y = .06
    physics = rig.pose.bones[record["chains"][0]["phys"][1]]
    physics.rotation_mode = "XYZ"
    physics.rotation_euler.x = .09
    foreign = make_rig("Untouched Foreign Character")
    foreign.pose.bones["Body"].rotation_euler.z = .043
    bpy.context.window_manager.character_designer_skirt.source = source
    skirt_rig._activate(bpy.context, source, "OBJECT")
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
    bpy.context.view_layer.update()
    return source, rig, main, body, hair, foreign, skirt_rig.read_record(source)


def call_display(source, layer):
    bpy.context.window_manager.character_designer_skirt.source = source
    result = bpy.ops.character_designer.dress_wiggle_display(layer=layer)
    if result != {"FINISHED"}:
        raise AssertionError((layer, result))
    bpy.context.view_layer.update()


def hidden(rig, name):
    bone = rig.data.bones[name]
    return (bone.hide, getattr(rig.pose.bones[name], "hide", bone.hide))


class NativeDressWiggleUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()
        ui.register()

    def assert_geometry(self, before, after, limit=5e-5):
        self.assertEqual(len(before), len(after))
        error = max((math.dist(first, second) for first, second in zip(before, after)), default=0)
        self.assertLessEqual(error, limit)

    def assert_pose(self, before, after, limit=5e-5):
        self.assertEqual(before.keys(), after.keys())
        error = max((abs(matrix[row][column] - after[name][row][column])
                     for name, matrix in before.items() for row in range(4) for column in range(4)), default=0)
        self.assertLessEqual(error, limit)

    def protected(self, main, body, hair, foreign):
        return {"native_channels": channels(main, {"Hips", "Body", "Hair"}),
                "body_mesh": mesh_data(body), "hair_mesh": mesh_data(hair),
                "body_evaluated": evaluated_vertices(body), "hair_evaluated": evaluated_vertices(hair),
                "main_rest": rest_state(main), "main_actions": action_state(main),
                "foreign": (channels(foreign), rest_state(foreign), action_state(foreign),
                            bone_display._snapshot(foreign))}

    def assert_protected(self, before, after, *, original=False):
        if not original:
            self.assertEqual(before, after)
            return
        # Original bakes an equivalent native matrix into local channels. Native
        # float decomposition can introduce ~1e-8 Euler / ~6e-8 scale rounding.
        # Keep raw meshes, weights, keys, Rest, Actions and foreign rigs exact;
        # independently bound and report the affected channel/geometry errors.
        maximum = 0.0
        for name, expected in before["native_channels"].items():
            actual = after["native_channels"][name]
            self.assertEqual(expected[0], actual[0], name)
            maximum = max(maximum, max((abs(left - right)
                for expected_field, actual_field in zip(expected[1:], actual[1:])
                for left, right in zip(expected_field, actual_field)), default=0.0))
        geometry = max((math.dist(left, right)
                        for field in ("body_evaluated", "hair_evaluated")
                        for left, right in zip(before[field], after[field])), default=0.0)
        MEASUREMENTS.append({"case": self.id(), "native_channel_error": maximum,
                             "body_hair_geometry_error": geometry})
        self.assertLessEqual(maximum, 2e-6)
        self.assertLessEqual(geometry, 2e-6)
        for field in before.keys() - {"native_channels", "body_evaluated", "hair_evaluated"}:
            self.assertEqual(before[field], after[field], field)
        for field in ("body_evaluated", "hair_evaluated"):
            self.assertEqual(len(before[field]), len(after[field]))

    def check_layers(self, shared):
        source, rig, main, body, hair, foreign, record = fixture(shared)
        protected = self.protected(main, body, hair, foreign)
        source_data, source_channels = mesh_data(source), channels(rig)
        source_vertices = evaluated_vertices(source)
        controls, deform, mechanism = skirt_rig._bone_collection_layout(record)
        owned = controls | deform | mechanism
        unrelated = {name: hidden(main, name) for name in ("Hips", "Body", "Hair")}
        for layer, visible in (("CONTROLS", controls),
                               ("MCH", {name for chain in record["chains"] for name in chain["manual"]}),
                               ("PHYS", {name for chain in record["chains"] for name in chain["phys"]}),
                               ("CONTROLS", controls)):
            call_display(source, layer)
            self.assertTrue(all(hidden(rig, name) == (name not in visible, name not in visible)
                                for name in owned), layer)
            self.assertEqual({name: hidden(main, name) for name in unrelated}, unrelated)
            self.assertEqual(channels(rig), source_channels)
            self.assertEqual(mesh_data(source), source_data)
            self.assert_geometry(source_vertices, evaluated_vertices(source))
            self.assert_protected(protected, self.protected(main, body, hair, foreign))
        skirt_rig.select_controls(bpy.context, source)
        self.assertTrue(all(hidden(rig, name) == (name not in controls, name not in controls)
                            for name in owned))
        self.assertEqual(mesh_data(source), source_data)
        self.assert_protected(protected, self.protected(main, body, hair, foreign))

    def check_original(self, shared):
        source, rig, main, body, hair, foreign, record = fixture(shared)
        call_display(source, "CONTROLS")
        before = self.protected(main, body, hair, foreign)
        source_data, source_channels = mesh_data(source), channels(rig)
        names = [name for chain in record["chains"] for name in chain["def"]] + [record["controls"]["waist"]]
        pose, vertices = evaluated_pose(rig, names), evaluated_vertices(source)
        call_display(source, "ORIGINAL")
        self.assertTrue(body_original_mode.active(main))
        controls, deform, mechanism = skirt_rig._bone_collection_layout(record)
        visible = {name for name in deform | {record["controls"]["waist"]}
                   if rig.data.bones[name].use_deform}
        self.assertTrue(all(hidden(rig, name) == (name not in visible, name not in visible)
                            for name in controls | deform | mechanism))
        self.assert_pose(pose, evaluated_pose(rig, names))
        self.assert_geometry(vertices, evaluated_vertices(source))
        self.assert_protected(before, self.protected(main, body, hair, foreign), original=True)
        call_display(source, "CONTROLS")
        self.assertFalse(body_original_mode.active(main))
        self.assertEqual(mesh_data(source), source_data)
        # Dress input channels remain exact. Shared Body/Hair channels use the
        # separate, independently bounded native-decomposition check above.
        owned = controls | deform | mechanism
        self.assertEqual(channels(rig, owned), {name: source_channels[name] for name in owned})
        self.assert_pose(pose, evaluated_pose(rig, names))
        self.assert_geometry(vertices, evaluated_vertices(source))
        self.assert_protected(before, self.protected(main, body, hair, foreign), original=True)

    def test_independent_owned_layers_keep_author_data_and_foreign_rigs(self):
        self.check_layers(False)

    def test_shared_owned_layers_keep_body_hair_and_author_data(self):
        self.check_layers(True)

    def test_independent_original_controls_round_trip_preserves_pose(self):
        self.check_original(False)

    def test_shared_original_controls_round_trip_preserves_pose(self):
        self.check_original(True)

    def test_display_failure_restores_exact_visibility(self):
        source, rig, _main, _body, _hair, _foreign, _record = fixture(True)
        rig.data.collections_all["Body"].is_solo = True
        before = bone_display._snapshot(rig)
        with patch.object(bone_display, "_completed", side_effect=RuntimeError("Injected display completion failure")):
            with self.assertRaisesRegex(RuntimeError, "Injected"):
                ui._show_layer(bpy.context, source, "PHYS")
        self.assertEqual(bone_display._snapshot(rig), before)

    def test_foreign_solo_is_cleared_to_show_requested_owned_layer(self):
        source, rig, main, body, hair, foreign, record = fixture(True)
        main.data.collections_all["Body"].is_solo = True
        before, source_data = self.protected(main, body, hair, foreign), mesh_data(source)
        self.assertFalse(ui._controls_visible(source, record))
        call_display(source, "PHYS")
        self.assertFalse(any(group.is_solo for group in rig.data.collections_all))
        self.assertTrue(ui._layer_visible(source, record, "PHYS"))
        self.assertFalse(ui._controls_visible(source, record))
        call_display(source, "CONTROLS")
        self.assertTrue(ui._controls_visible(source, record))
        self.assertEqual(mesh_data(source), source_data)
        self.assert_protected(before, self.protected(main, body, hair, foreign))

    def test_unattached_original_refuses_before_any_pose_or_metadata_change(self):
        source, rig, _main, _body, _hair, _foreign, _record = fixture(False)
        rig.parent = None
        bpy.context.view_layer.update()
        self.assertFalse(ui._original_supported(source))
        before = (channels(rig), rest_state(rig), mesh_data(source), evaluated_vertices(source),
                  tuple(sorted((key, repr(source[key])) for key in source.keys())))
        try:
            result = bpy.ops.character_designer.dress_wiggle_display(layer="ORIGINAL")
        except RuntimeError as error:
            self.assertIn("Original needs an attached Main Rig", str(error))
            result = {"CANCELLED"}
        self.assertEqual(result, {"CANCELLED"})
        self.assertEqual((channels(rig), rest_state(rig), mesh_data(source), evaluated_vertices(source),
                          tuple(sorted((key, repr(source[key])) for key in source.keys()))), before)

    def test_controls_visibility_survives_disposable_save_reopen(self):
        source, rig, _main, _body, _hair, _foreign, record = fixture(False)
        call_display(source, "CONTROLS")
        source_name = source.name
        before, vertices = mesh_data(source), evaluated_vertices(source)
        with tempfile.TemporaryDirectory(prefix="dress-wiggle-ui-") as folder:
            path = str(Path(folder) / "isolated-display.blend")
            self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=path), {"FINISHED"})
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=path), {"FINISHED"})
            source = bpy.data.objects[source_name]
            rig, record = source[skirt_rig.RIG_KEY], skirt_rig.read_record(source)
            controls, deform, mechanism = skirt_rig._bone_collection_layout(record)
            self.assertTrue(all(hidden(rig, name) == (name not in controls, name not in controls)
                                for name in controls | deform | mechanism))
            self.assertEqual(mesh_data(source), before)
            self.assert_geometry(vertices, evaluated_vertices(source))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(NativeDressWiggleUITests))
    report = {"addon_root": str(ARGS.addons_root.resolve()),
              "loaded_addon": str(Path(character_designer.__file__).resolve()),
              "version": list(character_designer.bl_info["version"]),
              "blender_version": bpy.app.version_string,
              "tests": result.testsRun, "failures": len(result.failures),
              "errors": len(result.errors), "passed": result.wasSuccessful(),
              "measurements": MEASUREMENTS}
    if ARGS.report_json:
        ARGS.report_json.parent.mkdir(parents=True, exist_ok=True)
        ARGS.report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("DRESS_WIGGLE_UI_NATIVE", json.dumps(report), flush=True)
    if not result.wasSuccessful():
        raise SystemExit(1)
