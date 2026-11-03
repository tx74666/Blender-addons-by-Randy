"""Private native trajectory translation; no conversion-quality acceptance.

Uses the existing generated limb fixture and an explicitly initialized
controller Action/marker. The reader/solver is never invoked. All owner,
Source, curve, publication and recovery guards remain real and unpatched.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

import bpy
from mathutils import Vector


def require(value, message):
    if not value:
        raise AssertionError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projection', type=Path, required=True)
    parser.add_argument('--projection-sha256', required=True)
    parser.add_argument('--move-module', type=Path, required=True)
    parser.add_argument('--move-sha256', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    require(bpy.app.background and not bpy.data.filepath and '--enable-autoexec' in sys.argv,
            'Use a fresh trusted private background process.')
    require(not args.report.exists(), 'Preserve earlier evidence; use a new report.')
    require(sha(args.projection) == args.projection_sha256, 'Projection pin changed.')
    projection = json.loads(args.projection.read_text(encoding='utf-8'))
    package = Path(projection['source_root'])
    require(all(sha(package/name) == digest for name, digest in projection['files'].items()),
            'A frozen package source changed.')
    require(sha(args.move_module) == args.move_sha256, 'Move overlay pin changed.')
    sys.path[:0] = [str(package.parent), str(Path(__file__).resolve().parent)]
    import character_designer
    from character_designer import animation_retarget as retarget, animation_worklist as worklist
    from character_designer import animation_link as links, control_pose_offset as offset
    from character_designer import controller_animation as controller, limb_ik as limb, limb_ik_fk as match
    require(Path(character_designer.__file__).resolve().parent == package.resolve(), 'Wrong package root.')
    spec = importlib.util.spec_from_file_location('character_designer.controller_animation_move', args.move_module)
    move = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = move
    spec.loader.exec_module(move)
    report = dict(schema='x.controller-animation.move-native/1', passed=False, checks={}, errors=[],
                  projection=str(args.projection), projection_sha256=sha(args.projection),
                  move_module=str(args.move_module), move_sha256=sha(args.move_module),
                  limits=['Controller marker/curves initialized explicitly at fixture creation; not converter or Direct5 solver acceptance.',
                          'Small ordinary Worklist fixture; not CurrentRig, artist input or sparse correction-layer acceptance.'],
                  artist_operations=False, artist_file_opened=False, scene_saved=False, preferences_saved=False)
    checks = report['checks']
    registered = False
    try:
        character_designer.register()
        registered = True
        import test_limb_ik_blender as fixture
        fixture.reset_scene()
        rig = fixture.make_humanoid(roll_offset=.37)
        result, settings = fixture.analyze(rig)
        require(result == {'FINISHED'}, settings.last_message)
        settings.build_method = 'DIRECT_PREROLL'
        for name in ('LEFT_ARM', 'LEFT_LEG'):
            settings.selected_limb = name
            require(bpy.ops.character_designer.limb_ik_direct_preroll_check() == {'FINISHED'}, settings.last_message)
            require(bpy.ops.character_designer.limb_ik_build_selected() == {'FINISHED'}, settings.last_message)
        inventory = limb._validate_inventory(rig)
        targets = [rig.pose.bones[inventory['rigs'][key]['target'].name] for key in (('ARM','L'), ('LEG','L'))]
        bpy.context.scene.character_designer_setup.rig = rig
        action = bpy.data.actions.new('Native controller fixture Custom')
        action.use_fake_user = True
        slot, bag = retarget._new_channelbag(action, rig)
        for target in targets:
            for axis in range(3):
                curve = bag.fcurves.new(target.path_from_id('location'), index=axis)
                for frame, value in ((1., .02*axis), (5., .025+.03*axis), (9., -.01+.02*axis)):
                    point = curve.keyframe_points.insert(frame, value)
                    point.interpolation = 'BEZIER'
                    point.handle_left_type = point.handle_right_type = 'FREE'
                    point.handle_left = frame-.3, value-.007
                    point.handle_right = frame+.4, value+.011
                    point.type = 'BREAKDOWN'
                    point.select_control_point = axis != 1
                    point.select_left_handle = axis == 1
                curve.update()
            retarget._write_curve(bag, match.property_path(target), 0, [1.,9.], [1.,1.])
            mode = next(c for c in bag.fcurves if c.data_path == match.property_path(target))
            for point in mode.keyframe_points:
                point.interpolation = 'CONSTANT'
        retarget._write_curve(bag, rig.pose.bones['Chest'].path_from_id('location'), 1,
                              [1.,5.,9.], [.02,.07,.02])
        group = bag.groups.new('Artist controls')
        group.show_expanded = False
        for curve in bag.fcurves:
            curve.group = group
            curve.color_mode = 'CUSTOM'
            curve.color = (.2,.5,.8)
            curve.select = curve.array_index != 1
        extra_slot = action.slots.new(id_type='OBJECT', name='Other Artist Slot')
        extra_bag = action.layers[0].strips[0].channelbags.new(extra_slot)
        retarget._write_curve(extra_bag, 'location', 0, [1.,9.], [2.,3.])
        action[controller.RECORD] = json.dumps(dict(schema=controller.SCHEMA, frame_start=1.,
                                                   frame_end=9., limbs=['ARM.L','LEG.L']))
        source_copy = action.copy()
        source_copy.name = 'Native controller immutable Source'
        cache = args.report.parent/'source_cache.blend'
        require(not cache.exists(), 'Use a fresh fixture output directory.')
        bpy.data.libraries.write(str(cache), {source_copy}, fake_user=True)
        with bpy.data.libraries.load(str(cache), link=True) as (_source, destination):
            destination.actions = [source_copy.name]
        source = destination.actions[0]
        saved = worklist.state(bpy.context)
        saved.rig = rig
        row = saved.items.add()
        row.item_id, row.name, row.rig, row.side = '1'*32, 'Initialized controllers', rig, 'CUSTOM'
        row.custom_action, row.custom_slot = action, slot.handle
        row.source_action, row.source_slot = source, slot.handle
        row.source_file, row.source_hash = str(cache), sha(cache)
        row.link_identity = json.dumps(dict(schema=links.SCHEMA, linkId='2'*32, targetGuid='3'*32,
                                           clipGuid='4'*32, clipLocalId=1,
                                           sourcePackage=str(args.report.parent/'unused_packet.json'),
                                           sourcePackageSha256='5'*64))
        saved.active_index = 0
        worklist._association(row)
        ad = rig.animation_data_create()
        ad.action, ad.action_slot = action, slot
        ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True

        def pending():
            ad.action, ad.action_slot = action, slot
            saved.active_index = 0
            worklist._association(row)
            bpy.context.scene.frame_set(5)
            for pb in rig.pose.bones:
                pb.select = False
            for target in targets:
                target.select = True
            rig.data.bones.active = targets[0].bone
            targets[0].location += Vector((.013,-.009,.007))
            targets[1].location += Vector((-.008,.012,.003))
            rig.pose.bones['Chest'].location.x = .123
            rig.rotation_euler.z = .17

        api = offset._modules()
        original_states = move._states(api, action)[0]
        source_pin = sha(cache)
        pending()
        plan = move.prepare(bpy.context, targets=targets)
        expected = plan['expected_states']
        raw = plan['raw']
        result = move.apply(plan)
        require(result['action'] is not action and len(saved.items) == 2, 'A retained new Custom was not published.')
        require(move._states(api, result['action'])[0] == expected, 'Translated curves/artist UI differ.')
        require(move._states(api, action)[0] == original_states and sha(cache) == source_pin,
                'Original Custom or Source changed.')
        require(move._raw_matches(raw, controller) and bpy.context.scene.tool_settings.use_keyframe_insert_auto,
                'Selected/current/unselected/object pending raw or Auto Key changed.')
        checks['two_target_full_clip_bezier_translation_all_slots_ui_and_original_exact'] = True
        checks['selected_unselected_object_pending_inputs_and_autokey_retained'] = True
        checks['retained_new_custom_published_same_frame'] = bpy.context.scene.frame_current == 5

        pending()
        for target in targets:
            target.location = tuple(next(c for c in bag.fcurves if c.data_path == target.path_from_id('location')
                                         and c.array_index == axis).evaluate(5) for axis in range(3))
        before_ids = {value.as_pointer() for value in bpy.data.actions}
        try:
            move.prepare(bpy.context)
            raise AssertionError('Zero pending delta was accepted.')
        except ValueError as error:
            require('Auto Key' in str(error), str(error))
        require({value.as_pointer() for value in bpy.data.actions} == before_ids, 'Refusal created an Action.')
        checks['zero_pending_refuses_without_guessing_autokey_prevalue'] = True

        for phase in ('after_curve_translation','after_publication','after_raw_restore'):
            pending()
            before_ids = {value.as_pointer() for value in bpy.data.actions}
            before_rows = len(saved.items)
            plan = move.prepare(bpy.context)
            def fault(current):
                if current == phase:
                    raise RuntimeError('injected '+phase)
            with patch.object(move, '_fault', fault):
                try:
                    move.apply(plan)
                    raise AssertionError('Fault did not raise: '+phase)
                except RuntimeError as error:
                    require('injected '+phase in str(error), str(error))
            require(plan['phase'] == 'discarded' and ad.action is action and len(saved.items) == before_rows,
                    'Failed move lost its original binding or left a new row: '+phase)
            require({value.as_pointer() for value in bpy.data.actions} == before_ids,
                    'Failed move retained owned Actions: '+phase)
            require(move._raw_matches(plan['raw'], controller) and move._states(api,action)[0] == original_states,
                    'Failure lost pending inputs or artist curves: '+phase)
            checks['rollback_'+phase] = True
        require(all(sha(package/name) == digest for name,digest in projection['files'].items())
                and sha(args.move_module) == args.move_sha256, 'Source changed while running.')
        checks['frozen_inputs_retained'] = True
        report['passed'] = all(checks.values())
    except Exception:
        report['errors'].append(traceback.format_exc())
    finally:
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if registered:
            character_designer.unregister()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'passed':report['passed'], 'checks':len(checks), 'errors':len(report['errors'])}))
    if not report['passed']:
        raise RuntimeError('Native controller move checks failed; preserve the report.')


if __name__ == '__main__':
    main()
