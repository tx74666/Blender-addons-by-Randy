"""Focused native Hair display inheritance, using pinned independent strands.

Run on a complete frozen projection through the trusted isolated background
launcher. No artist file, preferences save, scene save or live refresh is used.
"""
from pathlib import Path
import copy
import hashlib
import json
import sys
import traceback
import unittest
from unittest.mock import patch

import bpy

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / 'tests/validate_hair_resegment_selected_blender.py'
FIXTURE_SHA = '2fd7e73fced03c8993250947da5c6dd9c58d0559f6b5065c7b54bbde8aa34d25'
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
assert sha(FIXTURE) == FIXTURE_SHA
scope = {'__file__': str(FIXTURE), '__name__': 'hair_color_pinned_fixture'}
exec(compile(FIXTURE.read_text(encoding='utf-8').split('\nresult = None\n', 1)[0],
             str(FIXTURE), 'exec'), scope)
args, cd, service, hair, registry, profiles, PACKAGE, manifest = (
    scope[key] for key in ('args', 'cd', 'service', 'hair', 'registry', 'profiles', 'PACKAGE', 'manifest'))
from character_designer import bone_color_palette as palette, control_colors, hair_replace as replace
from character_designer import hair_replace_authority as authority


def color_state(pose):
    native = pose.bone.color
    return dict(color=native.palette,
        color_custom=tuple(tuple(getattr(native.custom, field)) for field in palette.CHANNELS),
        color_constraints=bool(native.custom.show_colored_constraints),
        pose_color_state=control_colors.capture_bone(pose))


def colors(rig):
    return {pose.name: color_state(pose) for pose in rig.pose.bones}


def author(source, rig):
    value = scope['scope']['source_author'](source, rig)
    value['rig']['rest'] = tuple(sorted(value['rig']['rest']))
    value['rig']['pose'] = tuple(sorted(value['rig']['pose']))
    value['rig']['collections'] = tuple((name, parent, visible, solo, tuple(sorted(bones)), props)
        for name, parent, visible, solo, bones, props in value['rig']['collections'])
    return value, colors(rig)


def fixture():
    source, rig, record = scope['selected_fixture'](rich=False, automatic_pairs=False)
    # The actual palette API establishes its own recovery ownership and saved
    # group choices; later manual native/Pose edits remain authoritative.
    palette.apply_palette(rig, bpy.context)
    for ci, chain in enumerate(record['chains']):
        for bi, name in enumerate(chain['bones']):
            pose = rig.pose.bones[name]
            pose.color.palette = 'DEFAULT' if bi == 1 else 'CUSTOM'
            pose.color.custom.normal = (.12 + ci * .15, .16 + bi * .12, .38)
            pose.color.custom.select = (.45, .61, .77)
            pose.color.custom.active = (.78, .86, .94)
            pose.color.custom.show_colored_constraints = bi % 2 == 0
            pose.bone.color.palette = 'THEME03' if bi == 1 else 'CUSTOM'
            pose.bone.color.custom.normal = (.1 + ci * .2, .2 + bi * .1, .43)
            pose.bone.color.custom.select = (.42, .56, .67)
            pose.bone.color.custom.active = (.72, .85, .93)
            pose.bone.color.custom.show_colored_constraints = bi % 2 == 1
    return source, rig, record


def selected(record):
    return (record['chains'][0]['signature'], record['chains'][2]['signature'])


def create(source, record, count):
    return service.create(bpy.context, source, bone_count=count, frame_start=1, frame_end=1,
                          activate=False, strand_signatures=selected(record))


def ids():
    return scope['scope']['ids']()


class HairColorInheritance(unittest.TestCase):
    def test_same_count_exact_native_pose_and_backups(self):
        source, rig, record = fixture()
        before = author(source, rig)
        result = create(source, record, 4)
        self.assertEqual(author(source, rig), before)
        output = result['armature']
        for old, new in zip(record['chains'], hair._read_records(result['mesh'])['chains']):
            self.assertEqual([color_state(output.pose.bones[name]) for name in new['bones']],
                             [color_state(rig.pose.bones[name]) for name in old['bones']])
        self.assertEqual(output.data.show_bone_colors, rig.data.show_bone_colors)

    def test_legacy_v1_palette_and_backup_raw_values_are_inherited(self):
        source, rig, record = fixture()
        saved = palette._read(rig)
        saved['version'] = 1
        for group in palette.HAIR_GROUPS:
            saved['groups'].pop(group)
        rig[palette.CONFIG_KEY] = json.dumps(saved)
        for chain in record['chains']:
            for name in chain['bones']:
                pose = rig.pose.bones[name]
                backup = json.loads(pose[palette.BACKUP_KEY])
                backup['group'] = 'HAIR'
                pose[palette.BACKUP_KEY] = json.dumps(backup)
        before = author(source, rig)
        result = create(source, record, 6)
        self.assertEqual(author(source, rig), before)
        self.assertEqual(result['armature'][palette.CONFIG_KEY], rig[palette.CONFIG_KEY])
        for old, new in zip(record['chains'], hair._read_records(result['mesh'])['chains']):
            count = len(new['bones'])
            self.assertEqual([color_state(result['armature'].pose.bones[name]) for name in new['bones']],
                             service._chain_color_states(rig, old['bones'], count))

    def test_changed_counts_inherit_same_strand_depth(self):
        for count in (2, 6):
            with self.subTest(count=count):
                source, rig, record = fixture()
                before = author(source, rig)
                expected = {chain['signature']: service._chain_color_states(rig, chain['bones'], count)
                            for chain in record['chains'] if chain['signature'] in selected(record)}
                result = create(source, record, count)
                self.assertEqual(author(source, rig), before)
                updated = hair._read_records(result['mesh'])
                for old, new in zip(record['chains'], updated['chains']):
                    wanted = expected.get(old['signature'])
                    if wanted is None:
                        wanted = [color_state(rig.pose.bones[name]) for name in old['bones']]
                    self.assertEqual([color_state(result['armature'].pose.bones[name]) for name in new['bones']], wanted)

    def test_same_count_current_replacement_then_palette_restoration(self):
        source, rig, record = fixture()
        source_data, rig_data = source.data, rig.data
        raw_config = rig[palette.CONFIG_KEY]
        expected = {chain['signature']: [color_state(rig.pose.bones[name]) for name in chain['bones']]
                    for chain in record['chains']}
        restored = {chain['signature']: [palette._backup(rig.pose.bones[name]) for name in chain['bones']]
                    for chain in record['chains']}
        outcome = service.replace_selected(bpy.context, source, bone_count=4,
            frame_start=1, frame_end=1, activate=False, strand_signatures=selected(record))
        self.assertEqual(outcome['mode'], 'REPLACED')
        self.assertIs(source.data, source_data)
        self.assertIs(rig.data, rig_data)
        self.assertEqual(rig[palette.CONFIG_KEY], raw_config)
        updated = hair._read_records(source)
        for chain in updated['chains']:
            self.assertEqual([color_state(rig.pose.bones[name]) for name in chain['bones']], expected[chain['signature']])
        palette.restore_palette(rig, bpy.context)
        for chain in updated['chains']:
            for name, before in zip(chain['bones'], restored[chain['signature']]):
                pose = rig.pose.bones[name]
                self.assertEqual(control_colors._color_state(pose), before['color'])
                self.assertEqual(pose.get(control_colors.BACKUP_KEY), before['control_backup'])
                self.assertNotIn(palette.BACKUP_KEY, pose)

    def test_existing_gray_version_keeps_latest_current_colors(self):
        source, rig, record = fixture()
        result = create(source, record, 6)
        sample = result['armature']
        sample_record = hair._read_records(result['mesh'])
        for chain in sample_record['chains']:
            if chain['signature'] in selected(record):
                for name in chain['bones']:
                    pose = sample.pose.bones[name]
                    pose.color.palette = pose.bone.color.palette = 'DEFAULT'
                    pose.pop(palette.BACKUP_KEY, None)
                    pose.pop(control_colors.BACKUP_KEY, None)
        # A real edit made after sampling must win over an existing version.
        palette.edit_group(rig, 'HAIR_FRONT', dict(normal=(.27, .49, .74),
            select=(.42, .65, .88), active=(.71, .86, .98)), bpy.context)
        rig.pose.bones[record['chains'][2]['bones'][1]].color.custom.normal = (.61, .24, .15)
        expected = {chain['signature']: service._chain_color_states(rig, chain['bones'], 6)
                    for chain in record['chains'] if chain['signature'] in selected(record)}
        untouched = [color_state(rig.pose.bones[name]) for name in record['chains'][1]['bones']]
        sample_before = author(result['mesh'], sample)
        outcome = replace.replace_from_version(bpy.context, source, result['mesh'], selected(record),
                                               activate=False, import_existing=True)
        self.assertEqual(outcome['mode'], 'REPLACED')
        self.assertEqual(author(result['mesh'], sample), sample_before)
        for chain in hair._read_records(source)['chains']:
            wanted = expected.get(chain['signature'], untouched)
            self.assertEqual([color_state(rig.pose.bones[name]) for name in chain['bones']], wanted)

    def test_corrupt_palette_backup_refuses_without_leaks(self):
        source, rig, record = fixture()
        pose = rig.pose.bones[record['chains'][0]['bones'][0]]
        data = json.loads(pose[palette.BACKUP_KEY])
        data['owner'] = 'foreign-test-owner'
        pose[palette.BACKUP_KEY] = json.dumps(data)
        before, before_ids = author(source, rig), ids()
        with self.assertRaisesRegex(ValueError, 'another character palette'):
            create(source, record, 6)
        self.assertEqual(author(source, rig), before)
        self.assertEqual(ids(), before_ids)

    def test_copy_color_failure_cleans_owned_ids(self):
        source, rig, record = fixture()
        before, before_ids = author(source, rig), ids()
        real, calls = service._restore_chain_color, []
        def fail(pose, state):
            calls.append(pose.name)
            real(pose, state)
            if len(calls) == 2:
                raise RuntimeError('injected color inheritance failure')
        with patch.object(service, '_restore_chain_color', side_effect=fail):
            with self.assertRaisesRegex(ValueError, 'injected color inheritance failure'):
                create(source, record, 6)
        self.assertEqual(author(source, rig), before)
        self.assertEqual(ids(), before_ids)

    def test_replacement_failure_restores_both_color_levels_and_backups(self):
        source, rig, record = fixture()
        result = create(source, record, 6)
        before, before_ids = author(source, rig), ids()
        sample_before = author(result['mesh'], result['armature'])
        with patch.object(authority, 'commit', side_effect=RuntimeError('injected replacement commit failure')):
            with self.assertRaisesRegex(ValueError, 'injected replacement commit failure') as caught:
                replace.replace_from_version(bpy.context, source, result['mesh'], selected(record),
                                             activate=False, import_existing=True)
        self.assertTrue(caught.exception.recovery['values_restored'])
        self.assertEqual(author(source, rig), before)
        self.assertEqual(author(result['mesh'], result['armature']), sample_before)
        self.assertEqual(ids(), before_ids)


result = None
registered = False
report = dict(schema='character-designer.hair-color-inheritance-native/1', passed=False,
    projection=str(args.projection), projection_sha256=args.projection_sha,
    source_package=str(PACKAGE), harness_sha256=sha(__file__),
    pinned_fixture=str(FIXTURE), pinned_fixture_sha256=FIXTURE_SHA,
    artist_file_opened=False, scene_or_preferences_saved=False, autoexec_enabled=True,
    errors=[], cleanup_errors=[],
    limits=['Synthetic native three-strand fixture; no artist, solver or FPS certification.',
            'Changed-count colors use deterministic same-strand Rest depth; no new vertex/weight identity claim.',
            'Retained version colors remain unchanged; current character colors win on selected replacement.'])
try:
    cd.register()
    registered = True
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(HairColorInheritance))
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
    report.update(tests_run=result.testsRun if result else 0,
        failures=[(str(case), error) for case, error in result.failures] if result else [],
        test_errors=[(str(case), error) for case, error in result.errors] if result else [],
        source_unchanged=all(sha(PACKAGE / name) == digest for name, digest in manifest['files'].items()))
    report['passed'] = bool(result and result.wasSuccessful() and not report['errors']
                           and not report['cleanup_errors'] and report['source_unchanged'])
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
if not report['passed']:
    raise RuntimeError('Hair color inheritance acceptance failed; retain this report.')
