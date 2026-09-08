"""Compact bone groups: per-limb fallback, rig lifecycle and native persistence."""
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
from character_designer import bone_collections as groups, limb_ik
from test_limb_ik_blender import (analyze, cancelled_result, ensure_registered,
                                ensure_unregistered, make_humanoid, reset_scene)


def members(rig, name):
    return set(rig.data.collections_all[name].bones.keys())


def fixture(method):
    reset_scene()
    rig = make_humanoid()
    torso = rig.data.collections.new("Torso")
    torso.assign(rig.data.bones["Hips"])
    for label in ("Face (Secondary)", "Fingers (Detail)", "Arm.L (Tweak)"):
        rig.data.collections.new(label, parent=torso)
    original = {b.name for b in rig.data.bones}
    _result, settings = analyze(rig)
    settings.build_method = method
    return rig, settings, original


def check(rig, original, built_chains):
    assert tuple(rig.data.collections.keys()) == groups.BODY_NAMES
    assert members(rig, "Original") == original
    inventory = limb_ik._validate_inventory(rig)
    generated = {b.name for b in inventory["bones"]}
    assert members(rig, "Controls") == generated
    controls = {b.name for b in inventory["bones"]
                if b.get(limb_ik.ROLE_KEY) in limb_ik.CONTROL_VISUAL_ROLES or
                (b.get(limb_ik.ROLE_KEY) == "POLE_LINE" and not b.hide)}
    assert members(rig, "Animation") == (original - set(built_chains)) | controls
    assert rig.data.collections["Animation"].is_visible
    assert not rig.data.collections["Original"].is_visible
    assert not rig.data.collections["Controls"].is_visible
    if generated:
        limb_ik._removal_resources(bpy.context, rig, inventory)


def test_lifecycle(method):
    rig, settings, original = fixture(method)
    before_pose = {b.name: rig.pose.bones[b.name].matrix.copy() for b in rig.data.bones}
    assert bpy.ops.character_designer.simplify_bone_collections() == {"FINISHED"}
    for name, matrix in before_pose.items():
        assert rig.pose.bones[name].matrix == matrix
    check(rig, original, ())
    settings.selected_limb = "LEFT_ARM"
    assert bpy.ops.character_designer.limb_ik_build_selected() == {"FINISHED"}
    left_arm = ("upper_arm.L", "forearm.L", "hand.L")
    check(rig, original, left_arm)
    assert "hand.R" in members(rig, "Animation")
    assert "Hips" in members(rig, "Animation")
    assert bpy.ops.character_designer.limb_ik_build_all() == {"FINISHED"}
    chains = {name for r in limb_ik._validate_inventory(rig)["rigs"].values() for name in r["chain"]}
    check(rig, original, chains)
    assert bpy.ops.character_designer.limb_ik_rebuild() == {"FINISHED"}
    check(rig, original, chains)
    before = groups.snapshot_layout(rig)
    fail_original = limb_ik._build_plans
    attempts = 0

    def fail_build(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected collection lifecycle rebuild failure")
        return fail_original(*args, **kwargs)

    limb_ik._build_plans = fail_build
    try:
        assert cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) == {"CANCELLED"}
    finally:
        limb_ik._build_plans = fail_original
    assert groups.snapshot_layout(rig) == before
    check(rig, original, chains)
    with tempfile.TemporaryDirectory(prefix="cd-bone-groups-") as temp:
        name = rig.name
        path = str(Path(temp) / "groups.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        rig = bpy.data.objects[name]
        check(rig, original, chains)
    assert bpy.ops.character_designer.limb_ik_remove() == {"FINISHED"}
    check(rig, original, ())
    _result, settings = analyze(rig)
    settings.build_method = method
    assert bpy.ops.character_designer.limb_ik_build_all() == {"FINISHED"}
    check(rig, original, chains)


def test_layout_rollback():
    rig, _settings, _original = fixture("DIRECT_PREROLL")
    before = groups.snapshot_layout(rig)
    original_assign = groups._assign_exact

    def fail_assign(*_args):
        raise RuntimeError("injected grouping error")

    groups._assign_exact = fail_assign
    try:
        assert cancelled_result(bpy.ops.character_designer.simplify_bone_collections) == {"CANCELLED"}
    finally:
        groups._assign_exact = original_assign
    assert groups.snapshot_layout(rig) == before


def test_artist_name_collision():
    rig, _settings, _original = fixture("DIRECT_PREROLL")
    groups.simplify_body_collections(rig)
    rig.data.collections["Controls"].name = "Saved Controls"
    artist = rig.data.collections.new("Controls")
    artist.assign(rig.data.bones["Hips"])
    before = groups.snapshot_layout(rig)
    try:
        groups.simplify_body_collections(rig, compact=False)
    except ValueError:
        pass
    else:
        raise AssertionError("Automatic refresh adopted an artist Controls collection")
    assert groups.snapshot_layout(rig) == before


def main():
    ensure_registered()
    for cls in groups.BONE_COLLECTION_CLASSES:
        bpy.utils.register_class(cls)
    try:
        for method in ("DIRECT_PREROLL", "ROLL_DECOUPLED"):
            test_lifecycle(method)
            print(f"PASS compact collections {method}")
        test_layout_rollback()
        test_artist_name_collision()
    finally:
        reset_scene()
        for cls in reversed(groups.BONE_COLLECTION_CLASSES):
            bpy.utils.unregister_class(cls)
        ensure_unregistered()
    print("BONE_COLLECTIONS_PASSED")


if __name__ == "__main__":
    main()
