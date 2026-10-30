"""Isolated native paired Hair entries, UILayout and Undo/Redo acceptance.

Reuse the immutable approved125 fixture. No artist file, preferences save or
factory reset is performed from a GUI timer. This identified derivative adds
logical pair coverage to pinned GUI03; legacy fixtures and harnesses stay exact.
"""
from pathlib import Path
import copy
import hashlib
import json
import os
import traceback
from types import SimpleNamespace
from unittest.mock import patch

import bpy

REPO = Path(r'D:/MyRepository/Blender-addons-by-Randy')
BASE = REPO / 'tests/validate_hair_resegment_selected_blender.py'
BASE_SHA = '2fd7e73fced03c8993250947da5c6dd9c58d0559f6b5065c7b54bbde8aa34d25'
digest = lambda value: hashlib.sha256(Path(value).read_bytes()).hexdigest()
assert digest(BASE) == BASE_SHA
prefix = BASE.read_text(encoding='utf-8').split('\nresult = None\n', 1)[0]
base = {'__file__': str(BASE), '__name__': 'hair_browser_gui_fixture', 'GUI': True}
exec(compile(prefix, str(BASE), 'exec'), base)
args, cd, hair, registry, profiles, service, variants, PACKAGE, manifest = (
    base[name] for name in ('args', 'cd', 'hair', 'registry', 'profiles',
                           'service', 'variants', 'PACKAGE', 'manifest'))
from character_designer import hair_bones, hair_strand_list_ui as list_ui
from character_designer import hair_strand_browser as browser
from character_designer import hair_strand_entries as entries

private_temp = Path(os.environ['TEMP']).resolve()
private_config = Path(os.environ['BLENDER_USER_CONFIG']).resolve()
assert private_temp == Path(os.environ['TMP']).resolve()
assert private_temp.is_dir() and private_config.is_dir()
assert private_temp.is_relative_to(args.report.parent.resolve())
assert private_config.is_relative_to(args.report.parent.resolve())
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.filepaths.temporary_directory = str(private_temp) + os.sep
assert Path(bpy.app.tempdir).resolve().is_relative_to(private_temp)
report = dict(schema='character-designer.hair-strand-pairs-gui/1', passed=False,
    projection=str(args.projection), projection_sha256=args.projection_sha,
    script_sha256=digest(__file__), fixture_sha256=BASE_SHA,
    derivative_source=dict(path='D:\\Blender\\Projects\\Character\\X\\validation\\hair_strand_browser_20261029\\harnesses\\gui_browser03.py', sha256='8e00eae472f2ac2d70697ace6885822b9f40a4735ad8b850c486b0c2e138b2be'),
    unchanged_legacy_gui=dict(path='D:\\MyRepository\\Blender-addons-by-Randy\\tests\\validate_hair_strand_browser_gui.py', sha256='8e00eae472f2ac2d70697ace6885822b9f40a4735ad8b850c486b0c2e138b2be'),
    native_history=[], draw_records=[], screenshots=[], errors=[], cleanup_errors=[],
    private_temp=str(private_temp), private_config=str(private_config), app_temp=bpy.app.tempdir,
    artist_file_opened=False, preferences_saved=False, private_scene_save=True,
    limits=['Private synthetic three-strand GUI; no artist/full-character/dynamics/FPS acceptance.',
            'Native undo_push snapshots explicitly surround Python operator calls.',
            'Captured operator pins are separately authenticated after Undo while the WM cache remains newer.',
            'Real UILayout invokes the Hair Bones panel and native UIList; screenshot inspection remains separate.'])
READY = False
REGISTERED = False
PANEL_REGISTERED = False
STAGE = 0
DRAW_SEEN = set()
ORIGINAL_NAMES = ()
SIGNATURES = ()
EXPECTED_AUTHOR = None
EXPECTED_ACTIONS = None
EXPECTED_PHYSICAL_PARAMETERS = None
UNTOUCHED = None
VERSION_SIX = ''
VERSION_SEVEN = ''


def gui_fresh_main():
    # A timer must keep its native Window/Screen; no read_factory_settings here.
    active = bpy.context.object
    if active is not None and active.mode != 'OBJECT':
        assert bpy.ops.object.mode_set(mode='OBJECT') == {'FINISHED'}
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    assert not bpy.data.objects and not bpy.data.actions and not bpy.data.shape_keys
    assert hasattr(bpy.types.WindowManager, 'character_designer_hair_bones')


base['fresh_main'] = gui_fresh_main


def wm_state():
    return bpy.context.window_manager.character_designer_hair_bones


def checks(signatures):
    wanted = set(signatures)
    for item in wm_state().list_items:
        item.checked = bool(wanted.intersection(json.loads(item.signatures_json)))


def original():
    return tuple(bpy.data.objects[name] for name in ORIGINAL_NAMES)


def physical_author(source, rig):
    value = base['scope']['source_author'](source, rig)
    # source_author's properties entry is a stable plain-ID tuple. Preserve it;
    # inspect detached source metadata separately, excluding category storage.
    value['browser_source_properties'] = {
        key: base['normalized'](item, source, rig)
        for key, item in source.items()
        if key not in (registry.REGISTRY_KEY, profiles.PROFILE_KEY)}
    return value


def physical_parameters(source):
    data = registry.read(source, validate=True)
    return {item['signature']: {key: value for key, value in
            profiles.effective(source, item['strand_id'], registry=data).items()
            if key not in ('group', 'sync_mirror')} for item in data['strands']}


def cached_rows():
    return [dict(key=item.entry_key, signatures=json.loads(item.signatures_json),
                 members=json.loads(item.members_json), counts=json.loads(item.counts_json),
                 groups=json.loads(item.groups_json), group=item.group,
                 member_count=item.member_count, checked=item.checked)
            for item in wm_state().list_items]


def author_ok():
    source, rig = original()
    assert physical_author(source, rig) == EXPECTED_AUTHOR
    assert physical_parameters(source) == EXPECTED_PHYSICAL_PARAMETERS
    assert {name: base['scope']['curves'](bpy.data.actions[name])
            for name in EXPECTED_ACTIONS} == EXPECTED_ACTIONS
    for collection in variants.variants_for(source):
        mesh = collection.get(variants.MESH_KEY)
        if mesh is not None:
            assert base['chain_author'](mesh, SIGNATURES[2]) == UNTOUCHED
            assert physical_parameters(mesh) == EXPECTED_PHYSICAL_PARAMETERS


def groups(source):
    return {item['signature']: item['group'] for item in browser.list_rows(source)}


def state():
    source, rig = original()
    versions = sorted(variants.variants_for(source), key=lambda value: value.name)
    return dict(versions=[value.name for value in versions],
        visible=[value.name for value in versions if not value.hide_viewport],
        counts={value.name: {item['signature']: item['count']
                for item in hair._read_records(value.get(variants.MESH_KEY))['chains']}
                for value in versions},
        groups={value.name: groups(value.get(variants.MESH_KEY)) for value in versions},
        original_groups=groups(source), original_visible=source.visible_get(),
        active=bpy.context.object.name if bpy.context.object else None,
        mode=bpy.context.mode,
        pose_selection=sorted(pose.name for pose in bpy.context.selected_pose_bones or ()))


def history(label, call, *, reauthorize=False, checked_counts=None, new_registry_identity=False):
    frozen = list_ui._capture(bpy.context) if reauthorize else ''
    assert bpy.ops.ed.undo_push(message='Before ' + label) == {'FINISHED'}
    before = state()
    assert call() == {'FINISHED'}
    author_ok()
    after = state()
    if checked_counts is not None:
        assert len(after['visible']) == 1
        assert after['counts'][after['visible'][0]] == dict(zip(SIGNATURES, checked_counts))
    assert bpy.ops.ed.undo_push(message='After ' + label) == {'FINISHED'}
    latest_cache = wm_state().list_pin
    latest_rows = cached_rows()
    assert bpy.ops.ed.undo() == {'FINISHED'}
    author_ok()
    assert state() == before, (label, 'undo', state(), before)
    reauthorized = False
    identity_receipt = None
    if reauthorize:
        assert wm_state().list_pin == latest_cache
        assert json.loads(latest_cache) != json.loads(frozen)['fingerprint']
        resolved = list_ui._authenticate(SimpleNamespace(action_pin=frozen), bpy.context,
                                          require_checked=True)
        frozen_data = json.loads(frozen)
        assert resolved[0].name == frozen_data['fingerprint']['source']['name']
        assert cached_rows() == latest_rows
        if new_registry_identity:
            latest_fingerprint = json.loads(latest_cache)
            restored = browser.strict_snapshot(resolved[0])
            assert restored['fingerprint'] == frozen_data['fingerprint']
            assert latest_fingerprint['source_uid'] != restored['fingerprint']['source_uid']
            newer_pair = next(item for item in latest_rows if item['member_count'] == 2)
            restored_pair = next(item for item in entries.build_entries(restored['rows'])
                                 if item['member_count'] == 2)
            assert newer_pair['signatures'] == list(restored_pair['signatures'])
            assert newer_pair['key'] != restored_pair['key']
            assert {item['strand_id'] for item in newer_pair['members']}.isdisjoint(
                   item['strand_id'] for item in restored_pair['members'])
            assert {item['pair_id'] for item in newer_pair['members']}.isdisjoint(
                   item['pair_id'] for item in restored_pair['members'])
            identity_receipt = dict(cached_source_uid=latest_fingerprint['source_uid'],
                restored_source_uid=restored['fingerprint']['source_uid'],
                cached_pair=newer_pair, restored_pair=list_ui._entry_identity(restored_pair),
                original_pin_authenticated_with_real_newer_registry_ids=True)
        reauthorized = True
    assert bpy.ops.ed.redo() == {'FINISHED'}
    author_ok()
    assert state() == after, (label, 'redo', state(), after)
    report['native_history'].append(dict(operation=label, undo=True, redo=True,
        captured_pin_authorized_against_restored_source=reauthorized,
        regenerated_registry_identity_receipt=identity_receipt, before=before, after=after))
    return after


def expect_refusal(call):
    try:
        value = call()
    except (ValueError, RuntimeError):
        return
    assert value == {'CANCELLED'}, value


def refresh_failure(call):
    with patch.object(list_ui, 'refresh_ui', side_effect=RuntimeError('Injected committed UI refresh failure')):
        value = call()
    assert value == {'FINISHED'}, value
    assert wm_state().list_pin == ''
    return value


class NativeLayoutRecorder:
    """Delegate to real UILayout while recording labels from product draw code."""
    def __init__(self, layout, labels):
        object.__setattr__(self, '_layout', layout)
        object.__setattr__(self, '_labels', labels)

    def __getattr__(self, name):
        return getattr(self._layout, name)

    def __setattr__(self, name, value):
        setattr(self._layout, name, value)

    def label(self, *args, **kwargs):
        self._labels.append(kwargs.get('text', ''))
        return self._layout.label(*args, **kwargs)

    def prop(self, data, property, *args, **kwargs):
        # Product dialog draw receives an operator instance in Blender. This
        # harness calls that draw inside its QA panel, so only its dummy dialog
        # receiver redirects property drawing to an actual RNA operator button.
        if isinstance(data, SimpleNamespace) and hasattr(data, '_native_operator_properties'):
            data = data._native_operator_properties
            assert isinstance(data, bpy.types.OperatorProperties)
            assert property == 'bone_count' and data.bone_count == 6
            report['native_rebuild_target_property'] = dict(
                property=property, value=data.bone_count,
                rna_identifier=data.bl_rna.identifier,
                real_rna_operator_properties=True)
        return self._layout.prop(data, property, *args, **kwargs)

    def row(self, *args, **kwargs):
        return NativeLayoutRecorder(self._layout.row(*args, **kwargs), self._labels)

    def column(self, *args, **kwargs):
        return NativeLayoutRecorder(self._layout.column(*args, **kwargs), self._labels)

    def box(self, *args, **kwargs):
        return NativeLayoutRecorder(self._layout.box(*args, **kwargs), self._labels)

    def split(self, *args, **kwargs):
        return NativeLayoutRecorder(self._layout.split(*args, **kwargs), self._labels)


class HAIRBROWSERQA_PT_native(bpy.types.Panel):
    bl_idname = 'HAIRBROWSERQA_PT_native'
    bl_label = 'Paired Hair Entries Native QA'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'
    bl_order = -1000

    def draw(self, context):
        if not READY:
            self.layout.label(text='Preparing private Hair fixture')
            return
        try:
            source = hair_bones._source(context)
            saved = (wm_state().list_pin, cached_rows(),
                     source.get(registry.REGISTRY_KEY), source.get(profiles.PROFILE_KEY))
            # Rendering the browser cannot ask for geometry/native ownership proof.
            with patch.object(browser, 'strict_snapshot', side_effect=AssertionError('Draw attempted a strict mesh scan')):
                labels = []
                native_layout = NativeLayoutRecorder(self.layout, labels)
                hair_bones.CHARACTERDESIGNER_PT_hair_bones.draw(
                    SimpleNamespace(layout=native_layout), context)
                # Exercise product UIList and dialog draws on actual native
                # UILayout too, so their exact labels appear in the receipt.
                proof_box = native_layout.box()
                proof_box.label(text='Native pair row and rebuild dialog proof')
                for index, item in enumerate(wm_state().list_items):
                    list_ui.CHARACTERDESIGNER_UL_hair_strand_list.draw_item(
                        SimpleNamespace(), context, proof_box, wm_state(), item,
                        0, wm_state(), 'list_active_index', index)
                if wm_state().list_pin and list_ui._checked(wm_state()):
                    dialog_properties = proof_box.operator(
                        'character_designer.hair_strand_list_rebuild',
                        text='Native rebuild target proof')
                    dialog_properties.action_pin = list_ui._capture(context)
                    dialog_properties.bone_count = 6
                    list_ui.CHARACTERDESIGNER_OT_hair_strand_list_rebuild.draw(
                        SimpleNamespace(layout=proof_box,
                            action_pin=dialog_properties.action_pin,
                            bone_count=dialog_properties.bone_count,
                            _native_operator_properties=dialog_properties), context)
            assert saved == (wm_state().list_pin, cached_rows(),
                             source.get(registry.REGISTRY_KEY), source.get(profiles.PROFILE_KEY))
            key = ('version' if service.is_version(source) else 'original',
                   source[hair.RIG_KEY].data.pose_position)
            if key not in DRAW_SEEN:
                DRAW_SEEN.add(key)
                report['draw_records'].append(dict(kind=key[0], pose_position=key[1],
                    source=source.name, rows=len(wm_state().list_items), native_ui_list=True,
                    logical_entries=cached_rows(), physical_chains=sum(
                        item.member_count for item in wm_state().list_items),
                    native_labels=labels, draw_read_only=True))
                pair = next(item for item in wm_state().list_items if item.member_count == 2)
                assert len(wm_state().list_items) == 2
                assert sum(item.member_count for item in wm_state().list_items) == 3
                counts = json.loads(pair.counts_json)
                assert counts[0] == counts[1]
                assert str(counts[0]) + ' bones per side' in labels
                assert '1 checked entries (2 physical chains)' in labels
                if key == ('original', 'POSE'):
                    assert counts == [4, 4]
                    assert pair.group == 'MIXED'
                    assert set(json.loads(pair.groups_json)) == {'FRONT', 'SIDE'}
                    assert any('4 bones per side' in text and '6 bones per side' in text
                               for text in labels)
        except BaseException:
            text = traceback.format_exc()
            if text not in report['errors']:
                report['errors'].append(text)


def redraw():
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            area.spaces.active.show_region_ui = True
            region = next(region for region in area.regions if region.type == 'UI')
            report['sidebar_active_category'] = region.active_panel_category
            area.tag_redraw()


def screenshot(label):
    path = args.report.with_name(args.report.stem + '_' + label + '.png')
    assert not path.exists()
    assert bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'}
    assert path.is_file() and path.stat().st_size > 0
    report['screenshots'].append(str(path))


def finish():
    global READY
    READY = False
    try:
        bpy.context.preferences.filepaths.temporary_directory = str(private_temp) + os.sep
        assert Path(bpy.app.tempdir).resolve().is_relative_to(private_temp)
        report['app_temp_at_exit'] = bpy.app.tempdir
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if PANEL_REGISTERED:
            bpy.utils.unregister_class(HAIRBROWSERQA_PT_native)
        if REGISTERED:
            cd.unregister()
    except BaseException:
        report['cleanup_errors'].append(traceback.format_exc())
    report['legacy_gui_unchanged'] = digest(report['unchanged_legacy_gui']['path']) == report['unchanged_legacy_gui']['sha256']
    report['source_unchanged'] = all(digest(PACKAGE / name) == value
                                       for name, value in manifest['files'].items())
    report['passed'] = bool(len(report['native_history']) == 7
        and {('original', 'POSE'), ('version', 'POSE'), ('original', 'REST')} <= DRAW_SEEN
        and report.get('single_side_native_import_checks_pair_and_selects_both', False)
        and report.get('native_rebuild_target_property', {}).get('real_rna_operator_properties')
        and sum(bool(item['regenerated_registry_identity_receipt']) for item in report['native_history']) == 2
        and report['legacy_gui_unchanged']
        and not report['errors'] and not report['cleanup_errors'] and report['source_unchanged'])
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    bpy.ops.wm.quit_blender()
    return None


def step():
    global STAGE, REGISTERED, PANEL_REGISTERED, READY
    global ORIGINAL_NAMES, SIGNATURES, EXPECTED_AUTHOR, EXPECTED_ACTIONS, UNTOUCHED
    global EXPECTED_PHYSICAL_PARAMETERS
    global VERSION_SIX, VERSION_SEVEN
    try:
        if STAGE == 0:
            cd.register()
            REGISTERED = True
            source, rig, record = base['selected_fixture'](rich=False, automatic_pairs=False)
            configured = registry.read(source, validate=True)
            before_parameters = physical_parameters(source)
            configured = registry.manual_pair(source, configured['strands'][0]['strand_id'],
                                               configured['strands'][1]['strand_id'])
            profiles.reconcile(source, registry=configured)
            assert physical_parameters(source) == before_parameters
            profile = profiles.read(source, registry=configured)
            assert all(profile['strands'][item['strand_id']]['sync_mirror'] is False
                       for item in configured['strands'][:2])
            EXPECTED_PHYSICAL_PARAMETERS = before_parameters
            ORIGINAL_NAMES = source.name, rig.name
            SIGNATURES = tuple(item['signature'] for item in record['chains'])
            base['choose'](source, (SIGNATURES[0],))
            bpy.context.preferences.edit.use_global_undo = True
            assert bpy.ops.wm.save_as_mainfile(filepath=str(args.report.with_suffix('.blend')),
                                               check_existing=False) == {'FINISHED'}
            EXPECTED_AUTHOR = physical_author(source, rig)
            EXPECTED_ACTIONS = {action.name: base['scope']['curves'](action) for action in bpy.data.actions}
            UNTOUCHED = base['chain_author'](source, SIGNATURES[2])
            list_ui.refresh_ui(bpy.context, source, checked_signatures=(SIGNATURES[0],))
            assert len(wm_state().list_items) == 2
            pair = next(item for item in wm_state().list_items if item.member_count == 2)
            assert json.loads(pair.counts_json) == [4, 4]
            assert pair.group == 'MIXED'
            assert list_ui._visible(pair, 'FRONT') and list_ui._visible(pair, 'SIDE')
            assert not list_ui._visible(pair, 'BACK')
            before_selection = tuple(pose.name for pose in rig.pose.bones if pose.select)
            before_active = rig.data.bones.active.name if rig.data.bones.active else None
            assert len(before_selection) == 1
            checks(())
            assert bpy.ops.character_designer.hair_strand_list_check(
                action='IMPORT_POSE', action_pin='') == {'FINISHED'}
            assert list_ui._checked(wm_state()) == SIGNATURES[:2]
            assert bpy.ops.character_designer.hair_strand_list_select_pose(
                action_pin='') == {'FINISHED'}
            expected_names = {name for chain in record['chains'][:2] for name in chain['bones']}
            assert len(expected_names) == 8
            assert {pose.name for pose in rig.pose.bones if pose.select} == expected_names
            for pose in rig.pose.bones:
                pose.select = pose.name in before_selection
            rig.data.bones.active = rig.data.bones.get(before_active) if before_active else None
            assert physical_author(source, rig) == EXPECTED_AUTHOR
            report['single_side_native_import_checks_pair_and_selects_both'] = True
            report['initial_pair'] = cached_rows()
            # A frozen dialog scope cannot silently follow a switch to C.
            # A -> B remains the same logical pair and is intentionally valid.
            pin = list_ui._capture(bpy.context)
            checks((SIGNATURES[2],))
            expect_refusal(lambda: bpy.ops.character_designer.hair_strand_list_rebuild(
                'EXEC_DEFAULT', action_pin=pin, bone_count=6))
            assert not variants.variants_for(source)
            checks((SIGNATURES[0],))
            report['dialog_scope_change_refused'] = True
            bpy.utils.register_class(HAIRBROWSERQA_PT_native)
            PANEL_REGISTERED = True
            READY = True
            redraw()
        elif STAGE == 1:
            assert ('original', 'POSE') in DRAW_SEEN, report
            screenshot('original')
            first, second, third = SIGNATURES
            history('Assign Category', lambda: bpy.ops.character_designer.hair_strand_list_assign_category(
                'EXEC_DEFAULT', group='BACK', action_pin=''), reauthorize=True)
            assert groups(original()[0])[first] == groups(original()[0])[second] == 'BACK'
            source = original()[0]
            list_ui.refresh_ui(bpy.context, source, checked_signatures=(first, second))
            after = history('Checked A and B to Six', lambda: bpy.ops.character_designer.hair_strand_list_rebuild(
                'EXEC_DEFAULT', bone_count=6, action_pin=''), reauthorize=True, checked_counts=(6, 6, 4), new_registry_identity=True)
            VERSION_SIX = after['visible'][0]
            six = bpy.data.collections[VERSION_SIX].get(variants.MESH_KEY)
            list_ui.refresh_ui(bpy.context, six, checked_signatures=(first,))
            after = history('Checked Pair A and B to Seven', lambda: bpy.ops.character_designer.hair_strand_list_rebuild(
                'EXEC_DEFAULT', bone_count=7, action_pin=''), reauthorize=True, checked_counts=(7, 7, 4), new_registry_identity=True)
            VERSION_SEVEN = after['visible'][0]
            redraw()
        elif STAGE == 2:
            assert ('version', 'POSE') in DRAW_SEEN, report
            screenshot('version')
            after = history('Show Original', lambda: bpy.ops.character_designer.hair_show_original_version())
            assert after['original_visible'] and not after['visible']
            history('Show Retained Version', lambda: bpy.ops.character_designer.hair_show_version(
                collection_name=VERSION_SIX), checked_counts=(6, 6, 4))
            assert bpy.ops.character_designer.hair_show_original_version() == {'FINISHED'}
            list_ui.refresh_ui(bpy.context, original()[0], checked_signatures=(SIGNATURES[0],))
            history('Committed Category Refresh Failure', lambda: refresh_failure(
                lambda: bpy.ops.character_designer.hair_strand_list_assign_category(
                    'EXEC_DEFAULT', group='FRONT', action_pin='')))
            list_ui.refresh_ui(bpy.context, original()[0], checked_signatures=(SIGNATURES[0],))
            history('Committed Rebuild Refresh Failure', lambda: refresh_failure(
                lambda: bpy.ops.character_designer.hair_strand_list_rebuild(
                    'EXEC_DEFAULT', bone_count=8, action_pin='')), checked_counts=(8, 8, 4))
            assert bpy.ops.character_designer.hair_show_original_version() == {'FINISHED'}
            source, rig = original()
            rig.data.pose_position = 'REST'
            list_ui.refresh_ui(bpy.context, source, checked_signatures=(SIGNATURES[0],))
            before_selection=tuple(pose.name for pose in rig.pose.bones if pose.select)
            before_active=rig.data.bones.active.name if rig.data.bones.active else None
            assert bpy.ops.character_designer.hair_strand_list_select_pose() == {'FINISHED'}
            assert rig.data.pose_position == 'REST'
            expected_names={name for chain in hair._read_records(source)['chains']
                            if chain['signature'] in SIGNATURES[:2] for name in chain['bones']}
            assert len(expected_names)==8
            assert {pose.name for pose in rig.pose.bones if pose.select}==set(expected_names)
            # The selection operation intentionally changes selection. Restore
            # only this test's own entry selection before author comparisons.
            for pose in rig.pose.bones:pose.select=pose.name in before_selection
            rig.data.bones.active=rig.data.bones.get(before_active) if before_active else None
            assert tuple(pose.name for pose in rig.pose.bones if pose.select)==before_selection
            report['rest_selection_scope_verified_and_entry_context_restored']=True
            report['rest_selection_preserves_display'] = True
            redraw()
        elif STAGE == 3:
            assert ('original', 'REST') in DRAW_SEEN, report
            screenshot('rest')
            original()[1].data.pose_position = 'POSE'
            author_ok()
            assert bpy.ops.character_designer.hair_show_version(collection_name=VERSION_SEVEN) == {'FINISHED'}
            list_ui.refresh_ui(bpy.context, wm_state().source, checked_signatures=(SIGNATURES[0],))
            redraw()
        elif STAGE == 4:
            screenshot('final_version')
            author_ok()
            report['unselected_chain_and_original_authors_preserved'] = True
            return finish()
        STAGE += 1
        return .7
    except BaseException:
        report['errors'].append(traceback.format_exc())
        return finish()


bpy.app.timers.register(step, first_interval=.7)
