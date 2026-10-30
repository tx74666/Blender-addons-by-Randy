"""Small isolated native RNA cycle probe; no artist scene or preference save.

Run only through run_trusted_blender_validation.py with --source-root pointing
to a complete staged character_designer package. Writes checkpoints before each
native operation so a native fault retains its last completed phase.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import traceback

import addon_utils
import bpy


def context_pin():
    return dict(file=bpy.data.filepath, dirty=bpy.data.is_dirty,
        mode=bpy.context.mode, frame=bpy.context.scene.frame_current,
        subframe=bpy.context.scene.frame_subframe,
        active=bpy.context.view_layer.objects.active.as_pointer() if bpy.context.view_layer.objects.active else None,
        selected=[obj.as_pointer() for obj in bpy.context.selected_objects],
        objects=[(obj.as_pointer(), obj.name, obj.data.as_pointer() if obj.data else None)
                 for obj in bpy.data.objects],
        preference_addons=sorted(bpy.context.preferences.addons.keys()))


def preference_pin():
    path = Path(bpy.utils.user_resource('CONFIG')) / 'userpref.blend'
    return dict(path=str(path), exists=path.exists(),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    assert not args.output.exists(), args.output
    sys.path.insert(0, str(args.source_root.resolve().parent))
    from character_designer import physics_dependencies as dependency
    from character_designer import skirt_wiggle as skirt
    report = dict(schema='cd106.safe-native-schema-cycle/1', passed=False,
        source_root=str(args.source_root.resolve()), checkpoints=[], checks={},
        errors=[], autoexec_enabled=True, preferences_saved=False,
        artist_operations=False)
    def checkpoint(phase, **values):
        report['checkpoints'].append(dict(phase=phase, **values))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    try:
        before, prefs = context_pin(), preference_pin()
        checkpoint('before_inspection')
        info = dependency.status('SKIRT', refresh=True)
        assert info['state'] == 'DISABLED', info
        name = info['module']
        checkpoint('initial_disabled', status=info, native=dependency._registration_state())
        enabled = dependency.enable('SKIRT')
        assert enabled['state'] == 'READY', enabled
        assert skirt._backend().__name__ == name
        native_live = dependency._registration_state()
        assert len(native_live['schema']) == 43, native_live
        report['checks']['official_enable_original_backend_proof'] = True
        checkpoint('live_ready', status=enabled, native=native_live)
        addon_utils.disable(name, default_set=False)
        checkpoint('official_disable_returned')
        # Never read PointerRNA.fixed_type here. The shared PG target has been
        # freed by the official unregister, while its property remains present.
        native_disabled = dependency._registration_state()
        assert native_disabled['schema'] == native_live['schema']
        assert not native_disabled['classes']
        assert not native_disabled['wiggle_named_handlers']
        report['checks']['opaque_schema_inspection_after_disable'] = True
        checkpoint('disabled_schema_safe', native=native_disabled)
        try:
            skirt._backend()
        except ValueError as error:
            report['disabled_backend_reason'] = str(error)
            assert 'disabled' in str(error).lower(), error
        else:
            raise AssertionError('Disabled backend incorrectly accepted')
        skirt._BACKEND_CACHE, skirt._BACKEND_CACHE_TIME = None, 0.0
        assert not skirt.available()['available']
        report['checks']['direct_backend_and_available_safe_refusal'] = True
        checkpoint('disabled_adapter_safe')
        disabled = dependency.status('SKIRT', refresh=True)
        assert disabled['state'] == 'DISABLED', disabled
        assert disabled['verified_flat_residue'], disabled
        checkpoint('own_orphan_proof', status=disabled)
        ready = dependency.enable('SKIRT')
        assert ready['state'] == 'READY', ready
        assert skirt._backend().__name__ == name
        report['checks']['official_reenable_original_backend_proof'] = True
        assert context_pin() == before
        assert preference_pin() == prefs
        report['checks']['context_ids_and_preferences_unchanged'] = True
        report['passed'] = True
        checkpoint('complete', status=ready, native=dependency._registration_state())
    except Exception:
        report['errors'].append(traceback.format_exc())
        checkpoint('python_failure')
        raise


if __name__ == '__main__':
    main()
