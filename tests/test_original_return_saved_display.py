"""Pose-only return preserves saved display rather than re-normalizing its layout.

The actual return and display-restore functions operate on a small ordered RNA
model. The normalizer deliberately performs the three observed native changes:
Body membership, its saved JSON, and creation of an absent native-FK marker.
"""
import ast
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'
MODE_SOURCE = SOURCE / 'body_original_mode.py'
SESSION = 'original_session'
POSE_REFS = 'original_pose_refs'
BACKUP = 'managed_backup'
NATIVE_FK = 'native_fk_display'


def definitions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError('Missing exact product definitions.')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)


class ID(dict):
    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)


class Named(dict):
    def __iter__(self):
        return iter(self.values())


class Collection:
    def __init__(self, name):
        self.name, self.bones = name, Named()
        self.is_visible, self.is_solo = True, False

    def assign(self, bone):
        if bone.name not in self.bones:
            self.bones[bone.name] = bone
            bone.collections.append(self.name)

    def unassign(self, bone):
        if bone.name in self.bones:
            del self.bones[bone.name]
            bone.collections.remove(self.name)


class OriginalSavedDisplayTests(unittest.TestCase):
    def setUp(self):
        self.package = ModuleType('_original_saved_display_' + uuid.uuid4().hex)
        self.package.__path__ = []
        modules = patch.dict(sys.modules, {self.package.__name__: self.package})
        modules.start()
        self.addCleanup(modules.stop)
        self.bones = Named({name: NS(name=name, hide=False, hide_select=False, collections=[])
                            for name in ('native', 'control')})
        self.collections = Named({name: Collection(name) for name in ('Extra', 'Body', 'Original')})
        # Preserve a deliberately non-canonical, ordered artist layout.
        self.collections['Original'].assign(self.bones['native'])
        self.collections['Extra'].assign(self.bones['native'])
        self.collections['Extra'].assign(self.bones['control'])
        self.collections['Body'].assign(self.bones['control'])
        self.widget = NS(name='FK Ring')
        self.pb = NS(name='native', matrix=10.0, hide=False, custom_shape=self.widget, constraints=[])
        self.rig = ID(data=ID(bones=self.bones, collections_all=self.collections,
                             show_bone_custom_shapes=False, display_type='OCTAHEDRAL'),
                      pose=NS(bones=Named(native=self.pb)), as_pointer=lambda: 123,
                      channels={'native': 1}, locks={'native': 'original unlocked'})
        self.rig.data[BACKUP] = '{"managed":[{"name":"Body","bones":["control"]}],"keep":"artist"}'
        self.rig[POSE_REFS] = {'exact_original_owner': object()}
        self.cap = object()
        self.saved_display = dict(shapes=True, display='STICK',
            collections={name: [True, False] for name in self.collections.keys()},
            hidden={'native': [True, True], 'control': [False, True]},
            pose_hidden={'native': True},
            native_fk_display={'marker': None, 'shapes': {'native': 'FK Ring'}},
            managed_body={'collection': 'Body', 'members': ['control'], 'backup': self.rig.data[BACKUP]})
        self.saved = dict(names=['native'], constraints=[], rest={'native': {}},
            channels={'native': 0}, entered_channels={'native': 1}, locks={'native': 'saved locked'},
            dress_edit={}, display=deepcopy(self.saved_display))
        self.rig[SESSION] = 'exact active Original session'
        self.context = NS(scene=object())
        self.checked, self.finished, self.restored_owner = Mock(), Mock(), Mock()
        self.frame_cache = {123: 'old state', 987: 'unrelated cache'}
        self.rejections = {987: 'unrelated refusal'}
        self.groups = NS(BACKUP_KEY=BACKUP, _FRAME_CACHE=self.frame_cache,
                         _FRAME_OWNER_REJECTIONS=self.rejections)
        definitions(SOURCE / 'bone_collections.py', {'_assign_exact'}, self.groups.__dict__)
        native_namespace = dict(NATIVE_DISPLAY_KEY=NATIVE_FK,
            bpy=NS(data=NS(objects={'FK Ring': self.widget})), _error=ValueError)
        definitions(SOURCE / 'limb_fk_visuals.py', {'restore_native_display'}, native_namespace)
        self.package.limb_fk_visuals = NS(restore_native_display=native_namespace['restore_native_display'])
        display_namespace = dict(__package__=self.package.__name__, groups=self.groups,
                                 _check_object_restore=lambda *args: None)
        definitions(SOURCE / 'bone_display.py', {'_restore'}, display_namespace)

        def snapshot(rig):
            marker = rig.data.get(NATIVE_FK)
            return dict(shapes=rig.data.show_bone_custom_shapes, display=rig.data.display_type,
                collections={name: [c.is_visible, c.is_solo] for name, c in self.collections.items()},
                hidden={name: [b.hide, b.hide_select] for name, b in self.bones.items()},
                pose_hidden={'native': self.pb.hide},
                native_fk_display={'marker': marker,
                                   'shapes': {'native': self.pb.custom_shape.name if self.pb.custom_shape else ''}},
                managed_body={'collection': 'Body', 'members': list(self.collections['Body'].bones.keys()),
                              'backup': rig.data.get(BACKUP)})

        self.snapshot = snapshot
        self.display = NS(_snapshot=snapshot, _restore=display_namespace['_restore'], _completed=Mock())

        def normalize(*args, **kwargs):
            # Model the observed product normalization, not an inert method mock.
            self.groups._assign_exact(self.collections['Body'], self.rig.data, {'native'})
            raw = json.loads(self.rig.data[BACKUP])
            raw['managed'][0]['bones'] = ['native']
            self.rig.data[BACKUP] = json.dumps(raw)
            self.rig.data[NATIVE_FK] = '{"native":"FK Ring"}'

        self.normalizer = self.groups._frame_visibility = Mock(side_effect=normalize)
        self.resync = NS(apply=Mock(return_value=set()), verify=Mock(), commit=Mock(), rollback=Mock(),
                         restore_correctives=Mock(), prepare=Mock())
        self.dress = NS(_resolve=Mock(return_value=[]), capture=Mock(return_value={}),
            checkpoint=Mock(return_value={}), leave=Mock(), verify=Mock(), rollback=Mock())
        self.match = NS(_require_existing_switches=Mock(), _leg_pole_metadata=Mock(return_value={}),
            _repair_original_leg_poles=Mock(), repair_leg_poles=Mock(), _restore_leg_pole_metadata=Mock())

        @contextmanager
        def checkpoint(cap, state):
            yield

        def finish(cap):
            if cap is not None:
                self.checked(cap, self.rig)
                self.finished(cap)
                self.rig.pop(POSE_REFS)

        self.owner = NS(_check_pose_capability=self.checked, pose_return_checkpoint=checkpoint,
                        finish_pose_session=finish, restore_pose_session=self.restored_owner)
        self.package.body_original_owner, self.package.body_rest_resync = self.owner, self.resync
        self.package.forearm_original_inventory = NS(validate=Mock(return_value={'exact': 'inventory'}))

        def restore_channels(rig, channels):
            rig.channels = deepcopy(channels)
            self.pb.matrix = 10.0 if channels == self.saved['channels'] else self.desired

        def match_pose(context, rig, desired, changed, **kwargs):
            self.pb.matrix = desired['native']

        self.match_pose = Mock(side_effect=match_pose)
        self.desired = 10.0
        self.ns = dict(__package__=self.package.__name__, SESSION=SESSION,
            DISPLAY_REFS='display_refs', DISPLAY_OWNER='display_owner',
            _require=lambda *args, **kwargs: self.saved, active=lambda rig: SESSION in rig,
            _validate_session_rest=lambda *args: {'native': {}}, _ensure_dress_editable=Mock(),
            _resolve=lambda *args: [], _extra_views=lambda *args: [], _update=Mock(),
            _pose=lambda *args: {'native': self.desired}, _channels=lambda rig: deepcopy(rig.channels),
            _locks=lambda rig, names: deepcopy(rig.locks), _restore_channels=restore_channels,
            _restore_locks=lambda rig, state: setattr(rig, 'locks', deepcopy(state)),
            _verify=Mock(), match=self.match, dress_pose=self.dress, display=self.display, groups=self.groups,
            limb_ik=NS(_validate_inventory=Mock(return_value={})),
            poses=NS(_difference=lambda a, b: abs(a - b), _match=self.match_pose))
        definitions(MODE_SOURCE, {'_leave'}, self.ns)

    def invoke(self, *, legacy=False):
        return self.ns['_leave'](self.context, self.rig, Mock(),
                                 _original_pose=None if legacy else self.cap)

    def exact_metadata(self):
        return (deepcopy(dict(self.rig.data)),
                {name: list(bone.collections) for name, bone in self.bones.items()})

    def test_pose_only_noedit_retains_custom_order_and_absent_fk_marker(self):
        before = self.exact_metadata()
        self.assertTrue(self.invoke())
        self.assertEqual(self.exact_metadata(), before)
        self.assertEqual(self.snapshot(self.rig), self.saved_display)
        self.assertNotIn(NATIVE_FK, self.rig.data)
        self.normalizer.assert_not_called()
        self.match_pose.assert_not_called()
        self.assertNotIn(SESSION, self.rig)
        self.assertNotIn(POSE_REFS, self.rig)
        self.assertEqual(self.frame_cache, {987: 'unrelated cache'})
        self.assertEqual(self.rejections, {987: 'unrelated refusal'})
        self.assertEqual(self.checked.call_count, 2)

    def test_pose_only_edited_return_matches_pose_and_preserves_saved_modes_display(self):
        before = self.exact_metadata()
        self.desired, self.rig.channels = 11.0, {'native': 2}
        self.assertTrue(self.invoke())
        self.match_pose.assert_called_once_with(self.context, self.rig, {'native': 11.0}, {'native'},
            preserve_modes=True, precise_limbs=(), _pose_only_owner=True)
        self.assertEqual(self.pb.matrix, 11.0)
        self.assertEqual(self.exact_metadata(), before)
        self.assertEqual(self.snapshot(self.rig), self.saved_display)
        self.normalizer.assert_not_called()
        self.match._repair_original_leg_poles.assert_called_once_with(
            self.context, self.rig, desired_pose={'native': 11.0}, _original_pose=self.cap)

    def test_existing_fk_marker_is_restored_byte_for_byte(self):
        marker = '{ "native" : "FK Ring" }'
        self.saved['display']['native_fk_display']['marker'] = marker
        self.saved['display']['native_fk_display']['shapes']['native'] = ''
        self.rig.data[NATIVE_FK] = marker
        self.assertTrue(self.invoke())
        self.assertEqual(self.rig.data[NATIVE_FK], marker)
        self.assertIsNone(self.pb.custom_shape)
        self.normalizer.assert_not_called()

    def test_legacy_route_retains_the_existing_display_normalization(self):
        self.assertTrue(self.invoke(legacy=True))
        self.normalizer.assert_called_once_with(self.context.scene, objects=(self.rig,), _original_pose=None)
        self.assertIn(NATIVE_FK, self.rig.data)
        self.assertEqual(list(self.collections['Body'].bones.keys()), ['native'])
        self.assertEqual(self.bones['native'].collections, ['Original', 'Extra', 'Body'])
        self.assertEqual(self.bones['control'].collections, ['Extra'])
        self.match.repair_leg_poles.assert_called_once()
        self.match._repair_original_leg_poles.assert_not_called()
        self.checked.assert_not_called()

    def test_late_guard_failure_restores_active_original_and_entered_display(self):
        before_display, before_metadata = self.snapshot(self.rig), self.exact_metadata()
        session, refs = self.rig[SESSION], self.rig[POSE_REFS]
        self.checked.side_effect = [None, ValueError('Final Original owner changed')]
        with self.assertRaisesRegex(ValueError, 'owner changed'):
            self.invoke()
        self.assertEqual(self.rig[SESSION], session)
        self.assertIs(self.rig[POSE_REFS], refs)
        self.assertEqual(self.rig.channels, {'native': 1})
        self.assertEqual(self.rig.locks, {'native': 'original unlocked'})
        self.assertEqual(self.snapshot(self.rig), before_display)
        self.assertEqual(self.exact_metadata(), before_metadata)
        self.restored_owner.assert_called_once_with(self.cap)
        self.finished.assert_not_called()
        self.dress.rollback.assert_called_once()
        self.resync.rollback.assert_called_once()
        self.normalizer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
