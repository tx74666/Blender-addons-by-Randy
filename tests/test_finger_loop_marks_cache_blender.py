"""Saved loop drawing and warm master-eye/side actions must not read geometry."""
import json
import sys
import time
import traceback
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import C, bound_fixture, character_designer, bank, definition, fingerprint
from character_designer import finger_loop_marks as marks, finger_loop_marks_ui as ui
from character_designer import finger_definition_ui as guides


def select_ring(obj, key, row):
    ids = set(json.loads(obj.character_designer_finger_bank.slots[key].guide.record)['body']['rings'][row])
    bm = bmesh.from_edit_mesh(obj.data)
    for seq in (bm.verts, bm.edges, bm.faces):
        for item in seq: item.select_set(False)
    C.tool_settings.mesh_select_mode = (False, True, False)
    for edge in bm.edges:
        if all(v.index in ids for v in edge.verts): edge.select_set(True)
    bmesh.update_edit_mesh(obj.data)


def fixture():
    ui._reset()
    obj, rig, _ = bound_fixture()
    for side in ('L', 'R'):
        bank.select(C, 'INDEX', side)
        for row in (2, 5):
            select_ring(obj, 'INDEX.'+side, row)
            assert bpy.ops.character_designer.finger_loop_marks(action='MARK') == {'FINISHED'}
    bank.select(C, 'INDEX', 'L')
    C.scene.character_designer_finger_definition.overlays_enabled = True
    C.view_layer.update()
    # Warm the retained Basic guides too; the master eye controls both tools.
    guides.show(invalidate=False)
    guides.display_frames(C)
    bpy.ops.character_designer.finger_setup(action='SIDE')
    guides.display_frames(C)
    bpy.ops.character_designer.finger_setup(action='SIDE')
    guides.display_frames(C)
    ui.refresh(C)
    return obj, rig


def test_eye_and_side_keep_same_batches_without_mesh_reads():
    obj, _ = fixture()
    left = ui.visible(C)[0][1]
    assert len(left['groups']) == 1
    cache = ui._cache[obj.as_pointer()]
    right = cache['entries']['INDEX.R']
    before = fingerprint(obj)
    def forbidden(*args, **kwargs): raise AssertionError('Warm action read geometry')
    with patch.object(definition, '_snapshot', side_effect=forbidden), \
         patch.object(definition, 'frame', side_effect=forbidden), \
         patch.object(bank, 'dirty', side_effect=forbidden), \
         patch.object(bmesh, 'from_edit_mesh', side_effect=forbidden):
        for _ in range(30):
            bpy.ops.character_designer.finger_definition(action='OVERLAYS')
            assert not ui.visible(C)
            bpy.ops.character_designer.finger_definition(action='OVERLAYS')
            assert ui.visible(C)[0][1] is left
            bpy.ops.character_designer.finger_setup(action='SIDE')
            assert ui.visible(C)[0][1] is right
            bpy.ops.character_designer.finger_setup(action='SIDE')
            assert ui.visible(C)[0][1] is left
    assert ui._cache[obj.as_pointer()] is cache
    assert fingerprint(obj) == before


def test_repeated_drawing_only_uploads_one_batch_per_finger():
    obj, _ = fixture()
    calls = dict(upload=0, draw=0)
    class Shader:
        def bind(self): pass
        def uniform_float(self, *_): pass
    class Batch:
        def draw(self, _): calls['draw'] += 1
    def batch(*args, **kwargs):
        calls['upload'] += 1
        return Batch()
    noop = lambda *_: None
    gpu = SimpleNamespace(
        shader=SimpleNamespace(from_builtin=lambda _: Shader()),
        matrix=SimpleNamespace(push_pop=nullcontext, multiply_matrix=noop),
        state=SimpleNamespace(depth_test_get=lambda: 'NONE', line_width_get=lambda: 1.,
            blend_get=lambda: 'NONE', depth_test_set=noop, line_width_set=noop, blend_set=noop))
    def forbidden(*args, **kwargs): raise AssertionError('Drawing did non-cache work')
    with patch.dict(sys.modules, {'gpu': gpu, 'gpu_extras.batch': SimpleNamespace(batch_for_shader=batch)}), \
         patch.object(definition, '_snapshot', side_effect=forbidden), \
         patch.object(bmesh, 'from_edit_mesh', side_effect=forbidden), \
         patch.object(marks, 'points', side_effect=forbidden), \
         patch.object(marks, 'records', side_effect=forbidden), \
         patch.object(marks, '_saved', side_effect=forbidden):
        ui._draw()
        started = time.perf_counter()
        for _ in range(2000): ui._draw()
        elapsed = time.perf_counter()-started
    assert calls == {'upload': 1, 'draw': 2001}, calls
    print(f'LOOP_CACHE_CPU_DISPATCH: {elapsed/2000*1000:.4f} ms/draw; 0 mesh reads; 1 total upload (GPU mocked)', flush=True)


def test_panel_redraws_reuse_metadata_and_explicit_refresh_parses_once():
    obj, _ = fixture()
    class Layout:
        def row(self, **_): return self
        def operator(self, *_, **__): return SimpleNamespace()
    layout = Layout()
    ui._reset()
    with patch.object(json, 'loads', wraps=json.loads) as parse:
        ui.refresh(C)
        assert parse.call_count == 1, parse.call_count
        ui.draw_controls(layout, layout, C)
        assert parse.call_count == 2, parse.call_count
    def forbidden(*args, **kwargs): raise AssertionError('Panel redraw did uncached work')
    with patch.object(json, 'loads', side_effect=forbidden), \
         patch.object(bmesh, 'from_edit_mesh', side_effect=forbidden):
        for _ in range(2000): ui.draw_controls(layout, layout, C)
    # Cache follows saved data, and never gives a writer mutable cached records.
    raw = obj[marks.PROPERTY]
    assert dict(ui._mark_flags(raw))['INDEX.L'] == {'1', '2'}
    marks.clear(C)
    assert 'INDEX.L' not in dict(ui._mark_flags(obj[marks.PROPERTY]))
    assert dict(ui._mark_flags(raw))['INDEX.L'] == {'1', '2'}


def test_single_side_marks_never_reflect_or_borrow_opposite_targets():
    obj, rig = fixture()
    rig.data.use_mirror_x = True
    bank.select(C, 'INDEX', 'R')
    marks.clear(C)
    ui.refresh(C)
    assert not ui.visible(C) and not marks.records(obj, 'INDEX.R')
    left = ui._cache[obj.as_pointer()]['entries']['INDEX.L']
    assert 'INDEX.R' not in ui._cache[obj.as_pointer()]['entries']
    try: marks.align(C)
    except ValueError: pass
    else: raise AssertionError('Unmarked current side borrowed opposite marks')
    assert not marks.records(obj, 'INDEX.R')
    rig.data.use_mirror_x = False
    assert not ui.visible(C)
    assert marks.effective_key(obj, 'INDEX.R', False) == 'INDEX.R'
    bank.select(C, 'INDEX', 'L')
    assert ui.visible(C)[0][1] is left


def test_restore_clear_and_lifecycle_do_not_change_artist_data():
    obj, _ = fixture()
    before = fingerprint(obj)
    saved = obj[marks.PROPERTY]
    ui._reset()
    assert not ui.visible(C)
    with patch.object(definition, '_snapshot', side_effect=AssertionError('Reload read mesh')):
        ui._restore()
        assert len(ui.visible(C)[0][1]['groups']) == 1
        assert obj[marks.PROPERTY] == saved
    bank.clear(C)
    assert not marks.records(obj, 'INDEX.L') and marks.records(obj, 'INDEX.R')
    assert not ui.visible(C)
    assert fingerprint(obj) == before
    ui.unregister_runtime()
    assert not ui._cache and not ui._handles
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        assert ui._reset not in handlers
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        assert ui._restore not in handlers
    assert not any(getattr(f, '__module__', '') == ui.__name__ for f in bpy.app.handlers.depsgraph_update_post)
    ui.register_runtime()


def test_clear_preserves_opposite_marks_even_with_x_mirror():
    obj, rig = fixture()
    before = fingerprint(obj)
    rig.data.use_mirror_x = False
    assert bpy.ops.character_designer.finger_loop_marks(action='CLEAR') == {'FINISHED'}
    assert not marks.records(obj, 'INDEX.L') and marks.records(obj, 'INDEX.R')
    assert not ui.visible(C)
    rig.data.use_mirror_x = True
    assert not ui.visible(C)
    assert bpy.ops.character_designer.finger_loop_marks(action='CLEAR') == {'FINISHED'}
    assert not marks.records(obj, 'INDEX.L') and marks.records(obj, 'INDEX.R')
    assert not ui.visible(C) and fingerprint(obj) == before
    obj, rig = fixture()
    rig.data.use_mirror_x = True
    assert bpy.ops.character_designer.finger_loop_marks(action='CLEAR') == {'FINISHED'}
    assert not ui.visible(C)
    assert not marks.records(obj, 'INDEX.L') and marks.records(obj, 'INDEX.R')


def main():
    character_designer.register()
    tests = [fn for name, fn in globals().items() if name.startswith('test_')]
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('FINGER_LOOP_MARKS_CACHE_TESTS_PASSED', len(tests), flush=True)


if __name__ == '__main__':
    try: main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
