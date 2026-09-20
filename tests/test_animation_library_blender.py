"""Run in disposable background Blender; no source character file is saved."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import bpy
from mathutils import Matrix

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'addons'),str(ROOT/'tests')]
import character_designer
from character_designer import animation_library as lib, unity_animation as ua, unity_animation_adapter as adapter
from character_designer import animation_retarget as curves
import test_unity_animation_blender as fixture


def test_collection(folder):
    fixture.reset();rig=fixture.make_rig();s=lib.state(bpy.context);s.collections.clear();s.target=rig
    path,expected,data=fixture.package(rig,folder,'one.cdanim.json')
    data.update(projectId='fixture',sourceGuid='source',clipGuid='clip',clipLocalId=1)
    path.write_text(json.dumps(data),encoding='utf8')
    second=Path(folder)/'two.cdanim.json';data['clipLocalId']=2;data['clipName']='Second';second.write_text(json.dumps(data),encoding='utf8')
    rig.pose.bones['Hips'].location=(.1,.2,.3);rig.pose.bones['Hips'].keyframe_insert('location',frame=1)
    original=rig.animation_data.action;original.use_fake_user=True
    track=rig.animation_data.nla_tracks.new();track.strips.new('Preserved NLA',1,original.copy())
    rig.animation_data.action_blend_type='ADD';rig.animation_data.action_influence=.4
    bpy.context.view_layer.update();before=ua._snapshot(bpy.context,rig)
    object_count=len(bpy.data.objects);rest=fixture.rest_hash(rig)
    items=lib.add_files(bpy.context,[path,second]);c=lib.current(bpy.context)[0]
    actions,errors=lib.import_selected(bpy.context)
    assert not errors,errors
    assert len(actions)==2 and ua._snapshot(bpy.context,rig)==before and len(bpy.data.objects)==object_count
    assert not lib.add_files(bpy.context,[path,second],c)
    assert len(lib.import_selected(bpy.context)[0])==2 and len(items[0].results)==1
    first_action=lib.use(bpy.context,items[0])
    for i,frame in enumerate(ua.load_package(path)['frames']):
        ua._set_frame(bpy.context.scene,1+frame['time']*24/1.001);bpy.context.view_layer.update()
        for name,matrix in expected[i].items():
            assert fixture.max_matrix_error(rig.matrix_world@rig.pose.bones[name].matrix,matrix)<8e-5,name
    for _ in range(3):lib.use(bpy.context,items[1]);lib.use(bpy.context,items[0])
    curve=curves._curves(first_action)[0];curve.keyframe_points[0].co.y+=.1
    edited=lib.action_hash(first_action);assert lib.import_selected(bpy.context)[0][0]==first_action
    assert lib.action_hash(first_action)==edited
    lib.restore(bpy.context);assert ua._snapshot(bpy.context,rig)==before
    assert fixture.rest_hash(rig)==rest
    # Same source, another native rig: never re-use a target-specific Action.
    other=rig.copy();other.data=rig.data.copy();bpy.context.scene.collection.objects.link(other);other.animation_data_clear()
    s.target=other;new=lib.use(bpy.context,items[0]);assert new!=first_action;lib.restore(bpy.context);s.target=rig
    lib.use(bpy.context,items[1]);lib.remove_clip(bpy.context,c,1)
    assert not s.session and len(c.clips)==1 and len(bpy.data.actions)>=3
    # Missing/corrupt input cannot clear a collection or original state.
    broken=Path(folder)/'broken.cdanim.json';broken.write_text('{}')
    try:lib.add_files(bpy.context,[path,broken],c)
    except ua.UnityAnimationError:pass
    else:raise AssertionError('Invalid input was accepted')
    assert len(c.clips)==1 and ua._snapshot(bpy.context,rig)==before
    lib.use(bpy.context,c.clips[0]);save=Path(folder)/'library.blend';bpy.ops.wm.save_as_mainfile(filepath=str(save))
    bpy.ops.wm.open_mainfile(filepath=str(save),use_scripts=False)
    s=lib.state(bpy.context);assert len(s.collections)==1 and s.collections[0].clips[0].packet
    rig=s.session_target;lib.restore(bpy.context);assert ua._snapshot(bpy.context,rig)==before
    print('PASS collection: unattached import, dedup, edited versions, repeat switches, second target, removal, save/reopen, restore, no rig changes')


def test_actual(blend,manifest,folder):
    digest=hashlib.sha256(Path(blend).read_bytes()).hexdigest()
    bpy.ops.wm.open_mainfile(filepath=str(blend),use_scripts=False)
    # The file is only read; output is a separate validation copy.
    rig=bpy.data.objects['CoshaRig'];s=lib.state(bpy.context);s.target=rig
    rest=fixture.rest_hash(rig);before=ua._snapshot(bpy.context,rig)
    item=lib.add_files(bpy.context,[manifest])[0];data=lib.read(item)
    action=adapter.bake(bpy.context,rig,data)
    print('ACTUAL_COMPATIBILITY',action['unity_compatibility'],flush=True)
    assert ua._snapshot(bpy.context,rig)==before and fixture.rest_hash(rig)==rest
    actions,errors=lib.import_selected(bpy.context);assert not errors,errors
    lib.use(bpy.context,item)
    sample_errors=[];adapt=adapter.plan(bpy.context,rig,data)
    for i in range(len(data['frames'])):
        ua._set_frame(bpy.context.scene,1+data['frames'][i]['time']*bpy.context.scene.render.fps/bpy.context.scene.render.fps_base)
        bpy.context.view_layer.update()
        desired=adapter.desired_pose(rig,data,adapt,i)
        sample_errors.append(max(fixture.max_matrix_error(rig.pose.bones[n].matrix,m) for n,m in desired.items()))
    assert max(sample_errors)<3e-4,max(sample_errors)
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(folder)/'Cosha_library_validation.blend'))
    lib.restore(bpy.context)
    assert ua._snapshot(bpy.context,rig)==before and fixture.rest_hash(rig)==rest
    assert hashlib.sha256(Path(blend).read_bytes()).hexdigest()==digest
    report={'clip':data['clipName'],'source':data.get('sourceName'),'compatibility':json.loads(action['unity_compatibility']),
            'samples':len(sample_errors),'maximum_action_error':max(sample_errors),'source_file_unchanged':True}
    (Path(folder)/'native_walk.json').write_text(json.dumps(report,indent=2))
    print('ACTUAL_NATIVE_WALK_PASSED',json.dumps(report),flush=True)


if __name__=='__main__':
    character_designer.register()
    if '--actual' in sys.argv:
        args=sys.argv[sys.argv.index('--actual')+1:];test_actual(*args)
    else:
        with tempfile.TemporaryDirectory(prefix='cd-animation-library-') as folder:test_collection(folder)
