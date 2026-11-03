"""Bounded pure admission/helper tests; actual math and worklists run natively."""
import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/control_pose_offset.py'
spec = importlib.util.spec_from_file_location('offset_under_test', SOURCE)
offset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offset)


class ExpiredRowReference(RuntimeError):
    pass


class RelocatingRow:
    """A safe fake: Collection.add invalidates every previous RNA wrapper."""
    def __init__(self, owner, index):
        object.__setattr__(self, '_owner', owner)
        object.__setattr__(self, '_index', index)
        object.__setattr__(self, '_epoch', owner.epoch)
    def _record(self):
        owner = object.__getattribute__(self, '_owner')
        if object.__getattribute__(self, '_epoch') != owner.epoch:
            owner.expired_reads += 1
            raise ExpiredRowReference('old RNA wrapper invalidated by row collection mutation')
        return owner.records[object.__getattribute__(self, '_index')]
    def __getattr__(self, name):
        return self._record()[name]
    def __setattr__(self, name, value):
        self._record()[name] = value


class RelocatingRows:
    def __init__(self, records):
        self.records, self.epoch, self.expired_reads = records, 0, 0
    def __len__(self):
        return len(self.records)
    def __iter__(self):
        return (RelocatingRow(self, index) for index in range(len(self.records)))
    def fresh(self, item_id):
        return RelocatingRow(self, next(i for i,r in enumerate(self.records) if r['item_id']==item_id))
    def add(self):
        self.epoch += 1
        self.records.append({})
        return RelocatingRow(self, len(self.records)-1)
    def remove(self, index):
        self.epoch += 1
        self.records.pop(index)


class RowAction(dict):
    library = None
    asset_data = None
    def __init__(self, name, fingerprint, database):
        self.name, self.fingerprint, self.database = name, fingerprint, database
        self.use_fake_user, self.layers = True, []
        self.slots = [NS(handle=1, target_id_type='OBJECT')]
    @property
    def name_full(self):
        return self.name
    @property
    def users(self):
        return int(self.use_fake_user)
    def copy(self):
        result = RowAction(self.name+'.copy',self.fingerprint,self.database)
        result.update(self)
        self.database.append(result)
        return result


class OffsetContractTests(unittest.TestCase):
    def test_factor_default_range_and_nonfinite_refusal(self):
        self.assertEqual(offset._factor(.5), .5)
        self.assertEqual(offset._factor(0), 0.)
        self.assertEqual(offset._factor(1), 1.)
        for value in (True, None, '0.5', -.01, 1.01, math.nan, math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                offset._factor(value)

    def test_sampling_retains_fractional_authored_times_and_endpoints(self):
        self.assertEqual(offset._frames(.5, 3.5, [1.25, 3.]), [.5, 1., 1.25, 2., 3., 3.5])
        self.assertEqual(offset._frames(2., 2., []), [2.])

    def test_bad_frames_refuse_instead_of_dropping_authored_keys(self):
        for args in ((3., 2., []), (1., math.inf, []), (1., 3., [0.]),
                     (1., 3., [math.nan]), (1., 50000., [])):
            with self.subTest(args=args), self.assertRaises(ValueError):
                offset._frames(*args)

    def test_native_channel_parser_handles_quoted_bone_names(self):
        curve = NS(data_path='pose.bones["Arm\\\".L"].rotation_quaternion', array_index=2)
        self.assertEqual(offset._curve_name(curve), ('Arm".L', 'rotation_quaternion', 2))
        curve.data_path = 'pose.bones["Arm.L"]["ik_fk"]'
        self.assertIsNone(offset._curve_name(curve))

    def test_constant_fk_proof_checks_bezier_handles_and_modifiers(self):
        point = NS(co=(1., 0.), interpolation='BEZIER', handle_left=(0., 0.), handle_right=(2., 0.))
        curve = NS(keyframe_points=[point], modifiers=[], sampled_points=[])
        self.assertTrue(offset._constant_zero(curve))
        point.handle_right = (2., .25)
        self.assertFalse(offset._constant_zero(curve))
        point.handle_right = (2., 0.)
        curve.modifiers.append(object())
        self.assertFalse(offset._constant_zero(curve))

    def test_container_clone_preserves_exact_ID_references_and_old_payload(self):
        owner = object()
        original = {'rows': [{'action': owner}], 'hashes': {'old': 'aaa'}}
        clone = offset._clone_containers(original)
        self.assertIs(clone['rows'][0]['action'], owner)
        clone['rows'].append({'action': object()})
        clone['hashes']['new'] = 'bbb'
        self.assertEqual(len(original['rows']), 1)
        self.assertEqual(original['hashes'], {'old': 'aaa'})

    def test_explicit_region_intersects_pose_without_expanding_it(self):
        rig = NS(pose=NS(bones={'Upper.L': object(), 'Upper.R': object(), 'Finger.L': object()}))
        self.assertEqual(offset._selection(None, rig, {'Upper.L': {}}, ['Upper.L', 'Upper.R']), {'Upper.L'})
        for names in ([], ['Missing'], ['Upper.R']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                offset._selection(None, rig, {'Upper.L': {}}, names)

    def test_free_fk_rejects_transform_drivers_and_axis_angle(self):
        class Bones(dict):
            def __iter__(self):
                return iter(self.values())
        pb = NS(name='Upper.L', constraints=[], rotation_mode='XYZ',
                path_from_id=lambda: 'pose.bones["Upper.L"]')
        rig = NS(pose=NS(bones=Bones({'Upper.L': pb})), animation_data=NS(drivers=[]))
        offset._native_fk(None, rig, {'Upper.L'}, [])
        rig.animation_data.drivers.append(NS(data_path='pose.bones["Upper.L"].rotation_euler'))
        with self.assertRaisesRegex(ValueError, 'driver'):
            offset._native_fk(None, rig, {'Upper.L'}, [])
        rig.animation_data.drivers.clear()
        pb.rotation_mode = 'AXIS_ANGLE'
        with self.assertRaisesRegex(ValueError, 'Axis Angle'):
            offset._native_fk(None, rig, {'Upper.L'}, [])

    def test_no_frame_handler_or_registration_api(self):
        source = SOURCE.read_text(encoding='utf8')
        self.assertNotIn('frame_change', source)
        self.assertNotIn('timers.register', source)
        self.assertFalse(hasattr(offset, 'register'))

    def test_external_fk_mode_driver_refuses_even_with_current_fk_zero(self):
        class Bones(dict):
            def __iter__(self):
                return iter(self.values())
        pb = NS(name='Upper.L', constraints=[object()], rotation_mode='XYZ')
        target = NS(name='Target.L')
        entry = {'chain': ['Upper.L', 'Lower.L', 'Hand.L'], 'target': target, 'entries': []}
        rig = NS(pose=NS(bones=Bones({'Upper.L': pb, 'Target.L': target})),
                 animation_data=NS(drivers=[NS(data_path='pose.bones["Target.L"]["ik_fk"]')]))
        api = NS(limb=NS(_validate_inventory=lambda _rig: {'rigs': {('ARM', 'L'): entry}}),
                 match=NS(mode_for_rig=lambda *_args: 'FK',
                          property_path=lambda _target: 'pose.bones["Target.L"]["ik_fk"]'))
        with self.assertRaisesRegex(ValueError, 'external driver'):
            offset._native_fk(api, rig, {'Upper.L'}, [])
        pb.constraints = []
        api.setup = NS(has_generated=lambda _rig: True)
        with self.assertRaisesRegex(ValueError, 'external driver'):
            offset._native_fk(api, rig, {'Upper.L'}, [])

    def test_upper_only_ik_chain_refuses_without_its_own_constraints(self):
        class Bones(dict):
            def __iter__(self):
                return iter(self.values())
        pb = NS(name='Upper.L', constraints=[], rotation_mode='XYZ')
        entry = {'chain': ['Upper.L', 'Lower.L', 'Hand.L']}
        rig = NS(pose=NS(bones=Bones({'Upper.L': pb})), animation_data=NS(drivers=[]))
        api = NS(setup=NS(has_generated=lambda _rig: True),
                 limb=NS(_validate_inventory=lambda _rig: {'rigs': {('ARM', 'L'): entry}}),
                 match=NS(mode_for_rig=lambda *_args: 'IK'))
        with self.assertRaisesRegex(ValueError, 'requires FK'):
            offset._native_fk(api, rig, {'Upper.L'}, [])

    def test_partial_legacy_mirror_cannot_capture_current_frame_as_fixed_delta(self):
        api = NS(poses=NS(_complete_native_transforms=lambda _fields: False))
        with self.assertRaisesRegex(ValueError, 'complete saved transforms'):
            offset._mirror_values(api, None, {'Arm.L': {'rotation_euler': {0: .5}}}, None)

    def test_prepared_variant_cancel_is_inert_but_closes_the_session(self):
        plan = {'variant_api': 1, 'phase': 'prepared'}
        offset.rollback_variant(plan)
        offset.rollback_variant(plan)
        self.assertEqual(plan['phase'], 'rolled_back')
        with self.assertRaisesRegex(ValueError, 'no longer active'):
            offset.new_variant_action(plan)

    def test_published_custom_retains_fake_user_after_generic_keying_clears_it(self):
        class Action(dict):
            library = None
            asset_data = None
            use_fake_user = False
            users = 1
        action = Action()
        row = NS(item_id='new-row')
        slot = object()
        ad = NS(action=action, action_slot=slot)
        worklist = NS(_slot=lambda *_args: slot, _association=lambda _item: None)
        plan = dict(phase='staged', staged_action=action, api=NS(worklist=worklist),
                    rig=NS(animation_data=ad), context=NS(scene=NS(frame_current=3, frame_subframe=.25)),
                    original=NS(name_full='Original Custom'), slot=1, frame=3.25,
                    variant_label='Pose Blend', revision_value=None,
                    saved=NS(items=[row], active_index=-1, status='', has_error=False))
        def publish(_plan, owned):
            self.assertIs(owned, action)
            self.assertTrue(owned.use_fake_user)
            return row, None
        with patch.object(offset, '_variant_owner'), patch.object(offset, '_publish', publish):
            result = offset.publish_variant(plan, action)
        self.assertTrue(action.use_fake_user)
        self.assertEqual(result['item_id'], 'new-row')

    def _row_transaction(self):
        actions=[]
        original=RowAction('Original','custom',actions)
        source=RowAction('Source','source',actions)
        foreign=RowAction('Foreign source','foreign',actions)
        actions.extend((original,source,foreign))
        rig=NS(animation_data=NS(action=original))
        rows=RelocatingRows([dict(item_id='parent',name='Original row',rig=rig,
            custom_action=original,custom_slot=1,source_action=source,source_slot=1,side='CUSTOM')])
        saved=NS(items=rows,active_index=0,status='before',has_error=False)
        context=NS(window_manager=NS(character_designer_animation=NS(target=rig)))
        api=NS(worklist=NS(_item=lambda state,item_id:state.items.fresh(item_id)),
            fingerprint=NS(fingerprint_action=lambda action,slot:dict(fingerprint_known=True,
                action_fingerprint=action.fingerprint+':'+str(slot))),
            revision=NS(_row=lambda row:{key:getattr(row,key) for key in rows.records[0]},
                CHECKPOINT='checkpoint',_modules=lambda:None),
            target=NS(_capture=lambda *_args,**_kw:object(),_validate_capture=lambda *_args:None),
            bpy=NS(data=NS(actions=NS(remove=actions.remove))))
        api.worklist.activate=lambda _context,item_id,_side:setattr(rig.animation_data,'action',rows.fresh(item_id).custom_action)
        api.revision._restore_current=lambda *_args:setattr(rig.animation_data,'action',original)
        plan=dict(api=api,context=context,rig=rig,saved=saved,item=rows.fresh('parent'),
            original=original,slot=1,hashes={1:'custom:1'},source_hash='source:1',strength=0.,
            names=['Upper.L'],frames=[1.,9.],asset_name='Pose',revision_value=None,
            compatibility='test native-ID/row-lifetime scaffold',flipped=False)
        return NS(actions=actions,original=original,source=source,foreign=foreign,rows=rows,
                  saved=saved,context=context,rig=rig,api=api,plan=plan)

    def test_create_refetches_original_source_row_after_publish_relocation(self):
        f=self._row_transaction()
        with patch.object(offset,'prepare',return_value=f.plan):
            result=offset.create(f.context,f.rig,object(),strength=0.,activate=False)
        self.assertEqual(len(f.rows),2)
        self.assertEqual(f.rows.expired_reads,0)
        self.assertIs(f.rows.fresh('parent').source_action,f.source)
        self.assertIs(f.rows.fresh(result['item_id']).custom_action,result['action'])

    def test_variant_publish_refetches_parent_after_inactive_append(self):
        f=self._row_transaction()
        f.plan.update(variant_api=1,item_id='parent')
        inactive=f.rows.add();inactive.item_id='inactive'
        self.assertEqual(f.rows.expired_reads,0)
        action=f.original.copy()
        row,_checkpoint=offset._publish(f.plan,action)
        self.assertEqual(f.rows.expired_reads,0)
        self.assertIs(row.source_action,f.source)
        self.assertIs(row.custom_action,action)
        self.assertEqual(len(f.rows),3)

    def test_source_rebinding_after_publication_still_refuses_and_cleans_owned_data(self):
        for field,value in (('source_action','foreign'),('source_slot',2)):
            with self.subTest(field=field):
                f=self._row_transaction();before=tuple(f.actions)
                real_activate=f.api.worklist.activate
                def rebind(*args):
                    real_activate(*args)
                    setattr(f.rows.fresh('parent'),field,f.foreign if value=='foreign' else value)
                f.api.worklist.activate=rebind
                with patch.object(offset,'prepare',return_value=f.plan):
                    with self.assertRaisesRegex(ValueError,'read-only Source changed'):
                        offset.create(f.context,f.rig,object(),strength=0.,activate=True)
                self.assertEqual(f.rows.expired_reads,0)
                self.assertEqual(len(f.rows),1)
                self.assertEqual(tuple(f.actions),before)
                self.assertIs(f.rig.animation_data.action,f.original)
                self.assertEqual(getattr(f.rows.fresh('parent'),field),f.foreign if value=='foreign' else value)


if __name__ == '__main__':
    unittest.main()
