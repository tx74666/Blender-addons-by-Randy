"""Registration branch parity and failure cleanup without starting Blender.

The product functions run unchanged against native-registration stand-ins.
These checks do not replace real RNA/keymap enable/disable validation.
"""
import ast
import builtins
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/__init__.py'
STARTUP = (
    'register_mesh_mirror_runtime', 'register_refine_symmetry_runtime',
    'register_finger_bones_runtime', 'register_finger_root_runtime',
    'register_animation_runtime', 'register_composition_runtime',
    'register_rigging_runtime', 'register_reference_view_handlers',
    'body_calibration_ui.register', 'control_pose_assets.register',
    'control_pose_capture.register', 'control_weight_paint.register',
    'register_limb_ik_viewport_handler', 'register_bone_collection_handlers',
    'register_bone_display_sync', 'register_forearm_twist_runtime',
    'playback_normal_bridge.register', '_register_workspace_filter_guard',
    '_register_source_watch', 'generated_names.register_handlers',
    'register_hair_motion_guards', 'skirt_wiggle_ui.register',
    'skirt_wiggle.register', 'limb_ik_wrist_rotation_ui.register',
)
SCHEMA = {
    'WindowManager': (
        'character_designer', 'character_designer_delta', 'character_designer_references',
        'character_designer_spline_ik', 'character_designer_limb_ik',
        'character_designer_forearm_twist', 'character_designer_finger_root',
        'character_designer_hair_bones', 'character_designer_hair_motion',
        'character_designer_skirt', 'character_designer_animation', 'update_views',
    ),
    'Scene': ('character_designer_setup', 'character_designer_body_region',
              'character_designer_ground', 'character_designer_animation_worklist'),
    'Object': ('character_designer_unity_export', 'update_settings'),
}


class RegistrationModel:
    def __init__(self):
        self.events, self.installs, self.classes, self.cleanup_options = [], [], [], []
        self.live, self.catalog_properties = set(), set()
        self.fail_hook = None
        self.integrity_error = None
        self.types = NS(**{name: type(name, (), {}) for name in SCHEMA})
        package = NS()
        module_names = (
            'body_calibration_ui', 'control_pose_assets', 'control_pose_capture',
            'control_weight_paint', 'generated_names', 'playback_normal_bridge',
            'limb_ik_wrist_rotation_ui', 'custom_vertex_groups', 'custom_vertex_parts',
            'unity_update_selection_ui', 'skirt_wiggle_ui', 'skirt_wiggle',
        )
        for name in module_names:
            module = NS(register=self.hook(name + '.register'),
                        unregister=self.cleanup(name + '.unregister'),
                        register_handlers=self.hook(name + '.register_handlers'),
                        unregister_handlers=self.cleanup(name + '.unregister_handlers'))
            setattr(package, name, module)
        for name in ('custom_vertex_groups', 'custom_vertex_parts'):
            module = getattr(package, name)
            module.register_properties = self.catalog_register(name)
            module.unregister_properties = self.catalog_unregister(name)
        updates = package.unity_update_selection_ui
        updates.SETTINGS_PROPERTY, updates.VIEW_PROPERTY = 'update_settings', 'update_views'
        updates.register_properties = self.register_updates
        updates.unregister_properties = self.unregister_updates

        def import_module(name, globals=None, locals=None, fromlist=(), level=0):
            if not name and level == 1:
                return package
            return builtins.__import__(name, globals, locals, fromlist, level)

        namespace = dict(vars(package), __builtins__=dict(vars(builtins), __import__=import_module),
            __package__='registration_test', bpy=NS(types=self.types, context=NS(),
                utils=NS(register_class=self.register_class, unregister_class=self.unregister_class)),
            PointerProperty=lambda **kwargs: NS(**kwargs),
            _validate_registration_integrity=self.check_integrity,
            _settings=lambda context: None,
            _stop_live_preview=lambda **kwargs: self.events.append(('cleanup', 'live_preview')),
            stop_worklist_ui=lambda: self.events.append(('cleanup', 'worklist_ui')))
        for name in STARTUP:
            if '.' not in name:
                namespace[name] = self.hook(name)
                cleanup = name.replace('register_', 'unregister_', 1)
                namespace[cleanup] = self.cleanup(cleanup)
        state_names = (
            'CharacterDesignerSetup', 'CharacterDesignerBodyRegionState',
            'CharacterDesignerGroundState', 'CharacterDesignerAnimationWorklistState',
            'CharacterDesignerUnityExport', 'CharacterDesignerState',
            'CharacterDesignerHairBonesState', 'CharacterDesignerHairMotionState',
            'CharacterDesignerSkirtState', 'CharacterDesignerAnimationState',
            'CharacterDesignerDeltaState', 'CharacterDesignerLimbIKState',
            'CharacterDesignerForearmTwistState', 'CharacterDesignerFingerRootState',
            'CharacterDesignerSplineIKState', 'CharacterDesignerReferenceState',
        )
        for name in state_names:
            namespace[name] = type(name, (), {})
        namespace['CLASSES'] = tuple(namespace[name] for name in state_names)
        tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'register', '_register_runtime_hooks'}]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
        self.subject = namespace['register']

    def hook(self, name):
        def apply():
            self.events.append(('hook', name))
            if name == self.fail_hook:
                raise RuntimeError('injected hook failure: ' + name)
            if name not in self.live:
                self.live.add(name)
                self.installs.append(name)
        return apply

    def cleanup(self, name):
        def apply(*arguments, **keywords):
            self.events.append(('cleanup', name))
            self.cleanup_options.append((name, arguments, keywords))
            self.live.discard(name.replace('unregister', 'register', 1))
        return apply

    def catalog_register(self, name):
        def apply():
            self.events.append(('property', name))
            self.catalog_properties.add(name)
        return apply

    def catalog_unregister(self, name):
        return lambda: self.catalog_properties.discard(name)

    def register_updates(self):
        self.events.append(('property', 'unity_update_selection_ui'))
        for owner, name in ((self.types.Object, 'update_settings'),
                            (self.types.WindowManager, 'update_views')):
            if not hasattr(owner, name):
                setattr(owner, name, object())

    def unregister_updates(self):
        for owner, name in ((self.types.Object, 'update_settings'),
                            (self.types.WindowManager, 'update_views')):
            if hasattr(owner, name):
                delattr(owner, name)

    def register_class(self, cls):
        self.events.append(('class', cls.__name__))
        self.classes.append(cls)

    def unregister_class(self, cls):
        self.events.append(('unclass', cls.__name__))
        self.classes.remove(cls)

    def check_integrity(self):
        self.events.append(('integrity', None))
        if self.integrity_error:
            raise self.integrity_error

    def schema(self):
        return {(owner, name): getattr(getattr(self.types, owner), name)
                for owner, names in SCHEMA.items() for name in names
                if hasattr(getattr(self.types, owner), name)}

    def hook_calls(self):
        return tuple(name for kind, name in self.events if kind == 'hook')


class RegistrationHooksTests(unittest.TestCase):
    def test_initial_startup_keeps_existing_hook_order(self):
        model = RegistrationModel()
        model.subject()
        self.assertEqual(model.hook_calls(), STARTUP)
        self.assertEqual(len(model.schema()), sum(map(len, SCHEMA.values())))

    def test_repeated_registration_reuses_all_hooks_without_replacing_schema_or_classes(self):
        model = RegistrationModel()
        model.subject()
        before, classes = model.schema(), tuple(model.classes)
        model.events.clear()
        model.subject()
        self.assertEqual(model.hook_calls(), STARTUP)
        self.assertEqual(model.events[0], ('integrity', None))
        self.assertFalse(any(kind == 'class' for kind, _name in model.events))
        self.assertEqual(tuple(model.classes), classes)
        self.assertEqual(model.installs, list(STARTUP))
        self.assertEqual(model.schema(), before)

    def test_repeated_registration_restores_a_missing_wrist_hook(self):
        model = RegistrationModel()
        model.subject()
        before = model.schema()
        wrist = 'limb_ik_wrist_rotation_ui.register'
        model.live.remove(wrist)
        model.events.clear()
        model.subject()
        self.assertIn(wrist, model.live)
        self.assertEqual(model.installs.count(wrist), 2)
        self.assertEqual(model.schema(), before)

    def test_partial_registration_refuses_before_installing_hooks_or_properties(self):
        model = RegistrationModel()
        model.types.WindowManager.character_designer = object()
        before = model.schema()
        with self.assertRaisesRegex(RuntimeError, 'partially registered'):
            model.subject()
        self.assertEqual(model.events, [])
        self.assertEqual(model.schema(), before)

    def test_integrity_failure_preserves_existing_registration(self):
        model = RegistrationModel()
        model.subject()
        before, live = model.schema(), model.live.copy()
        model.integrity_error = RuntimeError('invalid registration')
        model.events.clear()
        with self.assertRaisesRegex(RuntimeError, 'invalid registration'):
            model.subject()
        self.assertEqual(model.events, [('integrity', None)])
        self.assertEqual(model.schema(), before)
        self.assertEqual(model.live, live)

    def test_early_and_last_hook_failure_keep_fresh_registration_cleanup(self):
        for failure in (STARTUP[0], STARTUP[-1]):
            with self.subTest(failure=failure):
                model = RegistrationModel()
                model.fail_hook = failure
                with self.assertRaisesRegex(RuntimeError, 'injected hook failure'):
                    model.subject()
                self.assertEqual(model.schema(), {})
                self.assertEqual(model.classes, [])
                self.assertEqual(model.live, set())
                self.assertEqual(model.catalog_properties, set())
                self.assertEqual([options for options in model.cleanup_options
                                  if options[0] == 'playback_normal_bridge.unregister'],
                                 [('playback_normal_bridge.unregister', (), {'recover_pending': False})])
                model.fail_hook = None
                model.events.clear()
                model.subject()
                self.assertEqual(model.hook_calls(), STARTUP)


if __name__ == '__main__':
    unittest.main()
