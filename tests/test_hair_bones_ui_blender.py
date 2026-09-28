"""Registered UI workflow for reversible binding on the original hair mesh.

Disposable fixtures only; never loads or edits production character files.
Run: blender --background --factory-startup --disable-autoexec
     --python-exit-code 1 --python tests/test_hair_bones_ui_blender.py
"""

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import bmesh
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
import character_designer as cd
from character_designer import hair_bones as ui
from character_designer import hair_bones_binding as binding
from character_designer import hair_bones_groups as groups
from character_designer import hair_bones_rig as rig
from character_designer import hair_bones_variants as variants
from test_hair_bones_topology_blender import Fixture, bm_for
from test_hair_bones_rig_blender import activate, make_armature, reset
from test_hair_bones_variants_blender import source_state


def fixture(count=6):
    reset()
    armature = make_armature()
    builder = Fixture()
    layers = tuple(builder.tube(sides=5, rows=5 + index % 3,
                                origin=(index * 0.8, 0, 0), drift=0.015 * (index % 2))
                   for index in range(count))
    source = builder.object("UI Hair Source")
    source["artist_note"] = "Keep the original source and character rig"
    settings = ui._settings(bpy.context)
    settings.source = None
    settings.target_armature = None
    return source, layers, armature


def call(name, **kwargs):
    result = getattr(bpy.ops.character_designer, name)(**kwargs)
    assert result == {"FINISHED"}, (name, result, ui._settings(bpy.context).last_message)


class Layout:
    def __init__(self, calls=None, operators=None):
        self.calls = [] if calls is None else calls
        self.operators = [] if operators is None else operators
        self.enabled = True

    def row(self, **kwargs):
        return Layout(self.calls, self.operators)

    def label(self, **kwargs):
        self.calls.append(("label", kwargs.get("text"), self.enabled))

    def prop(self, owner, name, **kwargs):
        self.calls.append(("prop", name, self.enabled))

    def operator(self, name, **kwargs):
        self.calls.append(("operator", name, self.enabled))
        properties = SimpleNamespace()
        self.operators.append((name, kwargs, properties, self.enabled))
        return properties


def panel_layout():
    layout = Layout()
    ui.CHARACTERDESIGNER_PT_hair_bones.draw(SimpleNamespace(layout=layout), bpy.context)
    return layout


def panel_calls():
    return panel_layout().calls


def append_strand(source):
    """Make a real topology change that discovery can capture again."""
    builder = Fixture()
    builder.tube(sides=5, rows=6, origin=(8, 0, 0))
    bm = bm_for(source)
    vertices = [bm.verts.new(coordinate) for coordinate in builder.vertices]
    for face in builder.faces:
        bm.faces.new(tuple(vertices[index] for index in face))
    bmesh.update_edit_mesh(source.data, loop_triangles=True, destructive=True)


def assert_cancelled(name, message, **kwargs):
    try:
        result = getattr(bpy.ops.character_designer, name)(**kwargs)
    except RuntimeError as exc:
        # Blender promotes an operator's ERROR report to RuntimeError.
        assert message in str(exc), str(exc)
    else:
        assert result == {"CANCELLED"}, result
        assert message in ui._settings(bpy.context).last_message


def test_registration_contract():
    cd.register()
    cd._validate_registration_integrity()
    cd.register()
    cd._validate_registration_integrity()
    state = bpy.context.window_manager.character_designer_hair_bones
    assert state.bone_count == 4
    for name in ("mode", "active_group", "active_variant"):
        assert name not in state.bl_rna.properties
    assert "target_armature" in state.bl_rna.properties
    for name in ("select_hair_strands", "hair_bind_to_character", "hair_remove_binding",
                 "hair_cleanup_generated_copies", "hair_show_source", "hair_clear_groups",
                 "generate_hair_bones"):
        assert getattr(bpy.ops.character_designer, name).get_rna_type()
    for name in ("hair_group_selected", "hair_split_selected", "hair_select_group", "hair_edit_guide",
                 "hair_build_version", "hair_show_version"):
        try:
            getattr(bpy.ops.character_designer, name).get_rna_type()
        except (KeyError, RuntimeError):
            pass
        else:
            raise AssertionError("Retired operator is still registered: " + name)
    print("PASS test_registration_contract")


def test_bind_remove_preserves_original_mesh_and_character_rig():
    source, layers, armature = fixture()
    settings = ui._settings(bpy.context)
    settings.bone_count = 3
    # Blender can retain obsolete custom enum fields across add-on reloads.
    settings["mode"] = 1
    settings["active_group"] = 12345
    settings["active_variant"] = 9999
    call("select_hair_strands")
    assert settings.source is source and groups.captured_strand_count(source) == 6
    before = source_state(source)
    mesh_data = source.data
    object_set = set(bpy.data.objects)
    armature_set = set(bpy.data.armatures)
    original_bones = tuple((bone.name, rig._bone_state(bone)) for bone in armature.data.bones)
    target, head = binding.resolve_target(bpy.context, source)
    assert target is armature and head == "spine.006"
    before_ui = panel_calls()
    assert ("operator", "character_designer.hair_bind_to_character", True) in before_ui
    assert not any(item[1] == "character_designer.hair_remove_binding" for item in before_ui)
    assert not any(item[1] in {"active_variant", "mode", "character_designer.hair_build_version"} for item in before_ui)
    with patch.object(binding, "remove_generated_copies") as cleanup:
        call("hair_bind_to_character")
        cleanup.assert_not_called()
    assert set(bpy.data.objects) == object_set and set(bpy.data.armatures) == armature_set
    assert source.data is mesh_data and source.get(rig.RIG_KEY) is armature
    assert binding.is_bound(source) and not variants.variants_for(source)
    record = rig._read_records(source)
    assert len(record["chains"]) == 6
    assert sum(len(chain["bones"]) for chain in record["chains"]) == 18
    assert all(not chain.get("members") for chain in record["chains"])
    for chain in record["chains"]:
        assert armature.data.bones[chain["bones"][0]].parent.name == head
    for name, state in original_bones:
        assert rig._bone_state(armature.data.bones[name]) == state
    after = source_state(source)
    for key in ("geometry", "keys", "key_action", "action"):
        assert after[key] == before[key], key
    assert source["artist_note"] == "Keep the original source and character rig"
    assert bpy.context.active_object is armature and bpy.context.mode == "POSE"

    # Resolve from the character's selected owned bone even after WM state reset.
    armature.data.bones.active = armature.data.bones[record["chains"][0]["bones"][0]]
    settings.source = None
    assert ui._source(bpy.context) is source
    assert bpy.ops.character_designer.hair_remove_binding.poll()
    after_ui = panel_calls()
    assert ("operator", "character_designer.hair_remove_binding", True) in after_ui
    assert ("operator", "character_designer.hair_bind_to_character", False) in after_ui
    call("hair_remove_binding")
    assert settings.source is source and not binding.is_bound(source)
    assert source.data is mesh_data and source_state(source) == before
    assert set(bpy.data.objects) == object_set and set(bpy.data.armatures) == armature_set
    assert tuple((bone.name, rig._bone_state(bone)) for bone in armature.data.bones) == original_bones

    # The familiar source-edit and recapture path remains usable after removal.
    call("hair_show_source")
    assert bpy.context.active_object is source and bpy.context.mode == "EDIT_MESH"
    bm = bm_for(source)
    for index in layers[0][2]:
        bm.verts[index].co.x += 0.04
    bmesh.update_edit_mesh(source.data, loop_triangles=False, destructive=False)
    call("hair_clear_groups")
    assert groups.captured_strand_count(source) == 0
    assert not any(vertex.select for vertex in bm_for(source).verts)
    call("select_hair_strands")
    assert groups.captured_strand_count(source) == 6
    assert set(bpy.data.objects) == object_set
    print("PASS test_bind_remove_preserves_original_mesh_and_character_rig")


def test_compact_panel_keeps_binding_controls_before_capture():
    fixture(count=2)
    layout = panel_layout()
    selections = [item for item in layout.operators
                  if item[0] == "character_designer.select_hair_strands"]
    assert len(selections) == 1
    _, options, properties, _ = selections[0]
    assert options["text"] == "Select Hair Strands"
    assert properties.use_selected is False
    assert ("prop", "bone_count", True) in layout.calls
    assert ("operator", "character_designer.hair_bind_to_character", False) in layout.calls
    assert not any(item[1] in {"target_armature", "show_attachment_override",
                              "character_designer.hair_clear_groups"} for item in layout.calls)
    labels = [item[1] for item in layout.calls if item[0] == "label"]
    assert not any(text.startswith(("Main Rig:", "Attached Rig:", "Head:")) for text in labels)
    assert not any(text in {"One independent chain per strand.",
                           "Head controls the cap and strand roots.",
                           "All: find every visible strand.",
                           "Selected Tips: limit the search."} for text in labels)
    print("PASS compact panel preserves visible binding controls before capture")


def test_stale_capture_refresh_restores_binding_controls():
    source, _, _ = fixture(count=2)
    call("select_hair_strands")
    saved = source[groups.GROUPS_KEY]
    append_strand(source)
    layout = panel_layout()
    assert ("prop", "bone_count", True) in layout.calls
    assert ("operator", "character_designer.hair_bind_to_character", False) in layout.calls
    refresh = [item for item in layout.operators
               if item[0] == "character_designer.select_hair_strands"]
    assert len(refresh) == 1
    _, options, properties, _ = refresh[0]
    assert options["text"] == "Refresh Hair Strands"
    assert properties.use_selected is False and properties.replace_capture is True
    assert source[groups.GROUPS_KEY] == saved, "Drawing the panel must not clear stale captures"
    call("select_hair_strands", use_selected=False, replace_capture=True)
    assert groups.captured_strand_count(source) == 3
    assert source[groups.GROUPS_KEY] != saved
    assert ("operator", "character_designer.hair_bind_to_character", True) in panel_calls()
    call("hair_bind_to_character")
    assert len(rig._read_records(source)["chains"]) == 3
    call("hair_remove_binding")
    print("PASS stale capture keeps Bind visible and explicit refresh enables real binding")


def test_failed_refresh_preserves_previous_capture():
    source, _, _ = fixture(count=2)
    call("select_hair_strands")
    saved = source[groups.GROUPS_KEY]
    append_strand(source)
    with patch.object(ui, "select_strands", side_effect=ValueError("Discovery failed")):
        assert_cancelled("select_hair_strands", "Discovery failed", replace_capture=True)
    assert source[groups.GROUPS_KEY] == saved
    _, plans = ui.select_strands(bpy.context, respect_selection=False)
    invalid = dict(plans[-1], vertices=())
    # Validate at least one good strand first, then reject an invalid record.
    with patch.object(ui, "select_strands", return_value=(source, (plans[0], invalid))):
        assert_cancelled("select_hair_strands", "incomplete", replace_capture=True)
    assert source[groups.GROUPS_KEY] == saved
    assert ("operator", "character_designer.hair_bind_to_character", False) in panel_calls()
    print("PASS discovery and validation failures preserve the previous capture")


def test_legacy_generate_redirects_to_same_character_binding():
    source, _, armature = fixture(count=1)
    settings = ui._settings(bpy.context)
    settings.bone_count = 3
    settings.target_armature = armature
    assert ui._armature_poll(settings, armature)
    assert not ui._armature_poll(settings, source)
    call("select_hair_strands")
    objects_before = set(bpy.data.objects)
    armatures_before = set(bpy.data.armatures)
    call("generate_hair_bones")
    record = rig._read_records(source)
    assert len(record["chains"]) == 1 and len(record["chains"][0]["bones"]) == 3
    assert binding.is_bound(source) and source.get(rig.RIG_KEY) is armature
    assert not variants.variants_for(source)
    assert set(bpy.data.objects) == objects_before and set(bpy.data.armatures) == armatures_before
    assert bpy.context.mode == "POSE" and bpy.context.active_object is armature
    call("hair_remove_binding")
    assert not binding.is_bound(source)
    print("PASS test_legacy_generate_redirects_to_same_character_binding")


def test_legacy_copy_source_resolution_and_explicit_cleanup_wiring():
    source, _, armature = fixture(count=2)
    call("select_hair_strands")
    activate(source)
    # An old collection need not be rebound or modified to remain discoverable.
    collection = bpy.data.collections.new("Legacy UI Hair Version")
    bpy.context.scene.collection.children.link(collection)
    collection[variants.VERSION_KEY] = 1
    collection[variants.SOURCE_KEY] = source
    collection[variants.VISIBILITY_KEY] = json.dumps(variants._visibility(source))
    clone = bpy.data.objects.new("Legacy Hair Copy", source.data.copy())
    collection.objects.link(clone)
    clone[variants.VERSION_KEY] = 1
    clone[variants.SOURCE_KEY] = source
    activate(clone, "EDIT")
    ui._settings(bpy.context).source = None
    assert ui._source(bpy.context) is source
    assert not bpy.ops.character_designer.select_hair_strands.poll()
    calls = panel_calls()
    assert ("operator", "character_designer.hair_cleanup_generated_copies", True) in calls
    assert not any(item[1] in {"active_variant", "character_designer.hair_build_version",
                               "character_designer.hair_show_version"} for item in calls)
    assert bpy.ops.character_designer.hair_cleanup_generated_copies.poll()
    objects_before = set(bpy.data.objects)
    with patch.object(binding, "remove_generated_copies", return_value={"removed_meshes": 1, "removed_rigs": 1}) as cleanup:
        assert ui._source(bpy.context) is source
        cleanup.assert_not_called()
        call("hair_cleanup_generated_copies")
        cleanup.assert_called_once_with(bpy.context, source)
    assert set(bpy.data.objects) == objects_before
    assert ui._settings(bpy.context).source is source
    call("hair_show_source")
    assert bpy.context.active_object is source and bpy.context.mode == "EDIT_MESH"
    assert collection.hide_viewport and collection.hide_render
    print("PASS test_legacy_copy_source_resolution_and_explicit_cleanup_wiring")


if __name__ == "__main__":
    try:
        test_registration_contract()
        test_compact_panel_keeps_binding_controls_before_capture()
        test_stale_capture_refresh_restores_binding_controls()
        test_failed_refresh_preserves_previous_capture()
        test_bind_remove_preserves_original_mesh_and_character_rig()
        test_legacy_generate_redirects_to_same_character_binding()
        test_legacy_copy_source_resolution_and_explicit_cleanup_wiring()
        cd._validate_registration_integrity()
        cd.unregister()
        assert not hasattr(bpy.types.WindowManager, "character_designer_hair_bones")
        cd.register()
        cd._validate_registration_integrity()
        print("HAIR_BONES_UI_TESTS_OK")
    finally:
        cd.unregister()
