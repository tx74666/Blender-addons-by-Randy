"""Native logical L/R Hair entries; reuse pinned127 native protections."""
from pathlib import Path
import copy
import hashlib
import json
import traceback
import unittest
from unittest.mock import patch
import bpy

REPO=Path(__file__).resolve().parents[1]
PREVIOUS=REPO/'tests/validate_hair_strand_browser_blender.py'
PINNED=Path(r'D:/Blender/Projects/Character/X/validation/hair_strand_browser_20261029/harnesses/native_browser03.py')
PREVIOUS_SHA='e7195b1e424ba4920fdfdb53a7d6bd3dd43e7fba32772d852a93309610a5d16a'
digest=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert digest(PREVIOUS)==digest(PINNED)==PREVIOUS_SHA
previous={'__file__':str(PREVIOUS),'__name__':'paired_hair_previous_native'}
exec(compile(PINNED.read_text(encoding='utf-8').split('\nresult = None\n',1)[0],str(PREVIOUS),'exec'),previous)
args,cd,hair,registry,profiles,service,PACKAGE,manifest=(previous[n] for n in ('args','cd','hair','registry','profiles','service','PACKAGE','manifest'))
base=previous['base']
from character_designer import hair_strand_browser as browser
from character_designer import hair_strand_entries as entries
from character_designer import hair_strand_list_ui as ui
METRICS={}


def paired_fixture():
    source,rig,record=previous['fixture']()
    data=registry.read(source)
    a,b,c=data['strands']
    values=previous['effective_physical'](source)
    data=registry.manual_pair(source,a['strand_id'],b['strand_id'])
    profiles.reconcile(source,registry=data)
    assert previous['effective_physical'](source)==values
    assert not profiles.read(source)['strands'][a['strand_id']]['sync_mirror']
    ui.refresh_ui(bpy.context,source,checked_signatures=())
    return source,rig,record


def state():return bpy.context.window_manager.character_designer_hair_bones


def check(signatures):
    wanted=set(signatures)
    for item in state().list_items:item.checked=bool(wanted.intersection(json.loads(item.signatures_json)))


def pair_row():return next(item for item in state().list_items if item.member_count==2)


def restore_selection(rig,names,active):
    for pose in rig.pose.bones:pose.select=pose.name in names
    rig.data.bones.active=rig.data.bones.get(active) if active else None


class PairedEntriesNative(unittest.TestCase):
    def test_pair_is_one_four_per_side_row_selects_both_from_either_side(self):
        source,rig,record=paired_fixture()
        a,b,c=[row['signature'] for row in record['chains']]
        self.assertEqual(len(browser.list_rows(source)),3)
        self.assertEqual(len(state().list_items),2)
        pair=pair_row()
        self.assertEqual(pair.count,4)
        self.assertEqual(json.loads(pair.counts_json),[4,4])
        self.assertEqual(ui._cached_count_label(pair),'4 bones per side')
        self.assertEqual(pair.group,'MIXED')
        self.assertEqual(pair.side,'L/R')
        before=previous['physical_author'](source,rig)
        raw=source[registry.REGISTRY_KEY],source[profiles.PROFILE_KEY]
        for signature in (a,b):
            base['choose'](source,(signature,))
            old_names=tuple(p.name for p in rig.pose.bones if p.select)
            old_active=rig.data.bones.active.name
            self.assertEqual(bpy.ops.character_designer.hair_strand_list_check(action='IMPORT_POSE'),{'FINISHED'})
            self.assertEqual(set(ui._checked(state())),{a,b})
            self.assertTrue(pair.checked)
            self.assertEqual(bpy.ops.character_designer.hair_strand_list_select_pose(),{'FINISHED'})
            wanted={name for chain in record['chains'] if chain['signature'] in {a,b} for name in chain['bones']}
            self.assertEqual({p.name for p in rig.pose.bones if p.select},wanted)
            self.assertEqual(len(wanted),8)
            restore_selection(rig,old_names,old_active)
        self.assertEqual((source[registry.REGISTRY_KEY],source[profiles.PROFILE_KEY]),raw)
        METRICS[self._testMethodName]=dict(logical_rows=2,physical_chains=3,display='4 bones per side',selected_bones=8,both_native_input_sides_close_to_pair=True)

    def test_mixed_filter_keeps_whole_pair_category_preserves_both_values(self):
        source,rig,record=paired_fixture()
        a,b,c=[row['signature'] for row in record['chains']]
        ui.refresh_ui(bpy.context,source,checked_signatures=(b,))
        row=pair_row()
        self.assertEqual(set(ui._checked(state())),{a,b})
        self.assertTrue(ui._visible(row,'FRONT'))
        self.assertTrue(ui._visible(row,'SIDE'))
        self.assertFalse(ui._visible(row,'BACK'))
        before=previous['physical_author'](source,rig)
        values=previous['effective_physical'](source)
        profile=profiles.read(source)
        third=registry.read(source)['strands'][2]['strand_id']
        for category in ('FRONT','SIDE','BACK'):
            state().list_filter=category
            self.assertEqual(set(ui._checked(state())),{a,b})
        state().list_filter='FRONT'
        self.assertEqual(bpy.ops.character_designer.hair_strand_list_assign_category('EXEC_DEFAULT',group='BACK',action_pin=''),{'FINISHED'})
        self.assertEqual(previous['effective_physical'](source),values)
        self.assertEqual(previous['physical_author'](source,rig),before)
        current=profiles.read(source)
        self.assertEqual(current['group_defaults'],profile['group_defaults'])
        self.assertEqual(current['strands'][third],profile['strands'][third])
        self.assertEqual(previous['groups'](source),{a:'BACK',b:'BACK',c:'BACK'})
        self.assertEqual(len(state().list_items),2)
        self.assertEqual(pair_row().group,'BACK')
        self.assertFalse(current['strands'][registry.read(source)['strands'][0]['strand_id']]['sync_mirror'])
        METRICS[self._testMethodName]=dict(mixed_filters_keep_pair=True,both_category_updated=True,per_member_motion_depth_exact=True,unchecked_profile_exact=True,motion_sync_flag_unchanged=True)

    def test_pair_rebuild_six_then_seven_rebinds_both_keeps_asymmetric_author(self):
        source,rig,record=paired_fixture()
        a,b,c=[row['signature'] for row in record['chains']]
        expected=base['original_authors'](source,rig)
        untouched=base['chain_author'](source,c)
        native_animation=base['animation_author'](source,(a,b))
        values=previous['effective_physical'](source)
        ui.refresh_ui(bpy.context,source,checked_signatures=(b,))
        self.assertEqual(bpy.ops.character_designer.hair_strand_list_rebuild('EXEC_DEFAULT',bone_count=6,action_pin=''),{'FINISHED'})
        six=state().source
        self.assertEqual({x['signature']:x['count'] for x in hair._read_records(six)['chains']},{a:6,b:6,c:4})
        self.assertEqual(pair_row().count,6)
        self.assertEqual(len(state().list_items),2)
        self.assertEqual(base['chain_author'](six,c),untouched)
        base['assert_original'](self,source,rig,expected)
        base['assert_animation_preserved'](self,native_animation,base['animation_author'](six,(a,b)))
        self.assertEqual(previous['effective_physical'](six),values)
        ui.refresh_ui(bpy.context,six,checked_signatures=(a,))
        self.assertEqual(set(ui._checked(state())),{a,b})
        self.assertEqual(bpy.ops.character_designer.hair_strand_list_rebuild('EXEC_DEFAULT',bone_count=7,action_pin=''),{'FINISHED'})
        seven=state().source
        self.assertEqual({x['signature']:x['count'] for x in hair._read_records(seven)['chains']},{a:7,b:7,c:4})
        self.assertEqual(pair_row().count,7)
        self.assertEqual(ui._cached_count_label(pair_row()),'7 bones per side')
        self.assertEqual(base['chain_author'](seven,c),untouched)
        base['assert_original'](self,source,rig,expected)
        self.assertEqual(previous['effective_physical'](seven),values)
        self.assertEqual(len(base['variants'].variants_for(source)),2)
        METRICS[self._testMethodName]=dict(counts=[7,7,4],paired_rows=1,original_retained=True,asymmetric_paint_rest_action_slots_nla_drivers_constraints_exact=True,both_bindings_automatic=True)

    def test_forged_partial_or_changed_membership_refuses_before_create(self):
        source,rig,record=paired_fixture()
        a,b,c=[row['signature'] for row in record['chains']]
        ui.refresh_ui(bpy.context,source,checked_signatures=(a,))
        captured=json.loads(ui._capture(bpy.context))
        before=previous['physical_author'](source,rig)
        raw=source[registry.REGISTRY_KEY],source[profiles.PROFILE_KEY]
        changes=[]
        partial=copy.deepcopy(captured);partial['signatures']=[a];changes.append(partial)
        proof=copy.deepcopy(captured);proof['entries'][0]['members'][0]['pair_proof']='NONE';changes.append(proof)
        identity=copy.deepcopy(captured);identity['entries'][0]['members'][0]['strand_id']='stale-id';changes.append(identity)
        for changed in changes:
            with patch.object(service,'create',side_effect=AssertionError('Invalid pair scope reached create')) as call:
                previous['refused'](lambda:bpy.ops.character_designer.hair_strand_list_rebuild('EXEC_DEFAULT',bone_count=6,action_pin=json.dumps(changed)))
                self.assertFalse(call.called)
        row=pair_row();row.signatures_json=json.dumps([a])
        with patch.object(service,'create',side_effect=AssertionError('Forged cached entry reached create')) as call:
            previous['refused'](lambda:bpy.ops.character_designer.hair_strand_list_rebuild('EXEC_DEFAULT',bone_count=6,action_pin=json.dumps(captured)))
            self.assertFalse(call.called)
        self.assertEqual((source[registry.REGISTRY_KEY],source[profiles.PROFILE_KEY]),raw)
        self.assertEqual(previous['physical_author'](source,rig),before)
        self.assertFalse(base['variants'].variants_for(source))
        METRICS[self._testMethodName]=dict(partial_scope_proof_identity_cache_refused=True,no_native_mutation=True)

    def test_unproven_and_asymmetric_entries_stay_independent(self):
        source,rig,record=previous['fixture']()
        a,b,c=[row['signature'] for row in record['chains']]
        ui.refresh_ui(bpy.context,source,checked_signatures=(a,))
        self.assertEqual(len(state().list_items),3)
        self.assertEqual(tuple(item.member_count for item in state().list_items),(1,1,1))
        self.assertEqual(ui._checked(state()),(a,))
        self.assertTrue(all(item.side=='U' for item in state().list_items))
        METRICS[self._testMethodName]=dict(no_name_or_geometry_guess=True,three_singles=True)

    def test_native_mirror_pair_one_entry_expands_real_two_sides(self):
        from test_hair_bones_mirror_controls_blender import fixture as mirror_fixture
        base['fresh_main']()
        source,plans,rig=mirror_fixture()
        rig.data.bones['spine.006'].name='Head'
        base['binding'].bind_hair(bpy.context,source,plans,bone_count=4,armature=rig)
        record=hair._read_records(source)
        registry.initialize(source);profiles.initialize(source,registry=registry.read(source))
        sides={chain['mirror_side']:chain for chain in record['chains']}
        a,b,c=[sides[side]['signature'] for side in ('L','R','C')]
        base['choose'](source,(a,))
        ui.refresh_ui(bpy.context,source,checked_signatures=(a,))
        self.assertEqual(len(state().list_items),2)
        self.assertEqual(set(ui._checked(state())),{a,b})
        self.assertEqual(pair_row().count,4)
        center=base['chain_author'](source,c)
        self.assertEqual(bpy.ops.character_designer.hair_strand_list_select_pose(),{'FINISHED'})
        self.assertEqual(len(bpy.context.selected_pose_bones),8)
        self.assertEqual(bpy.ops.character_designer.hair_strand_list_rebuild('EXEC_DEFAULT',bone_count=6,action_pin=''),{'FINISHED'})
        result=state().source
        self.assertEqual({x['signature']:x['count'] for x in hair._read_records(result)['chains']},{a:6,b:6,c:4})
        self.assertEqual(base['chain_author'](result,c),center)
        self.assertEqual(len(state().list_items),2)
        self.assertTrue(all(member['pair_proof']=='MIRROR' for member in json.loads(pair_row().members_json)))
        mirror=next(m for m in result.modifiers if m.type=='MIRROR')
        self.assertTrue(mirror.use_mirror_vertex_groups)
        METRICS[self._testMethodName]=dict(native_mirror_proof=True,initial_bones_selected=8,count_per_side=6,center_exact=True)


result=None
registered=False
report=dict(schema='x2.paired-hair.native/1',passed=False,projection=str(args.projection),projection_sha256=args.projection_sha,
            script_sha256=digest(__file__),inherited_harness_sha256=PREVIOUS_SHA,metrics=METRICS,inherited_metrics=previous['METRICS'],errors=[],cleanup_errors=[],
            artist_file_opened=False,preferences_saved=False,private_scene_save=True,
            limits=['Private native fixtures only; no artist/full-character/dynamics/FPS acceptance.','Paired grouping is view-level; native chains remain physically distinct.'])
try:
    cd.register();registered=True
    suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(previous['BrowserNative']),unittest.defaultTestLoader.loadTestsFromTestCase(PairedEntriesNative)])
    result=unittest.TextTestRunner(verbosity=2).run(suite)
except BaseException:report['errors'].append(traceback.format_exc())
finally:
    if registered:
        try:
            if bpy.context.object and bpy.context.object.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
            cd.unregister()
        except BaseException:report['cleanup_errors'].append(traceback.format_exc())
    report.update(tests_run=result.testsRun if result else 0,failures=[(str(c),e) for c,e in result.failures] if result else [],test_errors=[(str(c),e) for c,e in result.errors] if result else [],source_unchanged=all(digest(PACKAGE/n)==h for n,h in manifest['files'].items()))
    report['passed']=bool(result and result.wasSuccessful() and not report['errors'] and not report['cleanup_errors'] and report['source_unchanged'])
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
if not report['passed']:raise RuntimeError('Paired Hair native acceptance failed; retain report.')