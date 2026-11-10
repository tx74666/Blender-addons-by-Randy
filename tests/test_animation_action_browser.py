"""Small pure checks for display classification and native-selector ownership.

RNA/UI are mocked here; these do not claim native registration or slot proof.
"""
import ast
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/animation_action_browser.py'


class Block(dict):
    library = None
    is_editable = True

    def __init__(self, name, uid):
        super().__init__()
        self.name, self.session_uid = name, uid


class Action(Block):
    is_action_layered = True
    use_fake_user = False

    def __init__(self, name, uid, slots=None):
        super().__init__(name, uid)
        self.slots = [NS(handle=10, target_id_type='OBJECT', name_display='Rig')] if slots is None else slots

    @property
    def fcurves(self):
        raise AssertionError('No curve scanning')

    @property
    def layers(self):
        raise AssertionError('No layer scanning')


class IDList(list):
    def get(self, name):
        return next((item for item in self if item.name == name), None)


class Layout:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.enabled = True

    def row(self, **kwargs):
        return Layout(self.events)

    def prop(self, *args, **kwargs):
        self.events.append(('prop', args, kwargs))

    def menu(self, *args, **kwargs):
        self.events.append(('menu', args, kwargs, self.enabled))

    def template_action(self, *args, **kwargs):
        self.events.append(('native', args, kwargs, self.enabled))

    def template_search(self, *args, **kwargs):
        self.events.append(('slots', args, kwargs))

    def context_pointer_set(self, *args):
        pass

    def label(self, **kwargs):
        self.events.append(('label', kwargs))

    def operator(self, name, **kwargs):
        operator = NS()
        self.events.append(('operator', name, kwargs, self.enabled, operator))
        return operator


class AnimData:
    def __init__(self, action):
        self.action, self.action_slot = action, action.slots[0]
        self.use_tweak_mode, self.use_nla = False, True
        self.action_influence, self.action_blend_type = .6, 'ADD'
        self.fail_slot = False

    def __setattr__(self, name, value):
        if name == 'action_slot' and getattr(self, 'fail_slot', False):
            self.fail_slot = False
            raise RuntimeError('slot assignment fault')
        object.__setattr__(self, name, value)


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.package = ModuleType('_browser_test_package')
        self.package.__path__ = []
        self.bpy, props, types = ModuleType('bpy'), ModuleType('bpy.props'), ModuleType('bpy.types')
        class Base:
            is_registered = False
        class WMMeta(type):
            def __setattr__(cls, name, value):
                super().__setattr__(name, value)
                if name == 'character_designer_action_browser_all':
                    marker = object()
                    cls.bl_rna.properties[name] = NS(as_pointer=lambda: id(marker))
            def __delattr__(cls, name):
                super().__delattr__(name)
                cls.bl_rna.properties.pop(name, None)
        class WM(metaclass=WMMeta):
            bl_rna = NS(properties={})
        types.Menu, types.Operator, types.WindowManager = Base, Base, WM
        for name in ('BoolProperty', 'IntProperty', 'StringProperty'):
            setattr(props, name, lambda **kwargs: kwargs.get('default', False))
        self.bpy.types = types
        self.bpy.props = props
        def register_class(cls):
            cls.is_registered = True
            setattr(types, cls.__name__, cls)
        def unregister_class(cls):
            cls.is_registered = False
            delattr(types, cls.__name__)
        self.bpy.utils = NS(register_class=register_class, unregister_class=unregister_class)
        self.first = Action('Long Current Name', 1)
        self.idle = Action('Idle', 2)
        self.rig = Block('Rig', 100)
        self.rig.type = 'ARMATURE'
        self.rig.data = NS(name='RigData', pose_position='POSE')
        self.rig.animation_data = AnimData(self.first)
        self.rig.animation_data_create = lambda: self.rig.animation_data
        self.rig.animation_data_clear = lambda: setattr(self.rig, 'animation_data', None)
        self.bpy.data = NS(actions=IDList([self.first, self.idle]), objects=IDList([self.rig]))
        self.animation = ModuleType(self.package.__name__ + '.animation')
        self.animation._target = lambda context: self.rig
        self.animation._link_idle = lambda: True
        self.unity = ModuleType(self.package.__name__ + '.unity_animation')
        self.unity.active_preview = lambda target: None
        self.native_events = []
        events = self.native_events
        class HeaderButtons:
            @classmethod
            def _draw_action_selector(cls, context, layout):
                events.append((cls, context, layout))
        self.header = HeaderButtons
        self.original = HeaderButtons.__dict__['_draw_action_selector']
        self.native_module = ModuleType('bl_ui.space_dopesheet')
        self.native_module.DOPESHEET_HT_editor_buttons = HeaderButtons
        modules = {'bpy': self.bpy, 'bpy.props': props, 'bpy.types': types,
                   self.package.__name__: self.package,
                   self.animation.__name__: self.animation, self.unity.__name__: self.unity,
                   'bl_ui': ModuleType('bl_ui'), 'bl_ui.space_dopesheet': self.native_module}
        self.patcher = patch.dict(sys.modules, modules)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        spec = importlib.util.spec_from_file_location(self.package.__name__ + '.animation_action_browser', SOURCE)
        self.ui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ui)
        self.context = NS(scene=NS(objects=self.bpy.data.objects,
                              character_designer_animation_worklist=NS(workspace_path='', items=[])),
                          view_layer=NS(objects=self.bpy.data.objects), object=self.rig, mode='POSE',
                          window_manager=WM(), screen=NS(is_animation_playing=False), space_data=NS(mode='ACTION'))

    def mark(self, action, role='WORK', clip='walk', label='Walk'):
        self.ui.classify_action(action, self.rig, role=role, clip_id=clip, label=label)

    def choose(self, action, slot=10):
        return self.ui.activate(self.context, target_uid='100', action_uid=str(action.session_uid), slot_handle=slot)

    def test_classification_writes_only_strings_and_preserves_existing_identity(self):
        self.first['existing_owner'] = 'byte-exact'
        self.mark(self.first)
        self.assertEqual(self.first['existing_owner'], 'byte-exact')
        self.assertEqual(self.first.name, 'Long Current Name')
        self.assertTrue(all(isinstance(self.first[key], str) for key in self.ui._KEYS))
        self.assertEqual(self.ui.display_label(self.first, self.rig), 'Walk')

    def test_unity_clip_name_is_short_label_only_and_explicit_label_takes_priority(self):
        self.idle['unity_clip_name'] = '  Idle Short  '
        self.assertEqual(self.ui.display_label(self.idle, self.rig), 'Idle Short')
        self.assertEqual(self.ui.display_role(self.idle, self.rig), 'UNKNOWN')
        self.assertNotIn(self.ui.CLIP_KEY, self.idle)
        self.assertEqual(len(self.ui.daily_rows(self.rig)), 2)
        self.mark(self.idle, label='Explicit Alias')
        self.assertEqual(self.ui.display_label(self.idle, self.rig), 'Explicit Alias')

    def test_empty_or_nontext_unity_clip_name_uses_actual_name(self):
        for value in ('', '   ', 5, None):
            self.idle['unity_clip_name'] = value
            self.assertEqual(self.ui.display_label(self.idle, self.rig), 'Idle')

    def test_linked_source_never_written(self):
        self.idle.library = NS(filepath='source.blend')
        with self.assertRaises(ValueError):
            self.mark(self.idle)
        self.assertEqual(dict(self.idle), {})
        self.assertEqual(self.ui.display_role(self.idle, self.rig), 'SOURCE')

    def test_classification_fault_restores_only_display_namespace(self):
        class FaultAction(Action):
            fault = False
            def __setitem__(self, key, value):
                if self.fault and key.endswith('clip'):
                    self.fault = False
                    raise RuntimeError('metadata write fault')
                return super().__setitem__(key, value)
        action = FaultAction('Untouched Name', 30)
        action['foreign_owner'] = 'exact'
        self.mark(action, 'ARCHIVED')
        before = dict(action)
        action.fault = True
        with self.assertRaisesRegex(RuntimeError, 'metadata write fault'):
            self.mark(action)
        self.assertEqual(dict(action), before)

    def test_current_archived_kept_unknown_idle_available_and_technical_hidden(self):
        self.mark(self.first, 'ARCHIVED')
        technical = Action('Inputs', 3)
        self.bpy.data.actions.append(technical)
        self.mark(technical, 'INPUT')
        rows = self.ui.daily_rows(self.rig)
        self.assertEqual([row['action'].session_uid for row in rows], [1, 2])

    def test_copied_work_metadata_groups_current_without_name_guess(self):
        self.mark(self.first)
        copied = Action('Completely Different Name', 3)
        copied.update(self.first)
        self.bpy.data.actions.append(copied)
        self.rig.animation_data.action = copied
        self.assertEqual([row['action'].session_uid for row in self.ui.daily_rows(self.rig)], [3, 2])

    def test_uncertain_group_safely_expands_then_recent_choice_groups(self):
        self.mark(self.first)
        copied = Action('Another', 3)
        copied.update(self.first)
        self.bpy.data.actions.append(copied)
        self.rig.animation_data.action = self.idle
        self.assertEqual(len(self.ui.daily_rows(self.rig)), 3)
        self.choose(copied)
        self.choose(self.idle)
        rows = self.ui.daily_rows(self.rig)
        self.assertEqual({row['action'].session_uid for row in rows}, {2, 3})

    def test_unrelated_rig_name_hint_remains_visible_but_is_not_editable(self):
        self.mark(self.idle, 'ARCHIVED')
        self.idle[self.ui.OWNER_KEY] = 'Another Rig'
        self.assertEqual(self.ui.display_role(self.idle, self.rig), 'UNKNOWN')
        rows = self.ui.daily_rows(self.rig)
        self.assertEqual(len(rows), 2)
        self.assertFalse(next(row for row in rows if row['action'] is self.idle)['editable'])
        with self.assertRaises(ValueError):
            self.choose(self.idle)

    def test_override_action_and_target_are_not_admitted_for_editing(self):
        self.idle.override_library = NS()
        with self.assertRaises(ValueError):
            self.mark(self.idle)
        with self.assertRaises(ValueError):
            self.choose(self.idle)
        self.rig.override_library = NS()
        self.assertFalse(self.ui.can_select(self.context, self.rig))

    def other_rig(self, ad):
        other = Block('Other Rig', 200)
        other.type, other.animation_data = 'ARMATURE', ad
        ad.nla_tracks = []
        self.bpy.data.objects.append(other)
        return other

    def test_other_armature_live_action_slot_consumer_blocks_selection(self):
        self.other_rig(AnimData(self.idle))
        with self.assertRaisesRegex(ValueError, 'another armature'):
            self.choose(self.idle)
        self.assertIs(self.rig.animation_data.action, self.first)

    def test_other_armature_nla_slot_consumer_blocks_selection(self):
        ad = AnimData(self.first)
        self.other_rig(ad)
        ad.nla_tracks = [NS(strips=[NS(type='META', action=None, strips=[NS(
            type='CLIP', action=self.idle, action_slot=self.idle.slots[0])])])]
        with self.assertRaisesRegex(ValueError, 'another armature'):
            self.choose(self.idle)

    def test_other_armature_different_slot_does_not_claim_chosen_slot(self):
        other_slot = NS(handle=11, target_id_type='OBJECT', name_display='Other Rig')
        self.idle.slots.append(other_slot)
        ad = AnimData(self.idle)
        ad.action_slot = other_slot
        self.other_rig(ad)
        self.choose(self.idle, slot=10)
        self.assertEqual(self.rig.animation_data.action_slot.handle, 10)

    def test_incompatible_slots_omitted_current_always_retained(self):
        self.idle.slots = [NS(handle=11, target_id_type='KEY', name_display='Shape Keys')]
        self.assertEqual(len(self.ui.daily_rows(self.rig)), 1)
        self.rig.animation_data.action = self.idle
        self.rig.animation_data.action_slot = self.idle.slots[0]
        self.assertEqual(len(self.ui.daily_rows(self.rig)), 2)

    def test_activation_preserves_nla_frame_mode_and_existing_data(self):
        before = vars(self.rig.animation_data).copy()
        self.context.scene.frame_current = 42
        self.choose(self.idle)
        after = vars(self.rig.animation_data).copy()
        for key in ('action', 'action_slot'):
            before.pop(key); after.pop(key)
        self.assertEqual(before, after)
        self.assertEqual(self.context.scene.frame_current, 42)
        self.assertEqual(self.context.mode, 'POSE')

    def test_success_retains_old_and_new_local_work_without_copying(self):
        self.mark(self.first)
        self.mark(self.idle, clip='idle', label='Idle')
        before = tuple(self.bpy.data.actions)
        self.choose(self.idle)
        self.assertTrue(self.first.use_fake_user)
        self.assertTrue(self.idle.use_fake_user)
        self.assertEqual(tuple(self.bpy.data.actions), before)

    def test_retention_fault_restores_both_flags_and_exact_binding(self):
        class FaultAction(Action):
            fail_retention = False
            def __setattr__(self, name, value):
                super().__setattr__(name, value)
                if name == 'use_fake_user' and self.fail_retention:
                    self.fail_retention = False
                    raise RuntimeError('fake user after-write fault')
        candidate = FaultAction('Idle', 2)
        candidate.fail_retention = True
        self.bpy.data.actions[1] = candidate
        before = self.rig.animation_data.action, self.rig.animation_data.action_slot
        with self.assertRaisesRegex(RuntimeError, 'fake user after-write fault'):
            self.choose(candidate)
        self.assertFalse(self.first.use_fake_user)
        self.assertFalse(candidate.use_fake_user)
        self.assertIs(self.rig.animation_data.action, before[0])
        self.assertIs(self.rig.animation_data.action_slot, before[1])
        self.assertEqual(self.ui._recent, {})

    def test_retention_leaves_technical_linked_and_override_flags_untouched(self):
        for kind in ('INPUT', 'CHECKPOINT', 'LINKED', 'OVERRIDE'):
            with self.subTest(kind=kind):
                old = Action('Prior', 20)
                if kind in {'INPUT', 'CHECKPOINT'}:
                    self.mark(old, kind)
                elif kind == 'LINKED':
                    old.library = NS(filepath='source.blend')
                else:
                    old.override_library = NS()
                self.bpy.data.actions = IDList([old, self.idle])
                self.rig.animation_data = AnimData(old)
                self.choose(self.idle)
                self.assertFalse(old.use_fake_user)
                self.assertTrue(self.idle.use_fake_user)

    def test_classification_and_draw_never_change_retention_flags(self):
        self.mark(self.first)
        self.mark(self.idle, 'ARCHIVED')
        self.ui.register()
        self.ui.draw_selector(Layout(), self.context, self.rig)
        self.ui.daily_rows(self.rig)
        self.assertFalse(self.first.use_fake_user)
        self.assertFalse(self.idle.use_fake_user)

    def test_plain_native_rest_allows_action_selection_and_keeps_rest_and_mode(self):
        self.rig.data.pose_position = 'REST'
        self.assertTrue(self.ui.can_select(self.context, self.rig))
        self.choose(self.idle)
        self.assertIs(self.rig.animation_data.action, self.idle)
        self.assertEqual(self.rig.data.pose_position, 'REST')
        self.assertEqual(self.context.mode, 'POSE')

    def test_slot_fault_restores_exact_action_slot(self):
        ad = self.rig.animation_data
        old = ad.action, ad.action_slot
        ad.fail_slot = True
        with self.assertRaisesRegex(RuntimeError, 'slot assignment fault'):
            self.choose(self.idle)
        self.assertIs(ad.action, old[0]); self.assertIs(ad.action_slot, old[1])
        self.assertEqual(self.ui._recent, {})

    def test_stale_uid_and_changed_role_rejected(self):
        with self.assertRaises(ValueError):
            self.ui.activate(self.context, target_uid='100', action_uid='999', slot_handle=10)
        self.mark(self.idle, 'CHECKPOINT')
        with self.assertRaises(ValueError):
            self.choose(self.idle)
        self.assertIs(self.rig.animation_data.action, self.first)

    def test_all_busy_and_mode_guards_refuse_before_native_assignment(self):
        checks = [('mode', 'EDIT_ARMATURE'), ('tweak', True), ('preview', True), ('job', True),
                  ('original', True), ('managed', True), ('playing', True)]
        for name, _ in checks:
            with self.subTest(name=name):
                self.context.mode = 'EDIT_ARMATURE' if name == 'mode' else 'POSE'
                self.rig.animation_data.use_tweak_mode = name == 'tweak'
                self.unity.active_preview = lambda target: name == 'preview'
                self.animation._link_idle = lambda: name != 'job'
                self.context.scene.character_designer_animation_worklist.workspace_path = 'managed' if name == 'managed' else ''
                self.context.screen.is_animation_playing = name == 'playing'
                if name == 'original':
                    self.rig['character_designer_body_original_mode_v1'] = '{}'
                else:
                    self.rig.pop('character_designer_body_original_mode_v1', None)
                with self.assertRaises(ValueError):
                    self.choose(self.idle)
                self.assertIs(self.rig.animation_data.action, self.first)

    def test_retained_inputs_and_composition_markers_block_switching(self):
        for key in ('character_designer_rigging_session_v1', 'character_designer_composition_session_v1'):
            self.rig[key] = '{}'
            with self.assertRaises(ValueError):
                self.choose(self.idle)
            del self.rig[key]

    def test_new_animation_data_fault_restores_its_original_absence(self):
        self.rig.animation_data = None
        def create():
            ad = AnimData(self.first)
            ad.fail_slot = True
            self.rig.animation_data = ad
            return ad
        self.rig.animation_data_create = create
        with self.assertRaisesRegex(RuntimeError, 'slot assignment fault'):
            self.choose(self.idle)
        self.assertIsNone(self.rig.animation_data)

    def test_default_draw_never_enumerates_actions(self):
        self.ui.register()
        class NoEnumeration:
            def __iter__(self):
                raise AssertionError('Header enumerated Actions')
        self.bpy.data.actions = NoEnumeration()
        layout = Layout()
        self.assertTrue(self.ui.draw_selector(layout, self.context, self.rig))
        self.assertEqual([event[0] for event in layout.events], ['menu', 'prop'])

    def test_all_draw_reuses_native_or_returns_for_original_delegation(self):
        self.ui.register()
        setattr(self.context.window_manager, self.ui.SHOW_ALL, True)
        layout = Layout()
        self.assertFalse(self.ui.draw_selector(layout, self.context, self.rig, render_all_native=False))
        self.assertFalse(any(event[0] == 'native' for event in layout.events))
        self.header._draw_action_selector(self.context, layout)
        self.assertEqual(len(self.native_events), 1)
        sidebar = Layout()
        self.assertTrue(self.ui.draw_selector(sidebar, self.context, self.rig))
        self.assertEqual(sum(event[0] == 'native' for event in sidebar.events), 1)

    def test_native_other_modes_and_managed_context_delegate_unchanged(self):
        self.ui.register()
        layout = Layout()
        self.context.space_data.mode = 'SHAPEKEY'
        self.header._draw_action_selector(self.context, layout)
        self.assertIs(self.native_events[-1][2], layout)
        self.context.space_data.mode = 'ACTION'
        self.context.scene.character_designer_animation_worklist.items = ['managed']
        self.header._draw_action_selector(self.context, layout)
        self.assertEqual(len(self.native_events), 2)
        self.assertEqual(layout.events, [])

    def test_detached_legacy_markers_allow_browsing_but_refuse_daily_switch(self):
        self.mark(self.first, label='Short Walk')
        self.ui.register()
        for key in ('character_designer_worklist_main_session_v1',
                    'character_designer_worklist_main_restored_v1'):
            with self.subTest(marker=key):
                self.rig[key] = 'preserved exact legacy record'
                self.assertTrue(self.ui._eligible(self.context, self.rig))
                self.assertTrue(self.ui._idle(self.context, self.rig))
                self.assertFalse(self.ui.can_select(self.context, self.rig))
                layout = Layout()
                self.assertTrue(self.ui.draw_selector(layout, self.context, self.rig))
                menu_event = next(event for event in layout.events if event[0] == 'menu')
                self.assertTrue(menu_event[3])
                self.assertEqual(menu_event[2]['text'], 'Short Walk')
                menu = self.ui.CHARACTERDESIGNER_MT_working_actions()
                menu.layout = Layout()
                menu.draw(self.context)
                choices = [event for event in menu.layout.events if event[0] == 'operator']
                self.assertEqual(len(choices), 2)
                self.assertFalse(any(event[3] for event in choices))
                with self.assertRaises(ValueError):
                    self.choose(self.idle)
                self.assertEqual(self.rig[key], 'preserved exact legacy record')
                self.assertIs(self.rig.animation_data.action, self.first)
                self.assertFalse(self.first.use_fake_user)
                del self.rig[key]

    def test_detached_marker_all_uses_enabled_native_sidebar_and_original_header(self):
        self.ui.register()
        self.rig['character_designer_worklist_main_session_v1'] = '{}'
        setattr(self.context.window_manager, self.ui.SHOW_ALL, True)
        layout = Layout()
        self.assertTrue(self.ui.draw_selector(layout, self.context, self.rig))
        self.assertTrue(next(event for event in layout.events if event[0] == 'native')[3])
        self.header._draw_action_selector(self.context, Layout())
        self.assertEqual(len(self.native_events), 1)
        self.assertTrue(self.native_events[0][2].enabled)
        self.assertFalse(self.ui.can_select(self.context, self.rig))

    def test_actual_workspace_or_items_still_refuse_display_takeover_with_legacy_marker(self):
        self.ui.register()
        self.rig['character_designer_worklist_main_session_v1'] = '{}'
        saved = self.context.scene.character_designer_animation_worklist
        for workspace, items in (('active_workspace', []), ('', ['active_row'])):
            with self.subTest(workspace=workspace, items=items):
                saved.workspace_path, saved.items = workspace, items
                self.assertFalse(self.ui._eligible(self.context, self.rig))
                self.assertFalse(self.ui._idle(self.context, self.rig))
                self.assertFalse(self.ui.can_select(self.context, self.rig))
                self.assertFalse(self.ui.draw_selector(Layout(), self.context, self.rig))

    def test_registration_is_idempotent_and_unregistration_restores_original(self):
        self.ui.register()
        own = self.header.__dict__['_draw_action_selector']
        self.ui.register()
        self.assertIs(self.header.__dict__['_draw_action_selector'], own)
        self.ui.unregister(); self.ui.unregister()
        self.assertIs(self.header.__dict__['_draw_action_selector'], self.original)
        self.assertFalse(hasattr(self.bpy.types.WindowManager, self.ui.SHOW_ALL))

    def test_foreign_outer_wrapper_survives_unregistration(self):
        self.ui.register()
        inner = self.header.__dict__['_draw_action_selector']
        foreign_calls = []
        def foreign(cls, context, layout):
            foreign_calls.append(True)
            return inner.__get__(None, cls)(context, layout)
        outer = classmethod(foreign)
        self.header._draw_action_selector = outer
        self.ui.unregister()
        self.assertIs(self.header.__dict__['_draw_action_selector'], outer)
        self.header._draw_action_selector(self.context, Layout())
        self.assertEqual(foreign_calls, [True]); self.assertEqual(len(self.native_events), 1)

    def test_registration_fault_rolls_back_only_owned_classes_property(self):
        self.header._draw_action_selector = lambda *args: None
        foreign = self.header.__dict__['_draw_action_selector']
        with self.assertRaises(RuntimeError):
            self.ui.register()
        self.assertIs(self.header.__dict__['_draw_action_selector'], foreign)
        self.assertFalse(hasattr(self.bpy.types.WindowManager, self.ui.SHOW_ALL))
        self.assertFalse(any(cls.is_registered for cls in self.ui.CLASSES))

    def test_foreign_property_retained_when_registration_refuses(self):
        setattr(self.bpy.types.WindowManager, self.ui.SHOW_ALL, 'foreign')
        with self.assertRaises(RuntimeError):
            self.ui.register()
        self.assertEqual(getattr(self.bpy.types.WindowManager, self.ui.SHOW_ALL), 'foreign')
        self.assertIs(self.header.__dict__['_draw_action_selector'], self.original)

    def test_foreign_property_replacement_after_registration_survives_unload(self):
        self.ui.register()
        owned = self.ui._property_identity
        setattr(self.bpy.types.WindowManager, self.ui.SHOW_ALL, 'replacement')
        foreign = self.ui._view_property()
        self.assertIsNot(foreign, owned)
        self.ui.unregister()
        self.assertEqual(getattr(self.bpy.types.WindowManager, self.ui.SHOW_ALL), 'replacement')
        self.assertIs(self.ui._view_property(), foreign)

    def test_foreign_property_replacement_during_registration_fault_is_retained(self):
        def fault():
            setattr(self.bpy.types.WindowManager, self.ui.SHOW_ALL, 'replacement')
            raise RuntimeError('install fault')
        with patch.object(self.ui, '_install_header', fault):
            with self.assertRaisesRegex(RuntimeError, 'install fault'):
                self.ui.register()
        self.assertEqual(getattr(self.bpy.types.WindowManager, self.ui.SHOW_ALL), 'replacement')
        self.assertFalse(any(cls.is_registered for cls in self.ui.CLASSES))


class AnimationIntegrationTests(unittest.TestCase):
    """Execute the actual integrated functions, retaining unrelated fixtures."""

    def runtime_fixture(self):
        package = ModuleType('_browser_runtime_integration')
        package.__path__ = []
        browser = ModuleType(package.__name__ + '.animation_action_browser')
        browser.register, browser.unregister = Mock(), Mock()
        marker, foreign = object(), object()
        handlers = [foreign]
        stop = Mock()
        scope = {'__package__': package.__name__, 'bpy': NS(app=NS(handlers=NS(load_pre=handlers))),
                 '_animation_load_pre': marker, 'stop_animation_runtime': stop}
        tree = ast.parse(SOURCE.with_name('animation.py').read_bytes())
        names = {'register_animation_runtime', 'unregister_animation_runtime'}
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in functions}, names)
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE.with_name('animation.py')), 'exec'), scope)
        return NS(package=package, browser=browser, marker=marker, foreign=foreign,
                  handlers=handlers, stop=stop, scope=scope)

    def test_actual_runtime_registration_success_is_repeat_safe(self):
        fixture = self.runtime_fixture()
        with patch.dict(sys.modules, {fixture.package.__name__: fixture.package,
                                    fixture.browser.__name__: fixture.browser}):
            fixture.scope['register_animation_runtime']()
            fixture.scope['register_animation_runtime']()
        self.assertEqual(fixture.handlers, [fixture.foreign, fixture.marker])
        self.assertEqual(fixture.browser.register.call_count, 2)
        fixture.stop.assert_not_called()

    def test_actual_registration_failure_removes_only_newly_added_handler(self):
        for already_present in (False, True):
            with self.subTest(already_present=already_present):
                fixture = self.runtime_fixture()
                if already_present:
                    fixture.handlers.append(fixture.marker)
                before = list(fixture.handlers)
                fixture.browser.register.side_effect = RuntimeError('browser admission fault')
                with patch.dict(sys.modules, {fixture.package.__name__: fixture.package,
                                             fixture.browser.__name__: fixture.browser}):
                    with self.assertRaisesRegex(RuntimeError, 'browser admission fault'):
                        fixture.scope['register_animation_runtime']()
                self.assertEqual(fixture.handlers, before)
                fixture.stop.assert_not_called()
                fixture.browser.unregister.assert_not_called()

    def test_actual_unregistration_stops_runtime_removes_handler_and_unloads_browser(self):
        fixture = self.runtime_fixture()
        fixture.handlers.append(fixture.marker)
        calls = []
        fixture.stop.side_effect = lambda: calls.append('stop')
        fixture.browser.unregister.side_effect = lambda: calls.append('browser')
        with patch.dict(sys.modules, {fixture.browser.__name__: fixture.browser}):
            fixture.scope['unregister_animation_runtime']()
        self.assertEqual(calls, ['stop', 'browser'])
        self.assertEqual(fixture.handlers, [fixture.foreign])

    def test_actual_unregistration_preserves_cleanup_when_browser_not_loaded(self):
        fixture = self.runtime_fixture()
        fixture.handlers.append(fixture.marker)
        with patch.dict(sys.modules, {fixture.package.__name__: fixture.package}):
            sys.modules.pop(fixture.browser.__name__, None)
            fixture.scope['unregister_animation_runtime']()
            self.assertNotIn(fixture.browser.__name__, sys.modules)
        fixture.stop.assert_called_once_with()
        self.assertEqual(fixture.handlers, [fixture.foreign])

    def panel_fixture(self, browser=None):
        # This harness compiles the actual CHARACTERDESIGNER_PT_animation.draw;
        # its native layout rejects accidental file reads or Action creation.
        from test_animation_sections_ui import AnimationSectionsTests
        case = AnimationSectionsTests('test_unmanaged_native_action_and_slot_remain_visible_with_unity_collapsed')
        case.setUp()
        self.addCleanup(case.doCleanups)
        name = '_unmanaged_animation_section_fixture.animation_action_browser'
        with patch.dict(sys.modules, {}):
            sys.modules.pop(name, None)
            if browser is not None:
                sys.modules[name] = browser
            result = case.draw_unmanaged_animation()
        return result, case.context

    def test_actual_panel_without_browser_preserves_171_native_fallback(self):
        result, _ = self.panel_fixture()
        events = result.layout.events
        self.assertEqual(sum(event[0] == 'template_action' for event in events), 1)
        self.assertEqual(sum(event[0] == 'template_search' for event in events), 1)
        result.rig.animation_data_create.assert_not_called()

    def test_actual_panel_handled_browser_draws_no_duplicate_native_picker(self):
        browser = NS(draw_selector=Mock(return_value=True))
        result, context = self.panel_fixture(browser)
        browser.draw_selector.assert_called_once()
        args = browser.draw_selector.call_args.args
        self.assertIs(args[1], context)
        self.assertIs(args[2], result.rig)
        self.assertFalse(any(event[0] in {'template_action', 'template_search', 'context_pointer'}
                             for event in result.layout.events))

    def test_actual_panel_unhandled_browser_retains_exact_native_action_and_slot(self):
        browser = NS(draw_selector=Mock(return_value=False))
        result, _ = self.panel_fixture(browser)
        browser.draw_selector.assert_called_once()
        events = result.layout.events
        action = next(event for event in events if event[0] == 'template_action')
        slot = next(event for event in events if event[0] == 'template_search')
        self.assertIs(action[4], result.rig)
        self.assertEqual(action[3], {'new': 'action.new', 'unlink': 'action.unlink', 'text': 'Action'})
        self.assertEqual(slot[4], (result.rig.animation_data, 'action_slot',
                                   result.rig.animation_data, 'action_suitable_slots'))
        self.assertEqual(sum(event[0] == 'template_action' for event in events), 1)


if __name__ == '__main__':
    unittest.main()
