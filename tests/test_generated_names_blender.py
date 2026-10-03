"""Readable generated names and safe, reference-preserving legacy migration."""
import json
import math
import os
import re
import sys
from types import SimpleNamespace

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "addons"), os.path.join(ROOT, "tests")]
from character_designer import generated_names as names
from character_designer import root_control as root, torso_controls as torso
from character_designer import spine_ik_fk as spine, foot_controls as foot, eye_controls as eyes
from character_designer import limb_ik, skirt_rig as skirt, widget_collections as manager
import test_root_control_blender as root_fixture


def remap(value, replacements):
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [remap(item, replacements) for item in value]
    if isinstance(value, dict):
        return {replacements.get(key, key): remap(item, replacements) for key, item in value.items()}
    return value


def remap_json_properties(owner, replacements):
    """Simulate a valid old setup, including other modules' recovery references."""
    for key, value in tuple(owner.items()):
        if not isinstance(value, str):
            continue
        try:
            payload = json.loads(value)
        except (TypeError, ValueError):
            continue
        changed = remap(payload, replacements)
        if changed != payload:
            owner[key] = json.dumps(changed)


def records(rig):
    result = [(manager, manager.get_record(rig)) for manager in (root, torso, spine, eyes)]
    result.extend((foot, record) for record in foot.records(rig).values())
    return result


def legacy_widgets(rig):
    replacements, resources = {}, []
    for manager, record in records(rig):
        label = manager.OWNER_VALUE
        identifier = record["id"][:10]
        collection = bpy.data.collections[record["widget_collection"]]
        previous = collection.name
        collection.name = f"CD_{label}_{identifier}"
        replacements[previous] = collection.name
        resources.append((collection, collection.as_pointer(), collection.name))
        for role, entry in record["widgets"].items():
            obj = bpy.data.objects[entry["object"]]
            data = obj.data
            previous_object, previous_data = obj.name, data.name
            legacy = f"WGT_CD_{label}_{role}_{identifier}"
            obj.name = data.name = legacy
            replacements[previous_object] = obj.name
            replacements[previous_data] = data.name
            resources.extend(((obj, obj.as_pointer(), obj.name), (data, data.as_pointer(), data.name)))
    remap_json_properties(rig.data, replacements)
    return resources


def weights(obj):
    return (tuple(group.name for group in obj.vertex_groups),
            tuple(tuple((group.group, group.weight) for group in vertex.groups) for vertex in obj.data.vertices))


def add_artist_shape(obj):
    obj.shape_key_add(name="Basis")
    key = obj.shape_key_add(name="Artist fold")
    key.data[0].co.z += .015
    key.value = .23
    key.keyframe_insert("value", frame=1)
    return obj.data.shape_keys


def shape_state(data):
    return (data.as_pointer(), data.animation_data.action.as_pointer(),
            tuple((key.name, key.value, tuple(tuple(point.co) for point in key.data))
                  for key in data.key_blocks))


def assert_preserved_resources(resources):
    for item, pointer, legacy in resources:
        assert item.as_pointer() == pointer, legacy
        assert item.name != legacy, legacy
        assert not re.search(r"_[0-9a-f]{6,32}(?:\.\d{3})?$", item.name), item.name


def validate_character(rig):
    for manager in (root, torso, spine, foot, eyes):
        manager.validate(rig)
    limb_ik._validate_inventory(rig)


def test_helpers_preserve_unicode_and_reserve_artist_groups():
    rig = SimpleNamespace(name="角色 / 主骨架" * 30)
    widget = names.widget_name(rig, "Torso_" + "LongControl" * 10 + ".R")
    assert widget.startswith("WGT_角色"), widget
    assert widget.endswith(".R"), widget
    assert len(widget.encode("utf-8")) <= 59, widget
    assert len(names.collection_name(rig, "Torso").encode("utf-8")) <= 59
    source = SimpleNamespace(name="衣服 / 正装" * 30, vertex_groups=[])
    prefix = names.skirt_prefix(source)
    assert prefix.startswith("SK_衣服_正装"), prefix
    assert len(prefix.encode("utf-8")) <= 40, prefix
    artist_group = SimpleNamespace(name="SK_Dress_Artist pin")
    source = SimpleNamespace(name="Dress", vertex_groups=[artist_group])
    assert names.skirt_prefix(source) == "SK_Dress_02"
    assert artist_group.name == "SK_Dress_Artist pin"


def test_widgets_migrate_with_collision_and_preserve_character():
    rig = root_fixture.fixture()
    record = root.build(bpy.context, rig)
    desired = names.widget_name(rig, "Root")
    assert record["widgets"]["MASTER"]["object"] == desired
    assert not any(record["id"][:10] in entry["object"] for entry in record["widgets"].values())
    mesh = bpy.data.objects["Spine Weight Fixture"]
    shapes = add_artist_shape(mesh)
    before_shapes, before_weights = shape_state(shapes), weights(mesh)
    before_pose = root_fixture.poses(rig)
    before_bones = {bone.name: torso._state(bone) for bone in rig.data.bones}
    before_ids = {(manager.OWNER_VALUE, record["id"]) for manager, record in records(rig)}
    custom_shapes = {pb.name: pb.custom_shape for pb in rig.pose.bones if pb.custom_shape}
    resources = legacy_widgets(rig)
    validate_character(rig)
    artist_data = bpy.data.meshes.new(desired)
    artist = bpy.data.objects.new(desired, artist_data)
    bpy.context.scene.collection.objects.link(artist)
    artist_pointer = artist.as_pointer()
    result = names.clean_generated_names(context=bpy.context)
    assert result["renamed"] and not result["skipped"], result
    assert artist.name == artist_data.name == desired and artist.as_pointer() == artist_pointer
    current = root.get_record(rig)
    root_obj = rig.pose.bones[current["master"]].custom_shape
    assert root_obj.name == desired + ".001", root_obj.name
    assert current["widgets"]["MASTER"] == {"object": root_obj.name, "mesh": root_obj.data.name}
    assert_preserved_resources(resources)
    assert {(manager.OWNER_VALUE, record["id"]) for manager, record in records(rig)} == before_ids
    assert {pb.name: pb.custom_shape for pb in rig.pose.bones if pb.custom_shape} == custom_shapes
    assert shape_state(shapes) == before_shapes and weights(mesh) == before_weights
    assert set(rig.data.bones.keys()) == set(before_bones)
    for name, state in before_bones.items():
        assert torso._same_rest(rig.data.bones[name], state), name
    root._verify_pose(rig, before_pose)
    validate_character(rig)
    second = names.clean_generated_names(context=bpy.context)
    assert not second["renamed"] and not second["skipped"], second
    # Root removal still resolves the migrated widget/collection recovery names.
    root.remove(bpy.context, rig)
    assert root.get_record(rig) is None
    assert bpy.data.objects.get(artist.name) is artist
    assert shape_state(shapes) == before_shapes and weights(mesh) == before_weights
    limb_ik._validate_inventory(rig)


def test_corrupt_widget_ownership_is_skipped():
    rig = root_fixture.fixture()
    record = root.build(bpy.context, rig)
    collection = bpy.data.collections[record["widget_collection"]]
    obj = bpy.data.objects[record["widgets"]["MASTER"]["object"]]
    replacements = {}
    for item, legacy in ((collection, "CD_Root_" + record["id"][:10]),
                         (obj, "WGT_CD_Root_" + record["id"][:10]),
                         (obj.data, "WGT_CD_Root_" + record["id"][:10])):
        previous = item.name
        item.name = legacy
        replacements[previous] = item.name
    remap_json_properties(rig.data, replacements)
    obj[root.ID_KEY] = "corrupt-owner"
    before = (collection.name, obj.name, obj.data.name, rig.data[root.RECORD_KEY])
    result = names.clean_generated_names(context=bpy.context)
    assert result["skipped"], result
    assert (collection.name, obj.name, obj.data.name, rig.data[root.RECORD_KEY]) == before


def test_legacy_foot_mesh_with_changed_owner_is_not_renamed():
    rig = root_fixture.fixture()
    root.build(bpy.context, rig)
    resources = legacy_widgets(rig)
    record = foot.get_record(rig, ("LEG", "L"))
    entry = next(iter(record["widgets"].values()))
    mesh = bpy.data.meshes[entry["mesh"]]
    mesh[foot.OWNER_KEY] = "artist-edited-owner"
    # The historical Foot validator checks the object ID and mesh name, so the
    # name cleanup must independently verify this mesh's complete ownership.
    foot.validate(rig)
    before_names = [(item.as_pointer(), item.name) for item, _pointer, _legacy in resources]
    before_records = {key: value for key, value in rig.data.items()
                      if key.startswith("character_designer") and isinstance(value, str)}
    result = names.clean_generated_names(context=bpy.context)
    assert not result["renamed"] and result["skipped"], result
    assert any(item["name"] == rig.name for item in result["skipped"]), result
    assert [(item.as_pointer(), item.name) for item, _pointer, _legacy in resources] == before_names
    assert {key: value for key, value in rig.data.items()
            if key.startswith("character_designer") and isinstance(value, str)} == before_records
    assert mesh[foot.OWNER_KEY] == "artist-edited-owner"


def test_new_root_generation_reserves_artist_object_mesh_and_collection():
    rig = root_fixture.fixture()
    desired_widget = names.widget_name(rig, "Root")
    desired_collection = rig.name + " · Root"
    artist_data = bpy.data.meshes.new(desired_widget)
    artist = bpy.data.objects.new(desired_widget, artist_data)
    artist_collection = bpy.data.collections.new(desired_collection)
    bpy.context.scene.collection.children.link(artist_collection)
    artist_collection.objects.link(artist)
    artist_pointers = (artist.as_pointer(), artist_data.as_pointer(), artist_collection.as_pointer())
    record = root.build(bpy.context, rig)
    entry = record["widgets"]["MASTER"]
    assert entry == {"object": desired_widget + ".001", "mesh": desired_widget + ".001"}, entry
    assert record["widget_collection"] == desired_collection + ".001", record["widget_collection"]
    validate_character(rig)
    root.remove(bpy.context, rig)
    assert root.get_record(rig) is None
    assert artist.name == artist_data.name == desired_widget
    assert artist_collection.name == desired_collection
    assert (artist.as_pointer(), artist_data.as_pointer(), artist_collection.as_pointer()) == artist_pointers
    assert tuple(artist.users_collection) == (artist_collection,)
    assert tuple(artist_collection.objects) == (artist,)
    assert bpy.context.scene.collection.children.get(desired_collection) is artist_collection
    limb_ik._validate_inventory(rig)


def test_post_rename_validation_failure_rolls_back_names_and_raw_records():
    rig = root_fixture.fixture()
    root.build(bpy.context, rig)
    resources = legacy_widgets(rig)
    validate_character(rig)
    before_records = {key: value for key, value in rig.data.items()
                      if key.startswith("character_designer") and isinstance(value, str)}
    domains = (bpy.data.objects, bpy.data.meshes, bpy.data.collections)
    before_names = tuple({item.as_pointer(): item.name for item in domain} for domain in domains)
    original, calls = manager._resources, []

    def fail_after_rename(armature):
        calls.append(armature.as_pointer())
        if len(calls) == 2:
            assert any(item.name != legacy for item, _pointer, legacy in resources)
            assert rig.data[root.RECORD_KEY] != before_records[root.RECORD_KEY]
            raise ValueError("Injected post-rename name validation failure")
        return original(armature)

    manager._resources = fail_after_rename
    try:
        result = names.clean_generated_names(context=bpy.context)
    finally:
        manager._resources = original
    assert len(calls) == 2, calls
    assert not result["renamed"] and len(result["skipped"]) == 1, result
    assert "Injected post-rename" in result["skipped"][0]["reason"], result
    assert tuple({item.as_pointer(): item.name for item in domain} for domain in domains) == before_names
    assert {key: value for key, value in rig.data.items()
            if key.startswith("character_designer") and isinstance(value, str)} == before_records
    for item, pointer, legacy in resources:
        assert item.as_pointer() == pointer and item.name == legacy
    validate_character(rig)


def skirt_fixture():
    root_fixture.limb_tests.base.ensure_registered()
    root_fixture.limb_tests.base.reset_scene()
    columns, levels = 32, 10
    vertices, faces = [], []
    for ring in range(levels):
        fraction = ring / (levels - 1)
        for column in range(columns):
            angle = math.tau * column / columns
            vertices.append(((.35 + .35 * fraction) * math.cos(angle),
                             (.28 + .25 * fraction) * math.sin(angle), 1.1 - .7 * fraction))
    for ring in range(levels - 1):
        for column in range(columns):
            first = ring * columns + column
            next_point = ring * columns + (column + 1) % columns
            faces.append((first, first + columns, next_point + columns, next_point))
    data = bpy.data.meshes.new("Artist dress mesh")
    data.from_pydata(vertices, [], faces)
    source = bpy.data.objects.new("Dress", data)
    bpy.context.scene.collection.objects.link(source)
    bpy.context.view_layer.objects.active = source
    source.select_set(True)
    source.vertex_groups.new(name="Artist pin").add([3, 25], .42, "REPLACE")
    add_artist_shape(source)
    return source


def test_skirt_migration_keeps_hooks_bones_weights_and_shapes():
    source = skirt_fixture()
    prefix = names.skirt_prefix(source)
    record = skirt.build_skirt(bpy.context, source)
    rig = source[skirt.RIG_KEY]
    assert rig.name == prefix + "_Rig", rig.name
    assert all(record["owner"][:6] not in name for name in record["owned_objects"])
    rig.pose.bones[record["controls"]["chains"][0]["hem"]].location.x = .04
    before_pose = root_fixture.poses(rig)
    before_bones = {bone.name: torso._state(bone) for bone in rig.data.bones}
    before_weights, before_shapes = weights(source), shape_state(source.data.shape_keys)
    custom_shapes = {pb.name: pb.custom_shape for pb in rig.pose.bones if pb.custom_shape}
    owned = [obj for obj in bpy.data.objects if obj is not source and obj.get(skirt.OWNER_KEY) == record["owner"]]
    hooks = [(modifier, modifier.object, modifier.subtarget, tuple(modifier.vertex_indices))
             for obj in owned for modifier in obj.modifiers if modifier.type == "HOOK"]
    replacements, resources = {}, []
    for obj in owned:
        for item in (obj, obj.data):
            old = item.name
            assert old.startswith(prefix + "_"), old
            item.name = prefix + "_" + record["owner"][:6] + old[len(prefix):]
            replacements[old] = item.name
            resources.append((item, item.as_pointer(), item.name))
    helpers = bpy.data.collections[record["owned_collections"][1]]
    previous = helpers.name
    helpers.name = "Skirt wire and shapes | " + record["owner"][:6]
    replacements[previous] = helpers.name
    resources.append((helpers, helpers.as_pointer(), helpers.name))
    remap_json_properties(source, replacements)
    result = names.clean_generated_names(context=bpy.context)
    assert result["renamed"] and not result["skipped"], result
    assert_preserved_resources(resources)
    current = skirt.read_record(source)
    assert current["owner"] == record["owner"] and current["rig"] == rig.name
    assert set(current["owned_objects"]) == {obj.name for obj in owned}
    assert all(name in bpy.data.objects for name in current["cage"])
    assert all(chain["curve"] in bpy.data.objects for chain in current["chains"])
    assert all(name in bpy.data.collections for name in current["owned_collections"])
    assert {pb.name: pb.custom_shape for pb in rig.pose.bones if pb.custom_shape} == custom_shapes
    for modifier, target, subtarget, indices in hooks:
        assert modifier.object is target and modifier.subtarget == subtarget
        assert tuple(modifier.vertex_indices) == indices
    assert weights(source) == before_weights and shape_state(source.data.shape_keys) == before_shapes
    assert set(rig.data.bones.keys()) == set(before_bones)
    for name, state in before_bones.items():
        assert torso._same_rest(rig.data.bones[name], state), name
    root._verify_pose(rig, before_pose)
    skirt._check_existing_geometry(source, current)
    second = names.clean_generated_names(context=bpy.context)
    assert not second["renamed"] and not second["skipped"], second
    skirt.remove_skirt(bpy.context, source)
    assert not skirt.read_record(source)
    assert len(bpy.data.objects) == 1
    assert abs(source.vertex_groups["Artist pin"].weight(3) - .42) < 1e-6
    assert shape_state(source.data.shape_keys) == before_shapes


if __name__ == "__main__":
    for test in (test_helpers_preserve_unicode_and_reserve_artist_groups,
                 test_widgets_migrate_with_collision_and_preserve_character,
                 test_corrupt_widget_ownership_is_skipped,
                 test_legacy_foot_mesh_with_changed_owner_is_not_renamed,
                 test_new_root_generation_reserves_artist_object_mesh_and_collection,
                 test_post_rename_validation_failure_rolls_back_names_and_raw_records,
                 test_skirt_migration_keeps_hooks_bones_weights_and_shapes):
        test()
        print("PASS", test.__name__, flush=True)
    print("GENERATED_NAMES_TESTS_PASS 7", flush=True)
