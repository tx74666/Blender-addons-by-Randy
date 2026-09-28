"""Whole-hair selection and bounded discovery work on disposable Blender meshes.

Run with --background --factory-startup --disable-autoexec --python-exit-code 1
--python tests/test_hair_strands_discovery_blender.py. No production files are
opened or saved. Performance assertions count geometric work, not wall time.
"""

import json
from pathlib import Path
import sys
from unittest.mock import patch

import bmesh
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
import character_designer as cd
from character_designer import hair_bones as ui
from character_designer import hair_bones_groups as groups
from character_designer import hair_bones_topology as topology
from test_hair_bones_binding_blender import test_in_place_bind_cap_mask_and_repeat_remove
from test_hair_bones_rig_blender import reset
from test_hair_bones_topology_blender import (
    Fixture, bm_for, flattened, geometry_snapshot, select, selection_snapshot,
    test_disjoint_seeds_hidden_and_coordinate_cache, validate_plans,
)
from test_hair_bones_ui_blender import call, fixture


def selected_vertices(source):
    return {vertex.index for vertex in bm_for(source).verts if vertex.select}


def captured_vertices(source):
    return {index for strand in groups._read(source)["strands"] for index in strand["vertices"]}


def test_main_button_ignores_existing_partial_selection():
    source, layers, _ = fixture(count=3)
    select(source, layers[0][-1], active=layers[0][-1][0])
    before = geometry_snapshot(source)
    assert not bpy.ops.character_designer.select_hair_strands.get_rna_type().properties["use_selected"].default

    call("select_hair_strands")

    expected = flattened(tuple(layer for strand in layers for layer in strand))
    assert groups.captured_strand_count(source) == 3
    assert selected_vertices(source) == expected
    assert captured_vertices(source) == expected
    assert geometry_snapshot(source) == before, "Whole-hair capture changed artist data"


def test_selected_tip_mode_then_main_button_widens_capture():
    source, layers, _ = fixture(count=3)
    select(source, layers[0][-1], active=layers[0][-1][0])
    before = geometry_snapshot(source)

    call("select_hair_strands", use_selected=True)
    assert groups.captured_strand_count(source) == 1
    assert selected_vertices(source) == flattened(layers[0])
    assert captured_vertices(source) == flattened(layers[0])
    original_group = groups.read_groups(source)[0]["id"]

    # The first invocation leaves a whole strand selected. A normal second
    # click must still find the other strands, without requiring deselection.
    call("select_hair_strands")
    expected = flattened(tuple(layer for strand in layers for layer in strand))
    assert groups.captured_strand_count(source) == 3
    assert selected_vertices(source) == expected
    assert captured_vertices(source) == expected
    assert original_group in {record["id"] for record in groups.read_groups(source)}
    assert geometry_snapshot(source) == before, "Expanding capture changed artist data"


def test_main_button_respects_hidden_geometry():
    source, layers, _ = fixture(count=3)
    hidden = flattened(layers[2])
    bm = bm_for(source)
    for index in hidden:
        bm.verts[index].hide_set(True)
    bmesh.update_edit_mesh(source.data, loop_triangles=False, destructive=False)
    select(source, layers[0][-1], active=layers[0][-1][0])
    before = geometry_snapshot(source)

    call("select_hair_strands")

    expected = flattened(layers[0] + layers[1])
    assert groups.captured_strand_count(source) == 2
    assert selected_vertices(source) == expected
    assert captured_vertices(source) == expected
    assert not selected_vertices(source) & hidden
    assert geometry_snapshot(source) == before, "Visible-only capture changed hidden or artist data"


def test_selected_tip_mode_requires_a_visible_seed_without_mutation():
    source, _, _ = fixture(count=3)
    select(source, ())
    before, selection = geometry_snapshot(source), selection_snapshot(source)
    try:
        result = bpy.ops.character_designer.select_hair_strands(use_selected=True)
    except RuntimeError as exc:
        assert "Select a tip" in str(exc), str(exc)
    else:
        assert result == {"CANCELLED"}, result
    assert "Select a tip" in ui._settings(bpy.context).last_message
    assert groups.GROUPS_KEY not in source
    assert geometry_snapshot(source) == before
    assert selection_snapshot(source) == selection


def test_discovery_api_preserves_explicit_selection_contract():
    source, layers, _ = fixture(count=3)
    select(source, layers[0][-1], active=layers[0][-1][0])
    before, selection = geometry_snapshot(source), selection_snapshot(source)

    _, local = topology.discover_strands(bpy.context)
    _, whole = topology.discover_strands(bpy.context, respect_selection=False)
    _, restricted = topology.discover_strands(bpy.context, selected_only=True,
                                               respect_selection=False)

    assert len(local) == 1 and set(local[0]["vertices"]) == flattened(layers[0])
    assert len(whole) == 3
    assert not restricted, "Selected-only discovery expanded past the selected tip"
    validate_plans(source, whole)
    assert geometry_snapshot(source) == before
    assert selection_snapshot(source) == selection, "Read-only discovery changed selection"


def test_many_flat_tip_cards_validate_partitions_once():
    reset()
    count = 20
    builder = Fixture()
    strands = []
    for index in range(count):
        start = len(builder.vertices)
        layers = builder.card(rows=24, width=3)
        strands.append(layers)
        for position in range(start, len(builder.vertices)):
            x, y, z = builder.vertices[position]
            builder.vertices[position] = (x + index * 1.5, y, z)
    source = builder.object("Many Flat Tip Hair Cards")
    before, selection = geometry_snapshot(source), selection_snapshot(source)
    with patch.object(cd, "_quad_band_from_rail_edge", wraps=cd._quad_band_from_rail_edge) as band, \
            patch.object(topology, "_strict_plan", wraps=topology._strict_plan) as strict:
        _, plans = topology.discover_strands(bpy.context, respect_selection=False)

    assert len(plans) == count
    assert {frozenset(plan["vertices"]) for plan in plans} == {flattened(layers) for layers in strands}
    validate_plans(source, plans)
    # Every card has one longitudinal and one competing transverse partition.
    # Allow some constant overhead, but do not revalidate each of its 23 bands.
    assert 0 < strict.call_count <= count * 4, strict.call_count
    assert 0 < band.call_count <= count * 4, band.call_count
    lookup = band.call_args_list[0].kwargs.get("edge_by_key")
    assert lookup is not None, "Discovery rebuilt the entire mesh edge index per band"
    assert all(call.kwargs.get("edge_by_key") is lookup for call in band.call_args_list)
    assert geometry_snapshot(source) == before
    assert selection_snapshot(source) == selection
    print("HAIR_DISCOVERY_WORK=" + json.dumps({"cards": count,
          "vertices": len(source.data.vertices), "strict_calls": strict.call_count,
          "quad_band_calls": band.call_count}))


def main():
    assert bpy.app.background
    cd.register()
    completed = []
    try:
        for test in (
            test_main_button_ignores_existing_partial_selection,
            test_selected_tip_mode_then_main_button_widens_capture,
            test_main_button_respects_hidden_geometry,
            test_selected_tip_mode_requires_a_visible_seed_without_mutation,
            test_discovery_api_preserves_explicit_selection_contract,
            test_many_flat_tip_cards_validate_partitions_once,
            test_disjoint_seeds_hidden_and_coordinate_cache,
            test_in_place_bind_cap_mask_and_repeat_remove,
        ):
            test()
            completed.append(test.__name__)
            print("HAIR_DISCOVERY_TEST=" + json.dumps({"test": test.__name__, "status": "passed"}))
        print("HAIR_DISCOVERY_RESULT=" + json.dumps({"status": "passed", "tests": completed}))
    finally:
        cd.unregister()


if __name__ == "__main__":
    main()
