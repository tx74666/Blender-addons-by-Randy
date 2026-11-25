"""Weight-paint pose session boundaries; native mode transitions are separate."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


class Items(dict):
    def __iter__(self):
        return iter(self.values())


class ID(dict):
    __hash__ = object.__hash__
    __eq__ = object.__eq__
    __ne__ = object.__ne__

    def __init__(self, name='', **fields):
        super().__init__()
        self.name = name
        self.__dict__.update(fields)

    def as_pointer(self):
        return id(self)

    def __bool__(self):
        return True


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.package_name = '_paint_session_' + uuid.uuid4().hex
        self.package = ModuleType(self.package_name)
        self.package.__path__ = [str(SOURCE)]
        self.bpy = ModuleType('bpy')
        self.bpy_types = ModuleType('bpy.types')
        self.bpy_types.Operator = type('Operator', (), {})
        self.bpy.types = self.bpy_types
        self.scene = ID('Scene', tool_settings=NS(use_keyframe_insert_auto=True),
                        frame_current=19, frame_subframe=0.)
        self.layer = ID('Layer', objects=Items())
        self.rig = ID('Rig', type='ARMATURE', mode='POSE')
        self.rig.data = ID('Data', bones=Items(), collections_all=[])
        self.rig.pose = NS(bones=Items())
        self.mesh = ID('Mesh', type='MESH', data=ID('MeshData'), modifiers=[])
        self.context = NS(scene=self.scene, view_layer=self.layer, object=self.mesh,
                          mode='PAINT_WEIGHT')
        self.bpy.context = self.context
        self.modules = {self.package_name: self.package, 'bpy': self.bpy, 'bpy.types': self.bpy_types}
        self.limb = ModuleType(self.package_name + '.limb_ik')
        self.limb._constraint_registry = lambda pb, strict: {}
        self.limb.OWNER_VALUE = 'owned'
        self.modules[self.limb.__name__] = self.limb
        self.groups = ModuleType(self.package_name + '.bone_collections')
        self.groups.body_collection = Mock(return_value=None)
        self.modules[self.groups.__name__] = self.groups
        self.patcher = patch.dict(sys.modules, self.modules)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.helper = self.load('weight_paint_pose_session')
        self.entry = self.load('weight_paint_entry')

    def load(self, name):
        spec = importlib.util.spec_from_file_location(self.package_name + '.' + name, SOURCE / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        setattr(self.package, name, module)
        self.addCleanup(sys.modules.pop, spec.name, None)
        return module

    def state(self):
        saved = dict(scene=self.scene, layer=self.layer, rig=self.rig,
                     rig_data=self.rig.data, mesh=self.mesh, mesh_data=self.mesh.data,
                     phase='PAINT', session_version=1)
        self.entry._STATE = saved
        return saved

    def bone(self, name, parent=None, constraints=()):
        bone = ID(name)
        pb = ID(name, bone=bone, parent=parent, constraints=list(constraints))
        self.rig.data.bones[name], self.rig.pose.bones[name] = bone, pb
        return pb

    def test_session_identity_survives_manual_mode_change(self):
        self.state()
        self.context.mode = 'OBJECT'
        self.assertTrue(self.entry.active(self.context))
        self.assertEqual(self.entry.phase(self.context), 'PAINT')

    def test_same_rig_is_owned_across_scene_context(self):
        self.state()
        self.context.scene = ID('Other Scene')
        self.assertFalse(self.entry.active(self.context))
        self.assertTrue(self.entry.owns_rig(self.rig))
        self.assertFalse(self.entry.owns_rig(ID('Unrelated')))

    def test_native_exit_does_not_claim_missing_recovery(self):
        self.assertTrue(self.entry.can_end_native(self.context))
        self.assertTrue(self.entry.CHARACTERDESIGNER_OT_finish_painting.poll(self.context))
        self.context.mode = 'OBJECT'
        self.assertFalse(self.entry.CHARACTERDESIGNER_OT_finish_painting.poll(self.context))

    def test_other_rig_native_paint_exit_not_globally_blocked(self):
        self.state()
        self.context.scene = ID('Other Scene')
        other = ID('Other Mesh', type='MESH', modifiers=[])
        self.context.object = other
        self.assertTrue(self.entry.can_end_native(self.context))
        other.modifiers = [NS(type='ARMATURE', object=self.rig)]
        self.assertFalse(self.entry.can_end_native(self.context))

    def test_legacy_display_session_remains_owned_and_finish_enabled(self):
        saved = self.state()
        saved.pop('phase')
        saved.pop('session_version')
        self.assertEqual(self.entry.phase(self.context), 'PAINT')
        self.assertTrue(self.entry.CHARACTERDESIGNER_OT_finish_painting.poll(self.context))
        self.assertTrue(self.entry.CHARACTERDESIGNER_OT_preview_weight_pose.poll(self.context))

    def test_same_module_reload_preserves_state_reference(self):
        state = self.state()
        exec(compile((SOURCE / 'weight_paint_entry.py').read_text(), 'entry reload', 'exec'), self.entry.__dict__)
        self.assertIs(self.entry._STATE, state)

    def test_four_existing_and_explicit_operator_interfaces(self):
        self.assertEqual(tuple(c.bl_idname for c in self.entry.CLASSES), (
            'character_designer.paint_weights', 'character_designer.preview_weight_pose',
            'character_designer.resume_weight_paint', 'character_designer.finish_painting'))

    def test_preview_and_resume_polls_allow_reentry_only_in_supported_native_modes(self):
        saved = self.state()
        for phase in ('PAINT', 'POSE_PREVIEW'):
            saved['phase'] = phase
            for mode in ('OBJECT', 'POSE', 'PAINT_WEIGHT'):
                with self.subTest(phase=phase, mode=mode):
                    self.context.mode = mode
                    self.assertTrue(self.entry.CHARACTERDESIGNER_OT_preview_weight_pose.poll(self.context))
                    self.assertTrue(self.entry.CHARACTERDESIGNER_OT_resume_weight_paint.poll(self.context))
                    self.assertTrue(self.entry.CHARACTERDESIGNER_OT_finish_painting.poll(self.context))
            self.context.mode = 'EDIT_ARMATURE'
            self.assertFalse(self.entry.CHARACTERDESIGNER_OT_preview_weight_pose.poll(self.context))
            self.assertFalse(self.entry.CHARACTERDESIGNER_OT_resume_weight_paint.poll(self.context))
        self.context.mode = 'OBJECT'
        saved['phase'] = 'UNKNOWN_RECOVERY'
        self.assertFalse(self.entry.CHARACTERDESIGNER_OT_preview_weight_pose.poll(self.context))
        self.assertFalse(self.entry.CHARACTERDESIGNER_OT_resume_weight_paint.poll(self.context))

    def test_draw_labels_actual_paint_group_and_retained_preview_group_without_mutation(self):
        class Layout:
            def __init__(self):
                self.labels, self.operators = [], []
            def row(self, **_kwargs):
                return self
            def operator(self, name, **kwargs):
                self.operators.append((name, kwargs))
            def label(self, **kwargs):
                self.labels.append(kwargs['text'])
        state = self.state()
        bone = self.bone('selected deform').bone
        bone.use_deform = True
        group = ID('selected deform')
        groups = Items({group.name: group})
        groups.active = group
        self.mesh.vertex_groups = groups
        state['paint_group'] = 'earlier group'
        before = dict(state)
        layout = Layout()
        self.entry.draw(layout, self.context)
        self.assertIn('Painting Group: selected deform', layout.labels)
        self.assertEqual(layout.operators, [])
        self.assertEqual(state, before)
        state.update(phase='POSE_PREVIEW', paint_group='selected deform')
        groups.active = ID('selected IK control')
        before = dict(state)
        layout = Layout()
        self.entry.draw(layout, self.context)
        self.assertIn('Painting Group: selected deform', layout.labels)
        self.assertNotIn('Painting Group: selected IK control', layout.labels)
        self.assertEqual(layout.operators, [])
        self.assertEqual(state, before)

    def test_original_route_reenters_same_session_without_replacing_baseline(self):
        state = self.state()
        baseline = object()
        state.update(pose_baseline=baseline, paint_group='retained')
        with patch.object(self.entry, '_session', return_value=state), \
                patch.object(self.entry, '_paint_group', return_value='current') as group, \
                patch.object(self.entry, '_resume_into_paint') as resume, \
                patch.object(self.entry, 'enter') as enter:
            self.assertEqual(self.entry.show_painting(self.context, self.rig), 'PAINT')
            resume.assert_called_once_with(self.context, state, 'current')
            enter.assert_not_called()
            group.assert_called_once_with(state)
            self.assertIs(state['pose_baseline'], baseline)
            state['phase'] = 'POSE_PREVIEW'
            resume.reset_mock()
            self.entry.show_painting(self.context, self.rig)
            resume.assert_called_once_with(self.context, state, 'retained')
            self.assertIs(state['pose_baseline'], baseline)

    def test_original_route_starts_once_and_controls_never_start_a_session(self):
        with patch.object(self.entry, '_resolve', return_value=(self.mesh, self.rig, (), 'group')), \
                patch.object(self.entry, 'enter') as enter:
            self.assertEqual(self.entry.show_painting(self.context, self.rig), 'PAINT')
            enter.assert_called_once_with(self.context)
        with self.assertRaisesRegex(ValueError, 'no active painting session'):
            self.entry.show_controls(self.context, self.rig)

    def test_controls_route_reenters_preview_and_rejects_another_rig(self):
        state = self.state()
        state.update(phase='POSE_PREVIEW', paint_group='retained')
        with patch.object(self.entry, '_session', return_value=state), \
                patch.object(self.entry, '_preview_into_pose') as preview:
            self.assertEqual(self.entry.show_controls(self.context, self.rig), 'POSE_PREVIEW')
            preview.assert_called_once_with(self.context, state, 'retained', repair_legacy=False)
            with self.assertRaisesRegex(ValueError, 'other Armature'):
                self.entry.show_controls(self.context, ID('another rig'))
            self.assertEqual(preview.call_count, 1)

    def test_painted_chain_selects_real_ik_target_instead_of_unrelated_control(self):
        upper = self.bone('sourceA')
        lower = self.bone('sourceB', parent=upper)
        self.bone('hand handle')
        self.bone('pole handle')
        self.bone('other FK')
        lower.constraints.append(NS(type='IK', mute=False, influence=1., chain_count=2,
                                    target=self.rig, subtarget='hand handle',
                                    pole_target=self.rig, pole_subtarget='pole handle', name='IK'))
        targets = self.helper.preview_targets(self.rig, ('sourceA', 'sourceB', 'other FK'))
        prior = dict(pose_selection=('sourceA',), active_bone='sourceA')
        self.assertEqual(self.entry._posing_selection(self.rig, targets, 'sourceA', prior),
                         (('hand handle',), 'hand handle'))
        prior = dict(pose_selection=('sourceA', 'other FK'), active_bone='sourceA')
        self.assertEqual(self.entry._posing_selection(self.rig, targets, 'sourceA', prior),
                         (('other FK',), 'other FK'))

    def test_unsupported_source_does_not_arbitrarily_select_master_or_other_control(self):
        source = self.bone('unsupported source')
        self.bone('unrelated master')
        source.constraints.append(NS(type='COPY_TRANSFORMS', name='external driver',
                                     mute=False, influence=1., target=ID('external'), subtarget='x'))
        targets = dict(names=('unrelated master',), controls=('unrelated master',),
                       sources=(), unresolved=('unsupported source',))
        prior = dict(pose_selection=('unsupported source',), active_bone='unsupported source')
        self.assertEqual(self.entry._posing_selection(self.rig, targets, 'unsupported source', prior),
                         ((), ''))

    def finish_fixture(self):
        state = self.state()
        source = self.bone('source').bone
        source.use_deform = True
        handle = self.bone('handle').bone
        handle.use_deform = False
        self.rig.data.show_bone_custom_shapes = False
        self.rig.data.display_type = 'OCTAHEDRAL'
        self.rig.show_in_front = False
        self.rig.hide_select = False
        for bone in self.rig.data.bones:
            bone.hide, bone.hide_select = False, False
        self.mesh.vertex_groups = Items({'source': ID('source', index=0)})
        self.mesh.vertex_groups.active = self.mesh.vertex_groups['source']
        self.mesh.vertex_groups.active_index = 0
        self.mesh.data.use_paint_bone_selection = True
        self.mesh.data.use_paint_mask = self.mesh.data.use_paint_mask_vertex = False
        self.layer.objects.active = self.mesh
        prior = dict(selected_objects=(), pose_selection=('source', 'handle'),
                     active_bone='source', active_group_index=0)
        state.update(paint_group='source', pose_baseline=object(), auto_key=True,
                     mesh_flags=(False, False, False), context=prior,
                     display=self.entry._display(self.rig))
        checkpoint = dict(context=dict(prior), auto=False, phase='PAINT', group='source',
                          pose=object(), display=state['display'], flags=(True, False, False), rig_mode='POSE')
        self.scene.tool_settings.use_keyframe_insert_auto = False
        self.mesh['new weights'] = (.3, .7)
        pose_mode = Mock()
        def enter_pose(context, rig, selected, active):
            context.mode, rig.mode = 'POSE', 'POSE'
            context.view_layer.objects.active = rig
            pose_mode(selected, active)
        service = NS(_enter_pose_mode=enter_pose, _object_is_available=Mock(return_value=True))
        return state, checkpoint, service, pose_mode

    def test_finish_restores_pose_but_returns_verified_controls_not_entry_deform_display(self):
        state, checkpoint, service, pose_mode = self.finish_fixture()
        targets = dict(names=('handle',), controls=('handle',), sources=(), unresolved=())
        self.groups.body_collection.return_value = object()
        def normal_display(context, saved, *, repair_legacy):
            self.assertIs(saved, state)
            self.rig.data.bones['source'].hide = True
            self.rig.data.bones['handle'].hide = False
            self.rig.data.show_bone_custom_shapes = True
            return ('handle',)
        with patch.object(self.entry, '_session', return_value=state), \
                patch.object(self.entry, '_transition_checkpoint', return_value=checkpoint), \
                patch.object(self.entry, '_controls_targets', return_value=targets), \
                patch.object(self.entry, '_normal_controls_display', side_effect=normal_display) as display, \
                patch.object(self.entry, '_paint', return_value=service), \
                patch.object(self.helper, 'restore') as restore:
            self.entry.leave(self.context)
        display.assert_called_once_with(self.context, state, repair_legacy=False)
        restore.assert_called_once_with(self.context, self.mesh, self.rig, state['pose_baseline'])
        self.assertIsNone(self.entry._STATE)
        self.assertTrue(self.scene.tool_settings.use_keyframe_insert_auto)
        self.assertTrue(self.rig.data.bones['source'].hide)
        self.assertFalse(self.rig.data.bones['handle'].hide)
        self.assertTrue(self.rig.data.show_bone_custom_shapes)
        pose_mode.assert_called_once_with(('handle',), 'handle')
        self.assertTrue(self.entry.last_finish_status(self.context, self.rig)['ready'])
        self.assertEqual(self.mesh['new weights'], (.3, .7))

    def test_finish_all_unresolved_still_ends_safely_with_clear_not_ready_status(self):
        state, checkpoint, service, pose_mode = self.finish_fixture()
        self.mesh.vertex_groups.active = ID('unrelated active group')
        state['context'].update(pose_selection=('source',), active_bone='source')
        source = self.rig.pose.bones['source']
        source.constraints.append(NS(type='COPY_TRANSFORMS', name='external driver',
                                     mute=False, influence=1., target=ID('external'), subtarget='x'))
        with patch.object(self.entry, '_session', return_value=state), \
                patch.object(self.entry, '_transition_checkpoint', return_value=checkpoint), \
                patch.object(self.entry, '_paint', return_value=service), \
                patch.object(self.helper, 'restore') as restore:
            self.entry.leave(self.context)
        restore.assert_called_once()
        self.assertIsNone(self.entry._STATE)
        self.assertTrue(self.scene.tool_settings.use_keyframe_insert_auto)
        self.assertEqual(self.entry._display(self.rig), state['display'])
        pose_mode.assert_called_once_with((), '')
        status = self.entry.last_finish_status(self.context, self.rig)
        self.assertFalse(status['ready'])
        self.assertIn('No managed Body controls display', status['message'])
        self.assertEqual(self.mesh['new weights'], (.3, .7))

    def test_finish_native_ik_without_managed_body_reports_display_limit(self):
        state, checkpoint, service, pose_mode = self.finish_fixture()
        source = self.rig.pose.bones['source']
        source.constraints.append(NS(type='IK', mute=False, influence=1., chain_count=1,
                                     target=self.rig, subtarget='handle', pole_target=None,
                                     pole_subtarget='', name='native IK'))
        targets = self.entry._controls_targets(state)
        self.assertEqual(targets['controls'], ('handle',))
        self.assertEqual(targets['unresolved'], ())
        self.assertIsNone(self.groups.body_collection(self.rig))
        with patch.object(self.entry, '_session', return_value=state), \
                patch.object(self.entry, '_transition_checkpoint', return_value=checkpoint), \
                patch.object(self.entry, '_paint', return_value=service), \
                patch.object(self.helper, 'restore') as restore:
            self.entry.leave(self.context)
        restore.assert_called_once_with(self.context, self.mesh, self.rig, state['pose_baseline'])
        self.assertIsNone(self.entry._STATE)
        self.assertTrue(self.scene.tool_settings.use_keyframe_insert_auto)
        self.assertEqual(self.entry._display(self.rig), state['display'])
        pose_mode.assert_called_once_with(('handle',), 'handle')
        status = self.entry.last_finish_status(self.context, self.rig)
        self.assertFalse(status['ready'])
        self.assertIn('No managed Body controls display', status['message'])
        self.assertIn('previous display was restored', status['message'])
        self.assertNotIn('No verified local pose controls', status['message'])
        self.assertEqual(self.mesh['new weights'], (.3, .7))

    def test_finish_feedback_names_actual_display_without_assuming_fk_or_control_absence(self):
        labels = []
        layout = NS(row=lambda **_kwargs: layout, operator=Mock(),
                    label=lambda **kwargs: labels.append(kwargs['text']))
        self.context.mode, self.context.object = 'POSE', self.rig
        for ready, expected in ((True, 'Normal Body controls display is shown.'),
                                (False, 'No managed Body controls display is available.')):
            with self.subTest(ready=ready):
                labels.clear()
                self.entry._FINISH_STATUS = dict(scene=self.scene, layer=self.layer, rig=self.rig,
                                                ready=ready, message='Painting finished; weights kept.')
                self.entry.draw(layout, self.context)
                self.assertIn('Painting finished; weights kept.', labels)
                self.assertIn(expected, labels)
                self.assertFalse(any('FK bones' in label or 'No verified local posing controls' in label
                                     for label in labels))
                if not ready:
                    self.assertIn('Previous display restored.', labels)

    def test_fk_uses_actual_deform_names_without_prefix_conventions(self):
        self.bone('artist named forearm')
        self.bone('unrelated helper')
        targets = self.helper.preview_targets(self.rig, ('artist named forearm',))
        self.assertEqual(targets['sources'], ('artist named forearm',))
        self.assertEqual(targets['controls'], ())

    def test_native_ik_resolves_real_local_target_and_pole(self):
        upper = self.bone('sourceA')
        lower = self.bone('sourceB', parent=upper)
        self.bone('user hand handle')
        self.bone('user pole handle')
        lower.constraints.append(NS(type='IK', mute=False, influence=1., chain_count=2,
                                    target=self.rig, subtarget='user hand handle',
                                    pole_target=self.rig, pole_subtarget='user pole handle', name='native IK'))
        targets = self.helper.preview_targets(self.rig, ('sourceA', 'sourceB'))
        self.assertEqual(targets['sources'], ())
        self.assertEqual(targets['controls'], ('user hand handle', 'user pole handle'))

    def test_external_ik_is_not_bypassed_by_unlocking_deform_bones(self):
        source = self.bone('source')
        source.constraints.append(NS(type='IK', mute=False, influence=1., chain_count=1,
                                     target=ID('external'), subtarget='external control',
                                     pole_target=None, pole_subtarget='', name='IK'))
        with self.assertRaisesRegex(ValueError, 'no verified local pose controls'):
            self.helper.preview_targets(self.rig, ('source',))
        self.assertEqual(source.constraints[0].influence, 1.)

    def test_muted_native_ik_does_not_hide_fk_source(self):
        source = self.bone('source')
        source.constraints.append(NS(type='IK', mute=True, influence=1., name='IK'))
        self.assertEqual(self.helper.preview_targets(self.rig, ('source',))['sources'], ('source',))

    def test_author_change_refuses_before_frame_or_pose_write(self):
        saved = dict(properties={}, witness=('original action and drivers',))
        self.scene.frame_set = Mock()
        with patch.object(self.helper, 'author_witness', return_value=('changed action',)):
            with self.assertRaisesRegex(ValueError, 'author data.*kept'):
                self.helper.restore(self.context, self.mesh, self.rig, saved)
        self.scene.frame_set.assert_not_called()

    def test_pose_restoration_uses_raw_components_not_matrix_decomposition(self):
        pb = self.bone('source')
        for name, value in [('rotation_mode', 'QUATERNION'), ('location', (1., 2., 3.)),
                            ('rotation_euler', (.1, .2, .3)), ('rotation_quaternion', (-1., 0., 0., 0.)),
                            ('rotation_axis_angle', (0., 0., 1., 0.)), ('scale', (1., 1., 1.))]:
            setattr(pb, name, value)
        pb['switch'] = .25
        saved = dict(properties={'source': ('switch',)}, frame=(19, 0.),
                     pose=self.helper.raw_pose(self.rig, {'source': ('switch',)}), witness='proof')
        pb.location, pb.rotation_quaternion, pb['switch'] = (9., 9., 9.), (1., 0., 0., 0.), .75
        self.scene.frame_set = Mock()
        self.rig.update_tag = Mock()
        self.layer.update = Mock()
        with patch.object(self.helper, 'author_witness', return_value='proof'):
            self.helper.restore(self.context, self.mesh, self.rig, saved)
        self.assertEqual(pb.location, (1., 2., 3.))
        self.assertEqual(pb.rotation_quaternion, (-1., 0., 0., 0.))
        self.assertEqual(pb['switch'], .25)
        self.assertFalse(hasattr(pb, 'matrix_basis'))
        self.scene.frame_set.assert_called_once_with(19, subframe=0.)

    def test_deleted_numeric_control_refuses_before_frame_write(self):
        pb = self.bone('source')
        saved = dict(properties={'source': ('switch',)}, witness='proof', frame=(19, 0.),
                     pose=(('source', self.helper.identity(pb), (), (('switch', .25),)),))
        self.scene.frame_set = Mock()
        with patch.object(self.helper, 'author_witness', return_value='proof'):
            with self.assertRaisesRegex(ValueError, 'disappeared or changed type'):
                self.helper.restore(self.context, self.mesh, self.rig, saved)
        self.scene.frame_set.assert_not_called()

    def test_changed_numeric_control_type_refuses_before_frame_write(self):
        pb = self.bone('source')
        pb['switch'] = 0
        saved = dict(properties={'source': ('switch',)}, witness='proof', frame=(19, 0.),
                     pose=(('source', self.helper.identity(pb), (), (('switch', .25),)),))
        self.scene.frame_set = Mock()
        with patch.object(self.helper, 'author_witness', return_value='proof'):
            with self.assertRaisesRegex(ValueError, 'disappeared or changed type'):
                self.helper.restore(self.context, self.mesh, self.rig, saved)
        self.scene.frame_set.assert_not_called()

    def test_rollback_restores_auto_key_even_if_display_and_context_fail(self):
        saved = self.state()
        saved.update(phase='POSE_PREVIEW', paint_group='painted group', painted_weights=(.2, .8))
        self.scene.tool_settings.use_keyframe_insert_auto = False
        previous = dict(pose=None, display={}, flags=(), context={}, rig_mode='POSE',
                        auto=True, phase='PAINT', group='original group')
        flags = Mock()
        with patch.object(self.entry, '_restore_display', side_effect=RuntimeError('display fault')), \
                patch.object(self.entry, '_set_mesh_flags', flags), \
                patch.object(self.entry, '_restore_context', side_effect=RuntimeError('context fault')):
            with self.assertRaisesRegex(RuntimeError, 'display.*context'):
                self.entry._rollback(self.context, saved, previous)
        self.assertTrue(self.scene.tool_settings.use_keyframe_insert_auto)
        self.assertIs(self.entry._STATE, saved)
        self.assertEqual(saved['phase'], 'PAINT')
        self.assertEqual(saved['painted_weights'], (.2, .8))
        flags.assert_called_once()

    def test_only_explicit_unsupported_idproperties_rna_is_adapted(self):
        owner = NS(keys=Mock(side_effect=TypeError("bpy_struct.keys(): this type doesn't support IDProperties")))
        self.assertEqual(self.helper._properties(owner), ())
        owner.keys = Mock(side_effect=TypeError('unrelated custom property read failure'))
        with self.assertRaisesRegex(TypeError, 'unrelated'):
            self.helper._properties(owner)

    def test_indexed_numeric_array_driver_retains_existing_control(self):
        pb = self.bone('source')
        pb['姿态'] = (.2, .8)
        pb['unrelated'] = 2.
        pb.path_from_id = lambda: 'pose.bones["source"]'
        self.rig.animation_data = None
        with patch.object(self.helper, 'animated_paths', return_value={'pose.bones["source"]["姿态"][1]'}):
            self.assertEqual(self.helper.property_names(self.rig), {'source': ('姿态',)})
        with patch.object(self.helper, 'animated_paths', return_value={'pose.bones["source"]["姿态"][99]'}):
            self.assertEqual(self.helper.property_names(self.rig), {'source': ()})

    def test_owned_master_follow_is_not_misclassified_as_free_fk(self):
        root = self.bone('deform root')
        master = self.bone('authored master')
        self.rig.data['owner id'] = 'rig-id'
        master.bone['owner'] = 'owned'
        master.bone['role'] = 'MASTER'
        self.limb.ARMATURE_ID_KEY = 'owner id'
        self.limb._owned = lambda bone, aid, **kw: bone.get('owner') == 'owned' and bone.get('role') == kw['role']
        self.limb._constraint_registry = lambda pb, strict: ({'follow': dict(
            owner='owned', role='MASTER_FOLLOW', root='deform root',
            target='authored master', armature_id='rig-id')} if pb is root else {})
        root.constraints.append(NS(name='follow', type='COPY_TRANSFORMS', mute=False,
                                   influence=1., target=self.rig, subtarget='authored master'))
        result = self.helper.preview_targets(self.rig, ('deform root',))
        self.assertEqual(result['sources'], ())
        self.assertEqual(result['controls'], ('authored master',))
        master.bone['owner'] = 'foreign'
        self.bone('plain FK source')
        result = self.helper.preview_targets(self.rig, ('deform root', 'plain FK source'))
        self.assertEqual(result['sources'], ('plain FK source',))
        self.assertEqual(result['unresolved'], ('deform root',))

    def test_animated_nla_only_excludes_fields_with_real_active_curves(self):
        strip = ID('strip', action=None, strips=(), modifiers=(),
                   use_animated_time=True, use_animated_influence=True)
        strip.path_from_id = lambda field: 'nla_tracks["Track"].strips["Strip"].' + field
        curve = NS(data_path='strip_time', mute=False)
        strip.fcurves = [curve]
        seen = []
        with patch.object(self.helper, '_rna', side_effect=lambda owner, excluded=(): seen.append(set(excluded)) or ()), \
                patch.object(self.helper, '_curve', return_value=('full authored curve',)):
            snapshot = self.helper._strip(strip, {})
        self.assertEqual(seen[0], {'strip_time'})
        self.assertEqual(snapshot[3], (('full authored curve',),))
        strip.use_animated_time = False
        seen.clear()
        with patch.object(self.helper, '_rna', side_effect=lambda owner, excluded=(): seen.append(set(excluded)) or ()), \
                patch.object(self.helper, '_curve', return_value=()):
            self.helper._strip(strip, {})
        self.assertEqual(seen[0], set())

    def test_numeric_array_restoration_keeps_original_storage_object(self):
        class Array(list):
            typecode = 'f'
        pb = self.bone('source')
        array = Array([.25, .75])
        pb['vector'] = array
        for name, value in [('rotation_mode', 'XYZ'), ('location', (0., 0., 0.)),
                            ('rotation_euler', (0., 0., 0.)), ('rotation_quaternion', (1., 0., 0., 0.)),
                            ('rotation_axis_angle', (0., 0., 1., 0.)), ('scale', (1., 1., 1.))]:
            setattr(pb, name, value)
        properties = {'source': ('vector',)}
        saved = dict(properties=properties, property_kinds=self.helper.numeric_kinds(self.rig, properties),
                     frame=(19, 0.), pose=self.helper.raw_pose(self.rig, properties), witness='proof')
        array[:] = [.5, .5]
        self.scene.frame_set = Mock()
        self.rig.update_tag = Mock()
        self.layer.update = Mock()
        with patch.object(self.helper, 'author_witness', return_value='proof'):
            self.helper.restore(self.context, self.mesh, self.rig, saved)
        self.assertIs(pb['vector'], array)
        self.assertEqual(array, [.25, .75])
        self.assertEqual(array.typecode, 'f')
        array.typecode = 'd'
        self.scene.frame_set.reset_mock()
        with patch.object(self.helper, 'author_witness', return_value='proof'):
            with self.assertRaisesRegex(ValueError, 'storage type'):
                self.helper.restore(self.context, self.mesh, self.rig, saved)
        self.scene.frame_set.assert_not_called()

    def rig_output_fixture(self, properties, curves):
        for key, value in properties.items():
            self.rig[key] = value
        self.rig.animation_data = NS(action=None, drivers=curves, nla_tracks=(), use_nla=True)
        def witness():
            return (('animation definitions',), (), (), (), (), self.helper._properties(self.rig), (), ())
        return witness

    def test_legacy_scalar_frame_outputs_vary_but_unanimated_driver_inputs_stay_exact(self):
        curves = [NS(data_path='["output"]', array_index=0, mute=False,
                     driver=NS(variables=[NS(targets=[NS(id=self.rig, data_path='["input"]')])]))]
        witness = self.rig_output_fixture({'output': .5, 'input': .25}, curves)
        saved = dict(witness=witness())
        self.rig['output'] = .75
        self.assertTrue(self.helper._author_matches(self.rig, saved, witness()))
        self.rig['input'] = .4
        self.assertFalse(self.helper._author_matches(self.rig, saved, witness()))
        self.rig['input'] = .25
        self.rig['output'] = 1
        self.assertFalse(self.helper._author_matches(self.rig, saved, witness()))
        self.rig['output'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'non-finite'):
            self.helper._author_matches(self.rig, saved, witness())

    def test_only_exact_array_destination_component_can_vary_with_storage_evidence(self):
        class Array(list):
            typecode = 'f'
        vector = Array([.25, .75, 1.])
        witness = self.rig_output_fixture({'array': vector},
            [NS(data_path='["array"]', array_index=1, mute=False)])
        saved = dict(witness=witness(), rig_output_kinds=self.helper._rig_output_kinds(self.rig))
        vector[1] = .5
        self.assertTrue(self.helper._author_matches(self.rig, saved, witness()))
        legacy = dict(witness=saved['witness'])
        self.assertFalse(self.helper._author_matches(self.rig, legacy, witness()))
        vector[0] = .1
        self.assertFalse(self.helper._author_matches(self.rig, saved, witness()))
        vector[0] = .25
        vector.typecode = 'd'
        self.assertNotEqual(self.helper._rig_output_kinds(self.rig), saved['rig_output_kinds'])

    def test_muted_curve_and_other_action_slot_do_not_authorize_changed_properties(self):
        witness = self.rig_output_fixture({'output': .5},
            [NS(data_path='["output"]', array_index=0, mute=True)])
        curve = NS(data_path='["output"]', array_index=0, mute=False)
        bag = NS(slot_handle=2, fcurves=[curve])
        self.rig.animation_data.action = NS(slots=(NS(handle=1), NS(handle=2)),
            layers=[NS(strips=[NS(channelbags=[bag])])])
        self.rig.animation_data.action_slot = NS(handle=1)
        saved = dict(witness=witness())
        self.rig['output'] = .75
        self.assertFalse(self.helper._author_matches(self.rig, saved, witness()))
        self.rig.animation_data.action_slot.handle = 2
        self.assertTrue(self.helper._author_matches(self.rig, saved, witness()))

    def test_saved_frame_outputs_are_asserted_without_writing_custom_properties(self):
        witness = self.rig_output_fixture({'output': .5},
            [NS(data_path='["output"]', array_index=0, mute=False)])
        saved = dict(witness=witness())
        self.rig['output'] = .75
        with self.assertRaisesRegex(RuntimeError, 'reevaluate'):
            self.helper._check_evaluated_outputs(self.rig, saved)
        self.assertEqual(self.rig['output'], .75)
        self.rig['output'] = .5
        self.helper._check_evaluated_outputs(self.rig, saved)


if __name__ == '__main__':
    unittest.main()
