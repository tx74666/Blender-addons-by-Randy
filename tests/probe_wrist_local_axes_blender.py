"""Disposable Blender 5.2 probe for wrist operator orientation; no runtime edits."""
import json
import os
import sys
import tempfile

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "addons"), os.path.join(ROOT, "tests")]
from character_designer import limb_ik, root_control
import test_limb_ik_blender as base
import test_limb_ik_fk_blender as fixtures
from test_wrist_rotation_blender import select, update, rotation_vector


def run():
    base.ensure_registered()
    rows = []
    for prop in ("use_transform_at_custom_shape", "use_transform_around_custom_shape"):
        print("PROPERTY", prop, bpy.types.PoseBone.bl_rna.properties[prop].description, flush=True)
    for method in ("ROLL_DECOUPLED", "DIRECT_PREROLL"):
        for selected in ("LEFT_ARM", "RIGHT_ARM"):
            for manual in (False, True):
                for scale_case in ("uniform", "visual_offsets", "no_parent", "parent_nonuniform", "object_nonuniform"):
                    if scale_case == "no_parent" and method == "ROLL_DECOUPLED":
                        continue
                    rig, key, data = fixtures.build(method, selected)
                    if method == "DIRECT_PREROLL" and scale_case != "no_parent":
                        root_control.build(bpy.context, rig)
                    if manual:
                        assert bpy.ops.character_designer.limb_ik_auto_align_target(action="DISABLE") == {"FINISHED"}
                    data = limb_ik._validate_inventory(rig)["rigs"][key]
                    target = rig.pose.bones[data["target"].name]
                    hand = rig.pose.bones[data["chain"][2]]
                    rig.location = (.6, -.3, .2)
                    rig.rotation_euler = (.29, -.41, .13)
                    rig.scale = (.8, .8, .8) if scale_case != "object_nonuniform" else (.8, 1.13, .67)
                    if target.parent:
                        target.parent.rotation_mode = "XYZ"
                        target.parent.rotation_euler = (.26, .18, -.35)
                        target.parent.scale = (1.2, .81, 1.13) if scale_case == "parent_nonuniform" else (1, 1, 1)
                    target.location += Vector((-.31 if selected == "LEFT_ARM" else .31, -.28, .21))
                    target.rotation_euler = (.31, 0, .42)
                    if scale_case == "visual_offsets":
                        target.custom_shape_scale_xyz = (-.21, .13, .34)
                        target.custom_shape_rotation_euler = (.4, -.6, .2)
                        target.custom_shape_translation = (.14, -.03, .08)
                    # Keep visual offsets ordinary: the requested orientation is
                    # the displayed hand bone, independent of mesh presentation.
                    target.use_transform_around_custom_shape = False
                    select(rig, target)
                    update(rig)
                    basis = target.matrix_basis.copy()
                    w = bpy.context.window
                    a = next(a for a in w.screen.areas if a.type == "VIEW_3D")
                    region = next(r for r in a.regions if r.type == "WINDOW")
                    view = a.spaces.active.region_3d.view_matrix.inverted().to_quaternion()
                    for enabled in (False, True):
                        matrices = {pb.name: pb.matrix.copy() for pb in rig.pose.bones}
                        target.use_transform_at_custom_shape = enabled
                        update(rig)
                        assert max(abs(matrix[i][j] - rig.pose.bones[name].matrix[i][j])
                                   for name, matrix in matrices.items()
                                   for i in range(4) for j in range(4)) < 2e-5
                        for orientation, axis in (("LOCAL", "Y"), ("GLOBAL", "Y"), ("VIEW", "Z")):
                            for sign in (-1, 1):
                                target.matrix_basis = basis
                                update(rig)
                                before = rig.matrix_world @ hand.matrix
                                input_before = rig.matrix_world @ target.matrix
                                direction = (before.to_3x3() @ Vector((0, 1, 0))).normalized() if orientation == "LOCAL" else view @ Vector((0, 0, 1)) if orientation == "VIEW" else Vector((0, 1, 0))
                                angle = sign * .19
                                location_before = target.location.copy()
                                with bpy.context.temp_override(window=w, area=a, region=region):
                                    result = bpy.ops.transform.rotate(value=angle, orient_axis=axis, orient_type=orientation,
                                        constraint_axis=tuple(c == axis for c in "XYZ"))
                                assert result == {"FINISHED"}
                                update(rig)
                                after = rig.matrix_world @ hand.matrix
                                actual = rotation_vector(before, after)
                                row = dict(method=method, side=selected, manual=manual, scale=scale_case, at_shape=enabled,
                                    orientation=orientation, sign=sign,
                                    display=target.custom_shape_transform.name if target.custom_shape_transform else None,
                                    rotation_error=(actual - direction * angle).length,
                                    position_error=(after.translation - before.translation).length,
                                    channel_position_error=(target.location - location_before).length,
                                    input_hand_y_angle=input_before.to_3x3().col[1].angle(before.to_3x3().col[1]),
                                    actual=tuple(actual), expected=tuple(direction * angle))
                                rows.append(row)
                                print("ROW", json.dumps(row), flush=True)
                                if scale_case in {"uniform", "visual_offsets", "no_parent"}:
                                    if enabled or orientation != "LOCAL" or manual:
                                        assert row["rotation_error"] < 8e-5, row
                                    assert row["position_error"] < 2e-5, row
                                    assert row["channel_position_error"] < 2e-5, row
                                assert not target.use_transform_around_custom_shape
    outfile = os.environ.get("WRIST_PROBE_OUTPUT", os.path.join(tempfile.gettempdir(), "probe_wrist_local_axes_result.json"))
    with open(outfile, "w", encoding="utf-8") as output:
        json.dump(rows, output, indent=2)
    print("PROBE_COMPLETE", len(rows), outfile, flush=True)


if __name__ == "__main__":
    run()
