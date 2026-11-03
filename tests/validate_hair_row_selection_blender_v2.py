"""Selection-aware derivative of immutable Hair row native01 acceptance.

The first harness compared PoseBone.select as part of its generic rest-author
witness, although row selection intentionally changes exactly that flag.  This
derivative masks only that explicit tuple field; native selection assertions,
failure snapshots and every rest/pose/Action/physics field remain exact.
"""
from pathlib import Path
import copy
import hashlib
import json
import traceback
import unittest
from unittest.mock import patch

import bpy


REPO = Path(__file__).resolve().parents[1]
V1 = REPO / 'tests/validate_hair_row_selection_blender.py'
V1_SHA = '7b00875c273536f68de181895b47a58e3afea1bfa715142b66355cb23796dbe3'
digest = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
assert digest(V1) == V1_SHA
inherited = {'__file__': str(V1), '__name__': 'hair_row_selection_native01_immutable'}
exec(compile(V1.read_text(encoding='utf-8').split('\nresult = None\n', 1)[0],
             str(V1), 'exec'), inherited)
args, cd, hair, registry, profiles, PACKAGE, manifest, previous, base, ui, METRICS = (
    inherited[name] for name in ('args', 'cd', 'hair', 'registry', 'profiles',
                                 'PACKAGE', 'manifest', 'previous', 'base', 'ui', 'METRICS'))
fixture, keys, click, protected, unchanged_scope, native_state = (
    inherited[name] for name in ('fixture', 'keys', 'click', 'protected',
                                 'unchanged_scope', 'native_state'))

AUTHOR_SELECTION_PATH = "['source']['rig']['rest'][bone_index][9]"
original_physical_author = previous['physical_author']


def physical_author_selection_scope(source, rig):
    """Whitelist only PoseBone.select, checked independently by native_state."""
    value = copy.deepcopy(original_physical_author(source, rig))
    rows = value['source']['rig']['rest']
    assert len(rows) == len(rig.data.bones)
    result = []
    for row, bone in zip(rows, rig.data.bones):
        # Pinned rig_author layout: hide=7, hide_select=8, select=9,
        # matrix_local=10, length=11; no other field is altered here.
        assert len(row) == 13 and row[0] == bone.name and type(row[9]) is bool
        assert row[9] == bool(rig.pose.bones[bone.name].select)
        result.append((*row[:9], '$POSE_SELECTION_CHECKED_SEPARATELY', *row[10:]))
    value['source']['rig']['rest'] = tuple(result)
    return value


# The immutable V1 methods call this witness through their existing fixture
# namespace, including their direct physical-author check after pair assignment.
# Their structural selection/context snapshots and exact scope assertions are
# unchanged.  No immutable fixture source file is modified.
previous['physical_author'] = physical_author_selection_scope


class HairRowSelectionNativeV2(inherited['HairRowSelectionNative']):
    def test_single_pose_click_preserves_unkeyed_pose_actions_and_frame(self):
        source, rig, record = fixture()
        first, unused_second, third = (chain['signature'] for chain in record['chains'])
        base['choose'](source, (first,), every_bone=False)
        self.assertEqual(bpy.context.mode, 'POSE')
        self.assertEqual(len(inherited['selected_names'](rig)), 1)
        frame = (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe)
        before = protected(source, rig)
        self.assertEqual(click(keys()[2]), {'FINISHED'})
        self.assert_scope(rig, record, (third,))
        self.assertEqual(bpy.context.mode, 'POSE')
        self.assertEqual(rig.data.pose_position, 'POSE')
        self.assertEqual((bpy.context.scene.frame_current, bpy.context.scene.frame_subframe), frame)
        self.assertEqual(protected(source, rig), before)
        METRICS[self._testMethodName] = dict(single_pose_bone_to_full_other_chain=True,
                                            unkeyed_pose_rest_actions_physics_frame_exact=True)

    def test_object_mode_failure_restores_original_mesh_and_native_state(self):
        source, rig, record = fixture()
        self.assertEqual(click(keys()[0]), {'FINISHED'})
        base['activate'](source, 'OBJECT')
        self.assertIs(bpy.context.object, source)
        self.assertEqual(bpy.context.mode, 'OBJECT')
        before_success = protected(source, rig)
        self.assertEqual(click(keys()[2]), {'FINISHED'})
        self.assertIs(bpy.context.object, rig)
        self.assertEqual(bpy.context.mode, 'POSE')
        self.assert_scope(rig, record, (record['chains'][2]['signature'],))
        self.assertEqual(protected(source, rig), before_success)
        base['activate'](source, 'OBJECT')
        rig.show_in_front = False
        rig.data.bones[record['chains'][1]['bones'][0]].hide = True
        before = unchanged_scope(source, rig)
        real_selector = ui._select_list_chains

        def fail_after_mode_change(context, armature, names, active_name=None):
            real_selector(context, armature, names, active_name=active_name)
            self.assertIs(context.object, rig)
            self.assertEqual(context.mode, 'POSE')
            raise RuntimeError('Injected row failure after Object-to-Pose transition')

        with patch.object(ui, '_select_list_chains', side_effect=fail_after_mode_change) as selector:
            previous['refused'](lambda: click(keys()[1]))
            self.assertEqual(selector.call_count, 1)
        self.assertEqual(unchanged_scope(source, rig), before)
        self.assertIs(bpy.context.object, source)
        self.assertEqual(bpy.context.mode, 'OBJECT')
        METRICS[self._testMethodName] = dict(single_object_to_pose_success=True,
                                            object_to_pose_native_failure=True,
                                            original_mesh_mode_selection_visibility_scope_authors_exact=True)

    def test_multi_object_pose_refuses_without_touching_either_rig(self):
        source, rig, record = fixture()
        self.assertEqual(click(keys()[0]), {'FINISHED'})
        self.assertEqual(bpy.ops.object.mode_set(mode='OBJECT'), {'FINISHED'})
        other = rig.copy()
        other.data = rig.data.copy()
        other.name = 'Other Artist Pose Rig'
        bpy.context.scene.collection.objects.link(other)
        for obj in bpy.context.view_layer.objects:
            obj.select_set(obj in {rig, other})
        bpy.context.view_layer.objects.active = rig
        self.assertEqual(bpy.ops.object.mode_set(mode='POSE'), {'FINISHED'})
        self.assertEqual(set(bpy.context.objects_in_mode), {rig, other})
        self.assertIs(ui._hair()._source(bpy.context), source)
        browser = inherited['browser']
        browser.strict_snapshot(source)
        before = unchanged_scope(source, rig), native_state(other)
        with patch.object(ui, '_select_list_chains', side_effect=AssertionError(
                'Multi-object Pose Mode reached native selection')) as selector:
            previous['refused'](lambda: click(keys()[1]))
            self.assertFalse(selector.called)
        self.assertIn('alone in Pose Mode', inherited['state']().last_message)
        self.assertEqual((unchanged_scope(source, rig), native_state(other)), before)
        METRICS[self._testMethodName] = dict(two_native_pose_rigs=True,
                                            explicit_preflight_refusal=True, both_rigs_context_scope_exact=True)


result = None
registered = False
report = dict(
    schema='x2.hair-row-selection.native/2', passed=False,
    projection=str(args.projection), projection_sha256=args.projection_sha,
    script_sha256=digest(__file__), inherited_row_harness_sha256=V1_SHA,
    inherited_browser_harness_sha256=inherited['PREVIOUS_SHA'],
    author_witness_allowed_differences=[dict(path=AUTHOR_SELECTION_PATH,
        field='bool(rig.pose.bones[b.name].select)', reason='Explicit list row selection changes this flag.',
        independent_proof='V1 assert_scope and native_state remain exact; rollback/refusal compare exact native selection.')],
    metrics=METRICS, errors=[], cleanup_errors=[], artist_file_opened=False,
    preferences_saved=False, private_scene_save=False,
    limits=['Private rich three-strand native fixtures only; no artist/full-character/dynamics/FPS acceptance.',
            'EXEC_DEFAULT modifier properties and actual Python draw calls; no physical mouse/key acceptance.',
            'Native01 four author-witness failures remain retained; this is a distinct identified derivative.',
            'Generic author witness masks PoseBone.select only; native scope/rollback/import selection is checked exactly.'],
)
try:
    cd.register()
    registered = True
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(HairRowSelectionNativeV2))
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
        pinned_harnesses_unchanged=(digest(V1) == V1_SHA
            and digest(inherited['PREVIOUS']) == digest(inherited['PINNED']) == inherited['PREVIOUS_SHA']),
    )
    report['passed'] = bool(result and result.wasSuccessful() and not report['errors']
                            and not report['cleanup_errors'] and report['source_unchanged']
                            and report['pinned_harnesses_unchanged'])
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
if not report['passed']:
    raise RuntimeError('Hair row selection native v2 acceptance failed; retain this report.')
