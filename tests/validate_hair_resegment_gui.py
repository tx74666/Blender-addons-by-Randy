"""Private GUI native Undo/Redo acceptance; never opens artist data."""
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

import bpy

HARNESS = Path(__file__).with_name('validate_hair_resegment_blender52.py')
prefix = HARNESS.read_text(encoding='utf8').split('\nresult = None\n')[0]
prefix = prefix.replace('assert bpy.app.background and not bpy.data.filepath and not args.report.exists()',
                        'assert not bpy.app.background and not bpy.data.filepath and not args.report.exists()')
scope = {'__file__': str(HARNESS), '__name__': 'hair_resegment_gui_fixture'}
exec(compile(prefix, str(HARNESS), 'exec'), scope)
args, cd, service, variants = (scope[n] for n in ('args', 'cd', 'service', 'variants'))
private_temp = Path(os.environ['TEMP']).resolve()
assert private_temp == Path(os.environ['TMP']).resolve()
assert private_temp.is_relative_to(Path(r'D:\Blender\Projects\Character\X\validation\hair_resegment_complete_20261029'))
bpy.context.preferences.filepaths.temporary_directory = str(private_temp) + os.sep
assert Path(bpy.app.tempdir).resolve().is_relative_to(private_temp)
report = dict(schema='character-designer.hair-resegment-gui/1',passed=False,
    projection=str(args.projection),projection_sha256=args.projection_sha,
    script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    fixture_harness_sha256=hashlib.sha256(HARNESS.read_bytes()).hexdigest(),
    native_history=[],errors=[],private_temp=str(private_temp),app_temp=bpy.app.tempdir,
    artist_file_opened=False,preferences_saved=False,private_scene_save=True,
    limits=['Synthetic private GUI fixture, not artist acceptance.',
            'Native history snapshots are explicitly pushed around Python operator calls.'])


def run():
    try:
        cd.register()
        source,rig,record = scope['fixture']()
        names = source.name,rig.name
        bpy.context.window_manager.character_designer_hair_bones.source = source
        private_file = args.report.with_suffix('.blend')
        assert bpy.ops.wm.save_as_mainfile(filepath=str(private_file),check_existing=False)=={'FINISHED'}
        expected = scope['source_author'](source,rig)

        def get():
            return bpy.data.objects[names[0]],bpy.data.objects[names[1]]

        def author_ok():
            s,r = get()
            editing = s.mode == 'EDIT'
            if editing:
                s.update_from_editmode()
            assert scope['source_author'](s,r)==expected

        def state():
            s,r = get()
            versions=variants.variants_for(s)
            return dict(versions=[c.name for c in versions],
                visible=[c.name for c in versions if not c.hide_viewport],
                original_visible=s.visible_get(),active=bpy.context.object.name,
                mode=bpy.context.mode)

        def history(label,call,check_before,check_after):
            assert bpy.ops.ed.undo_push(message='Before '+label)=={'FINISHED'}
            before=state()
            check_before(before)
            assert call()=={'FINISHED'}
            author_ok()
            after=state()
            check_after(after)
            assert bpy.ops.ed.undo_push(message='After '+label)=={'FINISHED'}
            assert bpy.ops.ed.undo()=={'FINISHED'}
            author_ok()
            assert state()==before,(label,'undo',state(),before)
            assert bpy.ops.ed.redo()=={'FINISHED'}
            author_ok()
            assert state()==after,(label,'redo',state(),after)
            report['native_history'].append(dict(operation=label,undo=True,redo=True,before=before,after=after))

        def eq_len(value):
            return lambda s: (_ for _ in ()).throw(AssertionError(s)) if len(s['versions'])!=value else None

        history('Create Six',lambda:bpy.ops.character_designer.hair_resegment(bone_count=6),eq_len(0),eq_len(1))
        six=state()['versions'][0]
        history('Create Seven',lambda:bpy.ops.character_designer.hair_resegment(bone_count=7),eq_len(1),eq_len(2))
        history('Show Six',lambda:bpy.ops.character_designer.hair_show_version(collection_name=six),
            eq_len(2),lambda s:None if s['visible']==[six] else (_ for _ in ()).throw(AssertionError(s)))
        history('Return Original',lambda:bpy.ops.character_designer.hair_show_original_version(),eq_len(2),
            lambda s:None if s['original_visible'] and not s['visible'] else (_ for _ in ()).throw(AssertionError(s)))
        # Deleting versions is conservatively refused while temporary keyed
        # offsets exist, since native Undo re-evaluates those channels. Finalize
        # only this disposable fixture before exercising deletion history.
        bpy.context.scene.frame_set(3,subframe=.25)
        bpy.context.view_layer.update()
        expected=scope['source_author'](*get())
        report['cleanup_fixture_temporary_channels_explicitly_finalized']=True
        history('Cleanup Copies',lambda:bpy.ops.character_designer.hair_cleanup_generated_copies(),eq_len(2),eq_len(0))
        report['passed']=True
    except BaseException:
        report['errors'].append(traceback.format_exc())
    finally:
        try:
            if bpy.context.object and bpy.context.object.mode!='OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            cd.unregister()
        except BaseException:
            report['errors'].append(traceback.format_exc())
            report['passed']=False
        args.report.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
        bpy.ops.wm.quit_blender()
    return None

bpy.app.timers.register(run,first_interval=.5)
