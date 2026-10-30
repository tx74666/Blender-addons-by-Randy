"""Isolated native Hair browser UILayout and Undo/Redo acceptance.

Reuse the immutable approved125 fixture. No artist file, preferences save or
factory reset is performed from a GUI timer. Root launches a pinned derivative.
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

private_temp = Path(os.environ['TEMP']).resolve()
private_config = Path(os.environ['BLENDER_USER_CONFIG']).resolve()
assert private_temp == Path(os.environ['TMP']).resolve()
assert private_temp.is_dir() and private_config.is_dir()
assert private_temp.is_relative_to(args.report.parent.resolve())
assert private_config.is_relative_to(args.report.parent.resolve())
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.filepaths.temporary_directory = str(private_temp) + os.sep
assert Path(bpy.app.tempdir).resolve().is_relative_to(private_temp)
report = dict(schema='character-designer.hair-browser-gui/1', passed=False,
    projection=str(args.projection), projection_sha256=args.projection_sha,
    script_sha256=digest(__file__), fixture_sha256=BASE_SHA,
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
        item.checked = item.signature in wanted


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


def author_ok():
    source, rig = original()
    assert physical_author(source, rig) == EXPECTED_AUTHOR
    assert {name: base['scope']['curves'](bpy.data.actions[name])
            for name in EXPECTED_ACTIONS} == EXPECTED_ACTIONS
    for collection in variants.variants_for(source):
        mesh = collection.get(variants.MESH_KEY)
        if mesh is not None:
            assert base['chain_author'](mesh, SIGNATURES[2]) == UNTOUCHED


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


def history(label, call, *, reauthorize=False, checked_counts=None):
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
    assert bpy.ops.ed.undo() == {'FINISHED'}
    author_ok()
    assert state() == before, (label, 'undo', state(), before)
    reauthorized = False
    if reauthorize:
        assert wm_state().list_pin == latest_cache
        assert json.loads(latest_cache) != json.loads(frozen)['fingerprint']
        resolved = list_ui._authenticate(SimpleNamespace(action_pin=frozen), bpy.context,
                                          require_checked=True)
        assert resolved[0].name == json.loads(frozen)['fingerprint']['source']['name']
        reauthorized = True
    assert bpy.ops.ed.redo() == {'FINISHED'}
    author_ok()
    assert state() == after, (label, 'redo', state(), after)
    report['native_history'].append(dict(operation=label, undo=True, redo=True,
        captured_pin_authorized_against_restored_source=reauthorized, before=before, after=after))
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


class HAIRBROWSERQA_PT_native(bpy.types.Panel):
    bl_idname = 'HAIRBROWSERQA_PT_native'
    bl_label = 'Hair Browser Native QA'
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
            saved = (wm_state().list_pin, tuple((row.signature, row.checked) for row in wm_state().list_items),
                     source.get(registry.REGISTRY_KEY), source.get(profiles.PROFILE_KEY))
            # Rendering the browser cannot ask for geometry/native ownership proof.
            with patch.object(browser, 'strict_snapshot', side_effect=AssertionError('Draw attempted a strict mesh scan')):
                hair_bones.CHARACTERDESIGNER_PT_hair_bones.draw(self, context)
            assert saved == (wm_state().list_pin, tuple((row.signature, row.checked) for row in wm_state().list_items),
                             source.get(registry.REGISTRY_KEY), source.get(profiles.PROFILE_KEY))
            key = ('version' if service.is_version(source) else 'original',
                   source[hair.RIG_KEY].data.pose_position)
            if key not in DRAW_SEEN:
                DRAW_SEEN.add(key)
                report['draw_records'].append(dict(kind=key[0], pose_position=key[1],
                    source=source.name, rows=len(wm_state().list_items), native_ui_list=True,
                    draw_read_only=True))
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
    report['source_unchanged'] = all(digest(PACKAGE / name) == value
                                       for name, value in manifest['files'].items())
    report['passed'] = bool(len(report['native_history']) == 7
        and {('original', 'POSE'), ('version', 'POSE'), ('original', 'REST')} <= DRAW_SEEN
        and not report['errors'] and not report['cleanup_errors'] and report['source_unchanged'])
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    bpy.ops.wm.quit_blender()
    return None


def step():
    global STAGE, REGISTERED, PANEL_REGISTERED, READY
    global ORIGINAL_NAMES, SIGNATURES, EXPECTED_AUTHOR, EXPECTED_ACTIONS, UNTOUCHED
    global VERSION_SIX, VERSION_SEVEN
    try:
        if STAGE == 0:
            cd.register()
            REGISTERED = True
            source, rig, record = base['selected_fixture'](rich=False, automatic_pairs=False)
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
            # A frozen dialog scope cannot silently follow later check changes.
            pin = list_ui._capture(bpy.context)
            checks((SIGNATURES[1],))
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
            assert groups(original()[0])[first] == 'BACK'
            source = original()[0]
            list_ui.refresh_ui(bpy.context, source, checked_signatures=(first, second))
            after = history('Checked A and B to Six', lambda: bpy.ops.character_designer.hair_strand_list_rebuild(
                'EXEC_DEFAULT', bone_count=6, action_pin=''), reauthorize=True, checked_counts=(6, 6, 4))
            VERSION_SIX = after['visible'][0]
            six = bpy.data.collections[VERSION_SIX].get(variants.MESH_KEY)
            list_ui.refresh_ui(bpy.context, six, checked_signatures=(first,))
            after = history('Checked A to Seven', lambda: bpy.ops.character_designer.hair_strand_list_rebuild(
                'EXEC_DEFAULT', bone_count=7, action_pin=''), reauthorize=True, checked_counts=(7, 6, 4))
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
                    'EXEC_DEFAULT', bone_count=8, action_pin='')), checked_counts=(8, 4, 4))
            assert bpy.ops.character_designer.hair_show_original_version() == {'FINISHED'}
            source, rig = original()
            rig.data.pose_position = 'REST'
            list_ui.refresh_ui(bpy.context, source, checked_signatures=(SIGNATURES[0],))
            before_selection=tuple(pose.name for pose in rig.pose.bones if pose.select)
            before_active=rig.data.bones.active.name if rig.data.bones.active else None
            assert bpy.ops.character_designer.hair_strand_list_select_pose() == {'FINISHED'}
            assert rig.data.pose_position == 'REST'
            expected_names=next(chain['bones'] for chain in hair._read_records(source)['chains'] if chain['signature']==SIGNATURES[0])
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
