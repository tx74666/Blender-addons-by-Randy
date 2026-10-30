"""Private native dependency enable/disable/re-enable and fault evidence.

Launch only through run_trusted_blender_validation.py. --source-root is the
complete staged character_designer package. No download or preference save.
"""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

import bpy
import addon_utils
from bpy.props import BoolProperty, PointerProperty


class CD106_ForeignSchema(bpy.types.PropertyGroup):
    value: BoolProperty()


def preferences_pin():
    path = Path(bpy.utils.user_resource('CONFIG')) / 'userpref.blend'
    return {'path': str(path), 'exists': path.exists(),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None}


def fixture():
    mesh = bpy.data.meshes.new('Dependency Author Data')
    mesh.from_pydata(((0, 0, 0), (1, 0, 0), (0, 1, 0)), (), ((0, 1, 2),))
    obj = bpy.data.objects.new('Dependency Author', mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.shape_key_add(name='Basis')
    obj.shape_key_add(name='Author Shape').data[2].co.z = 0.125
    obj.vertex_groups.new(name='Author Weight').add((0, 1), 0.375, 'REPLACE')
    obj['author'] = 'keep'
    obj.keyframe_insert('location', frame=1)
    obj.location.x = 0.25
    obj.keyframe_insert('location', frame=8)
    bpy.context.scene.frame_set(4)
    return obj


def author_state():
    objects = []
    for obj in bpy.data.objects:
        row = {'pointer': obj.as_pointer(), 'name': obj.name, 'mode': obj.mode,
               'parent': obj.parent.as_pointer() if obj.parent else None,
               'location': tuple(obj.location), 'rotation': tuple(obj.rotation_euler),
               'scale': tuple(obj.scale), 'custom': {key: repr(obj[key]) for key in obj.keys()}}
        if obj.type == 'MESH':
            row.update(vertices=[tuple(vertex.co) for vertex in obj.data.vertices],
                polygons=[tuple(face.vertices) for face in obj.data.polygons],
                weights=[[(group.group, group.weight) for group in vertex.groups] for vertex in obj.data.vertices],
                keys=[(key.name, key.value, [tuple(point.co) for point in key.data])
                      for key in obj.data.shape_keys.key_blocks] if obj.data.shape_keys else [])
        objects.append(row)
    actions = []
    for action in bpy.data.actions:
        curves = []
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    curves.extend((curve.data_path, curve.array_index,
                        [(tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
                          point.interpolation, point.handle_left_type, point.handle_right_type)
                         for point in curve.keyframe_points]) for curve in bag.fcurves)
        actions.append((action.as_pointer(), action.name, curves))
    return dict(objects=objects, actions=actions,
        context=(bpy.context.mode, bpy.context.scene.frame_current, bpy.context.scene.frame_subframe,
                 bpy.context.view_layer.objects.active.as_pointer() if bpy.context.view_layer.objects.active else None,
                 tuple(obj.as_pointer() for obj in bpy.context.selected_objects)),
        preference_addons=sorted(bpy.context.preferences.addons.keys()), preferences_disk=preferences_pin())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    assert not args.output.exists()
    sys.path.insert(0, str(args.source_root.resolve().parent))
    from character_designer import physics_dependencies as dependency
    from character_designer import skirt_wiggle as skirt
    report = dict(schema='cd106.native-physics-dependency-registration/1', passed=False,
        errors=[], checks={}, autoexec_enabled=True, source_root=str(args.source_root),
        original_preferences_saved=False, artist_operations=False, fault_evidence={})
    try:
        fixture()
        before = author_state()
        info = dependency.status('SKIRT', refresh=True)
        assert info['state'] == 'DISABLED', info
        name = info['module']
        # An unknown native schema must reject before official activation.
        bpy.utils.register_class(CD106_ForeignSchema)
        bpy.types.Scene.wiggle = PointerProperty(type=CD106_ForeignSchema)
        try:
            with patch.object(addon_utils, 'enable', wraps=addon_utils.enable) as enable_spy:
                try:
                    dependency.enable('SKIRT')
                except ValueError:
                    pass
                else:
                    raise AssertionError('Unknown schema was overwritten')
                assert enable_spy.call_count == 0
            assert dependency.status('SKIRT')['state'] == 'CONFLICT'
            assert author_state() == before
            report['checks']['unknown_schema_zero_enable_no_author_write'] = True
        finally:
            del bpy.types.Scene.wiggle
            bpy.utils.unregister_class(CD106_ForeignSchema)
        # Real official enable catches this owned register failure and clears
        # sys.modules/check(), while the unique native property remains.
        mod = importlib.import_module(name)
        mod.__time__ = os.path.getmtime(mod.__file__)
        original_register = mod.register
        def failed_register():
            bpy.types.Object.wiggle_dependency_failure = BoolProperty(name='Owned failure witness')
            raise RuntimeError('owned native partial registration fault')
        mod.register = failed_register
        try:
            try:
                dependency.enable('SKIRT')
            except ValueError as error:
                report['fault_evidence']['exception'] = str(error)
            else:
                raise AssertionError('Partial registration unexpectedly succeeded')
            evidence = dependency.status('SKIRT')
            report['fault_evidence']['dependency'] = evidence
            report['fault_evidence']['native_registration'] = dependency._registration_state((mod,))
            report['fault_evidence']['official_check'] = addon_utils.check(name)
            assert evidence['state'] == 'CLEANUP_FAILED' and not evidence['cleanup_exact']
            assert not addon_utils.check(name)[1]
            assert hasattr(bpy.types.Object, 'wiggle_dependency_failure')
            assert author_state() == before
            report['checks']['partial_register_check_false_reports_native_cleanup_failure'] = True
        finally:
            mod.register = original_register
            if hasattr(bpy.types.Object, 'wiggle_dependency_failure'):
                del bpy.types.Object.wiggle_dependency_failure
            dependency._CACHE.clear()
        enabled = dependency.enable('SKIRT')
        assert enabled['state'] == 'READY' and skirt._backend().__name__ == name
        assert author_state() == before
        report['checks']['official_enable_verified_no_author_or_pref_write'] = True
        current = sys.modules[name]
        with patch.object(current, 'register', side_effect=AssertionError('READY must not re-register')):
            with patch.object(addon_utils, 'enable', wraps=addon_utils.enable) as enable_spy:
                assert dependency.enable('SKIRT')['state'] == 'READY'
                assert enable_spy.call_count == 0
        assert author_state() == before
        report['checks']['ready_recheck_zero_reregister'] = True
        # Exercise the common native lifecycle, including exact flat residue.
        addon_utils.disable(name, default_set=False)
        report['after_native_disable'] = dependency._registration_state()
        disabled = dependency.status('SKIRT', refresh=True)
        report['after_native_disable_dependency'] = disabled
        assert disabled['state'] == 'DISABLED' and disabled.get('verified_flat_residue')
        assert dependency.enable('SKIRT')['state'] == 'READY'
        assert skirt._backend().__name__ == name and author_state() == before
        report['checks']['official_enable_disable_service_reenable'] = True
        with patch.object(skirt, '_backend', side_effect=ValueError('owned exact-handler refusal')):
            with patch.object(addon_utils, 'enable', wraps=addon_utils.enable) as enable_spy:
                with patch.object(addon_utils, 'disable', wraps=addon_utils.disable) as disable_spy:
                    try:
                        dependency.enable('SKIRT')
                    except ValueError as error:
                        assert 'exact-handler refusal' in str(error)
                    else:
                        raise AssertionError('Enabled but incompatible proof was ignored')
                    assert enable_spy.call_count == disable_spy.call_count == 0
        report['checks']['enabled_backend_refusal_keeps_exact_reason_no_toggle'] = True
        assert dependency.status('SKIRT', refresh=True)['state'] == 'READY'
        # This host's Hair 1.1.2 reference cache is intentionally not installed.
        hair = dependency.status('HAIR', refresh=True)
        report['hair_dependency'] = hair
        assert hair['state'] == 'MISSING'
        with patch.object(addon_utils, 'disable', wraps=addon_utils.disable) as disable_spy:
            try:
                dependency.enable('HAIR')
            except ValueError:
                pass
            else:
                raise AssertionError('Missing Hair backend was claimed enabled')
            assert disable_spy.call_count == 0
        assert addon_utils.check(name)[1] and skirt._backend().__name__ == name
        assert author_state() == before
        report['checks']['missing_hair_does_not_disable_enabled_skirt'] = True
        report['passed'] = all(report['checks'].values())
    except BaseException:
        report['errors'].append(traceback.format_exc())
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        print(json.dumps({'native_dependency_passed': report['passed'], 'checks': report['checks'],
                          'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
