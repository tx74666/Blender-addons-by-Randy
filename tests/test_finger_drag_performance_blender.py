"""Preview proof equivalence, cache reuse, and complete mutation invalidation."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import bpy
import bmesh

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_preview_eye_blender import fixture, expect, C, ui, work, layout, character_designer
from character_designer import finger_preview_proof as proof, finger_preview_cache as cache
from character_designer import finger_definition_ui as guides, finger_definition as definition


def test_warm_drag_reuses_full_check_and_chain_resolution():
    obj, rig = fixture()
    ui.show(C)
    pair = work.settings(C)[2]
    def forbidden(*args, **kwargs): raise AssertionError('Warm drag repeated static validation')
    with patch.object(work, 'check_source', forbidden), patch.object(ui.targets, 'pair', forbidden):
        for t in (.41, .48, .53, .65, .70, .99, .01, .45):
            pair.joints[0].position = t
            ui._refresh(); expect(True)
    # Hidden/current real edits never obtain permission from the view proof.
    with patch.object(work, 'check_source', forbidden):
        try: work.apply(C)
        except AssertionError: pass
        else: raise AssertionError('Apply bypassed the uncached write validator')
    ui.hide(); pair.joints[0].position = .33
    assert ui._refresh_request is None


def test_packed_signature_detects_every_legacy_data_category():
    obj, rig = fixture()
    ui.hide()
    bpy.ops.object.mode_set(mode='OBJECT')
    mesh = obj.data
    attr = mesh.attributes.new('proof_float', 'FLOAT', 'POINT')
    ints = mesh.attributes.new('proof_int', 'INT', 'FACE')
    colors = mesh.color_attributes.new(name='proof_color', type='FLOAT_COLOR', domain='CORNER')
    uv = mesh.uv_layers.new(name='proof_uv')
    material = bpy.data.materials.new('Proof Material'); mesh.materials.append(material)
    mesh.normals_split_custom_set([(0, 0, 1)]*len(mesh.loops))
    # Resolve collection members afresh: adding layers can invalidate old RNA views.
    changes = [
        (lambda: tuple(mesh.vertices[0].co), lambda v: setattr(mesh.vertices[0], 'co', v), (.02, .03, .04)),
        (lambda: mesh.polygons[0].use_smooth, lambda v: setattr(mesh.polygons[0], 'use_smooth', v), True),
        (lambda: mesh.edges[0].use_seam, lambda v: setattr(mesh.edges[0], 'use_seam', v), True),
        (lambda: mesh.uv_layers['proof_uv'].data[0].uv[:], lambda v: setattr(mesh.uv_layers['proof_uv'].data[0], 'uv', v), (.2, .3)),
        (lambda: mesh.attributes['proof_float'].data[0].value, lambda v: setattr(mesh.attributes['proof_float'].data[0], 'value', v), .125),
        (lambda: mesh.attributes['proof_int'].data[0].value, lambda v: setattr(mesh.attributes['proof_int'].data[0], 'value', v), 17),
        (lambda: mesh.color_attributes['proof_color'].data[0].color[:], lambda v: setattr(mesh.color_attributes['proof_color'].data[0], 'color', v), (.1, .2, .3, .4)),
        (lambda: mesh.shape_keys.key_blocks[-1].data[0].co[:], lambda v: setattr(mesh.shape_keys.key_blocks[-1].data[0], 'co', v), (.4, .5, .6)),
        (lambda: mesh.shape_keys.key_blocks[-1].value, lambda v: setattr(mesh.shape_keys.key_blocks[-1], 'value', v), .432),
        (lambda: obj.vertex_groups[0].lock_weight, lambda v: setattr(obj.vertex_groups[0], 'lock_weight', v), True),
        (lambda: material.name, lambda v: setattr(material, 'name', v), 'Proof Renamed'),
        (lambda: obj.location[:], lambda v: setattr(obj, 'location', v), (1, 2, 3)),
    ]
    for get, set_value, value in changes:
        old = get()
        if old == value: continue
        before, legacy = proof.signature(obj), layout.fingerprint(obj)
        set_value(value); C.view_layer.update()
        assert proof.signature(obj) != before, value
        assert layout.fingerprint(obj) != legacy, value
        set_value(old); C.view_layer.update()
        # Blender may re-encode custom normals after coordinate restoration;
        # a conservative cache miss is allowed, stale-data equality is not.
    before = proof.signature(obj)
    obj.vertex_groups['ArtistWeight'].add([0], .123, 'REPLACE')
    assert proof.signature(obj) != before
    before = proof.signature(obj)
    mesh.normals_split_custom_set([(0, 1, 0)]*len(mesh.loops))
    assert proof.signature(obj) != before


def test_unchanged_proof_restores_only_current_reference_frames():
    obj, _ = fixture()
    ui.show(C)
    guides.show(invalidate=False)
    guides.display_frames(C); guides.cached_frame(C)
    pair = work.settings(C)[2]
    pair.joints[0].position = .41
    cache.clear()  # Model Blender's parameter-triggered geometry notification.
    with patch.object(definition, 'frame', side_effect=AssertionError('Reference recomputed')):
        ui._refresh()
        assert guides.cached_frame(C)[0] is not None
    # Saved record changes cannot be restored from the old cache.
    pair.joints[0].position = .42
    active = obj.character_designer_finger_bank.slots['INDEX.L']
    active.error = 'Test changed identity'
    cache.clear(); ui._refresh()
    assert cache.lookup(active.guide, active.error) is None


def test_packed_normals_uv_and_booleans_remain_exact():
    obj, _ = fixture()
    ui.hide(); bpy.ops.object.mode_set(mode='OBJECT')
    mesh = obj.data
    mesh.attributes.new('proof_short2', 'INT16_2D', 'CORNER')
    mesh.attributes.new('proof_bool', 'BOOLEAN', 'POINT')
    mesh.uv_layers.new(name='proof_bulk_uv')
    mesh.normals_split_custom_set([(0, 0, 1)]*len(mesh.loops))
    # Packed integer vectors must not fall back to per-corner Python text or
    # lose individual bits through a float conversion. This includes normals.
    with patch.object(proof, '_value', wraps=proof._value) as read:
        proof.signature(obj)
        assert read.call_count <= len(mesh.attributes)
    for value in ((-32768, 32767), (-32767, 32767), (32767, -32768)):
        before = proof.signature(obj)
        mesh.attributes['proof_short2'].data[0].value = value
        assert proof.signature(obj) != before
        assert proof.signature(obj) == proof.signature(obj)
    for target, field in ((mesh.vertices[0], 'hide'), (mesh.edges[0], 'hide'),
                          (mesh.polygons[0], 'hide'), (mesh.edges[0], 'use_seam'),
                          (mesh.edges[0], 'use_edge_sharp'), (mesh.polygons[0], 'use_smooth')):
        before = proof.signature(obj)
        setattr(target, field, not getattr(target, field))
        assert proof.signature(obj) != before, field
    before = proof.signature(obj)
    mesh.attributes['proof_bool'].data[0].value = True
    assert proof.signature(obj) != before
    before = proof.signature(obj)
    mesh.uv_layers['proof_bulk_uv'].uv[0].vector = (.12345, .67891)
    assert proof.signature(obj) != before
    bpy.ops.object.mode_set(mode='EDIT')
    before = proof.signature(obj)
    bm = bmesh.from_edit_mesh(mesh); bm.faces.ensure_lookup_table()
    bm.faces[0].loops[0][bm.loops.layers.uv['proof_bulk_uv']].uv.x += .001
    bmesh.update_edit_mesh(mesh)
    assert proof.signature(obj) != before
    bpy.ops.object.mode_set(mode='OBJECT')


def test_cold_invalid_and_unsupported_proofs_use_full_validation():
    obj, _ = fixture()
    state = work.state(C)[1]
    saved = proof.verify(obj, state)
    with patch.object(work, 'check_source', wraps=work.check_source) as check:
        assert proof.verify(obj, state, saved) == saved
        assert check.call_count == 0
        with patch.object(proof, 'signature', side_effect=AttributeError('unsupported RNA')):
            assert proof.verify(obj, state, saved) is None
        assert check.call_count == 1
    state.signature = 'invalid'
    try: proof.verify(obj, state, saved)
    except ValueError: pass
    else: raise AssertionError('Changed persistent source signature reused an old proof')


def test_pending_drag_rejects_edit_mode_weights_and_shape_layers():
    for kind in ('weights', 'shape'):
        obj, _ = fixture()
        ui.show(C)
        pair = work.settings(C)[2]
        pair.joints[0].position = .41
        bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
        if kind == 'weights':
            bm.verts[0][bm.verts.layers.deform.active][obj.vertex_groups['ArtistWeight'].index] = .123
        else:
            shape = list(bm.verts.layers.shape.values())[-1]
            bm.verts[0][shape].x += .0123
        bmesh.update_edit_mesh(obj.data)
        ui._refresh(); expect(True)
        assert ui._preview.get('stale'), 'Changed data was presented as a valid current plan'
        assert 'changed since preparation' in obj.character_designer_finger_workflow.status


if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'): test(); print('PASS', name, flush=True)
    print('FINGER_DRAG_PERFORMANCE_PASS')
