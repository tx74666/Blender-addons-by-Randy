"""Empty-origin geometry/state checks in a disposable Blender factory scene.

Run serially with --background --factory-startup --disable-autoexec --threads 2
--python-exit-code 1 --python tests/test_rr_empty_origin_blender.py.
Imports the six existing mesh/curve origin regressions without duplicating them.
No production .blend is opened or saved; the linked-ID fixture uses a temp file.
"""

import importlib
import importlib.util
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
ADDONS_DIR = TEST_DIR.parent / "addons"
sys.path.insert(0, str(ADDONS_DIR))
rr = importlib.import_module("random_realm_builder_exporter")
origin = importlib.import_module("random_realm_builder_exporter.rr_modeling_origin")
assert Path(rr.__file__).resolve().is_relative_to(ADDONS_DIR.resolve()), rr.__file__

FLAGS = (
    "use_transform_data_origin", "use_transform_pivot_point_align", "use_transform_skip_children",
)
CHANNELS = (
    "location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
    "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale",
)
ROTATION_SCALE_CHANNELS = tuple(name for name in CHANNELS if name != "location")


class RR_TEST_PG_empty_origin_settings(bpy.types.PropertyGroup):
    modeling_origin_mode: bpy.props.StringProperty(default="SELECTION")


class ContextWithSelection:
    """Reverse selected-object iteration without changing real Blender selection."""

    def __init__(self, selected):
        self.selected_objects = list(selected)

    def __getattr__(self, name):
        return getattr(bpy.context, name)


def clear_scene():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in (bpy.data.meshes, bpy.data.curves, bpy.data.actions):
        for block in list(collection):
            if block.users == 0:
                collection.remove(block)


def make_object(name, kind="EMPTY", parent=None):
    data = None
    if kind == "MESH":
        data = bpy.data.meshes.new(name + "Mesh")
        data.from_pydata(((0, 0, 0), (2, 0, 0), (0, 3, 0), (0, 0, 4)), (), ((0, 1, 2), (0, 1, 3)))
        data.update()
    elif kind == "CURVE":
        data = bpy.data.curves.new(name + "Curve", "CURVE")
        data.dimensions = "3D"
        spline = data.splines.new("POLY")
        spline.points.add(2)
        for point, co in zip(spline.points, ((0, 0, 0, 1), (2, -3, 1, 1), (4, 2, 5, 1))):
            point.co = co
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    obj[rr.EXPORT_STABLE_ID_PROP] = "stable_" + name
    obj["test_origin_identity"] = name
    return obj


def select(objects, active=None):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active or (objects[0] if objects else None)
    bpy.context.view_layer.update()


def local_geometry(obj):
    if obj.type == "MESH":
        return tuple(tuple(vertex.co) for vertex in obj.data.vertices)
    if obj.type == "CURVE":
        return tuple(tuple(point.co[:3]) for spline in obj.data.splines for point in spline.points)
    return ()


def animation_snapshot(obj):
    animation = obj.animation_data
    if animation is None or animation.action is None:
        return None
    action = animation.action
    if hasattr(action, "fcurves"):
        curves = list(action.fcurves)
    else:
        curves = [
            curve for layer in action.layers for strip in layer.strips
            for channelbag in getattr(strip, "channelbags", ()) for curve in channelbag.fcurves
        ]
    return (action.name, tuple(sorted(
        (curve.data_path, curve.array_index, tuple(
            (tuple(point.co), tuple(point.handle_left), tuple(point.handle_right), point.interpolation)
            for point in curve.keyframe_points
        )) for curve in curves
    )))


def evaluated_world_vertices(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return tuple(tuple(evaluated.matrix_world @ vertex.co) for vertex in mesh.vertices)
    finally:
        evaluated.to_mesh_clear()


def freeze_rna_value(value):
    if isinstance(value, bpy.types.ID):
        return (type(value).__name__, value.name)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return tuple(freeze_rna_value(item) for item in value)


def constraint_snapshot(obj):
    result = []
    for constraint in obj.constraints:
        values = []
        for prop in constraint.bl_rna.properties:
            if prop.is_readonly or prop.type not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM", "POINTER"}:
                continue
            values.append((prop.identifier, freeze_rna_value(getattr(constraint, prop.identifier))))
        targets = tuple((getattr(item.target, "name", None), item.subtarget, item.weight)
                        for item in getattr(constraint, "targets", ()))
        result.append((constraint.type, tuple(values), targets))
    return tuple(result)


def driver_snapshot(obj):
    animation = obj.animation_data
    if animation is None:
        return ()
    return tuple((curve.data_path, curve.array_index, curve.mute,
                  curve.driver.type, curve.driver.expression, curve.driver.use_self,
                  tuple((variable.name, variable.type,
                         tuple((getattr(target.id, "name", None), target.data_path,
                                target.bone_target, target.transform_type, target.transform_space)
                               for target in variable.targets))
                        for variable in curve.driver.variables))
                 for curve in animation.drivers)


def snapshot():
    bpy.context.view_layer.update()
    objects = {}
    for obj in bpy.context.scene.objects:
        geometry = local_geometry(obj)
        objects[obj.name] = {
            "world": obj.matrix_world.copy(), "basis": obj.matrix_basis.copy(),
            "parent_inverse": obj.matrix_parent_inverse.copy(),
            "channels": {name: tuple(getattr(obj, name)) for name in CHANNELS},
            "rotation_mode": obj.rotation_mode, "parent": obj.parent.name if obj.parent else None,
            "parent_type": obj.parent_type, "parent_bone": obj.parent_bone,
            "parent_vertices": tuple(obj.parent_vertices),
            "stable_id": obj.get(rr.EXPORT_STABLE_ID_PROP),
            "identity": obj.get("test_origin_identity"),
            "local_geometry": geometry,
            "world_geometry": tuple(tuple(obj.matrix_world @ Vector(co)) for co in geometry),
            "animation": animation_snapshot(obj),
            "constraints": constraint_snapshot(obj), "drivers": driver_snapshot(obj),
        }
    return {
        "objects": objects,
        "selected": tuple(sorted(obj.name for obj in bpy.context.selected_objects)),
        "active": bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
        "mode": bpy.context.mode, "cursor": bpy.context.scene.cursor.matrix.copy(),
        "flags": tuple(getattr(bpy.context.scene.tool_settings, name) for name in FLAGS),
    }


class EmptyOriginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Register only the production operator and the settings it reads. No
        # add-on timers, scene handlers, export code or live UI are activated.
        bpy.utils.register_class(RR_TEST_PG_empty_origin_settings)
        bpy.types.Scene.rr_builder_export_settings = bpy.props.PointerProperty(type=RR_TEST_PG_empty_origin_settings)
        bpy.utils.register_class(rr.RR_OT_apply_modeling_origin_point)

    @classmethod
    def tearDownClass(cls):
        bpy.utils.unregister_class(rr.RR_OT_apply_modeling_origin_point)
        del bpy.types.Scene.rr_builder_export_settings
        bpy.utils.unregister_class(RR_TEST_PG_empty_origin_settings)

    def setUp(self):
        clear_scene()
        self.original_flags = tuple(getattr(bpy.context.scene.tool_settings, name) for name in FLAGS)
        self.original_global_undo = bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo = True
        for name, enabled in zip(FLAGS, (True, False, True)):
            setattr(bpy.context.scene.tool_settings, name, enabled)
        bpy.context.scene.cursor.location = (17.5, -8.25, 6.75)
        bpy.context.scene.cursor.rotation_euler = (0.2, -0.4, 0.6)
        bpy.context.scene.frame_set(1)

    def tearDown(self):
        clear_scene()
        for name, enabled in zip(FLAGS, self.original_flags):
            setattr(bpy.context.scene.tool_settings, name, enabled)
        bpy.context.preferences.edit.use_global_undo = self.original_global_undo

    def assert_numeric_close(self, actual, expected, places=4):
        if isinstance(expected, (tuple, list, Matrix, Vector)):
            self.assertEqual(len(actual), len(expected))
            for left, right in zip(actual, expected):
                self.assert_numeric_close(left, right, places)
        elif isinstance(expected, (float, int)) and not isinstance(expected, bool):
            if math.isnan(expected):
                self.assertTrue(math.isnan(actual))
            elif math.isinf(expected):
                self.assertEqual(actual, expected)
            else:
                self.assertAlmostEqual(actual, expected, places=places)
        else:
            self.assertEqual(actual, expected)

    def assert_snapshot_equal(self, actual, expected):
        self.assertEqual(actual.keys(), expected.keys())
        for key in expected:
            if isinstance(expected[key], dict):
                self.assert_snapshot_equal(actual[key], expected[key])
            else:
                self.assert_numeric_close(actual[key], expected[key])

    def make_hierarchy(self, transformed_parent=False):
        ancestor = make_object("Ancestor") if transformed_parent else None
        if ancestor:
            ancestor.location = (-9, 4, 2)
            ancestor.rotation_euler = (0.31, -0.43, 0.72)
            ancestor.scale = (-1.6, 0.7, 2.3)
        root = make_object("OriginEmpty", parent=ancestor)
        root.location = (3, -2, 7)
        root.rotation_mode = "QUATERNION"
        root.rotation_quaternion = Vector((1, 2, 3)).to_track_quat("Z", "Y")
        root.scale = (0.8, -1.2, 1.7)
        root.delta_location = (0.2, -0.3, 0.4)
        if ancestor:
            root.matrix_parent_inverse = Matrix.Translation((1.1, -2.2, 0.7))
        branch = make_object("BranchEmpty", parent=root)
        branch.location = (4, 1, -2)
        branch.rotation_euler = (-0.4, 0.2, 0.1)
        branch.scale = (1.1, 0.9, -0.8)
        branch.matrix_parent_inverse = Matrix.Translation((-1, 2, 3))
        leaf = make_object("GrandchildMesh", "MESH", branch)
        leaf.location = (-1, 3, 2)
        leaf.rotation_euler = (0.2, 0.5, -0.3)
        leaf.scale = (0.9, 1.4, 0.6)
        curve = make_object("DirectCurve", "CURVE", root)
        curve.location = (1, -4, 2)
        select([root])
        return root, branch, leaf, curve

    def apply(self, context=None):
        return origin.apply_modeling_origin(
            context or bpy.context, SimpleNamespace(modeling_origin_mode="SELECTION"), mode="SELECTION")

    def assert_success_preserved(self, before, selected, expected_target=None):
        after = snapshot()
        selected_names = {obj.name for obj in selected}
        target = before["cursor"].translation if expected_target is None else expected_target
        self.assertEqual(before["objects"].keys(), after["objects"].keys())
        for key in ("selected", "active", "mode", "cursor", "flags"):
            self.assert_numeric_close(after[key], before[key])
        for name, old in before["objects"].items():
            new = after["objects"][name]
            with self.subTest(object=name):
                for field in ("rotation_mode", "parent", "parent_type", "parent_bone", "parent_vertices",
                              "stable_id", "identity", "local_geometry", "world_geometry", "animation",
                              "constraints", "drivers"):
                    self.assert_numeric_close(new[field], old[field])
                if name in selected_names:
                    self.assert_numeric_close(new["world"].translation, target)
                    self.assert_numeric_close(new["world"].to_3x3(), old["world"].to_3x3())
                    for channel in ROTATION_SCALE_CHANNELS:
                        self.assert_numeric_close(new["channels"][channel], old["channels"][channel])
                else:
                    self.assert_numeric_close(new["world"], old["world"])
                    self.assert_numeric_close(new["basis"], old["basis"])
                    self.assert_snapshot_equal(new["channels"], old["channels"])

    def assert_rejected_without_changes(self, context=None):
        before = snapshot()
        with self.assertRaises(RuntimeError):
            self.apply(context)
        self.assert_snapshot_equal(snapshot(), before)

    def test_empty_selection_helper_requires_nonempty_all_empty_object_mode(self):
        root = make_object("SelectorEmpty")
        mesh = make_object("SelectorMesh", "MESH")
        select([root])
        self.assertEqual(origin.selected_empty_objects_for_modeling_origin(bpy.context), [root])
        self.assertEqual(origin.selected_empty_objects_for_modeling_origin(None), [])
        for mode, objects in (("OBJECT", []), ("OBJECT", [root, mesh]), ("EDIT_MESH", [root]), ("POSE", [root])):
            with self.subTest(mode=mode, objects=len(objects)):
                self.assertEqual(origin.selected_empty_objects_for_modeling_origin(
                    SimpleNamespace(mode=mode, selected_objects=objects)), [])

    def test_unparented_empty_moves_only_origin_with_no_children(self):
        root = make_object("SoloEmpty")
        root.location = (2, 3, 4)
        root.rotation_euler = (0.4, -0.2, 0.8)
        root.scale = (-2, 3, 0.75)
        select([root])
        before = snapshot()
        self.assertEqual(self.apply(), 1)
        self.assert_success_preserved(before, [root])

    def test_selection_resolver_distinguishes_cursor_target_and_ambiguous_selection(self):
        root = make_object("SelectorEmpty")
        second = make_object("SecondSelectorEmpty")
        target = make_object("SelectorTarget", "MESH")
        other = make_object("OtherSelectorTarget", "CURVE")
        cases = (
            ([root], root, [root], None),
            ([root, second], second, [root, second], None),
            ([root, target], target, [root], target),
            ([root, second, target], target, [root, second], target),
            ([root, target], root, [], None),
            ([root, target, other], target, [], None),
            ([target], target, [], None),
            ([], None, [], None),
        )
        for selected, active, expected_empties, expected_target in cases:
            with self.subTest(selected=[obj.name for obj in selected], active=active.name if active else None):
                select(selected, active)
                empties, resolved_target = origin.modeling_empty_origin_selection(bpy.context)
                self.assertCountEqual(empties, expected_empties)
                self.assertEqual(resolved_target, expected_target)
        self.assertEqual(origin.modeling_empty_origin_selection(None), ([], None))
        for mode in ("EDIT_MESH", "EDIT_CURVE", "POSE"):
            with self.subTest(mode=mode):
                self.assertEqual(origin.modeling_empty_origin_selection(SimpleNamespace(
                    mode=mode, selected_objects=[root, target], active_object=target,
                    view_layer=SimpleNamespace(objects=SimpleNamespace(active=target)))), ([], None))

    def test_empty_moves_to_independent_active_object_world_origin_not_cursor_or_bounds(self):
        for target_kind in ("MESH", "CURVE"):
            with self.subTest(target_kind=target_kind):
                clear_scene()
                root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
                target_parent = make_object("TargetParent")
                target_parent.location = (-7, 11, 9)
                target_parent.rotation_euler = (0.3, -0.5, 0.8)
                target_parent.scale = (-1.4, 0.6, 2.1)
                target = make_object("ActiveTarget", target_kind, target_parent)
                target.location = (2, -5, 4)
                target.matrix_parent_inverse = Matrix.Translation((1.2, -0.4, 2.3))
                select([root, target], active=target)
                before = snapshot()
                expected_target = before["objects"][target.name]["world"].translation.copy()
                self.assertGreater((expected_target - before["cursor"].translation).length, 1.0)
                self.assertEqual(origin.selected_empty_objects_for_modeling_origin(bpy.context), [])
                self.assertEqual(self.apply(), 1)
                self.assert_success_preserved(before, [root], expected_target)

    def test_active_descendant_target_origin_and_geometry_remain_fixed(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        select([root, leaf], active=leaf)
        before = snapshot()
        expected_target = before["objects"][leaf.name]["world"].translation.copy()
        self.assertEqual(self.apply(), 1)
        self.assert_success_preserved(before, [root], expected_target)

    def test_multiple_nested_empties_reach_active_target_with_reversed_iteration(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        select([root, branch, leaf], active=leaf)
        before = snapshot()
        expected_target = before["objects"][leaf.name]["world"].translation.copy()
        self.assertEqual(self.apply(ContextWithSelection([leaf, branch, root])), 2)
        self.assert_success_preserved(before, [root, branch], expected_target)

    def test_multilevel_children_keep_world_geometry_local_channels_and_parent_ids(self):
        root, branch, leaf, curve = self.make_hierarchy()
        parents = {obj.name: obj.parent for obj in (branch, leaf, curve)}
        before = snapshot()
        self.assertEqual(self.apply(), 1)
        self.assert_success_preserved(before, [root])
        for obj in (branch, leaf, curve):
            self.assertEqual(obj.parent, parents[obj.name])

    def test_rotated_nonuniform_negative_scale_ancestor_preserves_full_world_matrices(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        before = snapshot()
        self.assertEqual(self.apply(), 1)
        self.assert_success_preserved(before, [root])

    def test_nested_selected_empties_reach_cursor_when_child_is_iterated_first(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        select([root, branch], active=branch)
        before = snapshot()
        self.assertEqual(self.apply(ContextWithSelection([branch, root])), 2)
        self.assert_success_preserved(before, [root, branch])

    def test_selected_descendant_below_unselected_empty_reaches_cursor(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        inner = make_object("SelectedInnerEmpty", parent=branch)
        inner.location = (-3, 5, 1)
        inner.rotation_euler = (0.5, -0.7, 0.2)
        leaf.parent = inner
        select([root, inner], active=inner)
        before = snapshot()
        self.assertEqual(self.apply(ContextWithSelection([inner, root])), 2)
        self.assert_success_preserved(before, [root, inner])

    def test_child_animation_channels_and_future_world_motion_remain_unchanged(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        leaf.location = (-1, 3, 2)
        leaf.keyframe_insert("location", frame=1)
        leaf.location = (5, -2, 6)
        leaf.keyframe_insert("location", frame=12)
        bpy.context.scene.frame_set(12)
        future_world = leaf.matrix_world.copy()
        bpy.context.scene.frame_set(1)
        before = snapshot()
        self.assertIsNotNone(before["objects"][leaf.name]["animation"])
        self.assertEqual(self.apply(), 1)
        self.assert_success_preserved(before, [root])
        bpy.context.scene.frame_set(12)
        self.assert_numeric_close(leaf.matrix_world, future_world)

    def test_active_empty_with_nonempty_selected_is_rejected_without_partial_change(self):
        root, branch, leaf, curve = self.make_hierarchy()
        select([root, leaf], active=root)
        self.assert_rejected_without_changes()

    def test_multiple_nonempty_targets_are_rejected_without_partial_change(self):
        root, branch, leaf, curve = self.make_hierarchy()
        for active in (leaf, curve, root):
            with self.subTest(active=active.name):
                select([root, branch, leaf, curve], active=active)
                self.assert_rejected_without_changes()

    def test_new_target_selection_rejects_unsafe_empty_before_moving_any_origin(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        root.keyframe_insert("location", frame=1)
        select([root, branch, leaf], active=leaf)
        self.assert_rejected_without_changes()

    def test_external_active_target_with_empty_dependent_constraint_or_driver_is_rejected(self):
        for dependency in ("constraint", "driver"):
            with self.subTest(dependency=dependency):
                clear_scene()
                root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
                target = make_object("DependentExternalTarget", "MESH")
                target.location = (10, -3, 5)
                if dependency == "constraint":
                    constraint = target.constraints.new("COPY_LOCATION")
                    constraint.target = root
                    constraint.use_offset = True
                else:
                    driver = target.driver_add("location", 0).driver
                    variable = driver.variables.new()
                    variable.name = "origin_x"
                    variable.type = "TRANSFORMS"
                    variable.targets[0].id = root
                    variable.targets[0].transform_type = "LOC_X"
                    variable.targets[0].transform_space = "WORLD_SPACE"
                    driver.expression = "origin_x + 10.0"
                select([root, target], active=target)
                self.assertNotIn(target, origin.object_descendants(root))
                self.assertGreater((target.matrix_world.translation - root.matrix_world.translation).length, 1.0)
                self.assert_rejected_without_changes()

    def test_collection_instance_empty_is_rejected_before_valid_empty_changes(self):
        root, branch, leaf, curve = self.make_hierarchy()
        instance = make_object("InstanceEmpty")
        instance.instance_type = "COLLECTION"
        instance.instance_collection = bpy.data.collections.new("OriginInstanceCollection")
        select([root, instance], active=root)
        self.assert_rejected_without_changes(ContextWithSelection([root, instance]))

    def test_selected_empty_animation_is_rejected(self):
        root, branch, leaf, curve = self.make_hierarchy()
        root.keyframe_insert("location", frame=1)
        self.assert_rejected_without_changes()

    def test_constraint_on_selected_empty_is_rejected(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        target = make_object("ConstraintTarget")
        constraint = root.constraints.new("COPY_LOCATION")
        constraint.target = target
        self.assert_rejected_without_changes()

    def test_child_copy_constraint_to_fixed_external_target_keeps_world_and_settings(self):
        for owner_space in ("WORLD", "LOCAL"):
            with self.subTest(owner_space=owner_space):
                clear_scene()
                root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
                target = make_object("StableExternalConstraintTarget")
                target.location = (-4, 5, 8)
                constraint = leaf.constraints.new("COPY_LOCATION")
                constraint.target = target
                constraint.owner_space = owner_space
                constraint.target_space = "WORLD"
                constraint.influence = 0.65
                before = snapshot()
                self.assertEqual(self.apply(), 1)
                self.assert_success_preserved(before, [root])

    def test_elevator_sibling_copy_and_limit_rotation_preserve_two_animation_frames(self):
        for owner_space in ("WORLD", "LOCAL"):
            for target_space in ("WORLD", "LOCAL"):
                with self.subTest(owner_space=owner_space, target_space=target_space):
                    clear_scene()
                    root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
                    right = make_object("ElevatorDoor.R", "MESH", root)
                    right.location = (2, -0.25, 1)
                    left = make_object("ElevatorDoor.L", "MESH", root)
                    left.location = (-2, 0.25, 1)
                    limit = right.constraints.new("LIMIT_ROTATION")
                    limit.owner_space = owner_space
                    limit.use_limit_z = True
                    limit.min_z = -0.4
                    limit.max_z = 0.4
                    copied = left.constraints.new("COPY_ROTATION")
                    copied.target = right
                    copied.owner_space = owner_space
                    copied.target_space = target_space
                    copied.influence = 0.7
                    right.rotation_euler = (0.1, -0.2, -0.8)
                    right.keyframe_insert("rotation_euler", frame=1)
                    right.rotation_euler = (-0.3, 0.25, 1.2)
                    right.keyframe_insert("rotation_euler", frame=12)
                    bpy.context.scene.frame_set(12)
                    future = snapshot()
                    bpy.context.scene.frame_set(1)
                    before = snapshot()
                    self.assertEqual(self.apply(), 1)
                    self.assert_success_preserved(before, [root])
                    bpy.context.scene.frame_set(12)
                    self.assert_success_preserved(future, [root])

    def test_child_constraint_to_moving_empty_is_rejected_without_changes(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        constraint = leaf.constraints.new("COPY_ROTATION")
        constraint.target = root
        self.assert_rejected_without_changes()

    def test_child_constraint_collection_target_to_moving_empty_is_rejected(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        constraint = leaf.constraints.new("ARMATURE")
        constraint.targets.new().target = root
        # Check the reference even while this incomplete constraint is invalid;
        # its dependency must not bypass scanning merely by using a collection.
        self.assert_rejected_without_changes()

    def test_child_limit_rotation_with_fixed_custom_space_remains_supported(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        space = make_object("StableCustomSpace")
        space.location = (4, -5, 7)
        space.rotation_euler = (-0.4, 0.2, 0.3)
        limit = leaf.constraints.new("LIMIT_ROTATION")
        limit.owner_space = "CUSTOM"
        limit.space_object = space
        limit.use_limit_z = True
        limit.min_z = -0.25
        limit.max_z = 0.25
        before = snapshot()
        self.assertEqual(self.apply(), 1)
        self.assert_success_preserved(before, [root])

    def test_child_constraint_custom_space_depending_on_moving_empty_is_rejected(self):
        for indirect in (False, True):
            with self.subTest(indirect=indirect):
                clear_scene()
                root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
                space = root
                if indirect:
                    space = make_object("DependentCustomSpace")
                    follows_root = space.constraints.new("COPY_LOCATION")
                    follows_root.target = root
                limit = leaf.constraints.new("LIMIT_ROTATION")
                limit.owner_space = "CUSTOM"
                limit.space_object = space
                limit.use_limit_z = True
                limit.max_z = 0.5
                self.assert_rejected_without_changes()

    def test_child_constraint_target_indirectly_depending_on_moving_empty_is_rejected(self):
        for dependency in ("constraint", "driver", "parent"):
            with self.subTest(dependency=dependency):
                clear_scene()
                root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
                external = make_object("ExternalDependentTarget")
                if dependency == "driver":
                    driver = external.driver_add("location", 0).driver
                    variable = driver.variables.new()
                    variable.name = "origin_x"
                    variable.type = "TRANSFORMS"
                    variable.targets[0].id = root
                    variable.targets[0].transform_type = "LOC_X"
                    variable.targets[0].transform_space = "WORLD_SPACE"
                    driver.expression = "origin_x + 2.0"
                else:
                    dependent = external
                    if dependency == "parent":
                        dependent = make_object("ExternalDependentParent")
                        external.parent = dependent
                        external.location = (3, 2, 1)
                    follows_root = dependent.constraints.new("COPY_LOCATION")
                    follows_root.target = root
                copied = leaf.constraints.new("COPY_LOCATION")
                copied.target = external
                self.assertNotIn(external, origin.object_descendants(root))
                self.assert_rejected_without_changes()

    def test_constrained_elevator_children_survive_operator_undo(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        limit = curve.constraints.new("LIMIT_ROTATION")
        limit.owner_space = "LOCAL"
        limit.use_limit_z = True
        limit.min_z = -0.3
        limit.max_z = 0.3
        copied = leaf.constraints.new("COPY_ROTATION")
        copied.target = curve
        copied.owner_space = "LOCAL"
        copied.target_space = "LOCAL"
        before = snapshot()
        bpy.ops.ed.undo_push(message="Before constrained elevator origin")
        self.assertEqual(bpy.ops.rr_builder.apply_modeling_origin_point(mode="SELECTION"), {"FINISHED"})
        self.assert_success_preserved(before, [root])
        bpy.ops.ed.undo_push(message="After constrained elevator origin")
        self.assertEqual(bpy.ops.ed.undo(), {"FINISHED"})
        self.assert_snapshot_equal(snapshot(), before)

    def test_constraint_on_unmoved_ancestor_remains_supported(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        target = make_object("FixedAncestorConstraintTarget")
        target.location = (-6, 8, 3)
        constraint = root.parent.constraints.new("COPY_LOCATION")
        constraint.target = target
        before = snapshot()
        self.assertEqual(self.apply(), 1)
        self.assert_success_preserved(before, [root])

    def test_driver_on_child_transform_is_rejected(self):
        root, branch, leaf, curve = self.make_hierarchy()
        leaf.driver_add("location", 0).driver.expression = "2.5"
        self.assert_rejected_without_changes()

    def test_mirror_using_selected_empty_is_rejected_without_geometry_changes(self):
        root, branch, leaf, curve = self.make_hierarchy()
        mirror = leaf.modifiers.new("OriginDependentMirror", "MIRROR")
        mirror.mirror_object = root
        bpy.context.view_layer.update()
        geometry = evaluated_world_vertices(leaf)
        self.assert_rejected_without_changes()
        self.assert_numeric_close(evaluated_world_vertices(leaf), geometry)

    def test_mirror_using_unmoved_target_remains_supported(self):
        root, branch, leaf, curve = self.make_hierarchy()
        target = make_object("UnmovedMirrorTarget")
        target.location = (3, -5, 7)
        mirror = leaf.modifiers.new("IndependentMirror", "MIRROR")
        mirror.mirror_object = target
        bpy.context.view_layer.update()
        geometry = evaluated_world_vertices(leaf)
        before = snapshot()
        self.assertEqual(self.apply(), 1)
        self.assert_success_preserved(before, [root])
        self.assert_numeric_close(evaluated_world_vertices(leaf), geometry)

    def test_indirect_mirror_dependency_rolls_back_after_evaluated_geometry_changes(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        target = make_object("IndirectMirrorTarget")
        follows_root = target.constraints.new("COPY_LOCATION")
        follows_root.target = root
        mirror = leaf.modifiers.new("IndirectOriginDependentMirror", "MIRROR")
        mirror.mirror_object = target
        mirror.use_mirror_merge = False
        bpy.context.view_layer.update()
        geometry = evaluated_world_vertices(leaf)
        before = snapshot()
        apply_one = origin._apply_empty_origin
        attempted_origins = []

        def record_mutated_geometry(context, obj, point):
            apply_one(context, obj, point)
            attempted_origins.append(obj.name)
            self.assert_numeric_close(obj.matrix_world.translation, point)
            changed_geometry = evaluated_world_vertices(leaf)
            self.assertEqual(len(changed_geometry), len(geometry))
            self.assertGreater(max((Vector(new) - Vector(old)).length
                                   for new, old in zip(changed_geometry, geometry)), 1.0e-3)

        with mock.patch.object(origin, "_apply_empty_origin", side_effect=record_mutated_geometry):
            with self.assertRaisesRegex(RuntimeError, "unable to preserve.*evaluated geometry"):
                self.apply()
        self.assertEqual(attempted_origins, [root.name])
        self.assert_snapshot_equal(snapshot(), before)
        self.assert_numeric_close(evaluated_world_vertices(leaf), geometry)

    def test_geometry_nodes_without_interface_properties_does_not_block_empty_origin(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        tree = bpy.data.node_groups.new("EmptyOriginNoInterface", "GeometryNodeTree")
        self.addCleanup(bpy.data.node_groups.remove, tree)
        modifier = leaf.modifiers.new("NoInterfaceGeometryNodes", "NODES")
        modifier.node_group = tree
        self.assertEqual(len(tree.interface.items_tree), 0)
        before = snapshot()
        self.assertEqual(self.apply(), 1)
        self.assert_success_preserved(before, [root])
        self.assertEqual(modifier.node_group, tree)

    def test_geometry_nodes_without_interface_still_rejects_object_info_empty_dependency(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        tree = bpy.data.node_groups.new("EmptyOriginNoInterfaceDependency", "GeometryNodeTree")
        self.addCleanup(bpy.data.node_groups.remove, tree)
        node = tree.nodes.new("GeometryNodeObjectInfo")
        node.inputs["Object"].default_value = root
        modifier = leaf.modifiers.new("NoInterfaceDependentGeometryNodes", "NODES")
        modifier.node_group = tree
        self.assertEqual(len(tree.interface.items_tree), 0)
        self.assert_rejected_without_changes()

    def test_modifier_values_typeerror_keeps_scanning_real_geometry_node_references(self):
        root, branch, leaf, curve = self.make_hierarchy()
        tree = bpy.data.node_groups.new("EmptyOriginMissingIdProperties", "GeometryNodeTree")
        self.addCleanup(bpy.data.node_groups.remove, tree)
        modifier = leaf.modifiers.new("MissingIdProperties", "NODES")
        modifier.node_group = tree
        # Blender versions differ in whether an empty ID-property container is
        # allocated. Simulate only its missing-container TypeError, retaining
        # the real modifier RNA and real node group for dependency traversal.
        missing_values = mock.Mock(side_effect=TypeError("this type doesn't support IDProperties"))
        wrapper = SimpleNamespace(
            bl_rna=modifier.bl_rna, type=modifier.type, node_group=tree, values=missing_values,
        )
        self.assertFalse(origin._origin_modifier_references_selected_object(wrapper, {root}))
        node = tree.nodes.new("GeometryNodeObjectInfo")
        node.inputs["Object"].default_value = root
        self.assertTrue(origin._origin_modifier_references_selected_object(wrapper, {root}))
        self.assertEqual(missing_values.call_count, 2)
        missing_values.side_effect = RuntimeError("Unexpected modifier access failure")
        with self.assertRaisesRegex(RuntimeError, "Unexpected modifier access failure"):
            origin._origin_modifier_references_selected_object(wrapper, {root})

    def test_non_object_parenting_is_rejected(self):
        root, branch, leaf, curve = self.make_hierarchy()
        vertex_parent = make_object("VertexParent", "MESH")
        root.parent = vertex_parent
        root.parent_type = "VERTEX"
        root.parent_vertices[0] = 0
        self.assert_rejected_without_changes()

    def test_singular_ancestor_is_rejected_without_touching_valid_selected_empty(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        root.parent.scale.x = 0
        valid = make_object("IndependentValidEmpty")
        select([valid, root], active=valid)
        self.assert_rejected_without_changes(ContextWithSelection([valid, root]))

    def test_nonfinite_cursor_is_rejected_before_any_transform_write(self):
        root, branch, leaf, curve = self.make_hierarchy()
        bpy.context.scene.cursor.location.x = float("nan")
        self.assert_rejected_without_changes()

    def test_nonfinite_descendant_transform_is_rejected_before_any_write(self):
        root, branch, leaf, curve = self.make_hierarchy()
        leaf.location.z = float("nan")
        self.assert_rejected_without_changes()

    def test_linked_read_only_empty_is_rejected_before_local_empty_changes(self):
        with tempfile.TemporaryDirectory(prefix="rr_empty_origin_linked_") as temporary:
            source = make_object("LinkedOriginFixture")
            path = str(Path(temporary) / "origin_fixture.blend")
            bpy.data.libraries.write(path, {source})
            bpy.data.objects.remove(source, do_unlink=True)
            with bpy.data.libraries.load(path, link=True) as (data_from, data_to):
                data_to.objects = ["LinkedOriginFixture"]
            linked = data_to.objects[0]
            bpy.context.scene.collection.objects.link(linked)
            local = make_object("LocalValidEmpty")
            select([local, linked], active=local)
            self.assertIsNotNone(linked.library)
            self.assert_rejected_without_changes(ContextWithSelection([local, linked]))
            bpy.data.objects.remove(linked, do_unlink=True)

    def test_failure_after_first_origin_rolls_back_locations_and_parent_inverses(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        select([root, branch], active=branch)
        before = snapshot()
        apply_one = origin._apply_empty_origin
        applied_names = []

        def fail_second(context, obj, target):
            applied_names.append(obj.name)
            if obj == branch:
                self.assert_numeric_close(root.matrix_world.translation, target)
                raise RuntimeError("Injected second Empty failure")
            return apply_one(context, obj, target)

        with mock.patch.object(origin, "_apply_empty_origin", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "Injected second Empty failure"):
                self.apply(ContextWithSelection([branch, root]))
        self.assertEqual(applied_names, [root.name, branch.name])
        self.assert_snapshot_equal(snapshot(), before)

    def test_production_operator_undo_restores_hierarchy_geometry_and_selection(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        select([root, branch], active=branch)
        before = snapshot()
        self.assertIn("UNDO", rr.RR_OT_apply_modeling_origin_point.bl_options)
        bpy.ops.ed.undo_push(message="Before isolated Empty origin")
        self.assertEqual(bpy.ops.rr_builder.apply_modeling_origin_point(mode="SELECTION"), {"FINISHED"})
        self.assert_success_preserved(before, [root, branch])
        # Background Python does not have the interactive operator event loop;
        # delimit its post-operation state explicitly before exercising Undo.
        bpy.ops.ed.undo_push(message="After isolated Empty origin")
        self.assertEqual(bpy.ops.ed.undo(), {"FINISHED"})
        self.assert_snapshot_equal(snapshot(), before)

    def test_active_target_operator_undo_restores_descendant_and_selected_empties(self):
        root, branch, leaf, curve = self.make_hierarchy(transformed_parent=True)
        select([root, branch, leaf], active=leaf)
        before = snapshot()
        expected_target = before["objects"][leaf.name]["world"].translation.copy()
        bpy.ops.ed.undo_push(message="Before Empty origins to active object")
        self.assertEqual(bpy.ops.rr_builder.apply_modeling_origin_point(mode="SELECTION"), {"FINISHED"})
        self.assert_success_preserved(before, [root, branch], expected_target)
        bpy.ops.ed.undo_push(message="After Empty origins to active object")
        self.assertEqual(bpy.ops.ed.undo(), {"FINISHED"})
        self.assert_snapshot_equal(snapshot(), before)


def existing_origin_regressions():
    path = TEST_DIR / "test_rr_exporter_contracts_blender.py"
    spec = importlib.util.spec_from_file_location("rr_existing_origin_regressions", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = sorted(name for name in unittest.defaultTestLoader.getTestCaseNames(module.ExporterUvContractTests)
                   if name.startswith("test_origin_"))
    if len(names) != 6:
        raise RuntimeError(f"Expected six existing mesh/curve origin regressions, found {len(names)}")
    return unittest.TestSuite(module.ExporterUvContractTests(name) for name in names)


if __name__ == "__main__":
    if not bpy.app.background or bpy.data.filepath:
        raise RuntimeError("Run only in an isolated --background --factory-startup scene.")
    suite = unittest.TestSuite((unittest.defaultTestLoader.loadTestsFromTestCase(EmptyOriginTests),
                              existing_origin_regressions()))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print(f"RR_EMPTY_ORIGIN_PASS tests={result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
