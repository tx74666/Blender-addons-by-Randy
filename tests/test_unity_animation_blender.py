"""Unity evaluated-motion preview; run in disposable factory-startup Blender."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile

import bpy
from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
from character_designer import unity_animation as ua


def reset():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    for data in list(bpy.data.armatures):
        if data.users == 0:
            bpy.data.armatures.remove(data)
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = 24, 1.001
    scene.frame_start, scene.frame_end = 11, 173
    scene.use_preview_range = True
    scene.frame_preview_start, scene.frame_preview_end = 13, 161
    scene.tool_settings.use_keyframe_insert_auto = True
    scene.frame_set(77, subframe=.375)
    scene.unit_settings.scale_length = 1.0


def make_rig():
    data = bpy.data.armatures.new("Native")
    rig = bpy.data.objects.new("CoshaRig", data)
    bpy.context.scene.collection.objects.link(rig)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    specs = [
        ("Hips", None, (0, 0, 1)), ("spine", "Hips", (0, 0, 1.25)),
        ("Head", "spine", (0, -.025, 1.7)),
        ("forearm.L", "spine", (.3, .06, 1.4)),
        ("hand.L", "forearm.L", (.6, .08, 1.2)),
        ("forearm.R", "spine", (-.3, .06, 1.4)),
        ("hand.R", "forearm.R", (-.6, .08, 1.2)),
        ("foot.L", "Hips", (.12, -.2, .1)),
    ]
    for i, (name, parent, head) in enumerate(specs):
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = head, Vector(head) + Vector((.01, .025, .12))
        bone.roll = .11 * i
        bone.parent = data.edit_bones.get(parent) if parent else None
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.scale = (.7823157,) * 3
    rig.location = (1.2, -.4, .17)
    rig.rotation_euler = (.03, -.06, .4)
    rig.pose.bones["forearm.L"].rotation_mode = "YXZ"
    rig.pose.bones["hand.R"].rotation_mode = "AXIS_ANGLE"
    bpy.context.view_layer.update()
    return rig


def flat(matrix):
    return [float(v) for row in matrix for v in row]


def package(rig, folder, name="clip.json", *, stretch_bones=()):
    c = Matrix.Translation((2.0, -1.3, .7)) @ Quaternion((0, 0, 1), .17).to_matrix().to_4x4()
    c = c @ Matrix.Diagonal((-1, 1, 1, 1))
    inv = c.inverted()
    bones = list(rig.data.bones)
    index = {b.name: i for i, b in enumerate(bones)}
    corrections = {b.name: Quaternion((0, 1, 0), .15 + .1 * i).to_matrix().to_4x4()
                   for i, b in enumerate(bones)}
    rests = {b.name: rig.matrix_world @ b.matrix_local for b in bones}
    result = {"schema": ua.SCHEMA, "targetName": "Cosha", "clipName": "Actual Eval",
              "units": "metres", "coordinate": "unity-lh-y-up", "matrixLayout": "row-major",
              "sampleRate": 60, "duration": 1.0, "bones": [], "frames": []}
    for b in bones:
        result["bones"].append({"name": b.name, "path": b.name,
                                "parent": index[b.parent.name] if b.parent else -1,
                                "restSource": "bindpose",
                                "rest": flat(inv @ rests[b.name] @ corrections[b.name])})
    expected = []
    for time in (0.0, .125, .5, 1.0):
        posed = {}
        for b in bones:
            angle = (.7 if b.name.endswith(".L") else -.45) * time
            rotation = Quaternion((0, 1, 0), angle)
            move = (Vector((.3 * time, -.12 * time, .05 * time)) if b.name == "Hips"
                    else Vector((.002 * time, .01 * time, -.003 * time)) if b.name in stretch_bones
                    else Vector((0, 0, 0)))
            basis = Matrix.LocRotScale(move, rotation, Vector((1, 1, 1)))
            kwargs = {"parent_matrix": posed[b.parent.name], "parent_matrix_local": b.parent.matrix_local} if b.parent else {}
            posed[b.name] = b.convert_local_to_pose(basis, b.matrix_local, **kwargs)
        worlds = {name: rig.matrix_world @ mat for name, mat in posed.items()}
        expected.append(worlds)
        result["frames"].append({"time": time, "root": flat(Matrix.Translation((.3 * time, 0, 0))),
                                 "poses": [{"matrix": flat(inv @ worlds[b.name] @ corrections[b.name])} for b in bones]})
    path = Path(folder) / name
    path.write_text(json.dumps(result), encoding="utf-8")
    return path, expected, result


def max_matrix_error(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4))


def rest_hash(rig):
    return hashlib.sha256(json.dumps([(b.name, b.parent.name if b.parent else None, flat(b.matrix_local), b.use_deform)
                                     for b in rig.data.bones]).encode()).hexdigest()


def test_native_mapping_time_and_actual_skin(folder):
    reset()
    rig = make_rig()
    path, expected, data = package(rig, folder)
    mesh = bpy.data.meshes.new("Skin")
    points = [(.62, .09, 1.2), (.58, .085, 1.21), (.60, .10, 1.18)]
    mesh.from_pydata(points, [], [(0, 1, 2)])
    obj = bpy.data.objects.new("Skin", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.matrix_world = rig.matrix_world.copy()
    for bone in ("forearm.L", "hand.L"):
        obj.vertex_groups.new(name=bone).add([0, 1, 2], .5, "REPLACE")
    armature = obj.modifiers.new("Skin", "ARMATURE")
    armature.object = rig
    obj.shape_key_add(name="Basis")
    artist = obj.shape_key_add(name="Original Expression")
    for vertex in artist.data:
        vertex.co.z += .015
    artist.value = .35
    artist_before = [tuple(v.co) for v in artist.data]
    original_rest, original_mesh = rest_hash(rig), [tuple(v.co) for v in mesh.vertices]
    result = ua.import_test_action(bpy.context, rig, path, start_frame=7)
    assert result.sample_count == 4 and result.mapping_error < 1e-6
    assert abs(result.last_frame - (7 + 24 / 1.001)) < 1e-5
    assert bpy.context.scene.tool_settings.use_keyframe_insert_auto
    assert bpy.context.scene.render.fps == 24 and abs(bpy.context.scene.render.fps_base - 1.001) < 1e-6
    for i, frame in enumerate(data["frames"]):
        ua._set_frame(bpy.context.scene, 7 + frame["time"] * 24 / 1.001)
        bpy.context.view_layer.update()
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        for name, desired in expected[i].items():
            actual = rig.matrix_world @ evaluated.pose.bones[name].matrix
            assert max_matrix_error(actual, desired) < 5e-5, (name, i, actual, desired)
        evaluated_skin = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        for vertex, original_point in zip(evaluated_skin.data.vertices, points):
            p = Vector(original_point) + Vector((0, 0, .015 * .35))
            expected_skin = Vector((0, 0, 0))
            for bone in ("forearm.L", "hand.L"):
                expected_skin += .5 * (expected[i][bone] @ (rig.matrix_world @ rig.data.bones[bone].matrix_local).inverted()
                                        @ rig.matrix_world @ Vector(p))
            assert (obj.matrix_world @ vertex.co - expected_skin).length < 5e-5
        captured_skin = [v.co.copy() for v in evaluated_skin.data.vertices]
        for _ in range(3):
            ua._set_frame(bpy.context.scene, bpy.context.scene.frame_current_final)
            bpy.context.view_layer.update()
        repeated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        assert max((v.co - co).length for v, co in zip(repeated.data.vertices, captured_skin)) < 1e-7
    ua.restore_preview(bpy.context, rig)
    assert rig.animation_data is None and ua.active_preview(rig) is None
    assert result.action.name in bpy.data.actions
    assert bpy.context.scene.frame_current_final == 77.375
    assert bpy.context.scene.frame_start == 11 and bpy.context.scene.frame_end == 173
    assert bpy.context.scene.tool_settings.use_keyframe_insert_auto
    assert rest_hash(rig) == original_rest and [tuple(v.co) for v in mesh.vertices] == original_mesh
    assert [tuple(v.co) for v in artist.data] == artist_before and abs(artist.value - .35) < 1e-7


def test_existing_action_nla_and_saved_restore(folder):
    reset()
    rig = make_rig()
    rig.pose.bones["Hips"].location = (.12, -.03, .01)
    rig.pose.bones["Hips"].keyframe_insert("location", frame=1)
    original = rig.animation_data.action
    slot = rig.animation_data.action_slot.handle
    nla_action = original.copy()
    nla_action.name = "Untouched NLA"
    track = rig.animation_data.nla_tracks.new()
    strip = track.strips.new("Existing strip", 1, nla_action)
    track.mute = False
    rig.animation_data.action_blend_type, rig.animation_data.action_influence = "ADD", .4
    ua._set_frame(bpy.context.scene, bpy.context.scene.frame_current_final)
    bpy.context.view_layer.update()
    snapshot = ua._snapshot(bpy.context, rig)
    path, _, _ = package(rig, folder, "nla.json")
    result = ua.import_test_action(bpy.context, rig, path)
    assert rig.animation_data.use_nla is False and not track.mute and not strip.mute
    assert original.users > 0
    saved = Path(folder) / "preview.blend"
    names = rig.name, original.name, result.action.name
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    bpy.ops.wm.open_mainfile(filepath=str(saved), use_scripts=False)
    rig, original = bpy.data.objects[names[0]], bpy.data.actions[names[1]]
    assert ua.active_preview(rig) is bpy.data.actions[names[2]]
    ua.restore_preview(bpy.context, rig)
    assert rig.animation_data.action is original and rig.animation_data.action_slot.handle == slot
    assert rig.animation_data.use_nla and rig.animation_data.action_blend_type == "ADD"
    assert abs(rig.animation_data.action_influence - .4) < 1e-6
    assert not rig.animation_data.nla_tracks[0].mute
    restored = ua._snapshot(bpy.context, rig)
    assert all(restored[k] == snapshot[k] for k in snapshot if k != "pose")
    for bone, fields in snapshot["pose"].items():
        for field, values in fields.items():
            assert restored["pose"][bone][field] == values, (bone, field, values, restored["pose"][bone][field])


def test_preflight_failure_and_atomic_rollback(folder):
    reset()
    rig = make_rig()
    path, _, data = package(rig, folder, "fail.json")
    old = ua._snapshot(bpy.context, rig)
    constraint = rig.pose.bones["hand.L"].constraints.new("COPY_ROTATION")
    try:
        ua.import_test_action(bpy.context, rig, path)
        raise AssertionError("Constraint was accepted")
    except ua.UnityAnimationError as exc:
        assert "Body Setup" in str(exc)
    assert rig.pose.bones["hand.L"].constraints[0].as_pointer() == constraint.as_pointer()
    rig.pose.bones["hand.L"].constraints.remove(constraint)
    before = set(bpy.data.actions)
    writer = ua._write_curve
    def fail(*args):
        raise RuntimeError("Injected action-write failure")
    ua._write_curve = fail
    try:
        try:
            ua.import_test_action(bpy.context, rig, path)
            raise AssertionError("Failure injection did not fire")
        except RuntimeError as exc:
            assert "Injected" in str(exc)
    finally:
        ua._write_curve = writer
    assert set(bpy.data.actions) == before and rig.animation_data is None
    assert ua._snapshot(bpy.context, rig) == old
    data["frames"][1]["time"] = 0
    path.write_text(json.dumps(data), encoding="utf-8")
    try:
        ua.import_test_action(bpy.context, rig, path)
        raise AssertionError("Duplicate time was accepted")
    except ua.UnityAnimationError as exc:
        assert "strictly" in str(exc)
    assert set(bpy.data.actions) == before


def test_action_race_and_unique_names(folder):
    reset()
    rig = make_rig()
    path, _, _ = package(rig, folder, "race.json")
    first = ua.import_test_action(bpy.context, rig, path)
    try:
        ua.import_test_action(bpy.context, rig, path)
        raise AssertionError("Nested preview was accepted")
    except ua.UnityAnimationError:
        pass
    user_action = bpy.data.actions.new("User selected this")
    rig.animation_data.action = user_action
    try:
        ua.restore_preview(bpy.context, rig)
        raise AssertionError("Restored over user Action")
    except ua.UnityAnimationError:
        pass
    assert rig.animation_data.action is user_action
    rig.animation_data.action = first.action
    rig.animation_data.action_slot = first.action.slots[0]
    ua.restore_preview(bpy.context, rig)
    second = ua.import_test_action(bpy.context, rig, path)
    assert first.action.name != second.action.name
    ua.restore_preview(bpy.context, rig)


def test_mismatch_and_corrupt_recovery_are_nonmutating(folder):
    reset()
    rig = make_rig()
    path, _, data = package(rig, folder, "mismatch.json")
    data["bones"][4]["rest"][3] += .02
    path.write_text(json.dumps(data), encoding="utf-8")
    snapshot, actions = ua._snapshot(bpy.context, rig), set(bpy.data.actions)
    try:
        ua.import_test_action(bpy.context, rig, path)
        raise AssertionError("A mismatched character was accepted")
    except ua.UnityAnimationError as exc:
        assert "differ" in str(exc) or "sizes" in str(exc)
    assert set(bpy.data.actions) == actions and ua._snapshot(bpy.context, rig) == snapshot
    path, _, _ = package(rig, folder, "recovery.json")
    result = ua.import_test_action(bpy.context, rig, path)
    intact = result.action[ua.SESSION_KEY]
    invalid = json.loads(intact)
    invalid["pose"]["Hips"]["scale"] = [1, 2]
    result.action[ua.SESSION_KEY] = json.dumps(invalid)
    preview_before = ua._snapshot(bpy.context, rig)
    try:
        ua.restore_preview(bpy.context, rig)
        raise AssertionError("Corrupt recovery data was accepted")
    except ua.UnityAnimationError:
        pass
    assert ua._snapshot(bpy.context, rig) == preview_before and ua.active_preview(rig) is result.action
    result.action[ua.SESSION_KEY] = intact
    ua.restore_preview(bpy.context, rig)


def test_connected_data_copy_restore_and_save(folder):
    reset()
    rig = make_rig()
    # Two negative scale channels still form a valid uniformly scaled rotation.
    rig.scale = (-.7823157, -.7823157, .7823157)
    bpy.ops.object.mode_set(mode="EDIT")
    hand = rig.data.edit_bones["hand.L"]
    hand.head = hand.parent.tail
    hand.tail = hand.head + Vector((.02, .03, .08))
    hand.use_connect = True
    bpy.ops.object.mode_set(mode="POSE")
    rig.pose.bones["hand.L"]["user_value"] = {"setting": [1.0, 2.0, 3.0]}
    rig.pose.bones["hand.L"]["user_target"] = rig
    rig.pose.bones["hand.L"].lock_rotation = (True, False, True)
    rig.data.bones["hand.L"]["user_bone_value"] = 7
    rig.pose.bones["Hips"].location = (.02, -.01, 0)
    rig.pose.bones["Hips"].keyframe_insert("location", frame=1)
    prior = rig.animation_data.action
    prior.name = "Connected Original Action"
    track = rig.animation_data.nla_tracks.new()
    track.strips.new("Connected Original NLA", 1, prior.copy())
    rig.animation_data.action_blend_type, rig.animation_data.action_influence = "ADD", .4
    ua._set_frame(bpy.context.scene, bpy.context.scene.frame_current_final)
    bpy.context.view_layer.update()
    original_data, original_name = rig.data, rig.data.name
    original_hash, snapshot = rest_hash(rig), ua._snapshot(bpy.context, rig)
    objects_before, armatures_before = set(bpy.data.objects), set(bpy.data.armatures)
    object_names_before = set(bpy.data.objects.keys())
    path, expected, data = package(rig, folder, "connected.json", stretch_bones={"hand.L"})
    result = ua.import_test_action(bpy.context, rig, path)
    assert rig.data is not original_data and original_data.bones["hand.L"].use_connect
    assert not rig.data.bones["hand.L"].use_connect and rest_hash(rig) == original_hash
    assert set(bpy.data.objects) == objects_before and len(bpy.data.armatures) == len(armatures_before) + 1
    assert rig.mode == "POSE" and rig.pose.bones["hand.L"].get("user_target") is rig
    assert rig.pose.bones["hand.L"]["user_value"]["setting"].to_list() == [1.0, 2.0, 3.0]
    assert tuple(rig.pose.bones["hand.L"].lock_rotation) == (True, False, True)
    assert rig.data.bones["hand.L"]["user_bone_value"] == 7
    for i, frame in enumerate(data["frames"]):
        ua._set_frame(bpy.context.scene, 1 + frame["time"] * snapshot["fps"] / snapshot["fps_base"])
        bpy.context.view_layer.update()
        actual = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        assert max_matrix_error(rig.matrix_world @ actual.pose.bones["hand.L"].matrix, expected[i]["hand.L"]) < 5e-5
    saved = Path(folder) / "connected_preview.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    bpy.ops.wm.open_mainfile(filepath=str(saved), use_scripts=False)
    rig = bpy.data.objects["CoshaRig"]
    original_data = bpy.data.armatures[original_name]
    assert ua.active_preview(rig)[ua.ORIGINAL_DATA_KEY] is original_data
    ua.restore_preview(bpy.context, rig)
    assert rig.data is original_data and rig.data.bones["hand.L"].use_connect
    assert len(bpy.data.armatures) == len(armatures_before), [(d.name, d.users) for d in bpy.data.armatures]
    assert set(bpy.data.objects.keys()) == object_names_before
    assert rest_hash(rig) == original_hash and ua._snapshot(bpy.context, rig) == snapshot
    assert rig.pose.bones["hand.L"].get("user_target") is rig
    assert tuple(rig.pose.bones["hand.L"].lock_rotation) == (True, False, True)
    before_actions, before_armatures = set(bpy.data.actions), set(bpy.data.armatures)
    setter, calls = ua._set_frame, [0]
    def fail_first_frame(*args):
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("Injected failure after data copy was attached")
        return setter(*args)
    ua._set_frame = fail_first_frame
    try:
        try:
            ua.import_test_action(bpy.context, rig, path)
            raise AssertionError("Data-copy failure injection did not fire")
        except RuntimeError as exc:
            assert "Injected failure" in str(exc)
    finally:
        ua._set_frame = setter
    assert rig.data is original_data and ua.active_preview(rig) is None
    assert set(bpy.data.actions) == before_actions and set(bpy.data.armatures) == before_armatures
    assert ua._snapshot(bpy.context, rig) == snapshot
    # A longer bone along the same axis, or a different parent, can retain the
    # same matrix_local. Recovery must not silently discard those user edits.
    ua.import_test_action(bpy.context, rig, path)
    preview_data = rig.data
    original_tail = preview_data.bones["hand.L"].tail_local.copy()
    original_matrix = preview_data.bones["hand.L"].matrix_local.copy()
    bpy.ops.object.mode_set(mode="EDIT")
    hand = preview_data.edit_bones["hand.L"]
    hand.tail += (hand.tail - hand.head) * .4
    bpy.ops.object.mode_set(mode="POSE")
    assert max_matrix_error(original_matrix, preview_data.bones["hand.L"].matrix_local) < 1e-6
    try:
        ua.restore_preview(bpy.context, rig)
        raise AssertionError("An edited preview bone length was discarded")
    except ua.UnityAnimationError as exc:
        assert "edited" in str(exc)
    assert rig.data is preview_data and ua.active_preview(rig)
    bpy.ops.object.mode_set(mode="EDIT")
    hand = preview_data.edit_bones["hand.L"]
    hand.tail, hand.parent = original_tail, preview_data.edit_bones["spine"]
    bpy.ops.object.mode_set(mode="POSE")
    try:
        ua.restore_preview(bpy.context, rig)
        raise AssertionError("An edited preview bone parent was discarded")
    except ua.UnityAnimationError as exc:
        assert "edited" in str(exc)
    bpy.ops.object.mode_set(mode="EDIT")
    preview_data.edit_bones["hand.L"].parent = preview_data.edit_bones["forearm.L"]
    bpy.ops.object.mode_set(mode="POSE")
    # Simulate a user editing the shared original through another object. The
    # original ID itself must not be rewritten or replaced during recovery.
    original_data.bones["hand.L"].use_deform = False
    try:
        ua.restore_preview(bpy.context, rig)
        raise AssertionError("An externally edited original was accepted")
    except ua.UnityAnimationError as exc:
        assert "original rest skeleton changed" in str(exc)
    assert not original_data.bones["hand.L"].use_deform and rig.data is preview_data
    original_data.bones["hand.L"].use_deform = True
    ua.restore_preview(bpy.context, rig)


def test_other_rig_name_escaped_paths_and_unrelated_animation(folder):
    reset()
    rig = make_rig()
    rig.name = "A Different Character"
    quoted = 'finger["test\\quoted"].L'
    rig.data.bones["hand.L"].name = quoted
    path, expected, data = package(rig, folder, "different-name.json")
    mesh = bpy.data.meshes.new("Unrelated mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    other = bpy.data.objects.new("Unrelated Animated Object", mesh)
    bpy.context.scene.collection.objects.link(other)
    other.shape_key_add(name="Basis")
    shape = other.shape_key_add(name="User Shape")
    shape.data[0].co.z = .4
    for frame, value in ((1, .1), (100, .8)):
        shape.value = value
        shape.keyframe_insert("value", frame=frame)
        other.location.x = value
        other.keyframe_insert("location", frame=frame)
    ua._set_frame(bpy.context.scene, 77.375)
    bpy.context.view_layer.update()
    key_action, object_action = mesh.shape_keys.animation_data.action, other.animation_data.action
    key_data = [tuple(v.co) for v in shape.data]
    shape_before, object_before = shape.value, other.matrix_world.copy()
    result = ua.import_test_action(bpy.context, rig, path)
    for index in (0, 3):
        ua._set_frame(bpy.context.scene, 1 + data['frames'][index]['time'] * 24 / 1.001)
        bpy.context.view_layer.update()
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        assert max_matrix_error(rig.matrix_world @ evaluated.pose.bones[quoted].matrix, expected[index][quoted]) < 3e-5
    assert shape.value != shape_before
    ua.restore_preview(bpy.context, rig)
    assert abs(shape.value - shape_before) < 1e-7 and max_matrix_error(other.matrix_world, object_before) < 1e-7
    assert mesh.shape_keys.animation_data.action is key_action and other.animation_data.action is object_action
    assert [tuple(v.co) for v in shape.data] == key_data and result.action.name in bpy.data.actions


def test_real_x_read_only(folder, path):
    path = Path(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    bpy.ops.wm.open_mainfile(filepath=str(path), use_scripts=False)
    rig = bpy.data.objects["CoshaRig"]
    fingerprint, snapshot = rest_hash(rig), ua._snapshot(bpy.context, rig)
    motion, expected, data = package(rig, folder, "actual-x.json")
    result = ua.import_test_action(bpy.context, rig, motion)
    for i, frame in enumerate(data["frames"]):
        ua._set_frame(bpy.context.scene, 1 + frame["time"] * snapshot["fps"] / snapshot["fps_base"])
        bpy.context.view_layer.update()
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        for name, desired in expected[i].items():
            assert max_matrix_error(rig.matrix_world @ evaluated.pose.bones[name].matrix, desired) < 7e-5, name
    ua.restore_preview(bpy.context, rig)
    assert rest_hash(rig) == fingerprint and ua._snapshot(bpy.context, rig) == snapshot
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    print("PASS actual current X", len(rig.data.bones), "bones, bind/sample/restore/file unchanged", result.mapping_error)


def main():
    with tempfile.TemporaryDirectory(prefix="cdesigner-unity-animation-") as folder:
        for test in (test_native_mapping_time_and_actual_skin, test_existing_action_nla_and_saved_restore,
                     test_preflight_failure_and_atomic_rollback, test_action_race_and_unique_names,
                     test_mismatch_and_corrupt_recovery_are_nonmutating,
                     test_connected_data_copy_restore_and_save,
                     test_other_rig_name_escaped_paths_and_unrelated_animation):
            test(folder)
            print("PASS", test.__name__)
        if "--real-blend" in sys.argv:
            test_real_x_read_only(folder, sys.argv[sys.argv.index("--real-blend") + 1])
    print("UNITY_ANIMATION_TESTS_PASSED")


if __name__ == "__main__":
    main()
