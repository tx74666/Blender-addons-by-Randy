"""Independent native Hair-version transaction / Dress lease regression.

Reuses the author-rich shared-rig fixture; no artist file or preferences save.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_hair_wiggle2_blender08 as n
from character_designer import hair_resegment as versions
from character_designer import hair_bones_rig as hair_rig


def actions():
    result = {}
    for action in n.bpy.data.actions:
        writer = n.hair._AnimationHash()
        writer.rna(action)
        result[action.as_pointer()] = writer.hash.hexdigest()
    return result


def peer_state(source, dress, rig):
    saved = n.skirt._SESSIONS[dress.as_pointer()]
    return dict(session=id(saved), lease=id(saved['lease']), frame=saved['frame'],
        paused=saved['paused'], native=n.runtime._live(saved['lease']),
        native_all={bone.name: {field:n.runtime._raw(bone,field) for field in n.runtime._FLAGS}
                    for bone in rig.pose.bones},
        rig_group=n.runtime._group(rig),pose=n.pose_channels(rig),rest=n.rest_state(rig),
        hair_mesh=n.mesh_content(source),dress_mesh=n.mesh_content(dress),
        source_record=source[n.hair.hair.RECORD_KEY],
        dress_state=dress.get(n.skirt.STATE_KEY),
        frame_scene=(n.bpy.context.scene.frame_current,n.bpy.context.scene.frame_subframe),
        update_callback=id(n.BACKEND.update_prop))


def context_state():
    c=n.bpy.context
    return c.mode,c.active_object.as_pointer(),tuple(o.as_pointer() for o in c.selected_objects),tuple(
        (p.name,bool(p.select)) for p in c.active_object.pose.bones),c.active_object.data.bones.active.name


class PeerTests(unittest.TestCase):
    tearDown=n.HairWiggle2NativeTests.tearDown

    def test_01_running_selected_dress_refuses_without_any_native_or_context_change(self):
        source,dress,rig,data,profiles=n.fixture()
        n.start_skirt(dress)
        hair_rig._select_chains(n.bpy.context,rig,n.dress_names(dress))
        before=peer_state(source,dress,rig)
        context=context_state()
        ids=versions._new_ids()
        original_actions=actions()
        with self.assertRaisesRegex(ValueError,'Pause running'):
            versions.create(n.bpy.context,source,bone_count=6,frame_start=8.,frame_end=8.)
        n.require(peer_state(source,dress,rig)==before,'Running-peer refusal changed original native/author data.')
        n.require(context_state()==context,'Running-peer refusal changed Pose selection/context.')
        n.require(versions._new_ids()==ids and actions()==original_actions,'Refused creation changed IDs or Actions.')
        n.REPORT['cases'].append(dict(case='selected_running_dress_refusal',passed=True))

    def test_02_paused_selected_dress_survives_copy_without_callback_or_list_rebuild(self):
        source,dress,rig,data,profiles=n.fixture()
        n.start_skirt(dress)
        n.skirt.set_paused(n.bpy.context,dress,True)
        hair_rig._select_chains(n.bpy.context,rig,n.dress_names(dress))
        before=peer_state(source,dress,rig)
        original_actions=actions()
        callbacks=0
        code=n.BACKEND.update_prop.__code__
        def profile(frame,event,arg):
            nonlocal callbacks
            if event=='call' and frame.f_code is code:
                callbacks+=1
        sys.setprofile(profile)
        try:
            result=versions.create(n.bpy.context,source,bone_count=6,frame_start=8.,frame_end=8.)
        finally:
            sys.setprofile(None)
        n.require(callbacks==0,'Copied Wiggle flags invoked global update_prop.')
        n.require(peer_state(source,dress,rig)==before,'Paused creation changed peer native/author data.')
        now_actions=actions()
        n.require(all(now_actions.get(key)==value for key,value in original_actions.items()),'Creation rewrote an original Action.')
        clone,copy_rig=result['mesh'],result['armature']
        n.require(versions.physics_owner(clone) is rig,'New Hair lost the original character owner.')
        n.require(not any(p.wiggle_head or p.wiggle_tail for p in copy_rig.pose.bones),
                  'Copied non-Hair solver endpoints remained active.')
        n.require(not n.runtime._members(n.bpy.context.scene),'Paused peer returned to solver list.')
        n.skirt.set_paused(n.bpy.context,dress,False)
        n.require(n.runtime._members(n.bpy.context.scene)=={(rig.name,name) for name in n.dress_names(dress)},
                  'Peer resume included copied endpoints.')
        n.advance(1)
        n.require(n.skirt.status(n.bpy.context,dress)['running'],'Dress stopped on first resumed frame.')
        n.REPORT['cases'].append(dict(case='selected_paused_dress_copy_and_resume',global_update_calls=callbacks,passed=True))

    def test_03_rollback_retains_unrelated_new_author_text(self):
        source,dress,rig,data,profiles=n.fixture()
        before_ids=versions._new_ids()
        original_actions=actions()
        created=[]
        def unrelated_author_then_fail(*args,**kwargs):
            text=n.bpy.data.texts.new('External author callback data')
            text.write('Must survive the Hair transaction rollback.')
            created.append(text)
            raise RuntimeError('injected author callback then failure')
        with patch.object(versions,'_write_action',side_effect=unrelated_author_then_fail):
            with self.assertRaisesRegex(ValueError,'injected author callback'):
                versions.create(n.bpy.context,source,bone_count=6,frame_start=8.,frame_end=8.)
        text=created[0]
        n.require(n.bpy.data.texts.get(text.name) is text and text.as_string().startswith('Must survive'),
                  'Hair rollback deleted unrelated new author data.')
        after_ids=versions._new_ids()
        for kind in before_ids:
            expected=before_ids[kind]|({text.as_pointer()} if kind=='texts' else set())
            n.require(after_ids[kind]==expected,'Rollback leaked or deleted datablocks: '+kind)
        n.require(actions()==original_actions,'Rollback changed original Actions.')
        n.REPORT['cases'].append(dict(case='owned_only_rollback_retains_external_text',passed=True))


n.HairWiggle2NativeTests=PeerTests
n.REPORT['schema']='character-designer.hair-resegment-peer-native/1'
n.REPORT['peer_script']=n.pin(__file__)
n.REPORT['derived_fixture']=n.pin(n.__file__)
n.main()
