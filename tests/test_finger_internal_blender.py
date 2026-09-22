"""Straight interior axes, source-range isolation and one-click reference UI."""
import json
import math
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy
import bmesh
from mathutils import Vector, Matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import hands_fixture as fixture, select, refused, C, bank, definition, fingerprint, character_designer
from character_designer import finger_internal as internal, finger_definition_ui as ui


def capture():
    obj, chosen = fixture()
    select(obj, chosen['INDEX.L'])
    assert bpy.ops.character_designer.finger_setup(action='CAPTURE') == {'FINISHED'}
    return obj, chosen, obj.character_designer_finger_bank


def check_axis(obj, record):
    bm = definition._snapshot(obj, record['basis_key'])
    try:
        volume = internal.Volume(bm, record['body'])
        a, b = map(Vector, record['internal']['path'])
        assert len(record['internal']['path']) == 2
        assert volume.certify((a, b), record['internal']['margin']) is not None
        for i in range(101):
            p = a.lerp(b, i/100)
            assert volume.inside(p)
            assert volume.distance(p) >= record['internal']['margin']
        assert record['internal']['coverage'] >= .85
    finally: bm.free()


def test_one_capture_internal_pair_and_no_objects():
    obj, chosen = fixture()
    before, objects = fingerprint(obj), set(bpy.data.objects.keys())
    select(obj, chosen['INDEX.L'])
    bank.capture(C, 'START')  # Old pending reference must disappear on capture.
    for _ in range(3):
        assert bpy.ops.character_designer.finger_setup(action='CAPTURE') == {'FINISHED'}
        b = obj.character_designer_finger_bank
        assert len(b.slots) == 10 and not definition.state(C).pending
        assert definition.state(C).confirmed
        assert ui._pending() is None
        left, right = (json.loads(b.slots['INDEX.'+s].guide.record) for s in ('L', 'R'))
        check_axis(obj, left)
        check_axis(obj, right)
        assert left['surface']['origin']['input']['ids'] == chosen['INDEX.L']
        assert left['surface']['path'] != left['internal']['path']
        for a, p in zip(left['internal']['path'], right['internal']['path']):
            assert (Vector((-a[0], a[1], a[2]))-Vector(p)).length < 1e-6
    assert set(bpy.data.objects.keys()) == objects
    assert fingerprint(obj) == before


def test_explicit_range_is_not_retracted():
    obj, chosen = fixture()
    select(obj, chosen['INDEX.L'][1:5])
    bank.capture(C)
    record = json.loads(definition.state(C).record)
    raw = definition.frame(C, purpose='TOPOLOGY')
    axis = definition.frame(C)
    assert axis['length'] < raw['length']
    assert record['surface']['range_mode'] == 'SELECTED'
    check_axis(obj, record)


def test_active_shape_key_is_not_a_configuration():
    obj, chosen = fixture()
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.active_shape_key_index = 1
    obj.data.shape_keys.key_blocks[1].value = .3
    bpy.ops.object.mode_set(mode='EDIT')
    select(obj, chosen['INDEX.L'])
    before = fingerprint(obj)
    bank.capture(C)
    record = json.loads(definition.state(C).record)
    assert record['key'] == record['basis_key'] == 'Basis'
    assert definition.state(C).use_basis and definition.state(C).confirmed
    assert obj.active_shape_key_index == 1 and fingerprint(obj) == before
    check_axis(obj, record)
    # A deformation that would leave the displayed rest line outside is refused,
    # not silently shown as an internal guide and not stored as another record.
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for i in record['body']['vertices']: bm.verts[i].co.z += 1
    saved = definition.state(C).record
    refused(lambda: bank.capture(C))
    assert definition.state(C).record == saved
    refused(lambda: definition.frame(C))


def test_transform_and_open_edge_capture():
    obj, chosen, b = capture()
    original = json.loads(definition.state(C).record)['internal']['path']
    obj.matrix_world = Matrix.Translation((9, -4, 7)) @ Matrix.Rotation(.9, 4, 'Y')
    bank.capture(C)
    assert json.loads(definition.state(C).record)['internal']['path'] == original
    record = json.loads(definition.state(C).record)
    select(obj, [])
    C.tool_settings.mesh_select_mode = (False, True, False)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    rows = record['body']['rings']
    for a, z in zip(rows, rows[1:]): bm.edges.get((bm.verts[a[0]], bm.verts[z[0]])).select_set(True)
    bank.capture(C)
    record = json.loads(definition.state(C).record)
    assert record['input']['kind'] == 'EDGES'
    assert definition.frame(C)['direction'].dot(obj.matrix_world.to_3x3() @ (Vector(record['body']['tip'])-Vector(record['body']['root']))) > 0
    check_axis(obj, record)


def test_whole_segment_not_just_endpoints_and_failure_atomicity():
    obj, chosen, b = capture()
    saved = {s.name: s.guide.record for s in b.slots}
    select(obj, chosen['INDEX.L']+chosen['MIDDLE.L'])
    assert bpy.ops.character_designer.finger_setup(action='CAPTURE') == {'CANCELLED'}
    assert b.status.startswith('Update failed') and all(s.guide.record == saved[s.name] for s in b.slots)
    assert definition.frame(C)['internal']  # Valid old preview remains, labelled old.
    select(obj, chosen['INDEX.L'])
    record = json.loads(definition.state(C).record)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    rows = record['body']['rings']
    for j, row in enumerate(rows):
        for i in row: bm.verts[i].co.z += .24*math.sin(j*math.pi/(len(rows)-1))
    bm.normal_update()
    volume = internal.Volume(bm, record['body'])
    a, z = map(Vector, record['internal']['path'])
    assert volume.inside(a) and volume.inside(z)
    assert volume.certify((a, z), record['internal']['margin']) is None
    refused(lambda: internal.solve(bm, record['body'], record['basis']['path']))
    assert bpy.ops.character_designer.finger_setup(action='CAPTURE') == {'CANCELLED'}
    assert all(s.guide.record == saved[s.name] for s in b.slots)


def test_missing_surface_and_concave_body_are_not_assumed_convex():
    obj, chosen, b = capture()
    record = json.loads(definition.state(C).record)
    bm = definition._snapshot(obj)
    try:
        # A dent displaces one rail inward beyond the old centerline. Exact
        # volume tests must reject that old line, not trust averaged endpoints.
        row = record['body']['rings'][3]
        center = sum((bm.verts[i].co for i in row), Vector())/len(row)
        vertex = bm.verts[row[0]]
        vertex.co = center-(vertex.co-center)*.7
        volume = internal.Volume(bm, record['body'])
        assert volume.certify(record['internal']['path'], record['internal']['margin']) is None
        altered = dict(record['body'], faces=record['body']['faces'][1:])
        refused(lambda: internal.Volume(bm, altered))
    finally: bm.free()


def test_compact_ui_and_visibility_lifecycle():
    obj, chosen, b = capture()
    calls = []
    class UI:
        def row(self, **kw): return self
        def column(self, **kw): return self
        def box(self): return self
        def label(self, **kw): calls.append(('label', kw.get('text', '')))
        def operator(self, name, **kw):
            calls.append(('operator', kw.get('text', ''), name, kw.get('icon', '')))
            return SimpleNamespace()
    ui.draw_controls(UI(), C)
    labels = [c[1] for c in calls]
    for unwanted in ('Mark Start', 'Mark End', 'Confirm', 'Confirmed', 'Swap Ends', 'Reference: Basis', 'Set Top Surface (optional)', 'Reverse Bend', 'Show', 'Hide', 'L / R', 'Index Ã‚Â· L'):
        assert unwanted not in labels, unwanted
    assert not any('Detected rings' in label or 'rings: L' in label for label in labels)
    assert labels.count('Capture Detection') == 1
    for _ in range(3):
        bpy.ops.character_designer.finger_setup(action='TOGGLE')
        assert not ui.overlays_enabled()
        bpy.ops.character_designer.finger_setup(action='TOGGLE')
        assert ui.overlays_enabled()
    ui._invalidate()
    assert not ui._visible and ui._request is None and ui._pending_cache is None
    # Loading an older bank reference never presents its surface line as the
    # new interior result. Keep the data, ask for a one-click recapture.
    s = definition.state(C)
    previous = json.loads(s.record)
    previous.pop('internal')
    s.record = json.dumps(previous)
    ui.show()
    assert ui._drawable() is None
    calls.clear()
    ui.draw_controls(UI(), C)
    assert ('label', 'Recapture to create an internal axis.') in calls


def test_save_reopen_internal_and_raw_reference():
    obj, chosen, b = capture()
    saved = definition.state(C).record
    with tempfile.TemporaryDirectory(prefix='internal-axis-') as folder:
        path = str(Path(folder)/'axis.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        assert definition.state(C).record == saved
        assert definition.state(C).confirmed
        check_axis(C.edit_object, json.loads(saved))
        assert not any(o.type == 'EMPTY' for o in bpy.data.objects)


def test_root_transition_real_shell_and_bounded_range():
    obj, chosen, b = capture()
    record = json.loads(definition.state(C).record)
    candidate = record['body']
    bm = definition._snapshot(obj)
    try:
        centers, direction, _, _, width, *_ = internal.geometry(bm, candidate)
        path = [list(centers[0]-direction*width*.7), record['basis']['path'][-1]]
        body = internal.prepare_body(bm, candidate, path)
        assert body['root_extension']
        # The fixture's proximal cone is open. Do not fake an inside result.
        refused(lambda: internal.solve(bm, body, path))
        component = internal._component(bm, candidate)
        boundary = {e for f in component for e in f.edges if len(e.link_faces) == 1}
        directed = {loop.vert: loop.link_loop_next.vert for f in component for loop in f.loops if loop.edge in boundary}
        first = next(iter(directed))
        loop, vertex = [first], directed[first]
        while vertex != first:
            loop.append(vertex)
            vertex = directed[vertex]
        bm.faces.new(list(reversed(loop)))
        definition._index(bm)
        result = internal.solve(bm, body, path)
        assert result['coverage'] > .9
        assert (Vector(result['path'][0])-centers[0]).dot(direction) < -width*.4
        assert internal.Volume(bm, body).certify(result['path'], result['margin']) is not None
        # An overly deep palm selection is refused, never automatically cut.
        far = [list(centers[0]-direction*width*3), path[-1]]
        refused(lambda: internal.prepare_body(bm, candidate, far))
    finally: bm.free()


def test_selected_strip_controls_lateral_position_not_depth():
    obj, chosen, b = capture()
    record = json.loads(definition.state(C).record)
    bm = definition._snapshot(obj)
    try:
        # Keep the selected surface fixed, widen/shift the opposite surface.
        # Maximum volume clearance is no longer beneath the selected midline.
        hint = record['surface']['centering']
        normal = Vector(hint['normal'])
        main = internal.geometry(bm, record['body'])[1]
        side = main.cross(normal).normalized()
        selected = {v.index for i in record['input']['ids'] for v in bm.faces[i].verts}
        for row in record['body']['rings']:
            for i in row:
                if i not in selected: bm.verts[i].co += side*.04
        bm.normal_update()
        hint = internal.surface_centering(bm, record['body'], record['input'])
        main = internal.geometry(bm, record['body'])[1]
        side = main.cross(Vector(hint['normal'])).normalized()
        p, q = map(Vector, hint['path'])
        slope = (q-p).dot(side)/(q-p).dot(main)
        def error(axis):
            return max(abs((Vector(v)-p).dot(side)-slope*(Vector(v)-p).dot(main)) for v in axis['path'])
        old = internal.solve(bm, record['body'], record['basis']['path'])
        new = internal.solve(bm, record['body'], record['basis']['path'], hint)
        assert error(old) > .005, error(old)
        assert error(new) < 1e-6, error(new)
        assert new['coverage'] == old['coverage']
        assert internal.Volume(bm, record['body']).certify(new['path'], new['margin']) is not None
        # Rotating actual geometry (not only the view) preserves the result.
        transform = Matrix.Rotation(.73, 4, Vector((1, 2, 3)))
        for vertex in bm.verts: vertex.co = transform @ vertex.co
        bm.normal_update()
        rotated_hint = internal.surface_centering(bm, record['body'], record['input'])
        rotated_path = [list(transform @ Vector(v)) for v in record['basis']['path']]
        rotated = internal.solve(bm, record['body'], rotated_path, rotated_hint)
        assert all((transform @ Vector(a)-Vector(z)).length < 1e-5 for a, z in zip(new['path'], rotated['path'])), (new, rotated, [list(transform @ Vector(a)) for a in new['path']])
    finally: bm.free()
    # The actual bank persists and reflects the preference with the reference.
    left, right = (json.loads(b.slots['INDEX.'+s].guide.record) for s in ('L', 'R'))
    for a, z in zip(left['surface']['centering']['path'], right['surface']['centering']['path']):
        assert (Vector((-a[0], a[1], a[2]))-Vector(z)).length < 1e-6
    hint = left['surface']['centering']
    assert json.loads(definition.state(C).record)['surface']['centering'] == hint
    check_axis(obj, json.loads(definition.state(C).record))


if __name__ == '__main__':
    character_designer.register()
    tests = [v for k, v in list(globals().items()) if k.startswith('test_')]
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('INTERNAL_AXIS_PASSED', len(tests), flush=True)
