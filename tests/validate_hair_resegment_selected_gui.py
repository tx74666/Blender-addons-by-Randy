"""Private native Undo/Redo for one selected Hair strand and an explicit pair.

Uses the selected native harness's separately named three-chain fixture.  No
artist file is opened.  New private CONFIG/TEMP and recovery paths are mandatory.
"""
from pathlib import Path
import hashlib
import json
import os
import traceback

import bpy

NATIVE = Path(__file__).with_name('validate_hair_resegment_selected_blender.py')
text = NATIVE.read_text(encoding='utf-8')
prefix = text.split('\nresult = None\n', 1)[0]
assert prefix != text
scope = {'__file__': str(NATIVE), '__name__': 'selected_hair_gui_fixture', 'GUI': True}
exec(compile(prefix, str(NATIVE), 'exec'), scope)
args, cd, service, hair, variants = (scope[name] for name in ('args', 'cd', 'service', 'hair', 'variants'))
private_temp = Path(os.environ['TEMP']).resolve()
assert private_temp == Path(os.environ['TMP']).resolve() and private_temp.is_dir()
assert private_temp.is_relative_to(Path(r'D:/Blender/Projects/Character/X/validation/hair_selected_strands_20261029'))
bpy.context.preferences.filepaths.temporary_directory = str(private_temp) + os.sep
assert Path(bpy.app.tempdir).resolve().is_relative_to(private_temp)
report = dict(schema='character-designer.selected-hair-gui/1', passed=False,
    projection=str(args.projection), projection_sha256=args.projection_sha,
    script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    native_fixture_sha256=hashlib.sha256(NATIVE.read_bytes()).hexdigest(),
    native_history=[], errors=[], private_temp=str(private_temp), app_temp=bpy.app.tempdir,
    artist_file_opened=False, preferences_saved=False, private_scene_save=True,
    limits=['Private synthetic GUI only; no artist/full-character/dynamics/FPS acceptance.',
            'Native undo_push snapshots explicitly surround Python operator calls.',
            'Cleanup uses an explicitly finalized disposable fixture, not temporary author edits.'])


def gui_fresh_main():
    # read_factory_settings during a GUI timer invalidates its Window context.
    # This already factory-started singleton fixture removes only its private
    # default objects and leaves the native Window/Screen alive.
    active = getattr(bpy.context, 'object', None)
    if active is not None and active.mode != 'OBJECT':
        assert bpy.ops.object.mode_set(mode='OBJECT') == {'FINISHED'}
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    assert not bpy.data.objects and not bpy.data.actions and not bpy.data.shape_keys
    assert hasattr(bpy.types.WindowManager, 'character_designer_hair_bones')


scope['fresh_main'] = gui_fresh_main


def run():
    registered = False
    try:
        cd.register()
        registered = True
        source, rig, record = scope['selected_fixture'](rich=False)
        bpy.context.preferences.filepaths.temporary_directory = str(private_temp) + os.sep
        assert Path(bpy.app.tempdir).resolve().is_relative_to(private_temp)
        report['app_temp_after_fixture'] = bpy.app.tempdir
        first, second, third = [chain['signature'] for chain in record['chains']]
        scope['choose'](source, (first,))
        names = source.name, rig.name
        assert bpy.ops.wm.save_as_mainfile(filepath=str(args.report.with_suffix('.blend')),
                                           check_existing=False) == {'FINISHED'}
        # Current keyed inputs are deliberately edited after the private save.
        # Copy, switch and Undo must preserve them without evaluating old keys.
        family = service._family_pose(source)
        external = bpy.data.objects.new('Selected GUI External Author', None)
        bpy.context.scene.collection.objects.link(external)
        for frame, value in ((1., .1), (8., .4)):
            external.location.x = value
            external.keyframe_insert(data_path='location', index=0, frame=frame)
            rig['GUIArtistControl'] = value
            rig.keyframe_insert(data_path='["GUIArtistControl"]', frame=frame)
        bpy.context.scene.frame_set(3, subframe=.25)
        service._restore_family_pose(bpy.context, family)
        external.location.x = .777
        rig['GUIArtistControl'] = .888
        bpy.context.view_layer.update()
        expected = scope['scope']['source_author'](source, rig)
        extras = (float(external.location.x), float(rig['GUIArtistControl']))
        untouched = scope['chain_author'](source, third)
        original_action_names = tuple(action.name_full for action in bpy.data.actions)
        original_actions = {name: scope['scope']['curves'](bpy.data.actions[name]) for name in original_action_names}

        def original():
            return bpy.data.objects[names[0]], bpy.data.objects[names[1]]

        def author_ok():
            original_source, original_rig = original()
            assert scope['scope']['source_author'](original_source, original_rig) == expected
            current_extra = (float(bpy.data.objects['Selected GUI External Author'].location.x),
                             float(original_rig['GUIArtistControl']))
            assert current_extra == extras, (current_extra, extras)
            assert {name: scope['scope']['curves'](bpy.data.actions[name]) for name in original_action_names} == original_actions
            for collection in variants.variants_for(original_source):
                mesh = collection.get(variants.MESH_KEY)
                if mesh:
                    assert scope['chain_author'](mesh, third) == untouched

        def state():
            original_source, original_rig = original()
            collections = variants.variants_for(original_source)
            return dict(versions=[collection.name for collection in collections],
                visible=[collection.name for collection in collections if not collection.hide_viewport],
                counts={collection.name: {chain['signature']: chain['count']
                    for chain in hair._read_records(collection.get(variants.MESH_KEY))['chains']}
                    for collection in collections},
                original_visible=original_source.visible_get(),
                active=bpy.context.object.name if bpy.context.object else None, mode=bpy.context.mode,
                pose_selection=tuple(sorted(pose.name for pose in bpy.context.selected_pose_bones or ())))

        def history(label, call, before_check, after_check):
            assert bpy.ops.ed.undo_push(message='Before ' + label) == {'FINISHED'}
            before = state()
            before_check(before)
            assert call() == {'FINISHED'}
            author_ok()
            after = state()
            after_check(after)
            assert bpy.ops.ed.undo_push(message='After ' + label) == {'FINISHED'}
            assert bpy.ops.ed.undo() == {'FINISHED'}
            author_ok()
            assert state() == before, (label, 'undo', state(), before)
            assert bpy.ops.ed.redo() == {'FINISHED'}
            author_ok()
            assert state() == after, (label, 'redo', state(), after)
            report['native_history'].append(dict(operation=label, undo=True, redo=True,
                                                 before=before, after=after))

        def versions(count):
            return lambda value: None if len(value['versions']) == count else (_ for _ in ()).throw(AssertionError(value))

        def one_visible(counts):
            def check(value):
                assert len(value['visible']) == 1
                assert value['counts'][value['visible'][0]] == dict(zip((first, second, third), counts))
            return check

        history('Selected A to Six',
            lambda: bpy.ops.character_designer.hair_resegment('EXEC_DEFAULT', scope='SELECTED',
                bone_count=6, selection_pin=''), versions(0), one_visible((6, 4, 4)))
        first_collection = state()['visible'][0]
        first_mesh = bpy.data.collections[first_collection].get(variants.MESH_KEY)
        # Select both real independent symmetric chains. One middle bone per
        # chain is enough; selection does not require selecting every segment.
        current_rig = first_mesh[hair.RIG_KEY]
        assert bpy.context.mode == 'POSE' and bpy.context.object is current_rig
        current_record = hair._read_records(first_mesh)
        selected_names = [chain['bones'][len(chain['bones']) // 2] for chain in current_record['chains']
                          if chain['signature'] in {first, second}]
        for pose in current_rig.pose.bones:
            pose.select = pose.name in selected_names
        current_rig.data.bones.active = current_rig.data.bones[selected_names[0]]
        author_ok()
        history('Selected Symmetric Pair to Seven',
            lambda: bpy.ops.character_designer.hair_resegment('EXEC_DEFAULT', scope='SELECTED',
                bone_count=7, selection_pin=''), versions(1), one_visible((7, 7, 4)))
        history('Show Selected A Six',
            lambda: bpy.ops.character_designer.hair_show_version(collection_name=first_collection),
            versions(2), one_visible((6, 4, 4)))
        history('Return Original Hair', lambda: bpy.ops.character_designer.hair_show_original_version(),
            versions(2), lambda value: None if value['original_visible'] and not value['visible']
            else (_ for _ in ()).throw(AssertionError(value)))

        bpy.context.scene.frame_set(3, subframe=.25)
        bpy.context.view_layer.update()
        expected = scope['scope']['source_author'](*original())
        extras = (float(bpy.data.objects['Selected GUI External Author'].location.x),
                  float(original()[1]['GUIArtistControl']))
        # C's original keyed channels are now natively finalized too. Rebuild
        # only this disposable expected author snapshot before deletion history.
        untouched = scope['chain_author'](original()[0], third)
        report['cleanup_fixture_temporary_channels_explicitly_finalized'] = True
        history('Cleanup Selected Versions', lambda: bpy.ops.character_designer.hair_cleanup_generated_copies(),
                versions(2), versions(0))
        report['unselected_asymmetric_chain_exact_through_history'] = True
        report['external_and_rig_temporary_authors_preserved'] = True
        report['passed'] = True
    except BaseException:
        report['errors'].append(traceback.format_exc())
    finally:
        try:
            bpy.context.preferences.filepaths.temporary_directory = str(private_temp) + os.sep
            assert Path(bpy.app.tempdir).resolve().is_relative_to(private_temp)
            report['app_temp_at_exit'] = bpy.app.tempdir
            if getattr(bpy.context, 'object', None) and bpy.context.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            if registered:
                cd.unregister()
        except BaseException:
            report['errors'].append(traceback.format_exc())
            report['passed'] = False
        report['source_unchanged'] = all(scope['digest'](scope['PACKAGE'] / name) == value
                                       for name, value in scope['manifest']['files'].items())
        report['passed'] = report['passed'] and not report['errors'] and report['source_unchanged']
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=.5)
