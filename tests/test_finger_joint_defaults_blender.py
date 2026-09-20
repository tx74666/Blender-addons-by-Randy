"""Per-pair positional defaults; no geometry writes or preview eye changes."""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_workflow_blender import bound_fixture, fixture, capture_all, character_designer, C, bank, work, ui, layout, refused


def values(pair): return [j.position for j in pair.joints]


def close(a, b): assert len(a) == len(b) and all(abs(x-y) < 1e-6 for x, y in zip(a, b)), (a, b)


def setup():
    ui.hide()
    obj, rig, _ = bound_fixture()
    pair = work.prepare(C)
    return obj, rig, pair


def test_automatic_and_custom_restore_is_positions_only():
    obj, rig, pair = setup()
    baseline, count = layout.fingerprint(obj), len(bpy.data.objects)
    automatic = values(pair)
    assert json.loads(pair.automatic_defaults)['source'] == 'BONES'
    close(work.default_positions(pair), automatic)
    for j in pair.joints: j.position += .01
    saved = work.save_defaults(C)
    pair.joints[0].position += .01
    pair.joints[0].root_weight = .61
    pair.joints[0].width = .02
    pair.between = 2
    work.restore_defaults(C)
    close(values(pair), saved)
    work.restore_defaults(C, automatic=True)
    close(values(pair), automatic)
    close(work.default_positions(pair), saved)  # Automatic reset preserves preset.
    assert abs(pair.joints[0].root_weight-.61) < 1e-6
    assert abs(pair.joints[0].width-.02) < 1e-6 and pair.between == 2
    assert layout.fingerprint(obj) == baseline and len(bpy.data.objects) == count


def test_pair_isolation_side_sharing_and_reprepare():
    obj, rig, pair = setup()
    pair.joints[0].position += .01
    saved = work.save_defaults(C)
    profile = pair.custom_defaults
    pair.joints[0].position += .01
    current = values(pair)
    work.prepare(C)
    close(values(pair), current)
    assert pair.custom_defaults == profile
    bank.select(C, 'INDEX', 'R')
    assert work.settings(C)[2] == pair
    close(work.restore_defaults(C), saved)
    bank.select(C, 'MIDDLE', 'L')
    middle = work.prepare(C)
    assert middle != pair and not middle.custom_defaults
    original = values(middle)
    middle.joints[0].position += .02
    close(work.restore_defaults(C), original)
    assert pair.custom_defaults == profile
    bank.select(C, 'THUMB', 'L')
    thumb = work.prepare(C)
    assert len(thumb.joints) == 1 and not thumb.custom_defaults
    assert len(work.restore_defaults(C)) == 1


def test_failed_save_restore_preserves_settings_and_preset():
    obj, rig, pair = setup()
    work.save_defaults(C)
    saved = pair.custom_defaults
    pair.joints[1].position = pair.joints[0].position
    invalid = values(pair)
    refused(lambda: work.save_defaults(C))
    close(values(pair), invalid)
    assert pair.custom_defaults == saved
    work.restore_defaults(C)
    pair.joints[0].width = .15
    pair.joints[0].inner_spacing = 2
    pair.joints[1].width = .15
    before = values(pair)
    refused(lambda: work.restore_defaults(C))
    close(values(pair), before)
    assert pair.custom_defaults == saved
    pair.custom_defaults = '{broken'
    refused(lambda: work.restore_defaults(C))
    close(values(pair), before)
    pair.custom_defaults = saved
    pair.joints.add().position = .9
    refused(lambda: work.restore_defaults(C))
    assert len(pair.joints) == 3


def test_source_revision_and_existing_legacy_values():
    obj, rig, pair = setup()
    pair.automatic_defaults = ''  # File from .40; no initialization on draw/reset.
    before = values(pair)
    refused(lambda: work.restore_defaults(C))
    close(values(pair), before)
    work.prepare(C)
    close(values(pair), before)
    work.save_defaults(C)
    record = pair.custom_defaults
    slot = obj.character_designer_finger_bank.slots['INDEX.R']
    revision = slot.guide.revision
    slot.guide.revision = 'changed-test-revision'
    refused(lambda: work.restore_defaults(C))
    assert pair.custom_defaults == record
    slot.guide.revision = revision
    import bmesh
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[0].co.x += .001
    bmesh.update_edit_mesh(obj.data)
    refused(lambda: work.restore_defaults(C))
    refused(lambda: work.save_defaults(C))
    close(values(pair), before)
    assert pair.custom_defaults == record


def test_unbound_and_save_reopen_defaults():
    ui.hide()
    obj, chosen = fixture(); capture_all(obj, chosen); bank.select(C, 'INDEX', 'L')
    pair = work.prepare(C)
    close(values(pair), [.34, .68])
    assert json.loads(pair.automatic_defaults)['source'] == 'TOPOLOGY'
    pair.joints[0].position = .4
    saved = work.save_defaults(C)
    record = pair.custom_defaults
    path = str(Path(tempfile.mkdtemp(prefix='cd-joint-defaults-')) / 'defaults.blend')
    name = obj.name
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=False, use_scripts=False)
    obj = bpy.data.objects[name]
    pair = obj.character_designer_finger_workflow.pairs['INDEX']
    assert pair.custom_defaults == record
    close(work.default_positions(pair), saved)
    work.release(C)
    assert pair.custom_defaults == record
    work.prepare(C)
    pair.joints[0].position = .45
    close(work.restore_defaults(C), saved)


def test_operators_eye_and_cached_active_title():
    obj, rig, pair = setup()
    ui.show(C)
    baseline = layout.fingerprint(obj)
    assert bpy.ops.character_designer.finger_workflow(action='SAVE_DEFAULTS') == {'FINISHED'}
    saved = values(pair)
    pair.joints[0].position += .01
    assert bpy.ops.character_designer.finger_workflow(action='RESET_DEFAULTS') == {'FINISHED'}
    ui._refresh()
    close(values(pair), saved)
    assert ui.joint_preview_enabled(C) and ui.joint_preview_visible(C)
    ui.hide()
    pair.joints[0].position += .01
    assert bpy.ops.character_designer.finger_workflow(action='RESET_DEFAULTS') == {'FINISHED'}
    assert not ui.joint_preview_enabled(C) and not ui.joint_preview_visible(C)
    # Multi-selection is for viewing; title names the active editing target only.
    bank.select(C, 'MIDDLE', 'L', mode='TOGGLE')
    labels, buttons = [], []
    class Panel:
        def row(self, **kw): return self
        def box(self, **kw): return self
        def label(self, **kw): labels.append(kw.get('text', ''))
        def prop(self, *args, **kw): pass
        def operator(self, *args, **kw):
            op = SimpleNamespace(); buttons.append((op, kw)); return op
    old = layout.fingerprint
    try:
        layout.fingerprint = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('Panel scanned geometry'))
        ui.draw_controls(Panel(), C)
        assert 'Joint Topology & Weights (Middle)' in labels
    finally: layout.fingerprint = old
    assert layout.fingerprint(obj) == baseline


if __name__ == '__main__':
    character_designer.register()
    for test in [v for k, v in list(globals().items()) if k.startswith('test_')]:
        test(); print('PASS', test.__name__, flush=True)
    print('FINGER_JOINT_DEFAULTS_PASS', flush=True)
