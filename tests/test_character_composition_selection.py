"""Exercise actual Composition selection/recovery with old and new RNA seams."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


class Bone:
    __slots__ = ('name', 'hide_select')

    def __init__(self, name):
        self.name, self.hide_select = name, False


class LegacyBone(Bone):
    __slots__ = ('select',)

    def __init__(self, name):
        super().__init__(name)
        self.select = False


class Pose(dict):
    def __init__(self, bone):
        super().__init__(owned_value=0.25)
        self.name, self.bone = bone.name, bone
        self.lock_location = [False, True, False]
        self.lock_rotation = [True, False, True]
        self.lock_scale = [False, False, True]
        self.lock_rotation_w, self.lock_rotations_4d = True, False

    def path_from_id(self):
        return 'pose.bones["' + self.name + '"]'


class ModernPose(Pose):
    def __init__(self, bone):
        super().__init__(bone)
        self.select = False


class Bones(list):
    def __init__(self, poses, selection_owner):
        super().__init__([pb.bone for pb in poses])
        self.poses, self.owner, self._active = poses, selection_owner, None

    def get(self, name):
        return next((bone for bone in self if bone.name == name), None)

    @property
    def active(self):
        return self._active

    @active.setter
    def active(self, bone):
        self._active = bone
        if bone is not None:
            # Native active assignment may select: exact flags must follow it.
            self.owner(next(pb for pb in self.poses if pb.bone is bone)).select = True


class Objects(list):
    active = None

    def get(self, name):
        return next((obj for obj in self if obj.name == name), None)


class Rig(dict):
    def __bool__(self):
        return True

    def select_set(self, selected):
        self.selected = selected


class SelectionTests(unittest.TestCase):
    def setUp(self):
        package_name = '_cd_selection_' + uuid.uuid4().hex
        package = ModuleType(package_name)
        package.__path__ = [str(SOURCE)]
        self.bpy = ModuleType('bpy')
        self.bpy.ops = NS(object=NS(mode_set=Mock(side_effect=self.mode_set)))
        self.bpy.data = NS(actions=NS(remove=Mock()))
        module_patch = patch.dict(sys.modules, {package_name:package, 'bpy':self.bpy})
        module_patch.start(); self.addCleanup(module_patch.stop)
        name = package_name + '.animation_composition'
        spec = importlib.util.spec_from_file_location(name, SOURCE / 'animation_composition.py')
        self.backend = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.backend
        self.addCleanup(lambda:sys.modules.pop(name, None))
        spec.loader.exec_module(self.backend)
        self.links = NS(LINK_KEY='link', ACTION_KEY='action', PACKAGE_KEY='packet', PACKAGE_HASH_KEY='packet_sha')
        self.ua = NS(_snapshot=Mock(return_value={'frame':1., 'end':31}), _restore_snapshot=Mock())
        self.modules = {'unity_animation':self.ua, 'animation_link':self.links}
        host_patch = patch.object(self.backend, '_host', side_effect=self.modules.__getitem__)
        host_patch.start(); self.addCleanup(host_patch.stop)

    def fixture(self, api='modern'):
        bone_type, pose_type = (Bone, ModernPose) if api == 'modern' else (LegacyBone, Pose)
        poses = [pose_type(bone_type(name)) for name in ('Allowed', 'Outside', 'Active')]
        self.rig = Rig(link='protected link', action='protected action')
        self.rig.name, self.rig.mode, self.rig.selected = 'Exact native Rig', 'POSE', True
        self.rig.pose = NS(bones=poses)
        self.rig.data = NS(name='Exact native Armature', bones=Bones(poses, self.backend._pose_selection_owner))
        self.rig.data.bones.active = poses[2].bone
        for pb in poses:
            self.backend._pose_selection_owner(pb).select = pb.name == 'Outside'
        self.original_action = object()
        self.rig.animation_data = NS(action=self.original_action, nla_tracks=NS(active=None), use_tweak_mode=False)
        objects = Objects([self.rig]); objects.active = self.rig
        self.context = NS(view_layer=NS(name='Exact owner ViewLayer', objects=objects, update=Mock()), selected_objects=[self.rig],
            scene=NS(objects=objects, render=NS(fps=30, fps_base=1.), unit_settings=NS(scale_length=1.),
                tool_settings=NS(use_keyframe_insert_auto=True)), mode='POSE', temp_override=self.override)
        return poses

    @contextmanager
    def override(self, **_kwargs):
        yield

    def mode_set(self, *, mode):
        self.rig.mode = mode
        # Simulate mode recovery disturbing selection before final restoration.
        for pb in self.rig.pose.bones:
            self.backend._pose_selection_owner(pb).select = True
        return {'FINISHED'}

    def capture(self):
        with patch.object(self.backend, '_layout', return_value=[]):
            return self.backend._prior(self.context, self.rig)

    def assert_selection(self, names=('Outside',), active='Active'):
        self.assertEqual([pb.name for pb in self.rig.pose.bones if self.backend._pose_selection_owner(pb).select], list(names))
        self.assertEqual(self.rig.data.bones.active.name if self.rig.data.bones.active else '', active)

    def test_actual_prior_captures_per_object_pose_selection_without_Bone_select(self):
        self.fixture()
        prior = self.capture()
        self.assertEqual(prior['selected_bones'], ['Outside'])
        self.assertEqual(prior['active_bone'], 'Active')
        self.assertTrue(all(not hasattr(pb.bone, 'select') for pb in self.rig.pose.bones))

    def test_actual_prior_preserves_supported_legacy_Bone_selection(self):
        self.fixture('legacy')
        self.assertEqual(self.capture()['selected_bones'], ['Outside'])

    def test_modern_selection_has_priority_when_legacy_field_also_exists(self):
        pb = ModernPose(LegacyBone('Exact input'))
        pb.select, pb.bone.select = True, False
        self.assertIs(self.backend._pose_selection_owner(pb), pb)
        self.backend._pose_selection_owner(pb).select = False
        self.assertFalse(pb.bone.select)

    def test_unknown_API_fails_instead_of_defaulting_or_manufacturing_selection(self):
        pb = Pose(Bone('Unknown API'))
        with self.assertRaises(AttributeError):
            self.backend._pose_selection_owner(pb)
        self.assertFalse(hasattr(pb, 'select'))
        self.assertFalse(hasattr(pb.bone, 'select'))

    def test_actual_restriction_deselects_only_outside_scope_on_both_APIs(self):
        for api in ('modern', 'legacy'):
            with self.subTest(api=api):
                self.fixture(api)
                session = {'scope_channels':[['pose.bones["Allowed"].rotation_euler', 0]],
                           'edit_target':'B', 'prior':self.capture()}
                self.backend._restrict_selection(self.rig, session, enabled=True)
                self.assert_selection(())
                self.assertEqual([pb.bone.hide_select for pb in self.rig.pose.bones], [False, True, True])

    def test_actual_restore_preserves_mode_locks_properties_and_active_deselected_bone(self):
        for api in ('modern', 'legacy'):
            with self.subTest(api=api):
                self.fixture(api)
                prior = self.capture()
                for pb in self.rig.pose.bones:
                    pb.lock_location = [True] * 3
                    pb.bone.hide_select = True
                    pb['owned_value'] = 9.
                    self.backend._pose_selection_owner(pb).select = True
                self.rig.mode = 'OBJECT'
                self.rig['link'], self.rig['action'] = 'changed', 'changed'
                self.backend._restore_prior(self.context, self.rig, prior)
                self.assert_selection()
                self.assertEqual(self.rig.mode, 'POSE')
                self.assertEqual((self.rig['link'], self.rig['action']), ('protected link', 'protected action'))
                self.assertTrue(all(pb.lock_location == [False, True, False] and not pb.bone.hide_select
                                    and pb['owned_value'] == 0.25 for pb in self.rig.pose.bones))
                self.ua._restore_snapshot.assert_called_with(self.context, self.rig, prior['snapshot'], self.original_action)

    def test_actual_restore_preserves_no_active_bone_and_empty_selection(self):
        self.fixture()
        self.rig.data.bones.active = None
        for pb in self.rig.pose.bones:
            pb.select = False
        prior = self.capture()
        self.rig.data.bones.active = self.rig.pose.bones[0].bone
        self.backend._restore_prior(self.context, self.rig, prior)
        self.assert_selection((), '')

    def test_failed_enter_uses_actual_restore_to_recover_original_selection(self):
        self.fixture()
        spec = dict(referencePacketSha256='a'*64, referencePacketFile='D:/actual-A.json',
                    baseClipGuid='b'*32, baseClipLocalId=4, fps=30., referenceSpeed=1.,
                    referencePhase=0., editSpeed=1., editPhase=0., timeMode='HoldPose')
        base = Rig(packet='D:/actual-A.json', packet_sha='a'*64, unity_sample_rate=30.)
        base.name, base.frame_range = 'Protected actual A', (1., 31.)
        item = NS(item_id='exact native item', rig=self.rig, custom_action=NS(library=None), custom_slot=7, name='Actual B')
        saved = NS(items=[item], active_index=0)
        def exact_item(actual_saved, actual_id):
            self.assertIs(actual_saved, saved)
            self.assertEqual(actual_id, item.item_id)
            return item
        worklist = NS(_idle=Mock(), state=lambda _context:saved, _item=exact_item, _check_baseline=Mock())
        target = NS(ensure_active=Mock(), is_adopted=lambda _rig:True, SESSION='target native session')
        self.rig[target.SESSION] = {'exact native session':True}
        self.modules.update(animation_worklist=worklist, animation_worklist_target=target)
        self.ua.load_package = Mock(return_value={'_sha256':'a'*64, 'clipGuid':'b'*32, 'clipLocalId':4})
        self.ua._mapping, self.ua._set_playing, self.ua._set_frame = Mock(), Mock(), Mock()
        scope = NS(channels=(('pose.bones["Allowed"].rotation_euler', 0),),
                   defaults={('pose.bones["Allowed"].rotation_euler', 0):0.}, proof={'authenticated':True})
        export = NS(resolve_scope=Mock(return_value=scope))
        draft = NS(users=1)
        tracks = []
        self.rig.animation_data.nla_tracks = tracks
        class Tracks(list):
            active = None
        self.rig.animation_data.nla_tracks = Tracks()
        def make_track(ad, name, action, slot, start, end, **_kwargs):
            strip = NS(action=action, action_slot_handle=slot)
            for field in self.backend._STRIP_FIELDS:
                setattr(strip, field, 0.)
            track = NS(name=name, strips=[strip])
            ad.nla_tracks.append(track)
            return track
        self.rig.animation_data_create = lambda:self.rig.animation_data
        def fail_tweak(*_args, **_kwargs):
            self.rig.mode = 'OBJECT'
            self.rig.data.bones.active = self.rig.pose.bones[0].bone
            raise RuntimeError('Native Tweak failure after selection restriction')
        def exact_spec(actual_spec, actual_item):
            self.assertIs(actual_spec, spec)
            self.assertIs(actual_item, item)
            return spec
        patches = [patch.object(self.backend.contract, 'validate_item', side_effect=exact_spec),
            patch.object(self.backend, 'item_spec', return_value=spec), patch.object(self.backend, '_bag'),
            patch.object(self.backend, '_packet_for_item', return_value={'_path':'D:/actual-B.json', '_sha256':'c'*64}),
            patch.object(self.backend, '_service', return_value=export), patch.object(self.backend, '_following_hold', return_value=None),
            patch.object(self.backend, '_motion_hash', return_value='d'*64), patch.object(self.backend, '_editing_action', return_value=draft),
            patch.object(self.backend, '_frame_range', return_value=(1., 31.)), patch.object(self.backend, '_track', side_effect=make_track),
            patch.object(self.backend, '_store'), patch.object(self.backend, '_tweak', side_effect=fail_tweak)]
        for patched in patches:
            patched.start(); self.addCleanup(patched.stop)
        with self.assertRaisesRegex(RuntimeError, 'Native Tweak failure'):
            self.backend.enter(self.context, spec, item.item_id, base_action=base, base_slot=7)
        self.assert_selection()
        self.assertEqual(self.rig.mode, 'POSE')
        self.assertEqual(self.rig.animation_data.nla_tracks, [])
        self.assertTrue(all(not pb.bone.hide_select for pb in self.rig.pose.bones))
        self.assertNotIn(self.backend.SESSION, self.rig)
        self.assertNotIn(self.backend.REFS, self.rig)


if __name__ == '__main__':
    unittest.main()
