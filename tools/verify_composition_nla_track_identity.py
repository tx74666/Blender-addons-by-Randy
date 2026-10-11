"""Opt-in isolated Blender 5.2 API probe; never imports or deploys the add-on.

Root must separately authorize a factory-startup background launch. This file
was prepared as source only; its presence is not native acceptance evidence.
"""
import argparse
import ast
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback


OLD_SHA = 'c6b5c2f564239b002cfa5ff1be07bb3707e1cedcec4987bccad245710dc055f1'
NEW_SHA = '1861bcf7136e389da2cd2c40af57e7c5ea33c9bd8b9b90bf0557b8f649584ea5'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def extracted(path, expected, bpy):
    path = Path(path).resolve()
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected, 'Pinned source changed: ' + str(path))
    tree = ast.parse(raw.decode('utf-8-sig'))
    names = ('_nla_context', '_tweak')
    nodes = [next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
             for name in names]
    proof = {'path': str(path), 'sha256': expected, 'functions': {
        node.name: hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
        for node in nodes}}
    namespace = {'bpy': bpy, 'contextmanager': contextmanager}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['_tweak'], proof


def action(bpy, rig, name, component, value):
    result = bpy.data.actions.new(name)
    slot = result.slots.new(id_type='OBJECT', name=rig.name)
    bag = result.layers.new('Probe').strips.new(type='KEYFRAME').channelbags.new(slot)
    curve = bag.fcurves.new('pose.bones["Probe"].location', index=component)
    for frame in (1, 11):
        point = curve.keyframe_points.insert(frame, value)
        point.interpolation = 'LINEAR'
    return result, slot


def track(ad, name, selected_action, slot, start=1):
    result = ad.nla_tracks.new()
    result.name = name
    strip = result.strips.new(name, start, selected_action)
    strip.action_slot = slot
    strip.action_frame_start, strip.action_frame_end = 1, 11
    strip.blend_type, strip.influence, strip.extrapolation = 'REPLACE', 1.0, 'HOLD'
    strip.use_auto_blend = strip.use_animated_influence = strip.use_animated_time = False
    strip.use_sync_length = False
    return result


def action_proof(selected_action):
    return {'pointer': selected_action.as_pointer(), 'slots': [s.handle for s in selected_action.slots],
        'curves': [[bag.slot_handle, curve.data_path, curve.array_index,
            [[*p.co, p.interpolation, *p.handle_left, *p.handle_right] for p in curve.keyframe_points]]
            for layer in selected_action.layers for strip in layer.strips
            for bag in strip.channelbags for curve in bag.fcurves]}


def selection(ad):
    return {'active': ad.nla_tracks.active.as_pointer() if ad.nla_tracks.active else 0,
        'tracks': [[t.as_pointer(), t.select, [[s.as_pointer(), s.select] for s in t.strips]]
                   for t in ad.nla_tracks]}


def evaluated(bpy, rig):
    bpy.context.scene.frame_set(6)
    bpy.context.view_layer.update()
    values = list(rig.evaluated_get(bpy.context.evaluated_depsgraph_get()).pose.bones['Probe'].location)
    require(len(values) == 3 and all(math.isfinite(value) for value in values),
            'Evaluated native pose must contain exactly three finite components')
    return values


def run(options, report):
    import bpy  # Only a separately authorized native launch can reach this import.
    require(bpy.app.background and tuple(bpy.app.version[:2]) == (5, 2), 'Use Blender 5.2 background')
    require(not bpy.data.filepath, 'Factory startup only; never open an artist or model file')
    require(bpy.context.window is not None and bpy.context.window.screen is not None,
            'Factory native window/screen context required')
    report['blender'] = bpy.app.version_string
    old, old_pin = extracted(options.old_source, OLD_SHA, bpy)
    new, new_pin = extracted(options.new_source, NEW_SHA, bpy)
    require(old_pin['functions']['_nla_context'] == new_pin['functions']['_nla_context'],
            'Only the Tweak function may differ')
    report['source_pins'] = {'old': old_pin, 'new': new_pin}
    data = bpy.data.armatures.new('NLA Identity Probe')
    rig = bpy.data.objects.new('NLA Identity Probe', data)
    bpy.context.scene.collection.objects.link(rig)
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    bone = data.edit_bones.new('Probe')
    bone.head, bone.tail = (0, 0, 0), (0, 1, 0)
    bpy.ops.object.mode_set(mode='OBJECT')
    a, a_slot = action(bpy, rig, 'Protected Probe A', 0, 2)
    b, b_slot = action(bpy, rig, 'Editable Probe B', 1, 3)
    protected = [action_proof(a), action_proof(b)]
    ad = rig.animation_data_create()
    ad.action, ad.use_nla = None, True
    base = track(ad, 'Base', a, a_slot)
    edit = track(ad, 'Edit', b, b_slot)
    report['modes'] = []
    for mode in ('OBJECT', 'POSE'):
        bpy.ops.object.mode_set(mode=mode)
        aliases = [t for t in ad.nla_tracks if t == edit]
        require(len(aliases) == 1, 'Same-native track lookup must be unique')
        alias = aliases[0]
        require(alias is not edit and alias == edit and alias.as_pointer() == edit.as_pointer(),
                'Installed binary did not produce the expected separate RNA wrapper')
        case = {'mode': mode, 'distinct_python_wrapper': True, 'native_equal': True,
                'track_pointer': edit.as_pointer()}
        report['modes'].append(case)
        try:
            old(bpy.context, rig, edit, enabled=True)
        except RuntimeError as error:
            require('No active strip(s)' in str(error), 'Old operator failed for another reason')
            require(not ad.use_tweak_mode, 'Old boundary unexpectedly entered native Tweak')
            require(not any(t.select or any(s.select for s in t.strips) for t in ad.nla_tracks),
                    'Old identity predicate did not clear selection as expected')
            case['old_exact_failure'] = str(error)
        else:
            raise ValueError('Old boundary unexpectedly succeeded; no native reproduction claim')
        new(bpy.context, rig, edit, enabled=True)
        require(ad.action == b and ad.action_slot_handle == b_slot.handle, 'B Action/slot mismatch')
        pose_b = evaluated(bpy, rig)
        require(max(abs(x-y) for x, y in zip(pose_b, [2, 3, 0])) <= 1e-6,
                'Full Stack B must retain evaluated lower A')
        new(bpy.context, rig, None, enabled=False)
        new(bpy.context, rig, base, enabled=True)
        require(ad.action == a and ad.action_slot_handle == a_slot.handle, 'A Action/slot mismatch')
        pose_a = evaluated(bpy, rig)
        require(max(abs(x-y) for x, y in zip(pose_a, pose_b)) <= 1e-6,
                'Editing A must retain evaluated upper B')
        new(bpy.context, rig, None, enabled=False)
        new(bpy.context, rig, edit, enabled=True)
        require(max(abs(x-y) for x, y in zip(evaluated(bpy, rig), pose_b)) <= 1e-6,
                'Returning B changed the native full stack')
        new(bpy.context, rig, None, enabled=False)
        require([action_proof(a), action_proof(b)] == protected, 'Protected Action content changed')
        case.update({'new_b_full_stack': pose_b, 'switch_a_full_stack': pose_a,
                     'return_b_full_stack_equal': True, 'protected_actions_exact': True,
                     'native_tweak_exited': not ad.use_tweak_mode})
    other = bpy.data.objects.new('Foreign Probe', None)
    bpy.context.scene.collection.objects.link(other)
    foreign = track(other.animation_data_create(), 'Foreign', b, b_slot)
    multiple = track(ad, 'Multiple', b, b_slot, start=20)
    extra = multiple.strips.new('Second', 40, b)
    extra.action_slot = b_slot
    report['negative_cases'] = []
    for label, invalid in [('foreign_track', foreign), ('multiple_strips', multiple)]:
        before = selection(ad)
        try:
            new(bpy.context, rig, invalid, enabled=True)
        except ValueError as error:
            require('exact owned track and single Action strip' in str(error),
                    'Invalid native track failed at an unexpected boundary')
            require(selection(ad) == before and not ad.use_tweak_mode,
                    'Invalid native track mutated selection or entered Tweak')
            report['negative_cases'].append({'case': label, 'rejected_before_selection': True})
        else:
            raise ValueError('Invalid native track was accepted')
    require([action_proof(a), action_proof(b)] == protected, 'Final Action proof changed')
    report['completed'] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-source', required=True)
    parser.add_argument('--new-source', required=True)
    parser.add_argument('--report-out', required=True)
    options = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    output = Path(options.report_out)
    require(output.is_absolute() and output.parent.is_dir() and not output.exists()
            and output.suffix.lower() == '.json' and len(str(output)) < 260,
            'Use one new short absolute report path in an existing owned evidence directory')
    require(output.resolve() not in {Path(options.old_source).resolve(), Path(options.new_source).resolve()},
            'Report must not alias a protected source')
    report = {'schema': 'x.validation.composition-nla-track-identity-native-api/1',
        'completed': False, 'artist_model_sdk_deployment_export_or_save_performed': False}
    exit_code = 0
    try:
        run(options, report)
    except Exception as error:
        report.update({'error_type': type(error).__name__, 'error': str(error),
                       'traceback': traceback.format_exc()})
        exit_code = 1
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'completed': report['completed'], 'report': str(output)}))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
