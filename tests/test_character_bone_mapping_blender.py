"""Shared character attachment mappings, using disposable native Blender data."""

from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(ROOT / 'tests'))

import character_designer as cd
from character_designer import character_setup as setup
from character_designer import hair_bones as hair
from character_designer import hair_bones_binding as binding
from character_designer import hair_bones_rig as hair_rig
from test_hair_bones_rig_blender import activate, make_armature, reset
from test_hair_bones_ui_blender import fixture, call


def clean():
    reset()
    state = setup.settings(bpy.context)
    state.rig = state.body = None
    state.assets.clear()
    state.bone_mappings.clear()
    return state


def add_bones(armature, names):
    activate(armature, 'EDIT')
    for index, name in enumerate(names):
        bone = armature.data.edit_bones.new(name)
        bone.head, bone.tail = (0, 0, 1.1 + index * .1), (0, 0, 1.2 + index * .1)
    bpy.ops.object.mode_set(mode='OBJECT')


def fails(function, phrase):
    try:
        function()
    except ValueError as exc:
        assert phrase.casefold() in str(exc).casefold(), str(exc)
    else:
        raise AssertionError('Expected a rejected bone mapping')


def test_unique_detection_and_manual_choices():
    state = clean()
    armature = make_armature('Central Character')
    add_bones(armature, ['Hips', 'pelvis.L', 'pelvis.R', 'Root'])
    state.rig = armature
    assert setup.resolve_bone(bpy.context, 'HIPS') == 'Hips'
    assert setup.resolve_bone(bpy.context, 'PELVIS') == 'Hips'
    assert setup.bone_mapping_status(bpy.context, 'HIPS')['status'] == 'AUTO'
    assert state.hips_bone == 'Hips' and len(state.bone_mappings) == 0
    assert setup.resolve_bone(bpy.context, 'HEAD') == 'spine.006'
    add_bones(armature, ['Pelvis'])
    fails(lambda: setup.resolve_bone(bpy.context, 'HIPS'), 'Multiple')
    state.hips_bone = 'Hips'
    assert setup.resolve_bone(bpy.context, 'HIPS') == 'Hips'
    assert setup.bone_mapping_status(bpy.context, 'HIPS')['status'] == 'CONFIRMED'
    assert setup.resolve_bone(bpy.context, 'HIPS', override='Pelvis') == 'Pelvis'
    assert state.hips_bone == 'Hips'
    state.hips_bone = 'Deleted Artist Choice'
    fails(lambda: setup.resolve_bone(bpy.context, 'HIPS'), 'missing')
    assert state.hips_bone == 'Deleted Artist Choice'
    # Clearing a choice permits fresh detection, rather than deleting any bone.
    state.hips_bone = ''
    activate(armature, 'EDIT')
    for name in ('Pelvis', 'Hips'):
        armature.data.edit_bones.remove(armature.data.edit_bones[name])
    bpy.ops.object.mode_set(mode='OBJECT')
    assert setup.bone_candidates(armature, 'HIPS') == ()
    fails(lambda: setup.resolve_bone(bpy.context, 'HIPS'), 'Choose Hips')
    # Namespaces are common on imported characters; side suffixes stay excluded.
    add_bones(armature, ['mixamorig:Hips'])
    assert setup.resolve_bone(bpy.context, 'HIPS') == 'mixamorig:Hips'
    print('PASS unique detection, ambiguous/invalid mappings, no Root or side pelvis guesses')


def test_per_rig_mapping_survives_save_reload():
    state = clean()
    first = make_armature('First Character')
    second = make_armature('Second Character')
    add_bones(first, ['CentralA'])
    add_bones(second, ['CentralB'])
    state.rig = first
    state.hips_bone, state.head_bone = 'CentralA', 'spine.006'
    state.rig = second
    assert state.hips_bone == ''
    fails(lambda: setup.resolve_bone(bpy.context, 'HIPS'), 'Choose Hips')
    state.hips_bone, state.head_bone = 'CentralB', 'Arm'
    assert setup.resolve_bone(bpy.context, 'HEAD', armature=first) == 'spine.006'
    assert setup.resolve_bone(bpy.context, 'HIPS', armature=first) == 'CentralA'
    state.rig = first
    assert state.hips_bone == 'CentralA' and state.head_bone == 'spine.006'
    first.name = 'Renamed First Character'
    names = first.name, second.name
    with tempfile.TemporaryDirectory(prefix='cd_bone_mapping_') as temporary:
        path = str(Path(temporary) / 'shared-bones.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        cd.unregister()
        cd.register()
        state = setup.settings(bpy.context)
        assert state.rig is bpy.data.objects[names[0]]
        assert state.hips_bone == 'CentralA'
        state.rig = bpy.data.objects[names[1]]
        assert state.hips_bone == 'CentralB' and state.head_bone == 'Arm'
        state.rig.data.bones['CentralB'].name = 'Renamed Artist Bone'
        assert state.hips_bone == 'CentralB'
        fails(lambda: setup.resolve_bone(bpy.context, 'HIPS'), 'missing')
    print('PASS rig-keyed mappings survive rename, switch, save/reopen and addon reload')


def test_selected_bone_capture_in_pose_and_edit():
    state = clean()
    armature = make_armature('Pick This Character')
    add_bones(armature, ['Central Custom'])
    activate(armature, 'POSE')
    armature.data.bones.active = armature.data.bones['Central Custom']
    armature.pose.bones['Central Custom'].select = True
    for owner_key in ('character_designer_skirt_owner', 'character_designer_hair_bones_owner',
                      'character_designer_hair_variant_version'):
        armature[owner_key] = 'Generated accessory'
        assert not bpy.ops.character_designer.capture_character_bone.poll()
        assert state.rig is None
        del armature[owner_key]
    assert bpy.ops.character_designer.capture_character_bone(role='HIPS') == {'FINISHED'}
    assert state.rig is armature and state.hips_bone == 'Central Custom'
    activate(armature, 'EDIT')
    armature.data.edit_bones.active = armature.data.edit_bones['spine.006']
    armature.data.edit_bones.active.select = True
    assert bpy.ops.character_designer.capture_character_bone(role='HEAD') == {'FINISHED'}
    assert state.head_bone == 'spine.006'
    bpy.ops.object.mode_set(mode='OBJECT')
    assert not bpy.ops.character_designer.capture_character_bone.poll()
    assert set(armature.data.bones.keys()) == {'Neck', 'spine.006', 'eye.L', 'eye.R', 'Arm', 'Central Custom'}
    print('PASS selected Pose/Edit bone capture remembers rig and mapping without renaming bones')


def test_hair_consumes_mapping_and_retains_existing_attachment():
    clean()
    source, _, armature = fixture(count=2)
    add_bones(armature, ['Hair Anchor'])
    state = setup.settings(bpy.context)
    state.rig, state.head_bone = armature, 'Hair Anchor'
    activate(source, 'EDIT')
    call('select_hair_strands')
    assert binding.resolve_target(bpy.context, source, armature=armature) == (armature, 'Hair Anchor')
    call('hair_bind_to_character')
    record = hair_rig._read_records(source)
    assert record['parent'] == 'Hair Anchor'
    for chain in record['chains']:
        assert armature.data.bones[chain['bones'][0]].parent.name == 'Hair Anchor'
    second = make_armature('New Main Rig')
    state.rig = second
    state.head_bone = 'Arm'
    assert binding.resolve_target(bpy.context, source, armature=second) == (armature, 'Hair Anchor')
    hair._settings(bpy.context).source = source
    activate(source)
    call('hair_remove_binding')
    assert not binding.is_bound(source)
    assert binding.resolve_target(bpy.context, source, armature=second) == (second, 'Arm')
    print('PASS hair uses shared Head; existing attachment stays authoritative until removal')


def test_footwear_reference_per_character():
    state = clean()
    first, second = make_armature('First Character'), make_armature('Second Character')
    shoe = bpy.data.objects.new('Footwear', bpy.data.meshes.new('Footwear Mesh'))
    bpy.context.scene.collection.objects.link(shoe)
    modifier = shoe.modifiers.new('Character Bind', 'ARMATURE')
    modifier.object = first
    setup.remember_asset(bpy.context, shoe, 'SHOES')
    assert setup.footwear_reference(bpy.context, first) == shoe
    assert setup.footwear_reference(bpy.context, second) is None
    second_entry = setup._mapping(state, second, create=True)
    second_entry.footwear = shoe
    assert setup.footwear_reference(bpy.context, second) == shoe
    second_entry.footwear = None
    assert setup.footwear_reference(bpy.context, second) is None
    print('PASS footwear reference is scoped to its character')


if __name__ == '__main__':
    cd.register()
    try:
        test_unique_detection_and_manual_choices()
        test_per_rig_mapping_survives_save_reload()
        test_selected_bone_capture_in_pose_and_edit()
        test_hair_consumes_mapping_and_retains_existing_attachment()
        test_footwear_reference_per_character()
        print('CHARACTER_BONE_MAPPING_OK')
    finally:
        cd.unregister()
