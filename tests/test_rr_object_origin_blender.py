"""Object Mode Origin-to-Cursor regressions in an isolated factory scene.

Run serially with --background --factory-startup --disable-autoexec --threads 1
--python-exit-code 1 --python tests/test_rr_object_origin_blender.py.
No production project, add-on handlers, or timers are opened or registered.
"""

import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import bpy
from mathutils import Matrix, Vector


TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR))
import test_rr_empty_origin_blender as fixture

rr = fixture.rr
origin = fixture.origin
select = fixture.select
make_object = fixture.make_object


def geometry_points(obj, world=True):
    """Include Bezier handles and rational weights, not only curve anchors."""
    matrix = obj.matrix_world if world else Matrix.Identity(4)
    if obj.type == "MESH":
        return tuple(tuple(matrix @ vertex.co) for vertex in obj.data.vertices)
    points = []
    for spline in obj.data.splines:
        for point in spline.bezier_points:
            points.append(("BEZIER", tuple(matrix @ point.co),
                           tuple(matrix @ point.handle_left), tuple(matrix @ point.handle_right),
                           point.handle_left_type, point.handle_right_type, point.radius, point.tilt))
        for point in spline.points:
            points.append((spline.type, tuple(matrix @ Vector(point.co[:3])),
                           point.co.w, point.weight, point.radius, point.tilt))
    return tuple(points)


def shape_key_points(obj):
    keys = obj.data.shape_keys
    if keys is None:
        return ()
    return tuple((key.name, key.value, key.mute, key.relative_key.name,
                  tuple(tuple(obj.matrix_world @ point.co) for point in key.data))
                 for key in keys.key_blocks)


def geometry_snapshot():
    bpy.context.view_layer.update()
    return {
        obj.name: {
            "world_points": geometry_points(obj),
            "shape_keys": shape_key_points(obj),
            "evaluated": fixture.evaluated_world_vertices(obj),
            "topology": tuple(tuple(poly.vertices) for poly in obj.data.polygons)
                        if obj.type == "MESH" else (),
        }
        for obj in bpy.context.scene.objects if obj.type in {"MESH", "CURVE"}
    }


def make_arc(name="Arc"):
    data = bpy.data.meshes.new(name + "Mesh")
    coordinates = [(2.5 * math.cos(angle), 2.5 * math.sin(angle), 0.2)
                   for angle in (0.0, 0.3, 0.6, 0.9, 1.2)]
    data.from_pydata(coordinates, [(index, index + 1) for index in range(4)], [])
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj[rr.EXPORT_STABLE_ID_PROP] = "stable_" + name
    return obj


def make_rational_curve():
    data = bpy.data.curves.new("HandlesAndWeights", "CURVE")
    data.dimensions = "3D"
    bezier = data.splines.new("BEZIER")
    bezier.bezier_points.add(1)
    for index, point in enumerate(bezier.bezier_points):
        point.handle_left_type = point.handle_right_type = "FREE"
        point.co = (index * 3, index * 2 - 1, index + 0.3)
        point.handle_left = point.co + Vector((-0.7, 0.2, -0.4))
        point.handle_right = point.co + Vector((0.8, -0.3, 0.6))
        point.radius = 0.7 + index * 0.2
        point.tilt = index * 0.4
    nurbs = data.splines.new("NURBS")
    nurbs.points.add(3)
    nurbs.order_u = 3
    for index, point in enumerate(nurbs.points):
        point.co = (index, 0.5 * index ** 2, -index * 0.25, 0.7 + index * 0.3)
        point.weight = 0.8 + index * 0.1
        point.radius = 0.9 + index * 0.05
        point.tilt = -index * 0.2
    obj = bpy.data.objects.new("RationalCurve", data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def node_group_snapshot(group):
    return (
        tuple((item.name, item.identifier, item.item_type, getattr(item, "in_out", None))
              for item in group.interface.items_tree),
        tuple((node.name, node.bl_idname, tuple(node.location)) for node in group.nodes),
        tuple((link.from_node.name, link.from_socket.identifier,
               link.to_node.name, link.to_socket.identifier) for link in group.links),
    )


def instance_world_matrices(obj):
    bpy.context.view_layer.update()
    return tuple(instance.matrix_world.copy()
                 for instance in bpy.context.evaluated_depsgraph_get().object_instances
                 if instance.is_instance and instance.parent is not None
                 and instance.parent.original == obj)


class RecordingLayout:
    """Capture only operator availability, using the production draw method."""

    def __init__(self, records=None, enabled=True):
        self.records = [] if records is None else records
        self.enabled = enabled
        self.alignment = "EXPAND"

    def row(self, **kwargs):
        return RecordingLayout(self.records, self.enabled)

    column = row
    box = row

    def label(self, **kwargs):
        pass

    def prop(self, *args, **kwargs):
        pass

    def operator(self, name, **kwargs):
        operation = SimpleNamespace()
        self.records.append((name, kwargs.get("text", ""), self.enabled, operation))
        return operation


class ObjectOriginTests(unittest.TestCase):
    assert_numeric_close = fixture.EmptyOriginTests.assert_numeric_close
    assert_snapshot_equal = fixture.EmptyOriginTests.assert_snapshot_equal

    @classmethod
    def setUpClass(cls):
        bpy.utils.register_class(fixture.RR_TEST_PG_empty_origin_settings)
        bpy.types.Scene.rr_builder_export_settings = bpy.props.PointerProperty(
            type=fixture.RR_TEST_PG_empty_origin_settings)
        bpy.utils.register_class(rr.RR_OT_apply_modeling_origin_point)

    @classmethod
    def tearDownClass(cls):
        bpy.utils.unregister_class(rr.RR_OT_apply_modeling_origin_point)
        del bpy.types.Scene.rr_builder_export_settings
        bpy.utils.unregister_class(fixture.RR_TEST_PG_empty_origin_settings)

    def setUp(self):
        fixture.clear_scene()
        self.original_flags = tuple(getattr(bpy.context.scene.tool_settings, name) for name in fixture.FLAGS)
        for name, enabled in zip(fixture.FLAGS, (True, False, True)):
            setattr(bpy.context.scene.tool_settings, name, enabled)
        bpy.context.scene.cursor.location = (17.5, -8.25, 6.75)
        bpy.context.scene.cursor.rotation_euler = (0.2, -0.4, 0.6)
        bpy.context.scene.frame_set(1)

    def tearDown(self):
        fixture.clear_scene()
        for name, enabled in zip(fixture.FLAGS, self.original_flags):
            setattr(bpy.context.scene.tool_settings, name, enabled)

    def apply(self, context=None):
        return origin.apply_modeling_origin(context or bpy.context,
                                            SimpleNamespace(modeling_origin_mode="BOTTOM"), mode="SELECTION")

    def capture(self):
        return fixture.snapshot(), geometry_snapshot()

    def assert_success(self, before, movers):
        old_state, old_geometry = before
        after = fixture.snapshot()
        self.assert_snapshot_equal(geometry_snapshot(), old_geometry)
        for field in ("selected", "active", "mode", "cursor", "flags"):
            self.assert_numeric_close(after[field], old_state[field])
        moving_names = {obj.name for obj in movers}
        for name, old in old_state["objects"].items():
            new = after["objects"][name]
            with self.subTest(object=name):
                for field in ("rotation_mode", "parent", "parent_type", "parent_bone", "parent_vertices",
                              "stable_id", "identity", "animation", "constraints", "drivers"):
                    self.assert_numeric_close(new[field], old[field])
                if name in moving_names:
                    self.assert_numeric_close(new["world"].translation, old_state["cursor"].translation)
                    self.assert_numeric_close(new["world"].to_3x3(), old["world"].to_3x3())
                    for channel in fixture.ROTATION_SCALE_CHANNELS:
                        self.assert_numeric_close(new["channels"][channel], old["channels"][channel])
                else:
                    self.assert_numeric_close(new["world"], old["world"])
                    self.assert_snapshot_equal(new["channels"], old["channels"])

    def assert_rejected(self, context=None):
        before = self.capture()
        data_pointers = {obj.name: obj.data.as_pointer() for obj in bpy.context.scene.objects
                         if obj.data is not None}
        with self.assertRaises(RuntimeError):
            self.apply(context)
        after = self.capture()
        self.assert_snapshot_equal(after[0], before[0])
        self.assert_snapshot_equal(after[1], before[1])
        self.assertEqual(data_pointers, {obj.name: obj.data.as_pointer() for obj in bpy.context.scene.objects
                                        if obj.data is not None})

    def test_object_selection_accepts_mesh_and_curve_only_in_object_mode(self):
        arc, curve, empty = make_arc(), make_rational_curve(), make_object("Empty")
        self.assertEqual(origin.modeling_object_origin_selection(None), [])
        for mode, selected, expected in (
            ("OBJECT", [arc], [arc]), ("OBJECT", [curve], [curve]),
            ("OBJECT", [arc, curve], [arc, curve]), ("OBJECT", [], []),
            ("OBJECT", [empty, arc], []), ("EDIT_MESH", [arc], []),
            ("EDIT_CURVE", [curve], []), ("POSE", [arc], []),
        ):
            with self.subTest(mode=mode, selected=len(selected)):
                self.assertEqual(origin.modeling_object_origin_selection(
                    SimpleNamespace(mode=mode, selected_objects=selected)), expected)

    def test_edge_only_arc_moves_origin_to_cursor_without_changing_geometry(self):
        arc = make_arc()
        arc.location = (3, -2, 5)
        arc.rotation_euler = (0.4, -0.3, 0.8)
        arc.scale = (-1.3, 0.7, 2.0)
        select([arc])
        original_data = arc.data
        before = self.capture()
        self.assertEqual(len(arc.data.polygons), 0)
        self.assertEqual(self.apply(), 1)
        self.assert_success(before, [arc])
        self.assertEqual(arc.data, original_data)

    def test_world_cursor_with_sheared_parent_preserves_descendant_channels(self):
        parent = make_object("Parent")
        parent.location = (-5, 3, 8)
        parent.rotation_euler = (0.3, -0.6, 0.4)
        parent.scale = (-1.4, 0.7, 2.3)
        arc = make_arc()
        arc.parent = parent
        arc.location = (2, 1, -4)
        arc.rotation_euler = (0.5, 0.2, -0.7)
        arc.scale = (0.6, -1.2, 1.8)
        arc.delta_location = (0.3, -0.4, 0.2)
        arc.matrix_parent_inverse = Matrix(((1, .2, 0, 1), (0, 1, .3, -2), (0, 0, 1, .5), (0, 0, 0, 1)))
        child = make_object("ChildMesh", "MESH", arc)
        child.location = (1, -3, 2)
        child.rotation_euler = (0.2, 0.5, -0.3)
        child.delta_location = (-0.2, 0.1, 0.5)
        child.matrix_parent_inverse = Matrix.Translation((1, -2, 0.5))
        grandchild = make_object("GrandchildCurve", "CURVE", child)
        grandchild.location = (2, 1, -1)
        select([arc])
        before = self.capture()
        self.assertEqual(self.apply(), 1)
        self.assert_success(before, [arc])

    def test_shared_mesh_and_nonzero_shape_keys_preserve_every_key_and_other_user(self):
        mesh = make_object("KeyedMesh", "MESH")
        mesh.shape_key_add(name="Basis")
        raised = mesh.shape_key_add(name="Raised")
        for index, point in enumerate(raised.data):
            point.co.z += 0.3 * (index + 1)
        raised.value = 0.65
        other = bpy.data.objects.new("SharedOther", mesh.data)
        bpy.context.scene.collection.objects.link(other)
        other.location = (-8, 3, 2)
        mesh.location = (2, 4, 6)
        mesh.rotation_euler = (0.2, -0.5, 0.7)
        shared_data = other.data
        select([mesh])
        before = self.capture()
        self.assertEqual(self.apply(), 1)
        self.assert_success(before, [mesh])
        self.assertNotEqual(mesh.data, shared_data)
        self.assertEqual(other.data, shared_data)

    def test_selected_mesh_parent_and_curve_child_both_move_origins_without_moving_parts(self):
        parent = make_object("SelectedMeshParent", "MESH")
        parent.location = (3, -2, 5)
        parent.rotation_euler = (0.4, -0.3, 0.8)
        parent.scale = (-1.3, 0.7, 2.0)
        child = make_rational_curve()
        child.parent = parent
        child.location = (-2, 4, 1)
        child.rotation_euler = (-0.2, 0.5, -0.4)
        child.scale = (0.8, -1.2, 1.5)
        child.delta_location = (0.3, -0.1, 0.2)
        child.matrix_parent_inverse = Matrix(((1, .15, 0, 1), (0, 1, .2, -2), (0, 0, 1, .5), (0, 0, 0, 1)))
        grandchild = make_object("UnselectedGrandchild", "MESH", child)
        grandchild.location = (2, 3, -1)
        grandchild.rotation_euler = (0.2, -0.4, 0.3)
        grandchild.delta_location = (-0.2, 0.1, 0.4)
        grandchild.matrix_parent_inverse = Matrix.Translation((1, -2, 3))
        original_data = {obj.name: obj.data for obj in (parent, child, grandchild)}
        select([parent, child], active=child)
        before = self.capture()
        self.assertEqual(self.apply(fixture.ContextWithSelection([child, parent])), 2)
        self.assert_success(before, [parent, child])
        for obj in (parent, child, grandchild):
            self.assertEqual(obj.data, original_data[obj.name])

    def test_bezier_handles_and_nurbs_weights_remain_in_world_space(self):
        curve = make_rational_curve()
        curve.location = (4, -2, 7)
        curve.rotation_euler = (-0.3, 0.6, 0.2)
        curve.scale = (1.2, 0.8, -1.7)
        original_data = curve.data
        select([curve])
        before = self.capture()
        self.assertEqual(self.apply(), 1)
        self.assert_success(before, [curve])
        self.assertEqual(curve.data, original_data)

    def test_translation_invariant_modifiers_keep_evaluated_geometry(self):
        mesh = make_object("ModifiedMesh", "MESH")
        bevel = mesh.modifiers.new("Keep Bevel", "BEVEL")
        bevel.width = 0.13
        bevel.segments = 2
        solidify = mesh.modifiers.new("Keep Solidify", "SOLIDIFY")
        solidify.thickness = 0.2
        mesh.location = (-2, 3, 4)
        select([mesh])
        before = self.capture()
        modifiers = tuple((modifier.name, modifier.type) for modifier in mesh.modifiers)
        self.assertEqual(self.apply(), 1)
        self.assert_success(before, [mesh])
        self.assertEqual(tuple((modifier.name, modifier.type) for modifier in mesh.modifiers), modifiers)
        self.assertAlmostEqual(bevel.width, 0.13)
        self.assertAlmostEqual(solidify.thickness, 0.2)

    def test_geometry_nodes_passthrough_is_safe_but_instance_only_output_is_rejected(self):
        for instances_only in (False, True):
            with self.subTest(instances_only=instances_only):
                fixture.clear_scene()
                valid = make_arc("IndependentValidArc")
                mesh = make_object("GeometryNodesMesh", "MESH")
                mesh.location = (3, -2, 4)
                group = bpy.data.node_groups.new("OriginInstanceFixture", "GeometryNodeTree")
                group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
                group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
                group_input = group.nodes.new("NodeGroupInput")
                group_output = group.nodes.new("NodeGroupOutput")
                if instances_only:
                    to_instance = group.nodes.new("GeometryNodeGeometryToInstance")
                    group.links.new(group_input.outputs["Geometry"], to_instance.inputs["Geometry"])
                    group.links.new(to_instance.outputs["Instances"], group_output.inputs["Geometry"])
                else:
                    group.links.new(group_input.outputs["Geometry"], group_output.inputs["Geometry"])
                modifier = mesh.modifiers.new("Preserve Native Geometry Nodes", "NODES")
                modifier.node_group = group
                select([valid, mesh], active=valid)
                before = self.capture()
                native_graph = node_group_snapshot(group)
                original_instances = instance_world_matrices(mesh)
                if instances_only:
                    self.assertTrue(original_instances, "Fixture must expose actual unevaluated-mesh instances")
                    self.assertEqual(fixture.evaluated_world_vertices(mesh), ())
                    self.assert_rejected(fixture.ContextWithSelection([valid, mesh]))
                else:
                    self.assertFalse(original_instances)
                    self.assertEqual(self.apply(), 2)
                    self.assert_success(before, [valid, mesh])
                self.assertEqual(modifier.node_group, group)
                self.assertEqual(node_group_snapshot(group), native_graph)
                self.assert_numeric_close(instance_world_matrices(mesh), original_instances)
                bpy.data.objects.remove(mesh, do_unlink=True)
                bpy.data.node_groups.remove(group)

    def test_mirror_using_own_origin_rejects_and_rolls_back_complete_batch(self):
        valid = make_arc("FirstValid")
        mirrored = make_object("OriginDependentMirror", "MESH")
        modifier = mirrored.modifiers.new("Own Origin Mirror", "MIRROR")
        modifier.use_axis = (True, False, False)
        modifier.use_clip = False
        modifier.use_mirror_merge = False
        select([valid, mirrored], active=valid)
        self.assert_rejected(fixture.ContextWithSelection([valid, mirrored]))

    def test_second_commit_failure_restores_datablocks_shape_keys_and_child_inverses(self):
        first = make_object("FirstKeyedMesh", "MESH")
        first.shape_key_add(name="Basis")
        key = first.shape_key_add(name="Raised")
        key.data[2].co.z += 1.7
        key.value = 0.4
        child = make_object("Child", "MESH", first)
        child.location = (2, 3, 4)
        child.matrix_parent_inverse = Matrix.Translation((-1, 2, 3))
        second = make_rational_curve()
        select([first, second], active=first)
        before = self.capture()
        data_pointers = {obj.name: obj.data.as_pointer() for obj in (first, second, child)}
        counts = (len(bpy.data.meshes), len(bpy.data.curves), len(bpy.data.shape_keys))
        commit = origin._origin_commit_object_data
        committed = []

        def fail_after_second_commit(obj, original_data, staged_data, transform):
            commit(obj, original_data, staged_data, transform)
            committed.append(obj.name)
            if obj == second:
                self.assert_numeric_close(first.matrix_world.translation, before[0]["cursor"].translation)
                raise RuntimeError("Injected failure after second data commit")

        with mock.patch.object(origin, "_origin_commit_object_data", side_effect=fail_after_second_commit):
            with self.assertRaisesRegex(RuntimeError, "Injected failure after second data commit"):
                self.apply(fixture.ContextWithSelection([first, second]))
        self.assertEqual(committed, [first.name, second.name])
        after = self.capture()
        self.assert_snapshot_equal(after[0], before[0])
        self.assert_snapshot_equal(after[1], before[1])
        self.assertEqual(data_pointers, {obj.name: obj.data.as_pointer() for obj in (first, second, child)})
        self.assertEqual((len(bpy.data.meshes), len(bpy.data.curves), len(bpy.data.shape_keys)), counts)

    def test_linked_object_is_rejected_before_editing_local_object(self):
        with tempfile.TemporaryDirectory(prefix="rr_object_origin_linked_") as temporary:
            source = make_object("ReadOnlyObject", "MESH")
            path = str(Path(temporary) / "object.blend")
            bpy.data.libraries.write(path, {source})
            bpy.data.objects.remove(source, do_unlink=True)
            with bpy.data.libraries.load(path, link=True) as (_, data_to):
                data_to.objects = ["ReadOnlyObject"]
            linked = data_to.objects[0]
            bpy.context.scene.collection.objects.link(linked)
            local = make_arc("WritableArc")
            select([local, linked], active=local)
            self.assertIsNotNone(linked.library)
            self.assert_rejected(fixture.ContextWithSelection([local, linked]))
            bpy.data.objects.remove(linked, do_unlink=True)

    def test_local_object_with_linked_mesh_data_gets_editable_copy(self):
        with tempfile.TemporaryDirectory(prefix="rr_origin_linked_data_") as temporary:
            source = make_object("LinkedDataSource", "MESH")
            source_data = source.data
            data_name = source_data.name
            path = str(Path(temporary) / "data.blend")
            bpy.data.libraries.write(path, {source_data})
            bpy.data.objects.remove(source, do_unlink=True)
            bpy.data.meshes.remove(source_data)
            with bpy.data.libraries.load(path, link=True) as (_, data_to):
                data_to.meshes = [data_name]
            linked_data = data_to.meshes[0]
            local = bpy.data.objects.new("LocalWithLinkedData", linked_data)
            bpy.context.scene.collection.objects.link(local)
            local.location = (2, -3, 4)
            original_points = tuple(tuple(vertex.co) for vertex in linked_data.vertices)
            select([local])
            before = self.capture()
            self.assertEqual(self.apply(), 1)
            self.assert_success(before, [local])
            self.assertIsNone(local.library)
            self.assertIsNone(local.data.library)
            self.assertNotEqual(local.data, linked_data)
            self.assertEqual(tuple(tuple(vertex.co) for vertex in linked_data.vertices), original_points)
            bpy.data.meshes.remove(linked_data)

    def test_nonfinite_cursor_and_singular_object_reject_without_partial_changes(self):
        valid = make_arc("Valid")
        invalid = make_object("Invalid", "MESH")
        select([valid, invalid], active=valid)
        invalid.scale.x = 0
        self.assert_rejected()
        invalid.scale.x = 1
        bpy.context.scene.cursor.location.z = float("nan")
        self.assert_rejected()

    def test_animated_or_constrained_object_rejects_without_destroying_controls(self):
        for control in ("ANIMATED", "CONSTRAINED", "DRIVER"):
            with self.subTest(control=control):
                fixture.clear_scene()
                mesh = make_object("Controlled", "MESH")
                if control == "ANIMATED":
                    mesh.keyframe_insert("location", frame=1)
                elif control == "CONSTRAINED":
                    constraint = mesh.constraints.new("LIMIT_LOCATION")
                    constraint.use_min_x = True
                    constraint.min_x = -100
                else:
                    mesh.driver_add("location", 0).driver.expression = "2.0"
                select([mesh])
                self.assert_rejected()

    def test_ambiguous_empty_mesh_selection_rejects_without_moving_either(self):
        empty = make_object("Empty")
        mesh = make_arc()
        select([empty, mesh], active=empty)
        self.assert_rejected()

    def test_actual_apply_operator_uses_object_mode_cursor_despite_bottom_rule(self):
        arc = make_arc()
        select([arc])
        bpy.context.scene.rr_builder_export_settings.modeling_origin_mode = "BOTTOM"
        before = self.capture()
        self.assertEqual(bpy.ops.rr_builder.apply_modeling_origin_point(mode="SELECTION"), {"FINISHED"})
        self.assert_success(before, [arc])

    def test_object_mode_ui_enables_apply_without_enabling_bookmark_selection(self):
        arc = make_arc()
        select([arc])
        settings = SimpleNamespace(modeling_show_origin_rules=True, point_bookmark_group="G1",
                                   point_bookmark_source="SELECTION", point_bookmark_space="WORLD")
        for point in ("p1", "p2", "p3"):
            setattr(settings, "point_g1_" + point, SimpleNamespace(is_set=False, alias=""))
        layout = RecordingLayout()
        rr.RR_PT_builder_exporter.draw_modeling_page(None, layout, bpy.context, settings)
        apply = [record for record in layout.records if record[1] == "Apply to Selection"]
        self.assertEqual(len(apply), 1)
        self.assertTrue(apply[0][2])
        self.assertEqual(apply[0][3].mode, "SELECTION")
        stores = [record for record in layout.records if record[0] == "rr_builder.store_point_bookmark"]
        self.assertEqual(len(stores), 3)
        self.assertTrue(all(not record[2] for record in stores))
        self.assertEqual(settings.point_bookmark_source, "SELECTION")
        self.assertEqual(settings.point_bookmark_group, "G1")


if __name__ == "__main__":
    if not bpy.app.background or bpy.data.filepath:
        raise RuntimeError("Run only in an isolated --background --factory-startup scene.")
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ObjectOriginTests))
    if result.wasSuccessful():
        print(f"RR_OBJECT_ORIGIN_PASS tests={result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
