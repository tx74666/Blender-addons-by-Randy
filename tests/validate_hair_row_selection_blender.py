"""Focused native Hair row selection derivative; pinned fixtures stay unchanged.

Run with the trusted Blender launcher and one complete frozen projection.
Only private fixtures are created; this harness does not save or open a scene.
"""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import traceback
import unittest
from unittest.mock import patch

import bpy


REPO = Path(__file__).resolve().parents[1]
PREVIOUS = REPO / 'tests/validate_hair_strand_browser_blender.py'
PINNED = Path(r'D:/Blender/Projects/Character/X/validation/hair_strand_browser_20261029/harnesses/native_browser03.py')
PREVIOUS_SHA = 'e7195b1e424ba4920fdfdb53a7d6bd3dd43e7fba32772d852a93309610a5d16a'
digest = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
assert digest(PREVIOUS) == digest(PINNED) == PREVIOUS_SHA
previous = {'__file__': str(PREVIOUS), '__name__': 'hair_row_selection_pinned_fixture'}
exec(compile(PINNED.read_text(encoding='utf-8').split('\nresult = None\n', 1)[0],
             str(PREVIOUS), 'exec'), previous)
args, cd, hair, registry, profiles, service, PACKAGE, manifest = (
    previous[name] for name in ('args', 'cd', 'hair', 'registry', 'profiles',
                                'service', 'PACKAGE', 'manifest'))
base = previous['base']
from character_designer import hair_strand_browser as browser
from character_designer import hair_strand_list_ui as ui

METRICS = {}


def state():
    return bpy.context.window_manager.character_designer_hair_bones


def fixture(*, paired=False, gap_filter=False):
    source, rig, record = previous['fixture']()
    data = registry.read(source)
    if paired:
        first, second = data['strands'][:2]
        data = registry.manual_pair(source, first['strand_id'], second['strand_id'])
        profiles.reconcile(source, registry=data)
    if gap_filter:
        profiles.update(source, data['strands'][2]['strand_id'], group='FRONT', registry=data)
    ui.refresh_ui(bpy.context, source, checked_signatures=())
    state().list_filter = 'ALL'
    state().list_anchor_key = ''
    state().list_anchor_filter = ''
    return source, rig, record


def keys():
    return tuple(item.entry_key for item in state().list_items)


def selected_signatures():
    return set(ui._checked(state()))


def names_for(record, signatures):
    wanted = set(signatures)
    return {name for chain in record['chains'] if chain['signature'] in wanted
            for name in chain['bones']}


def selected_names(rig):
    return {pose.name for pose in rig.pose.bones if pose.select}


def click(key, *, extend=False, toggle=False, action_pin=None, filter_pin=None):
    return bpy.ops.character_designer.hair_strand_list_click(
        'EXEC_DEFAULT', entry_key=key, extend=extend, toggle=toggle,
        action_pin=ui._capture(bpy.context) if action_pin is None else action_pin,
        filter_pin=state().list_filter if filter_pin is None else filter_pin)


def native_state(rig):
    return dict(
        context=base['scope']['context_state'](),
        object_selection=tuple((obj.as_pointer(), obj.select_get())
                               for obj in bpy.context.scene.objects),
        bones=tuple((pose.name, bool(pose.select), bool(pose.bone.hide),
                     bool(pose.bone.hide_select)) for pose in rig.pose.bones),
        active=rig.data.bones.active.name if rig.data.bones.active else None,
        in_front=bool(rig.show_in_front), pose_position=rig.data.pose_position,
        collections=tuple((item.name, bool(item.is_visible), bool(item.is_solo))
                          for item in rig.data.collections_all),
    )


def ui_state():
    value = state()
    return dict(checked=tuple((item.entry_key, bool(item.checked)) for item in value.list_items),
                active=value.list_active_index, anchor=value.list_anchor_key,
                anchor_filter=value.list_anchor_filter, pin=value.list_pin,
                source=value.source.as_pointer() if value.source else None,
                filter=value.list_filter)


def protected(source, rig):
    return previous['physical_author'](source, rig), source[registry.REGISTRY_KEY], source[profiles.PROFILE_KEY]


def unchanged_scope(source, rig):
    return native_state(rig), ui_state(), protected(source, rig)


class LayoutRecorder:
    """Record actual Python draw calls, without creating an artist UI."""
    def __init__(self, events=None):
        self.events = [] if events is None else events

    def row(self, *args, **kwargs):
        return LayoutRecorder(self.events)

    column = row
    box = row
    split = row

    def label(self, *args, **kwargs):
        self.events.append(('label', args, kwargs))

    def prop(self, data, name, **kwargs):
        self.events.append(('prop', name, kwargs))

    def operator(self, identifier, **kwargs):
        self.events.append(('operator', identifier, kwargs))
        return SimpleNamespace()

    def template_list(self, *args, **kwargs):
        self.events.append(('template_list', args, kwargs))

    def separator(self, *args, **kwargs):
        pass


class HairRowSelectionNative(unittest.TestCase):
    def assert_scope(self, rig, record, signatures):
        self.assertEqual(selected_signatures(), set(signatures))
        self.assertEqual(selected_names(rig), names_for(record, signatures))

    def test_single_click_replaces_hidden_selection_and_preserves_authors_rest(self):
        source, rig, record = fixture()
        first, unused_second, third = (chain['signature'] for chain in record['chains'])
        ui.refresh_ui(bpy.context, source, checked_signatures=(third,))
        base['choose'](source, (third,), every_bone=True)
        state().list_filter = 'FRONT'
        rig.data.pose_position = 'REST'
        before = protected(source, rig)
        self.assertEqual(click(keys()[0]), {'FINISHED'})
        self.assert_scope(rig, record, (first,))
        self.assertEqual(rig.data.pose_position, 'REST')
        self.assertEqual(state().list_active_index, 0)
        self.assertEqual(state().list_anchor_key, keys()[0])
        self.assertEqual(state().list_anchor_filter, 'FRONT')
        self.assertEqual(protected(source, rig), before)
        METRICS[self._testMethodName] = dict(single_step_full_chain=True, hidden_scope_replaced=True,
                                            rest_pose_actions_weights_keys_physics_exact=True)

    def test_ctrl_add_remove_and_last_row_clear_native_selection(self):
        source, rig, record = fixture()
        first, second, unused_third = (chain['signature'] for chain in record['chains'])
        before = protected(source, rig)
        self.assertEqual(click(keys()[0]), {'FINISHED'})
        self.assertEqual(click(keys()[1], toggle=True), {'FINISHED'})
        self.assert_scope(rig, record, (first, second))
        self.assertEqual(click(keys()[0], toggle=True), {'FINISHED'})
        self.assert_scope(rig, record, (second,))
        self.assertEqual(click(keys()[1], toggle=True), {'FINISHED'})
        self.assert_scope(rig, record, ())
        self.assertIsNone(rig.data.bones.active)
        self.assertEqual(protected(source, rig), before)
        METRICS[self._testMethodName] = dict(ctrl_toggle_native_and_rows=True, final_empty_safe=True,
                                            empty_never_all=True, authors_exact=True)

    def test_shift_uses_visible_range_in_both_directions(self):
        source, rig, record = fixture(gap_filter=True)
        first, unused_second, third = (chain['signature'] for chain in record['chains'])
        state().list_filter = 'FRONT'
        before = protected(source, rig)
        self.assertEqual(click(keys()[0]), {'FINISHED'})
        self.assertEqual(click(keys()[2], extend=True), {'FINISHED'})
        self.assert_scope(rig, record, (first, third))
        self.assertEqual(click(keys()[2]), {'FINISHED'})
        self.assertEqual(click(keys()[0], extend=True), {'FINISHED'})
        self.assert_scope(rig, record, (first, third))
        self.assertEqual(protected(source, rig), before)
        METRICS[self._testMethodName] = dict(forward_reverse=True, filtered_middle_excluded=True,
                                            authors_exact=True)

    def test_ctrl_shift_adds_visible_range_to_existing_hidden_scope(self):
        source, rig, record = fixture(gap_filter=True)
        first, second, third = (chain['signature'] for chain in record['chains'])
        before = protected(source, rig)
        self.assertEqual(click(keys()[1]), {'FINISHED'})
        state().list_filter = 'FRONT'
        self.assertEqual(click(keys()[0], toggle=True), {'FINISHED'})
        self.assert_scope(rig, record, (first, second))
        self.assertEqual(click(keys()[2], extend=True, toggle=True), {'FINISHED'})
        self.assert_scope(rig, record, (first, second, third))
        self.assertEqual(protected(source, rig), before)
        METRICS[self._testMethodName] = dict(additive_visible_range=True, hidden_scope_retained=True,
                                            authors_exact=True)

    def test_pair_row_selects_both_complete_native_chains_and_keeps_batch_scope(self):
        source, rig, record = fixture(paired=True)
        first, second, third = (chain['signature'] for chain in record['chains'])
        pair = next(item for item in state().list_items if item.member_count == 2)
        self.assertEqual(len(state().list_items), 2)
        before = protected(source, rig)
        self.assertEqual(click(pair.entry_key), {'FINISHED'})
        self.assert_scope(rig, record, (first, second))
        self.assertEqual(len(selected_names(rig)), 8)
        self.assertEqual(protected(source, rig), before)
        physical = previous['effective_physical'](source)
        self.assertEqual(bpy.ops.character_designer.hair_strand_list_assign_category(
            'EXEC_DEFAULT', group='BACK', action_pin=ui._capture(bpy.context)), {'FINISHED'})
        self.assertEqual(previous['groups'](source), {first: 'BACK', second: 'BACK', third: 'BACK'})
        self.assertEqual(previous['effective_physical'](source), physical)
        self.assertEqual(selected_signatures(), {first, second})
        self.assertEqual(previous['physical_author'](source, rig), before[0])
        METRICS[self._testMethodName] = dict(logical_rows=2, physical_pair_selected=8,
                                            pair_batch_category_exact=True, physical_authors_exact=True)

    def test_import_selected_bone_retains_native_single_bone_and_clears_anchor(self):
        source, rig, record = fixture(paired=True)
        first, second, third = (chain['signature'] for chain in record['chains'])
        last_key = next(item.entry_key for item in state().list_items if item.member_count == 1)
        self.assertEqual(click(last_key), {'FINISHED'})
        base['choose'](source, (first,), every_bone=False)
        self.assertEqual(len(selected_names(rig)), 1)
        native_before = native_state(rig)
        before = protected(source, rig)
        self.assertEqual(bpy.ops.character_designer.hair_strand_list_check(
            'EXEC_DEFAULT', action='IMPORT_POSE', action_pin=ui._capture(bpy.context)), {'FINISHED'})
        self.assertEqual(selected_signatures(), {first, second})
        self.assertEqual(native_state(rig), native_before)
        self.assertEqual(protected(source, rig), before)
        self.assertEqual(state().list_anchor_key, '')
        self.assertEqual(state().list_anchor_filter, '')
        METRICS[self._testMethodName] = dict(single_bone_preserved=True, complete_pair_rows_imported=True,
                                            context_visibility_active_exact=True, anchor_reset=True)

    def test_stale_pin_filter_and_target_refuse_without_mutation(self):
        for condition in ('fingerprint', 'filter', 'target', 'hidden_target'):
            with self.subTest(condition=condition):
                source, rig, record = fixture()
                first = record['chains'][0]['signature']
                self.assertEqual(click(keys()[0]), {'FINISHED'})
                pin, category, key = ui._capture(bpy.context), state().list_filter, keys()[1]
                if condition == 'fingerprint':
                    data = registry.read(source)
                    profiles.update(source, data['strands'][0]['strand_id'],
                                    overrides={'stretch': .17}, registry=data)
                elif condition == 'filter':
                    state().list_filter = 'FRONT'
                elif condition == 'target':
                    key = 'strand:missing-entry'
                else:
                    state().list_filter = category = 'FRONT'
                before = unchanged_scope(source, rig)
                with patch.object(ui, '_select_list_chains', side_effect=AssertionError(
                        'A refused scope reached native selection')) as selector:
                    previous['refused'](lambda: click(key, action_pin=pin, filter_pin=category))
                    self.assertFalse(selector.called)
                self.assertEqual(unchanged_scope(source, rig), before)
                self.assertEqual(selected_signatures(), {first})
        METRICS[self._testMethodName] = dict(stale_fingerprint_filter_target_hidden_refused=True,
                                            native_cache_anchor_authors_exact=True)

    def test_native_selection_failure_rolls_back_full_context_and_row_scope(self):
        source, rig, record = fixture()
        self.assertEqual(click(keys()[0]), {'FINISHED'})
        rig.show_in_front = False
        target = rig.data.bones[record['chains'][1]['bones'][0]]
        target.hide = True
        before = unchanged_scope(source, rig)
        real_selector = ui._select_list_chains

        def fail_after_native(context, armature, names, active_name=None):
            real_selector(context, armature, names, active_name=active_name)
            armature.show_in_front = not before[0]['in_front']
            armature.data.bones[record['chains'][0]['bones'][0]].hide = True
            raise RuntimeError('Injected row-selection failure after native changes')

        with patch.object(ui, '_select_list_chains', side_effect=fail_after_native) as selector:
            previous['refused'](lambda: click(keys()[1]))
            self.assertEqual(selector.call_count, 1)
        self.assertEqual(unchanged_scope(source, rig), before)
        METRICS[self._testMethodName] = dict(injected_after_native=True,
                                            mode_selection_active_visibility_rows_anchor_exact=True)

    def test_draw_uses_highlighted_row_button_and_keeps_selected_bones_import(self):
        source, rig, record = fixture()
        self.assertEqual(click(keys()[0]), {'FINISHED'})
        before = unchanged_scope(source, rig)
        layout = LayoutRecorder()
        ui.draw_browser(layout, bpy.context, source, True)
        list_type = ui.CHARACTERDESIGNER_UL_hair_strand_list
        for index, item in enumerate(state().list_items):
            list_type.draw_item(None, bpy.context, layout, state(), item, 0,
                                state(), 'list_active_index', index)
        operators = [event for event in layout.events if event[0] == 'operator']
        self.assertFalse(any(event[1] == 'checked' for event in layout.events if event[0] == 'prop'))
        self.assertFalse(any(event[1] == 'character_designer.hair_strand_list_select_pose'
                             or event[2].get('text') == 'Select Checked' for event in operators))
        self.assertTrue(any(event[2].get('text') == 'Check Selected Bones' for event in operators))
        row_buttons = [event for event in operators
                       if event[1] == 'character_designer.hair_strand_list_click']
        self.assertEqual(len(row_buttons), len(state().list_items))
        self.assertEqual([bool(event[2].get('depress')) for event in row_buttons], [True, False, False])
        self.assertEqual(unchanged_scope(source, rig), before)
        METRICS[self._testMethodName] = dict(checkbox_absent=True, select_checked_absent=True,
                                            selected_bones_import_present=True, highlighted_row_buttons=True,
                                            actual_draw_readonly=True, physical_mouse_click_not_tested=True)

    def test_edit_mode_refuses_before_native_mutation(self):
        source, rig, record = fixture()
        self.assertEqual(click(keys()[0]), {'FINISHED'})
        self.assertEqual(bpy.ops.object.mode_set(mode='EDIT'), {'FINISHED'})
        before = unchanged_scope(source, rig)
        with patch.object(ui, '_select_list_chains', side_effect=AssertionError(
                'Edit Mode reached native selection')) as selector:
            previous['refused'](lambda: click(keys()[1]))
            self.assertFalse(selector.called)
        self.assertEqual(unchanged_scope(source, rig), before)
        self.assertEqual(bpy.ops.object.mode_set(mode='POSE'), {'FINISHED'})
        METRICS[self._testMethodName] = dict(edit_mode_refused=True, context_and_native_data_exact=True)


result = None
registered = False
report = dict(
    schema='x2.hair-row-selection.native/1', passed=False,
    projection=str(args.projection), projection_sha256=args.projection_sha,
    script_sha256=digest(__file__), inherited_harness_sha256=PREVIOUS_SHA,
    metrics=METRICS, errors=[], cleanup_errors=[], artist_file_opened=False,
    preferences_saved=False, private_scene_save=False,
    limits=['Private three-strand native fixtures only; no artist/full-character/dynamics/FPS acceptance.',
            'EXEC_DEFAULT modifier properties and actual Python draw calls; no physical mouse/key acceptance.',
            'Original pinned fixtures are reused unchanged; their full suites are not rerun by this derivative.'],
)
try:
    cd.register()
    registered = True
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(HairRowSelectionNative))
except BaseException:
    report['errors'].append(traceback.format_exc())
finally:
    if registered:
        try:
            if bpy.context.object and bpy.context.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            cd.unregister()
        except BaseException:
            report['cleanup_errors'].append(traceback.format_exc())
    report.update(
        tests_run=result.testsRun if result else 0,
        failures=[(str(case), error) for case, error in result.failures] if result else [],
        test_errors=[(str(case), error) for case, error in result.errors] if result else [],
        source_unchanged=all(digest(PACKAGE / name) == value for name, value in manifest['files'].items()),
        pinned_harnesses_unchanged=digest(PREVIOUS) == digest(PINNED) == PREVIOUS_SHA,
    )
    report['passed'] = bool(result and result.wasSuccessful() and not report['errors']
                            and not report['cleanup_errors'] and report['source_unchanged']
                            and report['pinned_harnesses_unchanged'])
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
if not report['passed']:
    raise RuntimeError('Hair row selection native acceptance failed; retain this report.')
