"""Registered Hair motion UI actions on disposable native owned Hair chains.

No physics outcome is mocked or asserted. One transport test records the
adapter call; actual Wiggle simulation belongs to its separate native suite.
"""
import copy
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy
from bpy.props import PointerProperty

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
from character_designer import hair_bones_rig as hair
from character_designer import hair_motion_profiles as profiles
from character_designer import hair_motion_ui as ui
from character_designer import hair_strand_registry as registry
from character_designer import hair_wiggle_adapter as wiggle
from character_designer import native_symmetry_pairs
from character_designer.symmetry_pairs import VertexPairMap
from test_hair_bones_rig_blender import activate, plain_snapshot, weights
from test_hair_strand_registry_blender import bound_fixture, empty_pairs


_REGISTERED = []
_WM_PROPERTY_CREATED = False


def ensure_ui():
    global _WM_PROPERTY_CREATED
    for cls in ui.HAIR_MOTION_CLASSES:
        if not cls.is_registered:
            bpy.utils.register_class(cls)
            _REGISTERED.append(cls)
    if not hasattr(bpy.types.WindowManager, 'character_designer_hair_motion'):
        bpy.types.WindowManager.character_designer_hair_motion = PointerProperty(
            type=ui.CharacterDesignerHairMotionState, options={'SKIP_SAVE'})
        _WM_PROPERTY_CREATED = True


def cleanup_ui():
    if _WM_PROPERTY_CREATED and hasattr(bpy.types.WindowManager, 'character_designer_hair_motion'):
        del bpy.types.WindowManager.character_designer_hair_motion
    for cls in reversed(_REGISTERED):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)


def state():
    return bpy.context.window_manager.character_designer_hair_motion


def call(name, *, cancelled=False, contains=None, **kwargs):
    try:
        result = getattr(bpy.ops.character_designer, name)(**kwargs)
    except RuntimeError as exc:
        # ERROR reports from a native bpy.ops execute are raised by Blender.
        assert cancelled, (name, str(exc))
        result = {'CANCELLED'}
    assert result == ({'CANCELLED'} if cancelled else {'FINISHED'}), (name, result)
    if contains is not None:
        assert contains in state().last_message, state().last_message
    return result


def close(actual, expected, epsilon=1e-6):
    assert abs(actual - expected) <= epsilon, (actual, expected)


def artist_state(obj):
    armature = obj[hair.RIG_KEY]
    return {
        'basis': tuple(tuple(vertex.co) for vertex in obj.data.vertices),
        'edges': tuple(tuple(edge.vertices) for edge in obj.data.edges),
        'faces': tuple(tuple(face.vertices) for face in obj.data.polygons),
        'uv': tuple((layer.name, tuple(tuple(point.uv) for point in layer.data)) for layer in obj.data.uv_layers),
        'weights': weights(obj),
        'groups': tuple((group.name, group.lock_weight) for group in obj.vertex_groups),
        'binding': obj[hair.RECORD_KEY],
        'rest': tuple((bone.name, bone.parent.name if bone.parent else None, tuple(bone.head_local),
                       tuple(bone.tail_local), bone.use_connect) for bone in armature.data.bones),
        'pose': tuple((bone.name, tuple(tuple(row) for row in bone.matrix_basis)) for bone in armature.pose.bones),
    }


def initialize_ui(obj, *, native=False):
    activate(obj)
    if native:
        call('hair_motion_initialize')
    else:
        with patch.object(native_symmetry_pairs, 'build_vertex_pairs', return_value=empty_pairs(obj)):
            call('hair_motion_initialize')
    assert obj[registry.REGISTRY_KEY] and obj[profiles.PROFILE_KEY]
    return registry.read(obj), profiles.read(obj)


def initialize_unpaired(obj):
    # Build a real, owned U inventory explicitly so this fixture exercises the
    # subsequent MANUAL action even when automatic geometry/graph proof exists.
    data, _record, _geometry = registry._live(obj)
    return registry._commit(obj, data)


def pair_fixture():
    obj, _plans, result = bound_fixture(symmetric=True)
    data = initialize_unpaired(obj)
    left, right = data['strands']
    registry.manual_pair(obj, left['strand_id'], right['strand_id'])
    data, record = initialize_ui(obj)
    return obj, result, data, record


class Layout:
    def __init__(self, events=None, *, enabled=True):
        self.events = [] if events is None else events
        self.enabled = enabled

    def row(self, **_kwargs):
        return Layout(self.events, enabled=self.enabled)

    box = column = row

    def label(self, **kwargs):
        self.events.append(('label', kwargs.get('text', ''), self.enabled))

    def prop(self, _data, name, **_kwargs):
        self.events.append(('prop', name, self.enabled))

    def operator(self, name, **kwargs):
        operation = SimpleNamespace()
        self.events.append(('operator', name, kwargs.get('text', ''), self.enabled, operation))
        return operation


def panel():
    layout = Layout()
    ui.CHARACTERDESIGNER_PT_hair_motion.draw(SimpleNamespace(layout=layout), bpy.context)
    return layout.events


def test_initialize_navigation_loads_settings_and_highlights_ordered_chain():
    obj, _plans, result = bound_fixture(strands=3)
    before = artist_state(obj)
    data, _record = initialize_ui(obj, native=True)
    items = sorted(data['strands'], key=lambda item: item['order'])
    assert artist_state(obj) == before
    armature = result['armature']
    assert bpy.context.active_object == armature and armature.mode == 'POSE'
    assert state().strand_id == items[0]['strand_id']
    assert armature.data.bones.active.name == items[0]['bones'][0]
    assert {bone.name for bone in armature.pose.bones if bone.select} == set(items[0]['bones'])
    profiles.update(obj, items[1]['strand_id'], group='BACK', overrides={'recovery': .23, 'gravity': 6.0},
                    sync_mirror=False, registry=data)
    saved = obj[profiles.PROFILE_KEY]
    original_select = hair._select_chains
    def select_ordered(context, selected_armature, names):
        assert isinstance(names, (list, tuple)), 'Highlight must retain root-to-tip list order'
        assert names == items[1]['bones']
        return original_select(context, selected_armature, names)
    with patch.object(hair, '_select_chains', side_effect=select_ordered):
        call('hair_motion_navigate', direction=1)
    assert state().strand_id == items[1]['strand_id'] and state().group == 'BACK'
    close(state().recovery, .23)
    close(state().gravity, 6.0)
    assert not state().sync_mirror
    assert armature.data.bones.active.name == items[1]['bones'][0]
    assert {bone.name for bone in armature.pose.bones if bone.select} == set(items[1]['bones'])
    assert obj[profiles.PROFILE_KEY] == saved and artist_state(obj) == before
    call('hair_motion_navigate', direction=-1)
    assert state().strand_id == items[0]['strand_id']
    call('hair_motion_navigate', direction=-1)
    assert state().strand_id == items[-1]['strand_id']
    for bone in armature.pose.bones:
        bone.select = False
    armature.pose.bones[items[1]['bones'][-1]].select = True
    armature.data.bones.active = armature.data.bones[items[1]['bones'][-1]]
    call('hair_motion_navigate', direction=0)
    assert state().strand_id == items[1]['strand_id']


def test_explicit_apply_syncs_only_settings_and_sync_off_keeps_other_side():
    obj, _result, data, _record = pair_fixture()
    left, right = data['strands']
    before = artist_state(obj)
    saved = obj[profiles.PROFILE_KEY]
    state().group, state().recovery, state().damping = 'FRONT', .32, .21
    state().gravity, state().stretch = 2.5, .12
    assert obj[profiles.PROFILE_KEY] == saved, 'Editing WM controls must await explicit Apply'
    call('hair_motion_apply', action='SETTINGS')
    effective = profiles.effective_all(obj, registry=data)
    assert effective[left['strand_id']] == effective[right['strand_id']]
    close(effective[left['strand_id']]['recovery'], .32)
    assert effective[left['strand_id']]['group'] == 'FRONT'
    other_before = copy.deepcopy(effective[right['strand_id']])
    state().sync_mirror, state().recovery = False, .84
    call('hair_motion_apply', action='SETTINGS')
    effective = profiles.effective_all(obj, registry=data)
    close(effective[left['strand_id']]['recovery'], .84)
    close(effective[right['strand_id']]['recovery'], other_before['recovery'])
    assert not effective[left['strand_id']]['sync_mirror'] and not effective[right['strand_id']]['sync_mirror']
    state().sync_mirror = True
    call('hair_motion_apply', action='SETTINGS')
    effective = profiles.effective_all(obj, registry=data)
    assert effective[left['strand_id']] == effective[right['strand_id']]
    assert artist_state(obj) == before


def test_navigation_reuses_one_fresh_full_registry_proof_per_action():
    obj, _plans, result = bound_fixture(strands=3)
    data, _record = initialize_ui(obj)
    items = sorted(data['strands'], key=lambda item: item['order'])
    raw_registry, raw_profile = obj[registry.REGISTRY_KEY], obj[profiles.PROFILE_KEY]
    before = artist_state(obj)
    with (patch.object(registry, 'read', wraps=registry.read) as reads,
          patch.object(registry, '_live', wraps=registry._live) as native_proofs):
        call('hair_motion_navigate', direction=1)
    strict_reads = [request for request in reads.call_args_list if request.kwargs.get('validate', True)]
    assert len(strict_reads) == 1 and native_proofs.call_count == 1
    assert state().strand_id == items[1]['strand_id']
    assert result['armature'].data.bones.active.name == items[1]['bones'][0]
    assert {bone.name for bone in result['armature'].pose.bones if bone.select} == set(items[1]['bones'])

    # Operation-local reuse must not hide an ownership edit on the next click.
    # Leave the active bone's SOURCE resolver intact; corrupt another chain so
    # this action must discover the change through its fresh complete proof.
    bone = result['armature'].data.bones[items[2]['bones'][0]]
    owner = bone[hair.OWNER_KEY]
    bone[hair.OWNER_KEY] = 'foreign-navigation-owner'
    try:
        with patch.object(registry, '_live', wraps=registry._live) as native_proofs:
            call('hair_motion_navigate', direction=1, cancelled=True, contains='source ownership changed')
        assert native_proofs.call_count == 1 and state().strand_id == items[1]['strand_id']
    finally:
        bone[hair.OWNER_KEY] = owner
    assert obj[registry.REGISTRY_KEY] == raw_registry and obj[profiles.PROFILE_KEY] == raw_profile
    assert artist_state(obj) == before


def test_center_settings_and_pair_preview_have_one_identity():
    obj, _plans, _result = bound_fixture(centered=True)
    data, _, geometry = registry._live(obj)
    center, other = data['strands']
    mapping = VertexPairMap(tuple((layer[2], layer[1]) for layer in center['layers']),
                            tuple(layer[0] for layer in center['layers']), tuple(other['vertices']), (), 1, True)
    registry._topology_pairs(data, geometry[0], mapping)
    registry._commit(obj, data)
    data, _record = initialize_ui(obj)
    center, other = data['strands']
    other_before = profiles.effective(obj, other['strand_id'], registry=data)
    state().recovery = .41
    call('hair_motion_apply', action='SETTINGS')
    close(profiles.effective(obj, center['strand_id'], registry=data)['recovery'], .41)
    assert profiles.effective(obj, other['strand_id'], registry=data) == other_before
    calls = []
    adapter = SimpleNamespace(status=lambda _context: {'active': False},
                              start_preview=lambda *args: calls.append(args))
    with patch.object(ui, '_backend', return_value=adapter):
        call('hair_motion_preview', scope='PAIR')
    assert calls[0][-1] == [center['strand_id']]
    events = panel()
    assert ('label', 'Center strand', True) in events
    assert ('prop', 'sync_mirror', False) in events


def test_group_presets_apply_group_and_restore_defaults_are_persistent():
    obj, _plans, _result = bound_fixture(strands=3)
    data, _record = initialize_ui(obj)
    items = data['strands']
    before = artist_state(obj)
    for index, group in ((0, 'FRONT'), (1, 'FRONT'), (2, 'BACK')):
        ui._load(bpy.context, obj, items[index]['strand_id'])
        state().group, state().sync_mirror = group, False
        call('hair_motion_apply', action='PRESET')
        values = profiles.effective(obj, items[index]['strand_id'], registry=data)
        close(values['recovery'], profiles.DEFAULTS[group]['recovery'])
        assert values['depth'] == profiles.DEFAULTS[group]['depth']
    ui._load(bpy.context, obj, items[0]['strand_id'])
    state().recovery = .17
    call('hair_motion_apply', action='SETTINGS')
    call('hair_motion_apply', action='RESTORE')
    close(state().recovery, profiles.DEFAULTS['FRONT']['recovery'])
    back_before = profiles.effective(obj, items[2]['strand_id'], registry=data)
    saved = obj[profiles.PROFILE_KEY]
    state().recovery, state().damping, state().gravity = .63, .42, 3.3
    assert obj[profiles.PROFILE_KEY] == saved
    # Apply to Group is itself an explicit Apply action, including the WM
    # values that have not yet been saved through Apply Strand Settings.
    call('hair_motion_apply', action='GROUP')
    first = profiles.effective(obj, items[0]['strand_id'], registry=data)
    second = profiles.effective(obj, items[1]['strand_id'], registry=data)
    assert first == second and first['group'] == 'FRONT'
    close(second['recovery'], .63)
    assert profiles.effective(obj, items[2]['strand_id'], registry=data) == back_before
    record = profiles.read(obj, registry=data)
    close(record['group_defaults']['FRONT']['recovery'], .63)
    assert not record['strands'][items[0]['strand_id']]['overrides']
    assert not record['strands'][items[1]['strand_id']]['overrides']
    assert artist_state(obj) == before


def test_along_chain_add_interpolate_apply_remove_and_invalid_order_rollback():
    obj, _plans, _result = bound_fixture()
    data, _record = initialize_ui(obj)
    strand_id = state().strand_id
    before = artist_state(obj)
    initial = profiles.effective(obj, strand_id, registry=data)
    midpoint = profiles.depth_sample(initial, .5)
    state().depth_index = 0
    call('hair_motion_depth', action='ADD')
    current = profiles.effective(obj, strand_id, registry=data)
    assert len(current['depth']) == 3 and state().depth_index == 1
    close(current['depth'][1]['position'], .5)
    for key, value in midpoint.items():
        close(current['depth'][1][key], value)
    saved = obj[profiles.PROFILE_KEY]
    state().depth_position, state().depth_mass = .4, 2.3
    state().depth_recovery, state().depth_damping, state().depth_gravity = .18, .24, 5.2
    assert obj[profiles.PROFILE_KEY] == saved
    call('hair_motion_depth', action='APPLY')
    current = profiles.effective(obj, strand_id, registry=data)
    close(current['depth'][1]['position'], .4)
    close(current['depth'][1]['mass'], 2.3)
    close(current['depth'][1]['recovery'], .18)
    sampled = profiles.depth_sample(current, .2)
    close(sampled['mass'], (current['depth'][0]['mass'] + current['depth'][1]['mass']) / 2)
    saved = obj[profiles.PROFILE_KEY]
    state().depth_position = 0
    call('hair_motion_depth', action='APPLY', cancelled=True, contains='positions must increase')
    assert obj[profiles.PROFILE_KEY] == saved and artist_state(obj) == before
    call('hair_motion_depth', action='NEXT')
    assert state().depth_index == 2
    call('hair_motion_depth', action='REMOVE', cancelled=True, contains='root and tip')
    assert obj[profiles.PROFILE_KEY] == saved
    call('hair_motion_depth', action='PREVIOUS')
    call('hair_motion_depth', action='REMOVE')
    assert len(profiles.effective(obj, strand_id, registry=data)['depth']) == 2
    assert artist_state(obj) == before


def test_action_late_load_failure_restores_source_metadata_and_depth_index():
    obj, _plans, _result = bound_fixture()
    initialize_ui(obj)
    saved = obj[profiles.PROFILE_KEY]
    before = artist_state(obj)
    depth_index = state().depth_index = 0
    with patch.object(ui, '_load', side_effect=RuntimeError('Injected final UI load failure')):
        call('hair_motion_depth', action='ADD', cancelled=True, contains='final UI load failure')
    assert obj[profiles.PROFILE_KEY] == saved and state().depth_index == depth_index
    assert artist_state(obj) == before
    state().recovery = .11
    with patch.object(ui, '_load', side_effect=RuntimeError('Injected final UI load failure')):
        call('hair_motion_apply', action='SETTINGS', cancelled=True)
    assert obj[profiles.PROFILE_KEY] == saved and artist_state(obj) == before


def test_depth_stop_callback_ownership_change_is_rejected_before_profile_write():
    obj, _plans, result = bound_fixture()
    data, _record = initialize_ui(obj)
    raw_registry, raw_profile = obj[registry.REGISTRY_KEY], obj[profiles.PROFILE_KEY]
    before = artist_state(obj)
    bone = result['armature'].data.bones[data['strands'][0]['bones'][0]]
    owner = bone[hair.OWNER_KEY]
    original_read = registry.read
    for action in ('ADD', 'APPLY', 'REMOVE'):
        events = []
        state().depth_index = 0
        def stop_callback(_context):
            events.append('stop')
            bone[hair.OWNER_KEY] = 'foreign-stop-callback-owner'
        def read_after_stop(*args, **kwargs):
            events.append('proof')
            return original_read(*args, **kwargs)
        backend = SimpleNamespace(status=lambda _context: {'active': True}, stop_preview=stop_callback)
        try:
            with (patch.object(ui, '_backend', return_value=backend),
                  patch.object(registry, 'read', side_effect=read_after_stop),
                  patch.object(profiles, '_write', side_effect=AssertionError('Invalid native proof wrote profile'))):
                call('hair_motion_depth', action=action, cancelled=True, contains='source ownership changed')
            assert events == ['stop', 'proof'] and state().depth_index == 0
        finally:
            bone[hair.OWNER_KEY] = owner
        assert obj[registry.REGISTRY_KEY] == raw_registry and obj[profiles.PROFILE_KEY] == raw_profile
        assert artist_state(obj) == before

    # Browsing controls is read-only, so it must not stop a running preview.
    with patch.object(ui, '_backend', side_effect=AssertionError('Read-only depth browsing stopped preview')):
        call('hair_motion_depth', action='NEXT')
        call('hair_motion_depth', action='PREVIOUS')
    assert state().depth_index == 0 and obj[profiles.PROFILE_KEY] == raw_profile
    assert artist_state(obj) == before


def test_draw_reads_metadata_only_and_missing_wiggle_displays_reason():
    obj, _plans, _result = bound_fixture()
    initialize_ui(obj)
    snapshot = plain_snapshot(obj)
    raw_registry, raw_profile = obj[registry.REGISTRY_KEY], obj[profiles.PROFILE_KEY]
    availability = wiggle.available(bpy.context)
    assert not availability['available'], 'Run the unavailable-backend test with factory preferences'
    with (patch.object(native_symmetry_pairs, 'build_vertex_pairs', side_effect=AssertionError('Draw searched native pairs')),
            patch.object(registry, '_live', side_effect=AssertionError('Draw verified full native Rest')),
            patch.object(registry, '_write', side_effect=AssertionError('Draw wrote registry')),
            patch.object(profiles, '_write', side_effect=AssertionError('Draw wrote settings'))):
        events = panel()
    assert ('label', availability['reason'], True) in events
    previews = [event for event in events if event[0] == 'operator' and event[1] == 'character_designer.hair_motion_preview']
    assert len(previews) == 2 and all(not event[3] for event in previews)
    assert obj[registry.REGISTRY_KEY] == raw_registry and obj[profiles.PROFILE_KEY] == raw_profile
    assert plain_snapshot(obj) == snapshot


def test_preview_transports_complete_effective_settings_and_exact_pair_or_all_ids():
    obj, _plans, _result = bound_fixture(strands=3)
    data = initialize_unpaired(obj)
    registry.manual_pair(obj, data['strands'][0]['strand_id'], data['strands'][1]['strand_id'])
    data, _record = initialize_ui(obj)
    expected = profiles.effective_all(obj, registry=data)
    before = artist_state(obj)
    calls = []
    adapter = SimpleNamespace(status=lambda _context: {'active': False},
                              start_preview=lambda *args: calls.append(args))
    with patch.object(ui, '_backend', return_value=adapter):
        call('hair_motion_preview', scope='PAIR')
        call('hair_motion_preview', scope='ALL')
    ids = [item['strand_id'] for item in data['strands']]
    assert len(calls) == 2
    for context, source, live_registry, effective, _chosen_ids in calls:
        assert context.scene == bpy.context.scene and source == obj and live_registry == registry.read(obj)
        assert effective == expected
    assert calls[0][-1] == ids[:2]
    assert calls[1][-1] == ids
    assert calls[0][-1][0] == state().strand_id
    assert artist_state(obj) == before


def test_stale_pair_selection_is_refused_before_stopping_or_starting_preview():
    obj, _result, _data, _record = pair_fixture()
    before = artist_state(obj)
    raw_profile, raw_registry = obj[profiles.PROFILE_KEY], obj[registry.REGISTRY_KEY]
    state().strand_id = 'old strand from another Hair source'
    calls = []
    adapter = SimpleNamespace(status=lambda _context: {'active': True},
                              stop_preview=lambda _context: calls.append('stop'),
                              start_preview=lambda *args: calls.append('start'))
    with patch.object(ui, '_backend', return_value=adapter):
        call('hair_motion_preview', scope='PAIR', cancelled=True, contains='Select a strand')
    assert not calls
    assert obj[profiles.PROFILE_KEY] == raw_profile and obj[registry.REGISTRY_KEY] == raw_registry
    assert artist_state(obj) == before


def test_malformed_profile_reconcile_reports_error_and_rolls_back_registry():
    obj, _plans, _result = bound_fixture()
    data, _record = initialize_ui(obj)
    raw_registry, before = obj[registry.REGISTRY_KEY], artist_state(obj)
    malformed = ('[]', 'null', json.dumps({'version': 1, 'source_uid': data['source_uid'],
                 'registry_topology': data['topology'], 'group_defaults': profiles.DEFAULTS, 'strands': []}))
    for raw in malformed:
        obj[profiles.PROFILE_KEY] = raw
        state().last_message = ''
        with patch.object(native_symmetry_pairs, 'build_vertex_pairs', return_value=empty_pairs(obj)):
            call('hair_motion_initialize', reconcile=True, cancelled=True)
        assert state().last_message, 'Malformed profile needs a controlled error, not an uncaught Python exception'
        assert obj[profiles.PROFILE_KEY] == raw and obj[registry.REGISTRY_KEY] == raw_registry
        assert artist_state(obj) == before


def test_new_pair_reconcile_preserves_authored_settings_and_opts_out_reciprocally():
    obj, _plans, _result = bound_fixture()
    data, _record = initialize_ui(obj)
    left, right = data['strands']
    profiles.update(obj, left['strand_id'], group='FRONT', overrides={'recovery': .31},
                    sync_mirror=False, registry=data)
    previous = profiles.effective_all(obj, registry=data)
    assert not previous[left['strand_id']]['sync_mirror'] and previous[right['strand_id']]['sync_mirror']
    registry.manual_pair(obj, left['strand_id'], right['strand_id'])
    before = artist_state(obj)
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', return_value=empty_pairs(obj)):
        call('hair_motion_initialize', reconcile=True)
    current = profiles.effective_all(obj)
    for strand_id in previous:
        assert not current[strand_id]['sync_mirror']
        assert {key: value for key, value in current[strand_id].items() if key != 'sync_mirror'} == {
            key: value for key, value in previous[strand_id].items() if key != 'sync_mirror'}
    assert artist_state(obj) == before


def test_missing_profile_apply_reports_initialize_error_without_creating_metadata():
    obj, _plans, _result = bound_fixture()
    initialize_ui(obj)
    raw_registry, before = obj[registry.REGISTRY_KEY], artist_state(obj)
    del obj[profiles.PROFILE_KEY]
    call('hair_motion_apply', action='SETTINGS', cancelled=True, contains='Initialize motion settings')
    assert profiles.PROFILE_KEY not in obj and obj[registry.REGISTRY_KEY] == raw_registry
    assert artist_state(obj) == before


def test_native_undo_redo_metadata_and_save_reopen_resolve_stable_ids():
    obj, _plans, _result = bound_fixture()
    data, _record = initialize_ui(obj)
    name, strand_id = obj.name, state().strand_id
    assert 'UNDO' in ui.CHARACTERDESIGNER_OT_hair_motion_initialize.bl_options
    assert 'UNDO' in ui.CHARACTERDESIGNER_OT_hair_motion_apply.bl_options
    assert 'UNDO' in ui.CHARACTERDESIGNER_OT_hair_motion_depth.bl_options
    assert bpy.types.WindowManager.bl_rna.properties['character_designer_hair_motion'].is_skip_save
    assert state().bl_rna.properties['strand_id'].is_skip_save
    activate(obj)
    initial = obj[profiles.PROFILE_KEY]
    bpy.context.preferences.edit.use_global_undo = True
    assert bpy.ops.ed.undo_push(message='Before Hair motion metadata') == {'FINISHED'}
    state().recovery = .19
    call('hair_motion_apply', action='SETTINGS')
    changed = obj[profiles.PROFILE_KEY]
    assert changed != initial
    assert bpy.ops.ed.undo_push(message='After Hair motion metadata') == {'FINISHED'}
    assert bpy.ops.ed.undo() == {'FINISHED'}
    obj = bpy.data.objects[name]
    assert obj[profiles.PROFILE_KEY] == initial
    assert registry.read(obj)['strands'][0]['strand_id'] == strand_id
    assert bpy.ops.ed.redo() == {'FINISHED'}
    obj = bpy.data.objects[name]
    assert obj[profiles.PROFILE_KEY] == changed
    registry_raw = obj[registry.REGISTRY_KEY]
    artist = artist_state(obj)
    with tempfile.TemporaryDirectory(prefix='cd_hair_motion_ui_') as directory:
        path = str(Path(directory) / 'hair_motion_ui.blend')
        assert bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False) == {'FINISHED'}
        assert bpy.ops.wm.open_mainfile(filepath=path, load_ui=False) == {'FINISHED'}
        obj = bpy.data.objects[name]
        assert obj[profiles.PROFILE_KEY] == changed and obj[registry.REGISTRY_KEY] == registry_raw
        assert artist_state(obj) == artist
        activate(obj)
        state().strand_id = ''
        call('hair_motion_navigate', direction=0)
        assert state().strand_id == strand_id
        close(state().recovery, .19)
        assert profiles.read(obj, registry=registry.read(obj))['source_uid'] == data['source_uid']


def test_manual_pair_buttons_preserve_authored_settings_and_rollback_late_failure():
    obj, _plans, _result = bound_fixture(symmetric=True)
    data = initialize_unpaired(obj)
    profiles.initialize(obj, registry=data)
    left, right = data['strands']
    profiles.update(obj, left['strand_id'], group='FRONT', overrides={'recovery': .21},
                    sync_mirror=False, registry=data)
    profiles.update(obj, right['strand_id'], group='SIDE', overrides={'gravity': 6.3},
                    sync_mirror=False, registry=data)
    authored = profiles.effective_all(obj, registry=data)
    activate(obj)
    ui._load(bpy.context, obj, left['strand_id'])
    before = artist_state(obj)
    raw_registry, raw_profile = obj[registry.REGISTRY_KEY], obj[profiles.PROFILE_KEY]
    call('hair_motion_pair', side='LEFT')
    assert state().pair_left_id == left['strand_id']
    assert obj[registry.REGISTRY_KEY] == raw_registry and obj[profiles.PROFILE_KEY] == raw_profile
    ui._load(bpy.context, obj, right['strand_id'])
    with patch.object(ui, '_load', side_effect=RuntimeError('Injected final pair UI failure')):
        call('hair_motion_pair', side='RIGHT', cancelled=True)
    assert obj[registry.REGISTRY_KEY] == raw_registry and obj[profiles.PROFILE_KEY] == raw_profile
    assert artist_state(obj) == before
    call('hair_motion_pair', side='RIGHT')
    current = registry.read(obj)
    assert all(item['pair_proof'] == 'MANUAL' for item in current['strands'])
    assert current['strands'][0]['mirror_id'] == right['strand_id']
    assert current['strands'][1]['mirror_id'] == left['strand_id']
    assert profiles.effective_all(obj, registry=current) == authored
    assert artist_state(obj) == before
    assert 'UNDO' in ui.CHARACTERDESIGNER_OT_hair_motion_pair.bl_options


def main():
    ensure_ui()
    try:
        tests = (test_initialize_navigation_loads_settings_and_highlights_ordered_chain,
                 test_explicit_apply_syncs_only_settings_and_sync_off_keeps_other_side,
                 test_navigation_reuses_one_fresh_full_registry_proof_per_action,
                 test_center_settings_and_pair_preview_have_one_identity,
                 test_group_presets_apply_group_and_restore_defaults_are_persistent,
                 test_along_chain_add_interpolate_apply_remove_and_invalid_order_rollback,
                 test_action_late_load_failure_restores_source_metadata_and_depth_index,
                 test_depth_stop_callback_ownership_change_is_rejected_before_profile_write,
                 test_draw_reads_metadata_only_and_missing_wiggle_displays_reason,
                 test_preview_transports_complete_effective_settings_and_exact_pair_or_all_ids,
                 test_stale_pair_selection_is_refused_before_stopping_or_starting_preview,
                 test_malformed_profile_reconcile_reports_error_and_rolls_back_registry,
                 test_new_pair_reconcile_preserves_authored_settings_and_opts_out_reciprocally,
                 test_missing_profile_apply_reports_initialize_error_without_creating_metadata,
                 test_native_undo_redo_metadata_and_save_reopen_resolve_stable_ids,
                 test_manual_pair_buttons_preserve_authored_settings_and_rollback_late_failure)
        for test in tests:
            test()
            print('PASS', test.__name__)
        print('HAIR_MOTION_UI_TESTS_OK', len(tests))
    finally:
        cleanup_ui()


if __name__ == '__main__':
    main()
