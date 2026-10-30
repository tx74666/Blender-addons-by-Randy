"""Focused Hair category/list native acceptance; immutable 125 fixture reuse."""
from pathlib import Path
import copy
import hashlib
import json
import traceback
import unittest
from unittest.mock import patch
import bpy

BASE = Path(__file__).with_name('validate_hair_resegment_selected_blender.py')
BASE_SHA = '2fd7e73fced03c8993250947da5c6dd9c58d0559f6b5065c7b54bbde8aa34d25'
digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert digest(BASE) == BASE_SHA
base = {'__file__': str(BASE), '__name__': 'hair_browser_fixture', 'GUI': globals().get('GUI', False)}
exec(compile(BASE.read_text(encoding='utf-8').split('\nresult = None\n', 1)[0], str(BASE), 'exec'), base)
args, cd, hair, registry, profiles, service, PACKAGE, manifest = (
    base[n] for n in ('args','cd','hair','registry','profiles','service','PACKAGE','manifest'))
from character_designer import hair_strand_browser as browser
from character_designer import hair_strand_list_ui as list_ui

METRICS = {}


def fixture():
    return base['selected_fixture'](rich=True, nla=True, automatic_pairs=False)


def physical_author(source, rig):
    value = base['original_authors'](source, rig)
    value['browser_source_properties'] = {key: base['normalized'](item, source, rig)
        for key,item in source.items() if key not in (registry.REGISTRY_KEY, profiles.PROFILE_KEY)}
    return value


def effective_physical(source):
    data = registry.read(source)
    return {row['signature']: {k:v for k,v in profiles.effective(source,row['strand_id'],registry=data).items()
                             if k not in ('group','sync_mirror')} for row in data['strands']}


def groups(source):
    return {row['signature']:row['group'] for row in browser.list_rows(source)}


def refused(call):
    try:
        result = call()
    except (ValueError, RuntimeError):
        return
    assert result == {'CANCELLED'}, result


def checks(signatures):
    wanted = set(signatures)
    state = bpy.context.window_manager.character_designer_hair_bones
    for row in state.list_items:
        row.checked = row.signature in wanted


def checked():
    return tuple(row.signature for row in bpy.context.window_manager.character_designer_hair_bones.list_items if row.checked)


class BrowserNative(unittest.TestCase):
    def test_read_only_category_physical_preservation_and_noop(self):
        source, rig, record = fixture()
        before = physical_author(source, rig)
        raw = source[registry.REGISTRY_KEY], source[profiles.PROFILE_KEY]
        context = base['scope']['context_state']()
        snap = browser.strict_snapshot(source)
        self.assertEqual([row['count'] for row in snap['rows']], [4,4,4])
        self.assertEqual(tuple(browser.list_rows(source)), snap['rows'])
        self.assertEqual((source[registry.REGISTRY_KEY],source[profiles.PROFILE_KEY]),raw)
        self.assertEqual(physical_author(source,rig), before)
        self.assertEqual(base['scope']['context_state'](),context)
        old = profiles.read(source)
        values = effective_physical(source)
        sig = record['chains'][0]['signature']
        target_id = snap['rows'][0]['strand_id']
        rig.data.pose_position = 'REST'
        result = browser.assign_category(bpy.context,source,(sig,),'BACK',expected_fingerprint=snap['fingerprint'])
        self.assertTrue(result['changed'])
        self.assertEqual(rig.data.pose_position,'REST')
        self.assertEqual(effective_physical(source), values)
        updated = profiles.read(source)
        self.assertEqual(updated['group_defaults'],old['group_defaults'])
        for key,value in old['strands'].items():
            if key != target_id: self.assertEqual(updated['strands'][key],value)
        self.assertEqual(source[registry.REGISTRY_KEY],raw[0])
        rig.data.pose_position = 'POSE'
        self.assertEqual(physical_author(source,rig),before)
        after = source[profiles.PROFILE_KEY]
        self.assertFalse(browser.assign_category(bpy.context,source,(sig,),'BACK')['changed'])
        self.assertEqual(source[profiles.PROFILE_KEY],after)
        METRICS[self._testMethodName] = dict(current_count=4,rest_assignment=True,motion_depth_exact=True,unchecked_exact=True)

    def test_synced_pair_requires_explicit_both_and_keeps_parameters(self):
        source,rig,record = fixture()
        data = registry.read(source)
        a,b,c = data['strands']
        data = registry.manual_pair(source,a['strand_id'],b['strand_id'])
        profiles.reconcile(source,registry=data)
        profiles.update(source,a['strand_id'],group='FRONT',sync_mirror=True,
                        overrides={'recovery':.72,'damping':.68,'gravity':1.7,'stretch':.12},registry=data)
        before = physical_author(source,rig)
        raw = source[profiles.PROFILE_KEY]
        values = effective_physical(source)
        with self.assertRaises((ValueError,RuntimeError)):
            browser.assign_category(bpy.context,source,(a['signature'],),'BACK')
        self.assertEqual(source[profiles.PROFILE_KEY],raw)
        result=browser.assign_category(bpy.context,source,(a['signature'],b['signature']),'BACK')
        self.assertTrue(result['changed'])
        self.assertEqual(effective_physical(source),values)
        self.assertEqual(physical_author(source,rig),before)
        self.assertEqual(groups(source)[a['signature']],'BACK')
        self.assertEqual(groups(source)[b['signature']],'BACK')
        METRICS[self._testMethodName]=dict(single_refused=True,both_selected=True,physical_exact=True)

    def test_missing_metadata_is_read_only_and_fault_rolls_back_absence(self):
        source,rig,record=fixture()
        for key in (profiles.PROFILE_KEY,registry.REGISTRY_KEY): del source[key]
        before=physical_author(source,rig)
        context=base['scope']['context_state']()
        snap=browser.strict_snapshot(source)
        self.assertTrue(all(row['group']=='UNASSIGNED' for row in snap['rows']))
        self.assertNotIn(registry.REGISTRY_KEY,source)
        self.assertNotIn(profiles.PROFILE_KEY,source)
        with patch.object(browser,'strict_snapshot',side_effect=RuntimeError('injected postcommit readback failure')):
            with self.assertRaises(RuntimeError):
                browser.assign_category(bpy.context,source,(record['chains'][0]['signature'],),'FRONT')
        self.assertNotIn(registry.REGISTRY_KEY,source)
        self.assertNotIn(profiles.PROFILE_KEY,source)
        self.assertEqual(physical_author(source,rig),before)
        self.assertEqual(base['scope']['context_state'](),context)
        browser.assign_category(bpy.context,source,(record['chains'][0]['signature'],),'FRONT')
        self.assertIn(registry.REGISTRY_KEY,source)
        self.assertIn(profiles.PROFILE_KEY,source)
        self.assertEqual(physical_author(source,rig),before)
        METRICS[self._testMethodName]=dict(read_without_writes=True,rollback_absence_exact=True,commit_initializes=True)

    def test_empty_foreign_duplicate_and_stale_fingerprint_refuse(self):
        source,rig,record=fixture()
        snap=browser.strict_snapshot(source)
        sig=record['chains'][0]['signature']
        raw=source[profiles.PROFILE_KEY]
        before=physical_author(source,rig)
        for signatures in ((),('not-owned',),(sig,sig)):
            with self.assertRaises((ValueError,RuntimeError)):
                browser.assign_category(bpy.context,source,signatures,'BACK')
        self.assertEqual(source[profiles.PROFILE_KEY],raw)
        profiles.update(source,snap['rows'][0]['strand_id'],overrides={'stretch':.19},registry=registry.read(source))
        raw=source[profiles.PROFILE_KEY]
        with self.assertRaises((ValueError,RuntimeError)):
            browser.assign_category(bpy.context,source,(sig,),'BACK',expected_fingerprint=snap['fingerprint'])
        self.assertEqual(source[profiles.PROFILE_KEY],raw)
        self.assertEqual(physical_author(source,rig),before)
        METRICS[self._testMethodName]=dict(empty_foreign_duplicate_stale=True)

    def test_ui_filters_hidden_checks_pose_import_and_stale_guard(self):
        source,rig,record=fixture()
        a,b,c=[row['signature'] for row in record['chains']]
        state=bpy.context.window_manager.character_designer_hair_bones
        list_ui.refresh_ui(bpy.context,source,checked_signatures=(b,))
        before=physical_author(source,rig)
        raw=source[registry.REGISTRY_KEY],source[profiles.PROFILE_KEY]
        state.list_filter='FRONT'
        self.assertEqual(bpy.ops.character_designer.hair_strand_list_check(action='SELECT_VISIBLE'),{'FINISHED'})
        self.assertEqual(set(checked()),{a,b})
        self.assertEqual((source[registry.REGISTRY_KEY],source[profiles.PROFILE_KEY]),raw)
        self.assertEqual(physical_author(source,rig),before)
        base['choose'](source,(b,c))
        self.assertEqual(bpy.ops.character_designer.hair_strand_list_check(action='IMPORT_POSE'),{'FINISHED'})
        self.assertEqual(set(checked()),{b,c})
        self.assertEqual(bpy.ops.character_designer.hair_strand_list_select_pose(),{'FINISHED'})
        self.assertEqual(set(base['selection'].selected(bpy.context,source)),{b,c})
        checks(())
        refused(lambda:bpy.ops.character_designer.hair_strand_list_rebuild('EXEC_DEFAULT',bone_count=6,action_pin=''))
        self.assertEqual(len(base['variants'].variants_for(source)),0)
        list_ui.refresh_ui(bpy.context,source,checked_signatures=(a,))
        rig.data.pose_position='REST'
        rest_author=physical_author(source,rig)
        refused(lambda:bpy.ops.character_designer.hair_strand_list_rebuild('EXEC_DEFAULT',bone_count=6,action_pin=''))
        self.assertEqual(rig.data.pose_position,'REST')
        self.assertEqual(physical_author(source,rig),rest_author)
        self.assertEqual(len(base['variants'].variants_for(source)),0)
        rig.data.pose_position='POSE'
        list_ui.refresh_ui(bpy.context,source,checked_signatures=(a,))
        data=registry.read(source)
        profiles.update(source,data['strands'][0]['strand_id'],overrides={'stretch':.17},registry=data)
        changed=source[profiles.PROFILE_KEY]
        refused(lambda:bpy.ops.character_designer.hair_strand_list_assign_category('EXEC_DEFAULT',group='BACK',action_pin=''))
        self.assertEqual(source[profiles.PROFILE_KEY],changed)
        self.assertEqual(len(base['variants'].variants_for(source)),0)
        METRICS[self._testMethodName]=dict(hidden_checks=True,pose_import_export=True,empty_and_stale_refused=True)

    def test_checked_batch_sequential_preserves_untouched_native_authors(self):
        source,rig,record=fixture()
        a,b,c=[row['signature'] for row in record['chains']]
        browser.assign_category(bpy.context,source,(a,b),'FRONT')
        expected=base['original_authors'](source,rig)
        untouched=base['chain_author'](source,c)
        animation=base['animation_author'](source,(b,c))
        list_ui.refresh_ui(bpy.context,source,checked_signatures=(a,b))
        self.assertEqual(bpy.ops.character_designer.hair_strand_list_rebuild('EXEC_DEFAULT',bone_count=6,action_pin=''),{'FINISHED'})
        out=bpy.context.window_manager.character_designer_hair_bones.source
        self.assertEqual({x['signature']:x['count'] for x in hair._read_records(out)['chains']},{a:6,b:6,c:4})
        self.assertEqual(set(checked()),{a,b})
        self.assertEqual(base['chain_author'](out,c),untouched)
        base['assert_original'](self,source,rig,expected)
        retained_b=base['chain_author'](out,b)
        retained_animation=base['animation_author'](out,(a,))
        list_ui.refresh_ui(bpy.context,out,checked_signatures=(a,))
        self.assertEqual(bpy.ops.character_designer.hair_strand_list_rebuild('EXEC_DEFAULT',bone_count=7,action_pin=''),{'FINISHED'})
        final=bpy.context.window_manager.character_designer_hair_bones.source
        self.assertEqual({x['signature']:x['count'] for x in hair._read_records(final)['chains']},{a:7,b:6,c:4})
        self.assertEqual(base['chain_author'](final,b),retained_b)
        self.assertEqual(base['chain_author'](final,c),untouched)
        base['assert_animation_preserved'](self,retained_animation,base['animation_author'](final,(a,)))
        base['assert_original'](self,source,rig,expected)
        self.assertEqual(groups(final)[a],'FRONT')
        self.assertEqual(groups(final)[b],'FRONT')
        self.assertEqual(len(base['variants'].variants_for(source)),2)
        METRICS[self._testMethodName]=dict(counts=[7,6,4],untouched_chain_driver_constraint_exact=True,original_actions_preserved=True)

    def test_native_copied_source_refuses_without_metadata_writes(self):
        source,rig,record=fixture()
        before=physical_author(source,rig)
        context=base['scope']['context_state']()
        inventory=base['scope']['ids']()
        duplicate=source.copy()
        bpy.context.scene.collection.objects.link(duplicate)
        raw={key:base['normalized'](value,source,rig) for key,value in duplicate.items()}
        try:
            with self.assertRaises((ValueError,RuntimeError)):
                browser.strict_snapshot(duplicate)
            self.assertEqual({key:base['normalized'](value,source,rig) for key,value in duplicate.items()},raw)
            self.assertEqual(physical_author(source,rig),before)
        finally:
            bpy.data.objects.remove(duplicate,do_unlink=True)
        self.assertEqual(base['scope']['context_state'](),context)
        self.assertEqual(base['scope']['ids'](),inventory)
        METRICS[self._testMethodName]=dict(native_source_pointer_refusal=True,original_and_duplicate_metadata_unmodified=True)

    def test_retained_family_active_and_recovery_adapter_states_refuse(self):
        source,rig,record=fixture()
        signature=record['chains'][0]['signature']
        result=service.create(bpy.context,source,bone_count=6,strand_signatures=(signature,),frame_start=1.,frame_end=8.)
        current,copied=result['mesh'],result['armature']
        from character_designer import hair_wiggle2_adapter as wiggle
        real_state=wiggle.preview_state
        before=physical_author(current,copied),physical_author(source,rig),source[profiles.PROFILE_KEY],current[profiles.PROFILE_KEY],base['scope']['context_state']()
        for flags in ({'active':True,'pending_recovery':False,'stopped_confirmed':False},
                      {'active':False,'pending_recovery':True,'stopped_confirmed':False}):
            def preview(item):
                value=dict(real_state(item))
                if item is source:value.update(flags)
                return value
            with patch.object(wiggle,'preview_state',side_effect=preview):
                with self.assertRaises((ValueError,RuntimeError)):
                    browser.assign_category(bpy.context,current,(signature,),'BACK')
            after=physical_author(current,copied),physical_author(source,rig),source[profiles.PROFILE_KEY],current[profiles.PROFILE_KEY],base['scope']['context_state']()
            self.assertEqual(after,before)
        METRICS[self._testMethodName]=dict(real_retained_source_family=True,injected_adapter_states=['active','pending_recovery'],native_metadata_author_context_exact=True,real_solver_transition=False)

    def test_zz_save_reopen_category_persistence(self):
        source,rig,record=fixture()
        a=record['chains'][0]['signature']
        browser.assign_category(bpy.context,source,(a,),'BACK')
        expected=groups(source),effective_physical(source)
        name=source.name
        path=args.report.with_name(args.report.stem+'_saved.blend')
        self.assertFalse(path.exists())
        self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=str(path),check_existing=False),{'FINISHED'})
        self.assertEqual(bpy.ops.wm.open_mainfile(filepath=str(path),use_scripts=True),{'FINISHED'})
        loaded=bpy.data.objects[name]
        self.assertEqual((groups(loaded),effective_physical(loaded)),expected)
        METRICS[self._testMethodName]=dict(private_saved=str(path),categories_motion_exact=True)


result = None
registered=False
report=dict(schema='x2.hair-browser.native/1',passed=False,projection=str(args.projection),projection_sha256=args.projection_sha,
            script_sha256=digest(__file__),fixture_sha256=BASE_SHA,metrics=METRICS,errors=[],cleanup_errors=[],
            artist_file_opened=False,preferences_saved=False,private_scene_save=True,
            limits=['Private three-strand fixtures; no artist/full-character/dynamics/FPS acceptance.',
                    'Unchanged approved125 core protections are inherited, not re-proven as artist acceptance.'])
try:
    cd.register();registered=True
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BrowserNative))
except BaseException:
    report['errors'].append(traceback.format_exc())
finally:
    if registered:
        try:
            if bpy.context.object and bpy.context.object.mode!='OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
            cd.unregister()
        except BaseException: report['cleanup_errors'].append(traceback.format_exc())
    report.update(tests_run=result.testsRun if result else 0,
                  failures=[(str(c),e) for c,e in result.failures] if result else [],
                  test_errors=[(str(c),e) for c,e in result.errors] if result else [],
                  source_unchanged=all(digest(PACKAGE/n)==h for n,h in manifest['files'].items()))
    report['passed']=bool(result and result.wasSuccessful() and not report['errors'] and not report['cleanup_errors'] and report['source_unchanged'])
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
if not report['passed']: raise RuntimeError('Hair browser native acceptance failed; retain this report.')