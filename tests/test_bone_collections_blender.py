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


def test_first_build_and_manual_visibility():
    rig, settings, original = fixture("DIRECT_PREROLL")
    settings.selected_limb = "LEFT_ARM"
    before = groups.snapshot_layout(rig)
    assert bpy.ops.character_designer.limb_ik_build_selected() == {"FINISHED"}
    check(rig, original, ("upper_arm.L", "forearm.L", "hand.L"))
    assert groups.has_layout_backup(rig)
    assert groups._load_backup(rig)["original"]["collections"] == before["collections"]
    # Revealing Original never changes the rig mode, and later generation respects it.
    rig.data.collections["Original"].is_visible = True
    rig.data.collections["Original"].is_solo = True
    rig.data.collections["Animation"].is_visible = False
    artist = rig.data.collections.new("Artist Picks")
    artist.assign(rig.data.bones["Hips"])
    assert bpy.ops.character_designer.limb_ik_build_all() == {"FINISHED"}
    assert rig.data.collections["Original"].is_visible
    assert rig.data.collections["Original"].is_solo
    assert not rig.data.collections["Animation"].is_visible
    assert members(rig, "Artist Picks") == {"Hips"}
    assert groups._load_backup(rig)["original"]["collections"] == before["collections"]
    assert bpy.ops.character_designer.limb_ik_remove() == {"FINISHED"}
    assert members(rig, "Animation") == original
    assert rig.data.collections["Original"].is_solo


def test_fk_visibility():
    rig, _settings, original = fixture("ROLL_DECOUPLED")
    assert bpy.ops.character_designer.limb_ik_build_all() == {"FINISHED"}
    inventory = limb_ik._validate_inventory(rig)
    fk = inventory["rigs"][("ARM", "L")]
    rig.pose.bones[fk["target"].name]["ik_fk"] = 0.0
    groups.finish_rig_edit(rig, groups.capture_managed_layout(rig))
    animator = members(rig, "Animation")
    assert set(fk["chain"]) <= animator
    assert fk["target"].name not in animator
    assert fk["pole"].name not in animator
    assert inventory["master"].name in animator
    for key, record in inventory["rigs"].items():
        if key != ("ARM", "L"):
            assert not set(record["chain"]) & animator
            assert record["target"].name in animator
    assert {"Hips"} <= animator
    rig.pose.bones[fk["target"].name]["ik_fk"] = 1.0
    groups.finish_rig_edit(rig, groups.capture_managed_layout(rig))
    all_chains = {name for record in inventory["rigs"].values() for name in record["chain"]}
    check(rig, original, all_chains)


def test_persistent_restore():
    rig, settings, _original = fixture("DIRECT_PREROLL")
    torso = rig.data.collections_all["Torso"]
    torso["artist_note"] = "Keep this hierarchy"
    torso["object_reference"] = rig
    torso["nested"] = {"numbers": [1, 2, 3], "reference": rig}
    torso.is_expanded = False
    torso.is_solo = True
    rig.data.collections.active = torso
    before = groups.snapshot_layout(rig)
    settings.selected_limb = "LEFT_ARM"
    assert bpy.ops.character_designer.limb_ik_build_selected() == {"FINISHED"}
    artist = rig.data.collections.new("Later Artist Collection")
    artist.assign(rig.data.bones["Hips"])
    # Later individual bone hiding is not reset by a collection-only restore.
    rig.data.bones["hand.R"].hide_select = True
    with tempfile.TemporaryDirectory(prefix="cd-collection-restore-") as temp:
        name = rig.name
        path = str(Path(temp) / "restore.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        rig = bpy.data.objects[name]
        # Compare remapped ID pointers to this reloaded object.
        for record in before["collections"]:
            if record["name"] == "Torso":
                record["properties"]["object_reference"] = rig
                record["properties"]["nested"]["reference"] = rig
        assert bpy.ops.character_designer.restore_bone_collections() == {"FINISHED"}
        restored = {record["name"]: record for record in groups.snapshot_layout(rig)["collections"]}
        for record in before["collections"]:
            assert restored[record["name"]] == record, record["name"]
        assert members(rig, "Later Artist Collection") == {"Hips"}
        assert rig.data.bones["hand.R"].hide_select
        controls = rig.data.collections_all[limb_ik.CONTROL_COLLECTION_NAME]
        assert controls.is_visible and controls.is_solo
        inventory = limb_ik._validate_inventory(rig)
        assert set(controls.bones.keys()) == {b.name for b in inventory["bones"]}
        assert not groups.has_layout_backup(rig)
        assert rig.data.get(groups.AUTO_KEY) == 0
        # Explicitly restored layouts are not automatically compacted again.
        _result, settings = analyze(rig)
        settings.build_method = "DIRECT_PREROLL"
        assert bpy.ops.character_designer.limb_ik_build_all() == {"FINISHED"}
        assert "Torso" in rig.data.collections_all
        assert "Animation" not in rig.data.collections_all


def test_restore_after_remove_and_conflicts():
    rig, settings, _original = fixture("DIRECT_PREROLL")
    before = groups.snapshot_layout(rig)
    settings.selected_limb = "LEFT_ARM"
    assert bpy.ops.character_designer.limb_ik_build_selected() == {"FINISHED"}
    animation = rig.data.collections_all["Animation"]
    animation.assign(rig.data.bones["upper_arm.L"])
    edited = groups.snapshot_layout(rig)
    assert cancelled_result(bpy.ops.character_designer.restore_bone_collections) == {"CANCELLED"}
    assert groups.snapshot_layout(rig) == edited
    animation.unassign(rig.data.bones["upper_arm.L"])
    collision = rig.data.collections.new("Torso")
    edited = groups.snapshot_layout(rig)
    assert cancelled_result(bpy.ops.character_designer.restore_bone_collections) == {"CANCELLED"}
    assert groups.snapshot_layout(rig) == edited
    rig.data.collections.remove(collision)
    assert bpy.ops.character_designer.limb_ik_remove() == {"FINISHED"}
    assert bpy.ops.character_designer.restore_bone_collections() == {"FINISHED"}
    assert groups.snapshot_layout(rig)["collections"] == before["collections"]


def test_failed_first_build():
    rig, settings, _original = fixture("DIRECT_PREROLL")
    settings.selected_limb = "LEFT_ARM"
    before = groups.snapshot_layout(rig)
    original = limb_ik._build_plans

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected first-build failure")

    limb_ik._build_plans = fail
    try:
        assert cancelled_result(bpy.ops.character_designer.limb_ik_build_selected) == {"CANCELLED"}
    finally:
        limb_ik._build_plans = original
    assert groups.snapshot_layout(rig) == before
    assert not groups.has_layout_backup(rig)


def test_animated_visibility():
    rig, settings, _original = fixture("DIRECT_PREROLL")
    settings.selected_limb = "LEFT_ARM"
    assert bpy.ops.character_designer.limb_ik_build_selected() == {"FINISHED"}
    record = limb_ik._validate_inventory(rig)["rigs"][("ARM", "L")]
    target = rig.pose.bones[record["target"].name]
    target["ik_fk"] = 1.0
    target.keyframe_insert(data_path='["ik_fk"]', frame=1)
    target["ik_fk"] = 0.0
    target.keyframe_insert(data_path='["ik_fk"]', frame=10)
    for curve in limb_ik._fcurves_for_action(rig.animation_data.action):
        for point in curve.keyframe_points:
            point.interpolation = "CONSTANT"
    rig.data.collections_all["Original"].is_visible = True
    rig.data.collections_all["Original"].is_solo = True
    rig.data.collections_all["Animation"].is_visible = False
    groups.register_handlers()
    groups.register_handlers()
    assert bpy.app.handlers.frame_change_post.count(groups._frame_visibility) == 1
    try:
        bpy.context.scene.frame_set(1)
        assert target.name in members(rig, "Animation")
        assert not set(record["chain"]) & members(rig, "Animation")
        bpy.context.scene.frame_set(10)
        assert target.name not in members(rig, "Animation")
        assert set(record["chain"]) <= members(rig, "Animation")
        assert rig.data.collections_all["Original"].is_solo
        assert not rig.data.collections_all["Animation"].is_visible
        # Manually repurposed memberships take precedence over playback updates.
        animation = rig.data.collections_all["Animation"]
        animation.assign(rig.data.bones[target.name])
        artist_members = members(rig, "Animation")
        bpy.context.scene.frame_set(1)
        assert members(rig, "Animation") == artist_members
        animation.unassign(rig.data.bones[target.name])
        bpy.context.scene.frame_set(2)
        assert target.name in members(rig, "Animation")
        with tempfile.TemporaryDirectory(prefix="cd-collection-playback-") as temp:
            name = rig.name
            path = str(Path(temp) / "animated.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            rig = bpy.data.objects[name]
            assert groups._frame_visibility in bpy.app.handlers.frame_change_post
            bpy.context.scene.frame_set(10)
            assert set(record["chain"]) <= members(rig, "Animation")
            assert bpy.ops.character_designer.restore_bone_collections() == {"FINISHED"}
            restored = groups.snapshot_layout(rig)["collections"]
            bpy.context.scene.frame_set(1)
            assert groups.snapshot_layout(rig)["collections"] == restored
    finally:
        groups.unregister_handlers()
    assert groups._frame_visibility not in bpy.app.handlers.frame_change_post


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
        test_first_build_and_manual_visibility()
        test_fk_visibility()
        test_persistent_restore()
        test_restore_after_remove_and_conflicts()
        test_failed_first_build()
        test_animated_visibility()
    finally:
        reset_scene()
        for cls in reversed(groups.BONE_COLLECTION_CLASSES):
            bpy.utils.unregister_class(cls)
        ensure_unregistered()
    print("BONE_COLLECTIONS_PASSED")


if __name__ == "__main__":
    main()
