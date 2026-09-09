"""Compact bone groups: per-limb fallback, rig lifecycle and native persistence."""
import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
from character_designer import bone_collections as groups, foot_controls, limb_ik, torso_controls
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


def test_foot_controls_collections(method):
    rig, settings, _original = fixture(method)
    bpy.ops.object.mode_set(mode="EDIT")
    for side in ("L", "R"):
        parent = rig.data.edit_bones[f"foot.{side}"]
        toe = rig.data.edit_bones.new(f"toe.{side}")
        toe.head = parent.tail
        toe.tail = toe.head + Vector((0.0, -0.12, 0.0))
        toe.parent = parent
        toe.use_connect = True
        toe.use_deform = True
    bpy.ops.object.mode_set(mode="POSE")
    _result, settings = analyze(rig)
    settings.build_method = method
    original = {bone.name for bone in rig.data.bones}
    original_layout = groups.snapshot_layout(rig)["collections"]
    assert bpy.ops.character_designer.limb_ik_build_all() == {"FINISHED"}
    baseline = groups._load_backup(rig)["original"]["collections"]
    assert baseline == original_layout
    rig.data.collections["Original"].is_visible = True
    rig.data.collections["Original"].is_solo = True
    rig.data.collections["Animation"].is_visible = False
    artist = rig.data.collections.new("Artist Foot Notes")
    artist.assign(rig.data.bones["Hips"])
    for side in ("L", "R"):
        previous = groups.capture_managed_layout(rig)
        foot_controls.build(bpy.context, rig, ("LEG", side), toe_name=f"toe.{side}")
        groups.finish_rig_edit(rig, previous)
    inventory = limb_ik._validate_inventory(rig)
    feet = foot_controls.collection_members(rig)
    assert tuple(rig.data.collections.keys()) == groups.BODY_NAMES + ("Artist Foot Notes",)
    assert members(rig, "Controls") == {bone.name for bone in inventory["bones"]} | feet["generated"]
    assert members(rig, "Original") == original
    assert not feet["generated"] & members(rig, "Original")
    assert feet["always"] <= members(rig, "Animation")
    assert not feet["replaced"] & members(rig, "Animation")
    assert not feet["hidden_base"] & members(rig, "Animation")
    assert all(names <= members(rig, "Animation") for names in feet["ik"].values())
    assert all(not rig.data.bones[name].hide for name in feet["replaced"])
    assert rig.data.collections["Original"].is_solo
    assert not rig.data.collections["Animation"].is_visible
    assert groups._load_backup(rig)["original"]["collections"] == baseline

    # Mode playback replaces the leg chain, but Toe Bend remains available in FK.
    record = foot_controls.get_record(rig, ("LEG", "L"))
    target = rig.pose.bones[record["target"]]
    target["ik_fk"] = 1.0
    target.keyframe_insert(data_path='["ik_fk"]', frame=1)
    target["ik_fk"] = 0.0
    target.keyframe_insert(data_path='["ik_fk"]', frame=10)
    for curve in limb_ik._fcurves_for_action(rig.animation_data.action):
        for point in curve.keyframe_points:
            point.interpolation = "CONSTANT"
    groups.register_handlers()
    try:
        bpy.context.scene.frame_set(1)
        assert record["roll"] in members(rig, "Animation")
        bpy.context.scene.frame_set(10)
        assert record["roll"] not in members(rig, "Animation")
        assert record["toe_control"] in members(rig, "Animation")
        assert record["toe"] not in members(rig, "Animation")
        assert set(inventory["rigs"][("LEG", "L")]["chain"]) <= members(rig, "Animation")
        bpy.context.scene.frame_set(1)
    finally:
        groups.unregister_handlers()
    action = rig.animation_data.action
    rig.animation_data.action = None
    if action.users == 0:
        bpy.data.actions.remove(action)
    target["ik_fk"] = 1.0

    # Removing one extension restores only that native toe's animation access.
    previous = groups.capture_managed_layout(rig)
    foot_controls.remove(bpy.context, rig, ("LEG", "L"))
    groups.finish_rig_edit(rig, previous)
    feet = foot_controls.collection_members(rig)
    assert "toe.L" in members(rig, "Animation")
    assert "toe.R" not in members(rig, "Animation")
    assert feet["always"] <= members(rig, "Animation")
    assert groups._load_backup(rig)["original"]["collections"] == baseline
    assert rig.data.collections["Original"].is_solo

    with tempfile.TemporaryDirectory(prefix="cd-foot-collections-") as temp:
        name = rig.name
        path = str(Path(temp) / "feet.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        rig = bpy.data.objects[name]
        feet = foot_controls.collection_members(rig)
        assert bpy.ops.character_designer.restore_bone_collections() == {"FINISHED"}
        restored = {item["name"]: item for item in groups.snapshot_layout(rig)["collections"]}
        for item in original_layout:
            assert restored[item["name"]] == item
        controls = rig.data.collections_all[limb_ik.CONTROL_COLLECTION_NAME]
        assert feet["generated"] <= set(controls.bones.keys())
        assert controls.is_visible
        assert members(rig, "Artist Foot Notes") == {"Hips"}
        # Extension removal after an explicit layout restore keeps that artist layout.
        previous = groups.capture_managed_layout(rig)
        foot_controls.remove(bpy.context, rig, ("LEG", "R"))
        groups.finish_rig_edit(rig, previous)
        assert "Animation" not in rig.data.collections_all
        assert "Torso" in rig.data.collections_all
        assert not foot_controls.collection_members(rig)["generated"]


def test_torso_controls_collections(method):
    rig, settings, _original = fixture(method)
    bpy.ops.object.mode_set(mode="EDIT")
    hips = rig.data.edit_bones["Hips"]
    lower = rig.data.edit_bones.new("Spine")
    lower.head, lower.tail = hips.tail, (0.0, 0.0, 1.28)
    lower.parent, lower.use_connect = hips, True
    upper = rig.data.edit_bones.new("Spine1")
    upper.head, upper.tail = lower.tail, (0.0, 0.0, 1.40)
    upper.parent, upper.use_connect = lower, True
    chest = rig.data.edit_bones["Chest"]
    chest.head, chest.parent, chest.use_connect = upper.tail, upper, True
    foot = rig.data.edit_bones["foot.L"]
    toe = rig.data.edit_bones.new("toe.L")
    toe.head, toe.tail = foot.tail, foot.tail + Vector((0.0, -0.12, 0.0))
    toe.parent, toe.use_connect = foot, True
    bpy.ops.object.mode_set(mode="POSE")
    _result, settings = analyze(rig)
    settings.build_method = method
    original = {bone.name for bone in rig.data.bones}
    original_layout = groups.snapshot_layout(rig)["collections"]
    assert bpy.ops.character_designer.limb_ik_build_all() == {"FINISHED"}
    foot_controls.build(bpy.context, rig, ("LEG", "L"), toe_name="toe.L")
    baseline = groups._load_backup(rig)["original"]["collections"]
    assert baseline == original_layout
    rig.data.collections["Original"].is_visible = True
    rig.data.collections["Original"].is_solo = True
    rig.data.collections["Animation"].is_visible = False
    artist = rig.data.collections.new("Artist Torso Notes")
    artist.assign(rig.data.bones["Hips"])
    chain = ("Spine", "Spine1", "Chest")
    digest = limb_ik._armature_digest(rig)
    torso_controls.build(bpy.context, rig, chain=chain, hips_name="Hips")
    assert limb_ik._armature_digest(rig) == digest
    inventory = limb_ik._validate_inventory(rig)
    feet = foot_controls.collection_members(rig)
    torso = torso_controls.collection_members(rig)
    assert torso["replaced"] == set(chain)
    assert len(torso["always"]) == 4
    assert tuple(rig.data.collections.keys()) == groups.BODY_NAMES + ("Artist Torso Notes",)
    assert members(rig, "Controls") == {bone.name for bone in inventory["bones"]} | feet["generated"] | torso["generated"]
    assert members(rig, "Original") == original
    assert not torso["generated"] & members(rig, "Original")
    assert torso["always"] | feet["always"] <= members(rig, "Animation")
    assert not (torso["replaced"] | feet["replaced"]) & members(rig, "Animation")
    assert "Hips" in members(rig, "Animation")
    assert all(not rig.data.bones[name].hide for name in torso["replaced"])
    assert rig.data.collections["Original"].is_solo
    assert not rig.data.collections["Animation"].is_visible
    assert groups._load_backup(rig)["original"]["collections"] == baseline
    before_repeat = groups.snapshot_layout(rig)
    torso_controls.build(bpy.context, rig, chain=chain, hips_name="Hips")
    assert groups.snapshot_layout(rig) == before_repeat

    # A leg mode change must not re-expose spine sources or hide their controls.
    target = rig.pose.bones[inventory["rigs"][("LEG", "L")]["target"].name]
    target["ik_fk"] = 0.0
    groups._FRAME_CACHE.clear()
    groups._frame_visibility(bpy.context.scene)
    assert torso["always"] | feet["always"] <= members(rig, "Animation")
    assert not torso["replaced"] & members(rig, "Animation")
    target["ik_fk"] = 1.0
    groups._frame_visibility(bpy.context.scene)

    # Removal restores native spine access without affecting the foot extension.
    torso_controls.remove(bpy.context, rig)
    assert set(chain) <= members(rig, "Animation")
    assert not torso_controls.collection_members(rig)["generated"]
    assert feet["always"] <= members(rig, "Animation")
    assert "toe.L" not in members(rig, "Animation")
    assert rig.data.collections["Original"].is_solo
    assert groups._load_backup(rig)["original"]["collections"] == baseline
    torso_controls.build(bpy.context, rig, chain=chain, hips_name="Hips")
    with tempfile.TemporaryDirectory(prefix="cd-torso-collections-") as temp:
        name = rig.name
        path = str(Path(temp) / "torso.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        rig = bpy.data.objects[name]
        torso = torso_controls.collection_members(rig)
        assert bpy.ops.character_designer.restore_bone_collections() == {"FINISHED"}
        restored = {item["name"]: item for item in groups.snapshot_layout(rig)["collections"]}
        for item in original_layout:
            assert restored[item["name"]] == item
        controls = rig.data.collections_all[limb_ik.CONTROL_COLLECTION_NAME]
        assert torso["generated"] | feet["generated"] <= set(controls.bones.keys())
        assert controls.is_visible
        assert members(rig, "Artist Torso Notes") == {"Hips"}
        torso_controls.remove(bpy.context, rig)
        assert "Animation" not in rig.data.collections_all
        assert "Torso" in rig.data.collections_all
        assert feet["generated"] <= set(rig.data.collections_all[limb_ik.CONTROL_COLLECTION_NAME].bones.keys())


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
        for method in ("DIRECT_PREROLL", "ROLL_DECOUPLED"):
            test_foot_controls_collections(method)
            print(f"PASS foot collections {method}")
            test_torso_controls_collections(method)
            print(f"PASS torso collections {method}")
    finally:
        reset_scene()
        for cls in reversed(groups.BONE_COLLECTION_CLASSES):
            bpy.utils.unregister_class(cls)
        ensure_unregistered()
    print("BONE_COLLECTIONS_PASSED")


if __name__ == "__main__":
    main()
