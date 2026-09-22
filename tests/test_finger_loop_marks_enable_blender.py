"""Real restricted enable, hot reload and failed-registration rollback.

Run in a disposable --background --factory-startup Blender process. This test
does not load or save a blend file, and deliberately uses addon_utils instead
of calling register() directly: restricted registration caused the regression.
"""
import importlib
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import addon_utils
import bpy

ADDON = 'character_designer'
ADDONS = str(Path(__file__).resolve().parents[1]/'addons')
INIT = os.path.normcase(os.path.realpath(os.path.join(ADDONS, ADDON, '__init__.py')))
HANDLERS = ('load_pre', 'load_post', 'undo_pre', 'undo_post', 'redo_pre', 'redo_post',
            'depsgraph_update_post', 'save_pre', 'save_post', 'frame_change_post')
MARKS = 'character_designer_finger_loop_marks'


def callbacks():
    return {value for name, module in tuple(sys.modules.items())
            if name == ADDON or name.startswith(ADDON+'.')
            for value in vars(module).values()
            if isinstance(value, types.FunctionType) and value.__module__.startswith(ADDON)}


def handler_counts():
    return {name: sum(getattr(fn, '__module__', '').startswith(ADDON)
                      for fn in getattr(bpy.app.handlers, name)) for name in HANDLERS}


def no_old_runtime(functions):
    assert not any(bpy.app.timers.is_registered(fn) for fn in functions), 'Old timer survived reload'
    assert not any(fn in functions for name in HANDLERS for fn in getattr(bpy.app.handlers, name)), 'Old handler survived reload'


def registered(module, expected_handlers=None):
    assert module is not None and module.__addon_enabled__, 'Real addon_utils.enable failed'
    assert os.path.normcase(os.path.realpath(module.__file__)) == INIT, module.__file__
    assert module.bl_info['version'] >= (0, 61, 55), module.bl_info['version']
    module._validate_registration_integrity()
    assert all(module._registered_rna_class(cls) is cls for cls in module.CLASSES)
    loop_class = bpy.types.Operator.bl_rna_get_subclass_py('CHARACTER_DESIGNER_OT_finger_loop_marks')
    assert loop_class is sys.modules[ADDON+'.finger_loop_marks_ui'].CHARACTERDESIGNER_OT_finger_loop_marks
    if expected_handlers is not None:
        assert handler_counts() == expected_handlers, (handler_counts(), expected_handlers)


def unregistered(module, baseline):
    assert all(module._registered_rna_class(cls) is None for cls in module.CLASSES), 'Failed cleanup left registered RNA'
    for owner, name in ((bpy.types.Scene, 'character_designer_setup'),
                        (bpy.types.Object, 'character_designer_unity_export'),
                        (bpy.types.Object, 'character_designer_finger_bank'),
                        (bpy.types.Scene, 'character_designer_finger_setup'),
                        (bpy.types.Scene, 'character_designer_finger_definition'),
                        (bpy.types.Scene, 'character_designer_finger_flex')):
        assert not hasattr(owner, name), name
    assert not any(hasattr(bpy.types.WindowManager, name) for name, _ in module._WINDOW_MANAGER_POINTER_TYPES)
    assert handler_counts() == baseline, (handler_counts(), baseline)


def fixture():
    mesh = bpy.data.meshes.new('ReloadMesh')
    mesh.from_pydata(((0., 0., 0.), (1., 0., 0.), (1., 1., 0.), (0., 1., 0.)),
                     (), ((0, 1, 2, 3),))
    obj = bpy.data.objects.new('ReloadCharacter', mesh)
    bpy.context.collection.objects.link(obj)
    for selected in bpy.context.selected_objects: selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name='Artist')
    key.data[2].co.z = .031
    key.value = .35
    obj.vertex_groups.new(name='ArtistWeight').add((0, 1, 2, 3), .375, 'REPLACE')
    obj['artist_note'] = 'Keep this unsaved object and its data through reload.'
    state = obj.character_designer_finger_bank
    state.active = 'INDEX.L'
    state.visible_digits = {'INDEX'}
    state.selection_initialized = True
    state.survey = json.dumps({'stamp': 'saved-metadata', 'candidates': {}, 'warnings': {}})
    slot = state.slots.add()
    slot.name = 'INDEX.L'
    slot.guide.source = obj
    slot.guide.record = json.dumps({'reload_test_reference': 'unchanged'})
    slot.guide.revision = 'saved-revision'
    slot.guide.use_basis = slot.guide.confirmed = True
    slot.bones = json.dumps({'saved_binding': 'unchanged'})
    bpy.context.scene.character_designer_finger_setup = obj
    bpy.context.scene.character_designer_setup.body = obj
    coordinates = ((0., 0., 0.), (1., 0., 0.), (1., 1., 0.), (0., 1., 0.))
    obj[MARKS] = json.dumps({'INDEX.L': {str(i): {'version': 1, 'key': 'INDEX.L',
        'basis_key': 'Basis', 'ids': [0, 1, 2, 3], 'coordinates': coordinates,
        'center': [.5, .5, 0.], 'normal': [0., 0., 1.], 'fraction': i/3} for i in (1, 2)}})
    return obj


def artist_state(obj):
    return dict(objects=tuple(sorted(bpy.data.objects.keys())),
                mesh=obj.data.as_pointer(), vertices=tuple(tuple(v.co) for v in obj.data.vertices),
                polygons=tuple(tuple(p.vertices) for p in obj.data.polygons),
                keys=tuple((key.name, key.value, tuple(tuple(v.co) for v in key.data))
                           for key in obj.data.shape_keys.key_blocks),
                weights=tuple(tuple((g.group, g.weight) for g in v.groups) for v in obj.data.vertices),
                active=bpy.context.view_layer.objects.active, mode=obj.mode,
                selected=tuple(sorted(o.name for o in bpy.context.selected_objects)),
                note=obj['artist_note'], marks=obj[MARKS])


def reference_state(obj):
    state = obj.character_designer_finger_bank
    slot = state.slots.get('INDEX.L')
    return (state.active, frozenset(state.visible_digits), state.selection_initialized, state.survey,
            slot.guide.source, slot.guide.record, slot.guide.revision, slot.guide.confirmed,
            slot.guide.use_basis, slot.bones,
            bpy.context.scene.character_designer_finger_setup,
            bpy.context.scene.character_designer_setup.body)


def main():
    assert not hasattr(bpy.types.WindowManager, 'character_designer'), 'Use --factory-startup'
    sys.path.insert(0, ADDONS)
    baseline = handler_counts()
    errors = []
    module = None
    try:
        module = addon_utils.enable(ADDON, default_set=False, refresh_handled=True, handle_error=errors.append)
        assert not errors, repr(errors)
        registered(module)
        expected_handlers = handler_counts()
        obj = fixture()
        mesh_before, reference_before = artist_state(obj), reference_state(obj)
        print('PASS real_restricted_enable', flush=True)

        old, functions = module, callbacks()
        addon_utils.disable(ADDON, default_set=False, refresh_handled=True, handle_error=errors.append)
        assert not errors, repr(errors)
        unregistered(old, baseline)
        no_old_runtime(functions)
        assert artist_state(obj) == mesh_before
        module = addon_utils.enable(ADDON, default_set=False, refresh_handled=True, handle_error=errors.append)
        assert not errors, repr(errors)
        registered(module, expected_handlers)
        assert reference_state(obj) == reference_before
        print('PASS real_disable_reenable_preserves_metadata', flush=True)

        for _ in range(2):
            old, functions = module, callbacks()
            old._reload_addon_deferred()
            module = sys.modules.get(ADDON)
            assert module is not old, 'Hot reload silently rolled back'
            registered(module, expected_handlers)
            no_old_runtime(functions)
            assert artist_state(obj) == mesh_before and reference_state(obj) == reference_before
        print('PASS real_hot_reload_no_stale_rna_or_runtime', flush=True)

        # Fail after the new loop runtime has partially registered. The failure
        # occurs under Blender's real RestrictBlend wrapper, not a mock context.
        old = module
        actual_enable = addon_utils.enable
        failed_modules, failed_functions, injected_errors = [], [], []

        def fail_enable(name, **kwargs):
            fresh = importlib.import_module(name)
            fresh.__time__ = os.path.getmtime(fresh.__file__)
            ui = sys.modules[ADDON+'.finger_loop_marks_ui']
            actual_register = ui.register_runtime
            failed_modules.append(fresh)
            failed_functions.extend(callbacks())

            def register_then_fail():
                assert not hasattr(bpy.context, 'scene'), 'Injection did not run under RestrictBlend'
                actual_register()
                raise RuntimeError('Injected loop runtime registration failure')

            kwargs['handle_error'] = injected_errors.append
            with patch.object(ui, 'register_runtime', side_effect=register_then_fail):
                result = actual_enable(name, **kwargs)
            assert result is None and len(injected_errors) == 1
            assert str(injected_errors[0]) == 'Injected loop runtime registration failure', injected_errors
            unregistered(fresh, baseline)
            no_old_runtime(failed_functions)
            return result

        with patch.object(addon_utils, 'enable', side_effect=fail_enable):
            old._reload_addon_deferred()
        module = sys.modules.get(ADDON)
        assert module is old, 'Failed reload did not restore previous Python module'
        assert len(failed_modules) == 1 and len(injected_errors) == 1
        registered(module, expected_handlers)
        assert module.ADDON_REFRESH_LAST_ERROR and not module.ADDON_REFRESH_PENDING
        assert all(module._registered_rna_class(cls) is not cls for cls in failed_modules[0].CLASSES)
        no_old_runtime(failed_functions)
        assert artist_state(obj) == mesh_before and reference_state(obj) == reference_before
        print('PASS restricted_failure_cleanup_and_old_module_rollback', flush=True)
    finally:
        addon_utils.disable(ADDON, default_set=False, refresh_handled=True)
    if module is not None:
        unregistered(module, baseline)
    print('FINGER_LOOP_MARKS_ENABLE_PASS', flush=True)


if __name__ == '__main__':
    main()
