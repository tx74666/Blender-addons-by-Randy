"""Thin dependency tests of Hair2 mapping, ownership boundaries and rollback."""
import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import math
from contextlib import nullcontext

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'addons/character_designer/hair_wiggle2_adapter.py'
LEGACY = ROOT / 'addons/character_designer/hair_wiggle_adapter.py'
PACKAGE = '_hair2_adapter_fixture'


class HairWiggleError(ValueError):
    pass


def functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding='utf8'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError('Missing tested functions: ' + str(set(names) - {node.name for node in nodes}))
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


class Position:
    def __init__(self, value):
        self.value = value

    def __sub__(self, other):
        return NS(length=abs(self.value - other.value))


class Source(dict):
    name = 'Hair'


class Hair2AdapterTests(unittest.TestCase):
    def test_negative_availability_cache_avoids_repeated_backend_reads(self):
        old = NS(_AVAILABLE_CACHE={'available': False, 'version': None, 'module': None,
                 'mapping_version': 2, 'reason': 'Disabled'}, _AVAILABLE_CACHE_TIME=10.)
        backend = Mock(side_effect=AssertionError('Negative cache unexpectedly scanned the backend'))
        ns = functions(SOURCE, ('available',), dict(_old=lambda: old, _backend=backend,
            time=NS(monotonic=lambda: 10.2), BACKEND_VERSION='2.2.4', MAPPING_VERSION=2))
        self.assertEqual(ns['available']()['reason'], 'Disabled')
        backend.assert_not_called()
        self.assertEqual(old._AVAILABLE_CACHE_TIME, 10.)

    def test_legacy_cache_does_not_authorize_current_hair2_backend(self):
        old = NS(_AVAILABLE_CACHE={'available': True, 'version': '1.1.2',
                 'mapping_version': 1, 'reason': ''}, _AVAILABLE_CACHE_TIME=10.)
        backend = Mock(return_value=NS(__name__='wiggle_2'))
        ns = functions(SOURCE, ('available',), dict(_old=lambda: old, _backend=backend,
            time=NS(monotonic=lambda: 10.2), BACKEND_VERSION='2.2.4', MAPPING_VERSION=2))
        self.assertEqual(ns['available']()['version'], '2.2.4')
        backend.assert_called_once_with()

    def mapping(self):
        old = functions(LEGACY, ('_number', '_profile', '_depth_values'),
                        dict(math=math, _fail=lambda text: (_ for _ in ()).throw(HairWiggleError(text))))
        ns = functions(SOURCE, ('_fail', '_settings'), dict(math=math, HairWiggleError=HairWiggleError,
                       _old=lambda: NS(**old)))
        scene = NS(gravity=NS(length=9.81), unit_settings=NS(scale_length=.01))
        rig = NS(data=NS(bones={name: NS(head_local=Position(i), tail_local=Position(i + 1))
                               for i, name in enumerate(('one', 'two'))}))
        profile = old['_profile'](dict(recovery=.4, damping=.3, gravity=4., stretch=.2, depth=[]))
        return ns, scene, rig, profile, old

    def test_mapping_matches_native_224_world_units(self):
        ns, scene, rig, profile, _ = self.mapping()
        value = ns['_settings'](scene, rig, ('strand',), {'strand': {'bones': ('one', 'two')}},
                                {'strand': profile})['one']
        self.assertAlmostEqual(value['wiggle_stiff'], 128.)
        self.assertAlmostEqual(value['wiggle_damp'], 6.)
        self.assertAlmostEqual(value['wiggle_gravity'], 4 / (9.81 * .01))
        self.assertEqual(value['wiggle_stretch'], .2)
        self.assertEqual(value['wiggle_mass'], 1.)
        self.assertTrue(value['wiggle_chain'])
        self.assertNotIn('wiggle_freeze', value)

    def test_depth_mapping_keeps_mass_and_gravity_independent(self):
        ns, scene, rig, _, old = self.mapping()
        profile = old['_profile'](dict(recovery=.4, damping=.3, gravity=4., stretch=.2, depth=[
            dict(position=0, recovery=0, damping=.1, mass=.2, gravity=1),
            dict(position=1, recovery=1, damping=.9, mass=2, gravity=7)]))
        values = ns['_settings'](scene, rig, ('s',), {'s': {'bones': ('one', 'two')}}, {'s': profile})
        self.assertAlmostEqual(values['one']['wiggle_stiff'], 200.)
        self.assertAlmostEqual(values['two']['wiggle_stiff'], 800.)
        self.assertAlmostEqual(values['two']['wiggle_damp'], 18.)
        self.assertAlmostEqual(values['two']['wiggle_mass'], 2.)
        self.assertAlmostEqual(values['two']['wiggle_gravity'], 7 / (.01 * 9.81))
        self.assertEqual(values['two']['wiggle_stretch'], .2)

    def test_zero_gravity_mapping_never_divides_by_zero(self):
        ns, scene, rig, profile, _ = self.mapping()
        scene.gravity.length = 0
        result = ns['_settings'](scene, rig, ('s',), {'s': {'bones': ('one',)}}, {'s': profile})
        self.assertEqual(result['one']['wiggle_gravity'], 0.)

    def test_invalid_chain_refuses_before_native_configuration(self):
        ns, scene, rig, profile, _ = self.mapping()
        rig.data.bones['one'].tail_local = Position(0)
        with self.assertRaisesRegex(HairWiggleError, 'Rest length'):
            ns['_settings'](scene, rig, ('s',), {'s': {'bones': ('one',)}}, {'s': profile})

    def test_invalid_profile_numbers_and_depth_order_still_refuse(self):
        _, _, _, _, old = self.mapping()
        for overrides in [dict(recovery=float('nan')), dict(damping=True), dict(gravity=21),
                          dict(depth=[dict(position=.7), dict(position=.2)])]:
            with self.subTest(overrides=overrides), self.assertRaises(HairWiggleError):
                old['_profile'](dict(recovery=.4, damping=.3, gravity=4, stretch=0, depth=[]) | overrides)

    def stop_fixture(self):
        bone = NS(name='Hair Bone', pose='simulated')
        peer = NS(pose='peer-current', mute=False)
        session = dict(source=Source(), scene=object(), armature=NS(pose=NS(bones={bone.name: bone})),
                       channels=[(bone, 'XYZ', {'pose': 'author'})], actions='proof', lease=object(),
                       ids=('s',), started_playback=True, physics_paused=False)
        old = NS(_restore_channels=Mock(side_effect=lambda saved: setattr(saved[0], 'pose', saved[2]['pose'])),
                 _animation_same=Mock(return_value=True))
        ns = functions(SOURCE, ('_fail', 'status', 'stop_preview'),
            dict(HairWiggleError=HairWiggleError, _SESSION=session, _LAST_REASON='', _STOPPING=False,
                 _TIMER_RUNNING=False, _mode_timer=object(), _old=lambda: old,
                 _RECOVERY_KEY='test_marker', _LAST_SOURCE_POINTER=None, _HISTORY_PENDING=False,
                 _animation_same=old._animation_same,
                 BACKEND_VERSION='2.2.4', MAPPING_VERSION=2,
                 bpy=NS(app=NS(timers=NS(is_registered=Mock(return_value=False), unregister=Mock()))),
                 runtime=NS(release=Mock())))
        return ns, session, bone, peer, old

    def test_stop_restores_only_owned_pose_without_frame_or_playback_access(self):
        ns, session, bone, peer, old = self.stop_fixture()
        result = ns['stop_preview'](reason='user stop')
        self.assertFalse(result['active'])
        self.assertEqual(bone.pose, 'author')
        self.assertEqual((peer.pose, peer.mute), ('peer-current', False))
        ns['runtime'].release.assert_called_once_with(session['lease'])
        old._animation_same.assert_called_once_with('proof')

    def test_failed_native_release_retains_session_and_does_not_restore_pose(self):
        ns, session, bone, peer, old = self.stop_fixture()
        ns['runtime'].release.side_effect = ValueError('native release refused')
        with self.assertRaisesRegex(HairWiggleError, 'needs attention'):
            ns['stop_preview']()
        self.assertIs(ns['_SESSION'], session)
        self.assertEqual(bone.pose, 'simulated')
        old._restore_channels.assert_not_called()
        self.assertFalse(ns['_STOPPING'])

    def test_failed_pose_restore_keeps_retry_state_after_native_release(self):
        ns, session, bone, peer, old = self.stop_fixture()
        old._restore_channels.side_effect = RuntimeError('owned pose failed')
        with self.assertRaisesRegex(HairWiggleError, 'owned pose failed'):
            ns['stop_preview']()
        self.assertIs(ns['_SESSION'], session)
        self.assertTrue(session['lease_released'])
        old._restore_channels.side_effect = lambda saved: setattr(saved[0], 'pose', saved[2]['pose'])
        ns['stop_preview']()
        self.assertEqual(ns['runtime'].release.call_count, 1)
        self.assertIsNone(ns['_SESSION'])

    def test_same_name_replacement_bone_is_not_overwritten(self):
        ns, session, bone, peer, old = self.stop_fixture()
        replacement = NS(name=bone.name, pose='replacement')
        session['armature'].pose.bones[bone.name] = replacement
        ns['stop_preview']()
        self.assertEqual(replacement.pose, 'replacement')
        old._restore_channels.assert_not_called()

    def test_external_association_edit_is_preserved_and_reported(self):
        ns, session, bone, peer, old = self.stop_fixture()
        old._animation_same.return_value = False
        with self.assertRaisesRegex(HairWiggleError, 'edits were preserved'):
            ns['stop_preview']()
        self.assertIsNone(ns['_SESSION'])
        self.assertEqual(bone.pose, 'author')

    def test_runtime_snapshot_requests_associations_only_and_never_legacy_full_actions(self):
        source = Source(registry='registry raw', record='rig record', rig='exact rig')
        source.as_pointer = lambda: 100
        source.data = NS(vertices=[], edges=[], polygons=[])
        bone = NS(name='Hair Bone')
        rig = NS(data=NS(bones={bone.name: bone}), pose=NS(bones={bone.name: bone}))
        scene = NS(unit_settings=NS(scale_length=1.), gravity=(0., 0., -9.81), frame_current=8)
        old = NS(_channels=Mock(return_value='owned pose'), _bone_proof=Mock(return_value='owned rest'),
                 _animation_proof=Mock(side_effect=AssertionError('Legacy full Action snapshot was called')))
        proof = Mock(return_value={'include_actions': False, 'sha256': 'bounded association digest'})
        ns = functions(SOURCE, ('_snapshot',), dict(_old=lambda: old, _animation_proof=proof,
            hair=NS(RIG_KEY='rig', RECORD_KEY='record'), strands=NS(REGISTRY_KEY='registry')))
        token = ns['_snapshot'](NS(scene=scene), source, rig,
            {'strands': [{'bones': [bone.name], 'anchor': None}]}, ('strand',), (bone.name,), object())
        proof.assert_called_once_with(include_actions=False)
        old._animation_proof.assert_not_called()
        self.assertFalse(token['actions']['include_actions'])

    def test_stop_wrong_exact_source_refuses_before_owned_cleanup(self):
        ns, session, bone, peer, old = self.stop_fixture()
        replacement = Source()
        replacement['other'] = True
        with self.assertRaisesRegex(HairWiggleError, 'another source'):
            ns['stop_preview'](source=replacement)
        ns['runtime'].release.assert_not_called()
        old._restore_channels.assert_not_called()
        self.assertIs(ns['_SESSION'], session)

    def test_start_error_is_scoped_to_the_actual_requested_source(self):
        package = ModuleType(PACKAGE)
        package.__path__ = []
        gate = ModuleType(PACKAGE + '.animation_physics')
        gate.require_enabled = Mock()
        source = Source()
        source.as_pointer = lambda: 987
        ns = functions(SOURCE, ('_fail', 'start_preview', 'status'), dict(__package__=PACKAGE,
            HairWiggleError=HairWiggleError, _SESSION=None, _LAST_REASON='', _LAST_SOURCE_POINTER=123,
            _HISTORY_PENDING=False, _RECOVERY_KEY='test_marker', BACKEND_VERSION='2.2.4', MAPPING_VERSION=2,
            _backend=Mock(side_effect=ValueError('backend unavailable'))))
        with patch.dict(sys.modules, {PACKAGE: package, gate.__name__: gate}):
            with self.assertRaisesRegex(HairWiggleError, 'backend unavailable'):
                ns['start_preview'](object(), source, {}, {}, ())
        self.assertEqual(ns['status']()['reason_scope'], 987)
        self.assertEqual(ns['status']()['source_pointer'], 987)
        self.assertEqual(ns['status']()['reason'], 'backend unavailable')

    def handler_fixture(self):
        backend = NS(wiggle_pre=lambda: None, wiggle_post=lambda: None)
        old = NS(_frame_guard=lambda: None, _FRAME_OWNERSHIP={})
        handlers = NS(frame_change_pre=[backend.wiggle_pre], frame_change_post=[backend.wiggle_post])
        ns = functions(SOURCE, ('_fail', '_handler_gate'),
            dict(HairWiggleError=HairWiggleError, sys=sys, __package__=PACKAGE,
                 _frame_guard=lambda: None, _old=lambda: old, bpy=NS(app=NS(handlers=handlers)),
                 runtime=NS(validate=Mock())))
        return ns, backend, old, handlers

    def test_unknown_frame_callback_still_refuses(self):
        ns, backend, old, handlers = self.handler_fixture()
        handlers.frame_change_post.append(lambda: None)
        with self.assertRaisesRegex(HairWiggleError, 'unverified frame callback'):
            ns['_handler_gate'](backend, object(), {'Hair Bone'})

    def test_exact_disjoint_peer_callback_is_accepted_after_lease_validation(self):
        ns, backend, old, handlers = self.handler_fixture()
        rig = object()
        skirt = ModuleType(PACKAGE + '.skirt_wiggle')
        skirt._frame_pre = lambda: None
        lease = object()
        skirt._SESSIONS = {'dress': dict(rig=rig, names=('PHYS',), lease=lease)}
        handlers.frame_change_pre.insert(0, skirt._frame_pre)
        with patch.dict(sys.modules, {skirt.__name__: skirt}):
            ns['_handler_gate'](backend, rig, {'Hair Bone'})
        ns['runtime'].validate.assert_called_once_with(lease)

    def test_peer_callback_without_lease_or_with_overlap_refuses(self):
        for lease, name in ((None, 'PHYS'), (object(), 'Hair Bone')):
            ns, backend, old, handlers = self.handler_fixture()
            rig = object()
            skirt = ModuleType(PACKAGE + '.skirt_wiggle')
            skirt._frame_pre = lambda: None
            skirt._SESSIONS = {'dress': dict(rig=rig, names=(name,), lease=lease)}
            with patch.dict(sys.modules, {skirt.__name__: skirt}), self.assertRaises(HairWiggleError):
                ns['_handler_gate'](backend, rig, {'Hair Bone'})

    def test_callback_name_does_not_grant_ownership(self):
        ns, backend, old, handlers = self.handler_fixture()
        def _frame_pre():
            pass
        handlers.frame_change_pre.append(_frame_pre)
        with self.assertRaises(HairWiggleError):
            ns['_handler_gate'](backend, object(), {'Hair Bone'})

    def test_facade_routes_current_public_api_to_hair2(self):
        names = ('available', 'status', 'start_preview', 'set_preview_paused', 'resume_preview', 'stop_preview')
        target = NS(**{name: Mock(return_value=name) for name in names})
        ns = functions(LEGACY, names, dict(_wiggle2_adapter=lambda: target))
        self.assertEqual(ns['available']('ctx'), 'available')
        self.assertEqual(ns['status']('ctx'), 'status')
        self.assertEqual(ns['start_preview'](1, 2, 3, 4, 5), 'start_preview')
        self.assertEqual(ns['set_preview_paused'](1, 2, True), 'set_preview_paused')
        self.assertEqual(ns['resume_preview'](1, 2), 'resume_preview')
        self.assertEqual(ns['stop_preview'](1, reason='stop'), 'stop_preview')
        target.stop_preview.assert_called_once_with(1, reason='stop', source=None)


    def recovery_fixture(self):
        source = Source(test_marker='owned marker')
        source.as_pointer = lambda: 123
        own = NS(name='Hair Bone', wiggle_head=False, wiggle_tail=True, raw={'flag': 'produced'}, group='runtime', pose='current')
        peer = NS(name='PHYS', wiggle_head=False, wiggle_tail=True, pose='peer-current')
        class Bones(dict):
            def __iter__(self):
                return iter(self.values())
        rig = NS(name='Rig', type='ARMATURE', pose=NS(bones=Bones({own.name: own, peer.name: peer})), raw={'enable': True})
        scene = NS(objects=[rig], raw={'enable': True})
        context = NS(scene=scene)
        token = dict(source=source, scene=scene, rig=rig, rig_name=rig.name, names=(own.name,), bones={own.name: own},
            fields=('flag',), backend=object(), marker_raw='owned marker', original={own.name: {'flag': 'original'}},
            groups={own.name: 'original-group'}, scene_original='old-scene-enable', rig_original='old-rig-enable',
            before_raw={own.name: {'flag': 'produced'}}, before_groups={own.name: 'runtime'},
            before_scene=True, before_rig=True, layout=[('Rig', (own.name, peer.name))],
            before_history_pending=True, before_last_source_pointer=42,
            needed=True, changed=False, stopped_confirmed=False, reason='')
        members = {('Rig', own.name), ('Rig', peer.name)}
        def restore(owner, field, value):
            owner.raw['enable' if field == 'wiggle_enable' else field] = value
            if owner is own and field == 'flag':
                owner.wiggle_tail = value == 'produced'
        def restore_members(lease, before):
            members.add(('Rig', own.name))
        runtime = NS(_writes=lambda backend: nullcontext(), _restore=Mock(side_effect=restore),
            _restore_group=Mock(side_effect=lambda bone, value: setattr(bone, 'group', value)),
            _list_remove=Mock(side_effect=lambda scene, rig_name, names: members.difference_update((rig_name, n) for n in names)),
            _restore_members=Mock(side_effect=restore_members))
        ns = functions(SOURCE, ('_fail', 'recover_saved', 'rollback_recovery'), dict(HairWiggleError=HairWiggleError,
            _RECOVERY_KEY='test_marker', _HISTORY_PENDING=True, _LAST_SOURCE_POINTER=None,
            runtime=runtime, prepare_recovery=Mock(return_value=token), _backend=Mock(return_value=token['backend'])))
        return ns, token, source, own, peer, rig, context, members

    def test_explicit_recovery_clears_owned_state_and_keeps_peer(self):
        ns, token, source, own, peer, rig, context, members = self.recovery_fixture()
        result = ns['recover_saved'](context, source)
        self.assertTrue(result['changed'])
        self.assertTrue(result['stopped_confirmed'])
        self.assertEqual((own.raw, own.group, own.pose), ({'flag': 'original'}, 'original-group', 'current'))
        self.assertEqual(peer.pose, 'peer-current')
        self.assertEqual(members, {('Rig', peer.name)})
        self.assertTrue(rig.raw['enable'])
        self.assertNotIn('test_marker', source)

    def test_recovery_receipt_rolls_back_exact_owned_state(self):
        ns, token, source, own, peer, rig, context, members = self.recovery_fixture()
        ns['recover_saved'](context, source)
        ns['rollback_recovery'](context, token)
        self.assertEqual((own.raw, own.group, own.pose), ({'flag': 'produced'}, 'runtime', 'current'))
        self.assertEqual(source['test_marker'], 'owned marker')
        self.assertEqual(members, {('Rig', own.name), ('Rig', peer.name)})
        self.assertEqual(peer.pose, 'peer-current')
        self.assertTrue(ns['_HISTORY_PENDING'])
        self.assertEqual(ns['_LAST_SOURCE_POINTER'], 42)

    def test_recovery_refuses_stale_own_token_before_writes(self):
        ns, token, source, own, peer, rig, context, members = self.recovery_fixture()
        with self.assertRaisesRegex(HairWiggleError, 'changed before recovery'):
            ns['recover_saved'](context, source, dict(needed=True, marker_raw='old marker'))
        ns['runtime']._restore.assert_not_called()
        self.assertEqual(source['test_marker'], 'owned marker')

    def test_recovery_partial_failure_restores_transaction_before_state(self):
        ns, token, source, own, peer, rig, context, members = self.recovery_fixture()
        original = ns['runtime']._restore_group.side_effect
        calls = [0]
        def fail_once(bone, value):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError('own PG setter failure')
            original(bone, value)
        ns['runtime']._restore_group.side_effect = fail_once
        with self.assertRaisesRegex(RuntimeError, 'own PG setter failure'):
            ns['recover_saved'](context, source)
        self.assertEqual((own.raw, own.group), ({'flag': 'produced'}, 'runtime'))
        self.assertEqual(source['test_marker'], 'owned marker')
        self.assertFalse(token['changed'])
        self.assertEqual(members, {('Rig', own.name), ('Rig', peer.name)})

    def test_history_post_drops_references_without_inspecting_rna(self):
        runtime = NS(forget=Mock())
        ns = functions(SOURCE, ('_history_post',), dict(_SESSION={'stale': object()}, _HISTORY_PENDING=False,
            _LAST_REASON='', runtime=runtime, _mode_timer=object(),
            bpy=NS(app=NS(timers=NS(is_registered=Mock(return_value=False), unregister=Mock())))))
        ns['_history_post']()
        self.assertIsNone(ns['_SESSION'])
        self.assertTrue(ns['_HISTORY_PENDING'])
        runtime.forget.assert_called_once_with('HAIR')


if __name__ == '__main__':
    unittest.main()
