"""Curve Tools modes share extraction without silently changing profile intent."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bmesh
import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))

import character_designer as cd
from character_designer import curve_tools
import test_character_designer_blender as fixture


CENTERS = ((0.0, 0.0, 0.0), (0.1, 0.04, 0.5), (0.2, 0.1, 1.0))


def activate(obj):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def geometry(obj):
    data = obj.data
    return (
        data.as_pointer(), data.dimensions, data.fill_mode, data.bevel_mode,
        data.bevel_depth, data.bevel_resolution, data.extrude, data.use_fill_caps,
        tuple((tuple(p.co), p.radius, p.tilt) for p in data.splines[0].points),
        tuple(tuple(row) for row in obj.matrix_world),
        dict(obj.items()),
    )


def source_snapshot(obj):
    return tuple(tuple(vertex.co) for vertex in obj.data.vertices)


def metadata_for(source, layers, centers, mode):
    bm = bmesh.new()
    try:
        bm.from_mesh(source.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        return cd._build_cross_section_metadata(
            source, bm, layers, centers, "OPEN", tool_mode=mode,
        )
    finally:
        bm.free()


class LayoutProbe:
    def __init__(self):
        self.properties = []
        self.buttons = []
        self.labels = []

    def row(self, **_kwargs):
        return self

    def column(self, **_kwargs):
        return self

    def box(self):
        return self

    def prop(self, _data, name, **_kwargs):
        self.properties.append(name)

    def operator(self, name, **kwargs):
        button = SimpleNamespace(operator=name, **kwargs)
        self.buttons.append(button)
        return button

    def label(self, **kwargs):
        self.labels.append(kwargs.get("text", ""))

    def separator(self):
        pass


class CurveModesTests(unittest.TestCase):
    def setUp(self):
        fixture.reset_scene()
        self.settings = bpy.context.window_manager.character_designer
        self.settings.ui_page = "MODELING"
        self.settings.curve_tools_mode = "GENERAL"
        self.settings.centerline_placement = "CENTERED"
        self.settings.last_level = "NONE"
        self.settings.last_message = ""

    def build(self, mode="GENERAL", name="ModeSource"):
        self.settings.curve_tools_mode = mode
        source, root, expected = fixture.make_open_grid(name, CENTERS)
        output = fixture.capture_then_build(source, root)
        return source, output, expected

    def test_general_extracts_unshaped_path_without_hair_orientation_or_tip(self):
        source, root, expected = fixture.make_closed_point_tip("GeneralPointTip")
        before = source_snapshot(source)
        self.settings.centerline_placement = "FRONT_FLUSH"
        with patch.object(cd, "_front_band_descriptors", side_effect=AssertionError("Hair orientation used")):
            output = fixture.capture_then_build(source, root)
        self.assertEqual(output[curve_tools.MODE_KEY], "GENERAL")
        self.assertEqual(output.data.fill_mode, "FULL")
        self.assertEqual(output.data.bevel_depth, 0.0)
        self.assertEqual(output["character_designer_alignment"], "CENTERED")
        metadata = fixture.read_cross_section_metadata(output)
        self.assertEqual(metadata["curve_tools_mode"], "GENERAL")
        self.assertEqual(metadata["sections"][-1]["kind"], "POINT")
        self.assertTrue(all(section["front_target_source"] == ("POINT_CENTER" if section["kind"] == "POINT" else "SECTION_CENTER")
                            for section in metadata["sections"]))
        for point, center in zip(output.data.splines[0].points, expected):
            fixture.assert_vector_close(point.co.xyz, center)
            self.assertEqual(point.radius, 1.0)
            self.assertEqual(point.tilt, 0.0)
        self.assertEqual(source_snapshot(source), before)

    def test_hair_retains_round_half_width_radius_and_tilt(self):
        source, root, expected = fixture.make_closed_point_tip("HairPointTip")
        before = source_snapshot(source)
        self.settings.curve_tools_mode = "HAIR"
        with patch.object(cd, "_front_band_descriptors", wraps=cd._front_band_descriptors) as orientation:
            output = fixture.capture_then_build(source, root)
        self.assertGreater(orientation.call_count, 0)
        self.assertEqual(output[curve_tools.MODE_KEY], "HAIR")
        fixture.assert_plain_poly_curve(output, expected)
        self.assertLess(output.data.splines[0].points[-1].radius, 0.1)
        self.assertEqual(source_snapshot(source), before)

    def test_general_refresh_preserves_authored_profile_radius_and_tilt(self):
        source, output, expected = self.build()
        activate(output)
        data = output.data
        data.bevel_depth = 0.123
        data.bevel_resolution = 2
        data.extrude = 0.031
        data.use_fill_caps = True
        for index, point in enumerate(data.splines[0].points):
            point.radius = 0.7 + index * 0.25
            point.tilt = -0.3 + index * 0.4
        authored = (data.bevel_depth, data.bevel_resolution, data.extrude,
                    data.use_fill_caps, data.fill_mode,
                    tuple((p.radius, p.tilt) for p in data.splines[0].points))
        for vertex in source.data.vertices:
            vertex.co.y += 0.25
        source.data.update()
        source_after_edit = source_snapshot(source)
        original = output.as_pointer()
        # Refresh is determined by the Curve identity, not the current dropdown.
        self.settings.curve_tools_mode = "HAIR"
        self.settings.centerline_placement = "FRONT_FLUSH"
        with patch.object(cd, "_front_band_descriptors", side_effect=AssertionError("Hair orientation used")):
            self.assertEqual(bpy.ops.character_designer.generate_or_update_centerline(), {"FINISHED"})
        self.assertEqual(output.as_pointer(), original)
        data = output.data
        self.assertEqual(authored, (data.bevel_depth, data.bevel_resolution, data.extrude,
                                   data.use_fill_caps, data.fill_mode,
                                   tuple((p.radius, p.tilt) for p in data.splines[0].points)))
        for point, center in zip(data.splines[0].points, expected):
            fixture.assert_vector_close(point.co.xyz, (center.x, center.y + 0.25, center.z))
        self.assertEqual(source_snapshot(source), source_after_edit)

    def test_unmarked_legacy_hair_refresh_stays_hair_in_general_ui(self):
        source, output, expected = self.build("HAIR", "LegacyHair")
        activate(output)
        del output[curve_tools.MODE_KEY]
        self.settings.curve_tools_mode = "GENERAL"
        self.assertEqual(curve_tools.object_mode(output), "HAIR")
        for vertex in source.data.vertices:
            vertex.co.z += 0.2
        source.data.update()
        with patch.object(cd, "_front_band_descriptors", wraps=cd._front_band_descriptors) as orientation:
            self.assertEqual(bpy.ops.character_designer.generate_or_update_centerline(), {"FINISHED"})
        self.assertGreater(orientation.call_count, 0)
        fixture.assert_plain_poly_curve(output, [center + fixture.Vector((0, 0, 0.2)) for center in expected])
        self.assertEqual(curve_tools.object_mode(output), "HAIR")

    def test_same_source_modes_resolve_separately_and_reject_cross_mode_update(self):
        source, general, expected = self.build()
        layers = cd._stored_centerline_layers(general)
        self.settings.curve_tools_mode = "HAIR"
        hair = fixture.capture_then_build(source, layers[0])
        self.assertIsNot(general, hair)
        self.assertIs(cd._automatic_centerline_target(bpy.context, source, layers, tool_mode="GENERAL"), general)
        self.assertIs(cd._automatic_centerline_target(bpy.context, source, layers, tool_mode="HAIR"), hair)
        activate(general)
        before = geometry(general)
        ids = (set(bpy.data.objects.keys()), set(bpy.data.curves.keys()))
        with self.assertRaisesRegex(cd.CenterlineError, "General/Hair"):
            cd._commit_existing_centerline(general, source, layers,
                metadata_for(source, layers, expected, "HAIR"), "CENTERED")
        self.assertEqual(geometry(general), before)
        self.assertEqual(ids, (set(bpy.data.objects.keys()), set(bpy.data.curves.keys())))

    def test_visible_generate_creates_and_updates_only_selected_mode(self):
        source, _root, _expected = fixture.make_open_grid("VisibleGenerate", CENTERS)

        def select_source():
            fixture.select_vertices(source, tuple(range(len(source.data.vertices))))
            bm = bmesh.from_edit_mesh(source.data)
            edge = next(edge for edge in bm.edges if {v.index for v in edge.verts} == {0, 1})
            bm.select_history.clear()
            bm.select_history.add(edge)
            bmesh.update_edit_mesh(source.data, loop_triangles=False, destructive=False)

        select_source()
        self.assertEqual(bpy.ops.character_designer.generate_or_update_centerline(), {"FINISHED"})
        general = self.settings.output_object
        self.assertEqual(curve_tools.object_mode(general), "GENERAL")
        general_before = geometry(general)
        self.settings.curve_tools_mode = "HAIR"
        select_source()
        self.assertEqual(bpy.ops.character_designer.generate_or_update_centerline(), {"FINISHED"})
        hair = self.settings.output_object
        self.assertIsNot(general, hair)
        self.assertEqual(curve_tools.object_mode(hair), "HAIR")
        self.assertEqual(geometry(general), general_before)
        self.settings.curve_tools_mode = "GENERAL"
        select_source()
        self.assertEqual(bpy.ops.character_designer.generate_or_update_centerline(), {"FINISHED"})
        self.assertIs(self.settings.output_object, general)
        self.assertEqual(len([o for o in bpy.context.scene.objects if o.type == "CURVE"]), 2)

    def test_reversed_general_selection_keeps_authored_point_direction_and_parameters(self):
        source, output, expected = self.build()
        activate(output)
        for index, point in enumerate(output.data.splines[0].points):
            point.radius = 0.25 + 0.5 * index
            point.tilt = -0.7 + 0.6 * index
        saved_layers = cd._stored_centerline_layers(output)
        before = geometry(output)
        fixture.select_vertices(source, tuple(range(len(source.data.vertices))))
        bm = bmesh.from_edit_mesh(source.data)
        end_edge = next(edge for edge in bm.edges
                        if {vertex.index for vertex in edge.verts} == {6, 7})
        bm.select_history.clear()
        bm.select_history.add(end_edge)
        bmesh.update_edit_mesh(source.data, loop_triangles=False, destructive=False)
        _source, _bm, incoming, centers, _kind, confirmable = cd._infer_selected_centerline(bpy.context)
        self.assertTrue(confirmable)
        self.assertEqual(tuple(incoming), tuple(reversed(saved_layers)))
        normalized, normalized_centers = cd._centerline_input_order(output, incoming, centers)
        self.assertEqual(tuple(normalized), saved_layers)
        for center, authored_center in zip(normalized_centers, expected):
            fixture.assert_vector_close(center, authored_center)
        self.assertEqual(bpy.ops.character_designer.generate_or_update_centerline(), {"FINISHED"})
        self.assertIs(self.settings.output_object, output)
        self.assertEqual(cd._stored_centerline_layers(output), saved_layers)
        # A reversed selection is an update of the same spatial control points.
        # Their Radius/Tilt values must not migrate to the opposite end.
        self.assertEqual(geometry(output), before)

    def test_direct_general_commit_rejects_reversed_section_order_atomically(self):
        source, output, expected = self.build()
        activate(output)
        layers = tuple(reversed(cd._stored_centerline_layers(output)))
        metadata = metadata_for(source, layers, tuple(reversed(expected)), "GENERAL")
        before = geometry(output)
        ids = (set(bpy.data.objects.keys()), set(bpy.data.curves.keys()))
        with self.assertRaisesRegex(cd.CenterlineError, "section order"):
            cd._commit_existing_centerline(output, source, layers, metadata, "CENTERED")
        self.assertEqual(geometry(output), before)
        self.assertEqual(ids, (set(bpy.data.objects.keys()), set(bpy.data.curves.keys())))

    def test_switching_mode_invalidates_capture_and_automatic_preview_signatures(self):
        source, root, _expected = fixture.make_open_grid("PreviewModeChange", CENTERS)
        fixture.select_vertices(source, root)
        self.assertEqual(bpy.ops.character_designer.capture_root_slice(), {"FINISHED"})
        fixture.select_vertices(source, tuple(range(len(source.data.vertices))))
        source_before = source_snapshot(source)
        selected_before = tuple(vertex.index for vertex in bmesh.from_edit_mesh(source.data).verts
                                if vertex.select)
        general_capture = cd._preview_input_signature(bpy.context, self.settings)
        general_auto = cd._auto_preview_input_signature(bpy.context)
        self.assertEqual(general_capture, cd._preview_input_signature(bpy.context, self.settings))
        self.assertEqual(general_auto, cd._auto_preview_input_signature(bpy.context))
        self.settings.curve_tools_mode = "HAIR"
        hair_capture = cd._preview_input_signature(bpy.context, self.settings)
        hair_auto = cd._auto_preview_input_signature(bpy.context)
        self.assertNotEqual(general_capture, hair_capture)
        self.assertNotEqual(general_auto, hair_auto)
        # The legacy page also means Hair even when the new dropdown says General.
        self.settings.ui_page = "HAIR"
        self.settings.curve_tools_mode = "GENERAL"
        self.assertEqual(hair_capture, cd._preview_input_signature(bpy.context, self.settings))
        self.assertEqual(hair_auto, cd._auto_preview_input_signature(bpy.context))
        self.assertEqual(source_snapshot(source), source_before)
        self.assertEqual(tuple(vertex.index for vertex in bmesh.from_edit_mesh(source.data).verts
                               if vertex.select), selected_before)

    def test_switching_mode_and_drawing_ui_does_not_write_curve_geometry(self):
        source, output, _expected = self.build()
        activate(output)
        before = geometry(output)
        source_before = source_snapshot(source)
        dirty = bpy.data.is_dirty
        for mode in ("HAIR", "GENERAL", "HAIR"):
            self.settings.curve_tools_mode = mode
            cd._draw_curve_tools(LayoutProbe(), bpy.context)
            self.assertEqual(geometry(output), before)
            self.assertEqual(source_snapshot(source), source_before)
        self.assertEqual(bpy.data.is_dirty, dirty)

    def test_panel_uses_selected_curve_mode_and_hides_hair_rules_for_general(self):
        source, general, _expected = self.build()
        activate(general)
        self.settings.curve_tools_mode = "HAIR"
        layout = LayoutProbe()
        cd.CHARACTERDESIGNER_PT_curve_tools.draw(SimpleNamespace(layout=layout), bpy.context)
        self.assertNotIn("centerline_placement", layout.properties)
        self.assertNotIn("character_designer.set_front_alignment", [b.operator for b in layout.buttons])
        self.assertIn("bevel_depth", layout.properties)
        self.settings.curve_tools_mode = "HAIR"
        hair = fixture.capture_then_build(source, cd._stored_centerline_layers(general)[0])
        activate(hair)
        self.settings.curve_tools_mode = "GENERAL"
        layout = LayoutProbe()
        cd._draw_curve_tools(layout, bpy.context)
        self.assertEqual([b.mode for b in layout.buttons if b.operator == "character_designer.set_front_alignment"],
                         ["CENTERED", "FRONT_FLUSH", "BLEND"])
        self.assertNotIn("bevel_depth", layout.properties)
        self.assertTrue(cd.CHARACTERDESIGNER_PT_curve_tools.poll(bpy.context))
        self.settings.ui_page = "RIG"
        self.settings.rig_section = "HAIR"
        self.assertFalse(cd.CHARACTERDESIGNER_PT_curve_tools.poll(bpy.context))

    def test_general_creation_failure_removes_partial_objects_and_data(self):
        source, _root, expected = fixture.make_open_grid("CreateFailureGeneral", CENTERS)
        layers = ((0, 1, 2), (3, 4, 5), (6, 7, 8))
        metadata = metadata_for(source, layers, expected, "GENERAL")
        ids = (set(bpy.data.objects.keys()), set(bpy.data.curves.keys()))
        source_before = source_snapshot(source)
        with patch.object(cd, "_visible_source_collection", side_effect=RuntimeError("injected link failure")):
            with self.assertRaisesRegex(RuntimeError, "injected link failure"):
                cd._create_centerline_object(bpy.context, source, layers, expected, metadata)
        self.assertEqual(ids, (set(bpy.data.objects.keys()), set(bpy.data.curves.keys())))
        self.assertEqual(source_snapshot(source), source_before)

    def test_general_update_failure_keeps_original_curve_and_removes_staged_data(self):
        source, output, _expected = self.build()
        activate(output)
        for vertex in source.data.vertices:
            vertex.co.x += 0.25
        source.data.update()
        source_obj, layers, _centers, metadata = cd._metadata_from_recorded_source(output)
        before = geometry(output)
        ids = (set(bpy.data.objects.keys()), set(bpy.data.curves.keys()))
        original_configure = cd._configure_centerline_data

        def configure_then_fail(data, solution, **kwargs):
            original_configure(data, solution, **kwargs)
            raise RuntimeError("injected after staged geometry")

        with patch.object(cd, "_configure_centerline_data", side_effect=configure_then_fail):
            with self.assertRaisesRegex(RuntimeError, "injected after staged geometry"):
                cd._commit_existing_centerline(output, source_obj, layers, metadata, "CENTERED", update_matrix=True)
        self.assertEqual(geometry(output), before)
        self.assertEqual(ids, (set(bpy.data.objects.keys()), set(bpy.data.curves.keys())))


if __name__ == "__main__":
    cd.register()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(CurveModesTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        cd.unregister()
    if not result.wasSuccessful():
        raise RuntimeError("Curve Tools mode regression failed")
