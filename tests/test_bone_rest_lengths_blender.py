"""Bone display lengths survive a real FBX roundtrip without changing skin.

Run only in a disposable Blender --background --factory-startup process:
    blender --background --factory-startup --threads 2 --python-exit-code 1 \
        --python tests/test_bone_rest_lengths_blender.py

The fixtures use owned temporary directories, no artist scene, add-on
registration, renderer, Unity process, or nested Blender process.
"""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace

import bpy
from mathutils import Matrix, Vector


ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = Path(os.environ.get('CD_BONE_LENGTH_ADDON_DIR',
                               str(ROOT / 'addons/character_designer'))).resolve()
RIG_NAME = 'LengthRig'
MODEL_NAME = 'LengthRig.fbx'
LEAVES = ('eye.L', 'eye.R', 'thumb.03.L')


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


worker = module('length_test_export_worker', ADDON_DIR / 'unity_export_worker.py')
lengths = module('length_test_metadata', ADDON_DIR / 'bone_rest_lengths.py')


def flat(matrix):
    return tuple(float(value) for row in matrix for value in row)


def maximum_error(first, second):
    assert len(first) == len(second), (len(first), len(second))
    return max((abs(a - b) for a, b in zip(first, second)), default=0.0)


def fixture(unit_scale=1.0, *, rotated=True):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = unit_scale
    data = bpy.data.armatures.new('Length Skeleton')
    rig = bpy.data.objects.new(RIG_NAME, data)
    scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    specs = (
        ('Hips', None, (0, 0, 0), (0, 0, .4)),
        ('Head', 'Hips', (0, 0, 1.05), (0, 0, 1.30)),
        ('eye.L', 'Head', (.035, -.025, 1.16), (.035, -.050, 1.16)),
        ('eye.R', 'Head', (-.035, -.025, 1.16), (-.035, -.050, 1.16)),
        ('hand.L', 'Hips', (.30, 0, .80), (.38, 0, .80)),
        ('thumb.01.L', 'hand.L', (.34, -.025, .80), (.40, -.025, .80)),
        ('thumb.02.L', 'thumb.01.L', (.40, -.025, .80), (.47, -.025, .80)),
        ('thumb.03.L', 'thumb.02.L', (.47, -.025, .80), (.485, -.025, .80)),
    )
    for name, parent, head, tail in specs:
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        bone.parent = data.edit_bones[parent] if parent else None
        bone.use_connect = False
        bone.use_deform = True
        bone.roll = .13 if name.startswith('thumb.') else -.08
    bpy.ops.object.mode_set(mode='OBJECT')
    rig.location = (3, -2, 1)
    rig.scale = (.7823157,) * 3
    if rotated:
        rig.rotation_euler = (.21, -.32, .48)

    vertices = [
        (.030, -.035, 1.155), (.040, -.035, 1.155), (.035, -.035, 1.165),
        (-.030, -.035, 1.155), (-.040, -.035, 1.155), (-.035, -.035, 1.165),
        (.465, -.030, .795), (.480, -.030, .795), (.480, -.020, .805),
        (.395, -.030, .795), (.435, -.030, .795), (.435, -.020, .805),
    ]
    mesh_data = bpy.data.meshes.new('Length Skin Data')
    mesh_data.from_pydata(vertices, [], [(0, 1, 2), (3, 4, 5), (6, 7, 8), (9, 10, 11)])
    mesh_data.update()
    mesh = bpy.data.objects.new('Length Skin', mesh_data)
    scene.collection.objects.link(mesh)
    mesh.parent = rig
    mesh.matrix_parent_inverse = Matrix.Identity(4)
    for name, indices, weight in (
        ('eye.L', [0, 1, 2], 1.0), ('eye.R', [3, 4, 5], 1.0),
        ('thumb.03.L', [6, 7, 8], 1.0), ('thumb.02.L', [9, 10, 11], .65),
        ('thumb.01.L', [9, 10, 11], .35),
    ):
        mesh.vertex_groups.new(name=name).add(indices, weight, 'REPLACE')
    skin = mesh.modifiers.new('Ordinary Skin', 'ARMATURE')
    skin.object = rig
    skin.use_vertex_groups = True
    skin.use_bone_envelopes = False
    mesh.shape_key_add(name='Basis')
    shape = mesh.shape_key_add(name='Artist Shape')
    for index, point in enumerate(shape.data):
        point.co.z += .004 if index < 6 else .002
    bpy.context.view_layer.update()
    return rig, mesh


def world_length(rig, bone, meters_per_unit):
    return ((rig.matrix_world @ bone.tail_local) -
            (rig.matrix_world @ bone.head_local)).length * meters_per_unit


def bone_snapshot(rig):
    return {
        bone.name: {
            'matrix': flat(bone.matrix_local), 'head': tuple(bone.head_local),
            'tail': tuple(bone.tail_local), 'parent': bone.parent.name if bone.parent else None,
            'connected': bone.use_connect, 'deform': bone.use_deform,
            'inherit_scale': bone.inherit_scale, 'length': float(bone.length),
        }
        for bone in rig.data.bones
    }


def mesh_snapshot(mesh):
    groups = {group.index: group.name for group in mesh.vertex_groups}
    keys = mesh.data.shape_keys
    return {
        'vertices': [tuple(vertex.co) for vertex in mesh.data.vertices],
        'faces': [tuple(face.vertices) for face in mesh.data.polygons],
        'weights': [sorted((groups[group.group], float(group.weight)) for group in vertex.groups)
                    for vertex in mesh.data.vertices],
        'shapes': [(key.name, key.value, key.mute, key.relative_key.name, key.vertex_group,
                    [tuple(point.co) for point in key.data]) for key in keys.key_blocks] if keys else [],
        'modifiers': [(mod.name, mod.type, mod.object.as_pointer() if mod.type == 'ARMATURE' and mod.object else None,
                       mod.use_vertex_groups if mod.type == 'ARMATURE' else None,
                       mod.use_bone_envelopes if mod.type == 'ARMATURE' else None)
                      for mod in mesh.modifiers],
    }


def curves(action):
    if action is None:
        return []
    result = list(getattr(action, 'fcurves', ()))
    for layer in getattr(action, 'layers', ()):
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', ()):
                result.extend(bag.fcurves)
    return {curve.as_pointer(): curve for curve in result}.values()


def action_snapshot(rig):
    ad = rig.animation_data
    action = ad.action if ad else None
    return {
        'pointer': action.as_pointer() if action else None,
        'slot': ad.action_slot.handle if ad and ad.action_slot else None,
        'curves': sorted((curve.data_path, curve.array_index, curve.mute,
                          [(tuple(key.co), key.interpolation, tuple(key.handle_left), tuple(key.handle_right))
                           for key in curve.keyframe_points]) for curve in curves(action)),
    }


def evaluated_points(mesh):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj = mesh.evaluated_get(depsgraph)
    data = obj.to_mesh()
    try:
        return [tuple(obj.matrix_world @ point.co) for point in data.vertices]
    finally:
        obj.to_mesh_clear()


def install_animation(rig, mesh):
    rig.data.pose_position = 'POSE'
    rotations = {
        'Head': (.08, -.03, .02), 'eye.L': (.12, .03, -.06),
        'eye.R': (-.10, -.02, .07), 'thumb.02.L': (.08, -.20, .10),
        'thumb.03.L': (0, .18, .25),
    }
    for name, rotation in rotations.items():
        bone = rig.pose.bones[name]
        bone.rotation_mode = 'XYZ'
        for frame, factor in ((1, 0.0), (7, 1.0), (13, -.6)):
            bone.rotation_euler = tuple(value * factor for value in rotation)
            bone.keyframe_insert(data_path='rotation_euler', frame=frame)
    mesh.data.shape_keys.key_blocks['Artist Shape'].value = .37
    bpy.context.scene.frame_set(4)
    bpy.context.view_layer.update()


def samples(rig, mesh):
    scene = bpy.context.scene
    current = scene.frame_current
    result = {}
    for frame in (1, 4, 7, 10, 13):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        result[frame] = {
            'pose': {bone.name: flat(bone.matrix) for bone in evaluated.pose.bones},
            'surface': evaluated_points(mesh),
        }
    scene.frame_set(current)
    bpy.context.view_layer.update()
    return result


def assert_samples_equal(before, after, tolerance=3e-6):
    assert set(before) == set(after)
    for frame, expected in before.items():
        actual = after[frame]
        assert set(expected['pose']) == set(actual['pose'])
        for name, matrix in expected['pose'].items():
            assert maximum_error(matrix, actual['pose'][name]) <= tolerance, (frame, name, 'pose')
        assert len(expected['surface']) == len(actual['surface'])
        for index, (first, second) in enumerate(zip(expected['surface'], actual['surface'])):
            assert maximum_error(first, second) <= tolerance, (frame, index, 'surface')


def write_manifest(model_path, digest, payload=None):
    manifest = {
        'schema': 'cdesigner.unity-character.v1', 'filename': model_path.name,
        'files': {model_path.name: digest},
    }
    if payload is not None:
        manifest['bone_rest_lengths'] = payload
    model_path.with_suffix('.cdesigner.json').write_text(json.dumps(manifest), encoding='utf-8')


def test_real_roundtrip(unit_scale):
    source, mesh = fixture(unit_scale)
    with tempfile.TemporaryDirectory(prefix='cdesigner_length_roundtrip_') as directory:
        folder = Path(directory)
        result = worker.export_job({
            'objects': [source.name, mesh.name], 'rig': source.name,
            'filename': MODEL_NAME, 'stage': str(folder), 'unit_scale': unit_scale,
        })
        assert result['ok'], result
        payload = result['bone_rest_lengths']
        assert payload['schema'] == 'cdesigner.bone-rest-lengths/1', payload
        assert payload['space'] == 'export-world-meters', payload
        model_path = folder / MODEL_NAME
        digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
        # Independent native measurement after the worker's stable-origin move.
        expected = {bone.name: world_length(source, bone, unit_scale) for bone in source.data.bones}
        write_manifest(model_path, digest, payload)
        loaded = lengths.load(model_path, digest, RIG_NAME)
        assert loaded == payload, (loaded, payload)

        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.context.scene.unit_settings.system = 'METRIC'
        bpy.context.scene.unit_settings.scale_length = unit_scale
        bpy.ops.preferences.addon_enable(module='io_scene_fbx')
        imported = bpy.ops.import_scene.fbx(
            filepath=str(model_path), use_anim=False, automatic_bone_orientation=False,
            force_connect_children=False, primary_bone_axis='Y', secondary_bone_axis='X',
            use_custom_props=False, use_image_search=False,
        )
        assert 'FINISHED' in imported
        rig = next(obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE')
        mesh = next(obj for obj in bpy.context.scene.objects if obj.type == 'MESH')
        bpy.context.view_layer.objects.active = rig
        bpy.context.view_layer.update()
        assert set(rig.data.bones.keys())  # Do not accept an empty imported skeleton.
        for name in LEAVES:
            imported_length = world_length(rig, rig.data.bones[name], unit_scale)
            assert imported_length > expected[name] * 2, (unit_scale, name, imported_length, expected[name])

        install_animation(rig, mesh)
        before_bones = bone_snapshot(rig)
        before_mesh = mesh_snapshot(mesh)
        before_action = action_snapshot(rig)
        before_world = flat(rig.matrix_world)
        before_samples = samples(rig, mesh)

        # Historical models keep their reconstructed long leaf tails unchanged.
        write_manifest(model_path, digest)
        assert lengths.load(model_path, digest, RIG_NAME) is None
        assert lengths.restore(bpy.context, rig, None, digest, RIG_NAME, meters_per_unit=unit_scale) == 0
        assert bone_snapshot(rig) == before_bones
        assert mesh_snapshot(mesh) == before_mesh
        assert action_snapshot(rig) == before_action
        assert_samples_equal(before_samples, samples(rig, mesh), tolerance=0.0)
        write_manifest(model_path, digest, payload)
        loaded = lengths.load(model_path, digest, RIG_NAME)

        changed = lengths.restore(bpy.context, rig, loaded, digest, RIG_NAME, meters_per_unit=unit_scale)
        assert changed == len(LEAVES), (unit_scale, changed)
        after_bones = bone_snapshot(rig)
        assert set(before_bones) == set(after_bones)
        for name, original in before_bones.items():
            restored = after_bones[name]
            assert original['parent'] == restored['parent'], name
            assert original['connected'] == restored['connected'], name
            assert original['deform'] == restored['deform'], name
            assert original['inherit_scale'] == restored['inherit_scale'], name
            assert maximum_error(original['head'], restored['head']) <= 3e-6, (name, 'head')
            assert maximum_error(original['matrix'], restored['matrix']) <= 3e-6, (name, 'Rest frame')
            if name in LEAVES:
                restored_length = world_length(rig, rig.data.bones[name], unit_scale)
                assert abs(restored_length - expected[name]) <= max(3e-8, expected[name] * 3e-5), (name, restored_length, expected[name])
            else:
                assert maximum_error(original['tail'], restored['tail']) <= 3e-6, (name, 'nonterminal tail changed')
        assert mesh_snapshot(mesh) == before_mesh, 'Weights, artist shapes or raw mesh changed.'
        assert action_snapshot(rig) == before_action, 'Pose animation was rewritten.'
        assert flat(rig.matrix_world) == before_world, 'Object placement was rewritten.'
        assert_samples_equal(before_samples, samples(rig, mesh))
        assert lengths.restore(bpy.context, rig, loaded, digest, RIG_NAME, meters_per_unit=unit_scale) == 0

    print('PASS real FBX leaf-length restore, animation/skin preservation, legacy and repeat, units', unit_scale)


def only_eye_changed():
    rig, mesh = fixture(rotated=False)
    digest = '1' * 64
    payload = lengths.capture(rig, digest, meters_per_unit=1.0)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    bone = rig.data.edit_bones['eye.L']
    bone.tail = bone.head + (bone.tail - bone.head).normalized() * .30
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.update()
    return rig, mesh, payload, digest


def assert_refused_unchanged(rig, mesh, payload, digest, rig_name=RIG_NAME):
    before = (bone_snapshot(rig), mesh_snapshot(mesh), action_snapshot(rig), samples(rig, mesh))
    try:
        changed = lengths.restore(bpy.context, rig, payload, digest, rig_name, meters_per_unit=1.0)
    except lengths.BoneLengthError:
        pass
    else:
        raise AssertionError(('Unsafe metadata/dependency was not refused.', changed))
    after = (bone_snapshot(rig), mesh_snapshot(mesh), action_snapshot(rig), samples(rig, mesh))
    assert before == after, 'Refused metadata/dependency left partial changes.'


def bone_row(payload, name):
    rows = payload['bones']
    if isinstance(rows, dict):
        return rows[name]
    return next(row for row in rows if row['name'] == name)


def test_mismatched_metadata():
    rig, mesh, payload, digest = only_eye_changed()
    assert_refused_unchanged(rig, mesh, payload, '2' * 64)
    assert_refused_unchanged(rig, mesh, payload, digest, 'AnotherRig')
    wrong_head = copy.deepcopy(payload)
    bone_row(wrong_head, 'eye.L')['head'][0] += .10
    assert_refused_unchanged(rig, mesh, wrong_head, digest)
    print('PASS wrong FBX hash/rig/head preserve the entire fixture')


def test_unsafe_dependencies():
    for case in ('bone_parented_attachment', 'envelope', 'bbone'):
        rig, mesh, payload, digest = only_eye_changed()
        attachment = None
        if case == 'bone_parented_attachment':
            attachment = bpy.data.objects.new('Artist Bone Attachment', None)
            bpy.context.scene.collection.objects.link(attachment)
            attachment.parent, attachment.parent_type, attachment.parent_bone = rig, 'BONE', 'eye.L'
            attachment.location = (.02, .03, .04)
        elif case == 'envelope':
            mesh.modifiers['Ordinary Skin'].use_bone_envelopes = True
        else:
            rig.data.bones['eye.L'].bbone_segments = 3
        bpy.context.view_layer.update()
        attachment_before = flat(attachment.matrix_world) if attachment else None
        # The imported dependency contract requires a preflight refusal.
        assert_refused_unchanged(rig, mesh, payload, digest)
        if attachment:
            assert flat(attachment.matrix_world) == attachment_before
    print('PASS bone attachment, envelope and B-Bone guards preserve the entire fixture')


def test_capture_unsafe_flags():
    for case in ('bone_parented_attachment', 'bbone'):
        rig, mesh = fixture(rotated=False)
        if case == 'bone_parented_attachment':
            attachment = bpy.data.objects.new('Source Bone Attachment', None)
            bpy.context.scene.collection.objects.link(attachment)
            attachment.parent, attachment.parent_type, attachment.parent_bone = rig, 'BONE', 'eye.L'
        else:
            rig.data.bones['eye.L'].bbone_segments = 3
        bpy.context.view_layer.update()
        before = (bone_snapshot(rig), mesh_snapshot(mesh), action_snapshot(rig))
        payload = lengths.capture(rig, '1' * 64)
        assert bone_row(payload, 'eye.L')['terminal'] is True
        assert bone_row(payload, 'eye.L')['restore'] is False
        assert bone_row(payload, 'eye.R')['restore'] is True
        assert before == (bone_snapshot(rig), mesh_snapshot(mesh), action_snapshot(rig))
    print('PASS capture marks source attachment/B-Bone leaves ineligible without editing data')


def context_snapshot():
    context = bpy.context
    return (context.window.scene.as_pointer(), context.window.view_layer.as_pointer(),
            context.scene.frame_current, context.mode,
            context.view_layer.objects.active.as_pointer() if context.view_layer.objects.active else None,
            tuple(sorted(obj.as_pointer() for obj in context.selected_objects)))


def id_snapshot():
    return {(block.bl_rna.identifier, block.as_pointer(), block.name)
            for block in bpy.data.user_map()}


def test_production_source_import_boundary():
    source_rig, mesh = fixture()
    with tempfile.TemporaryDirectory(prefix='cdesigner_length_source_boundary_') as directory:
        folder = Path(directory)
        exported = worker.export_job({
            'objects': [source_rig.name, mesh.name], 'rig': source_rig.name,
            'filename': MODEL_NAME, 'stage': str(folder), 'unit_scale': 1.0,
        })
        assert exported['ok'], exported
        model_path = folder / MODEL_NAME
        digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
        payload = exported['bone_rest_lengths']
        expected_lengths = {name: world_length(source_rig, source_rig.data.bones[name], 1.0)
                            for name in LEAVES}
        write_manifest(model_path, digest, payload)
        names = sorted(source_rig.data.bones.keys())
        indices = {name: index + 1 for index, name in enumerate(names)}
        packet_bones = [{'name': RIG_NAME, 'path': RIG_NAME, 'parent': -1,
                         'restSource': 'hierarchy', 'weighted': False,
                         'rest': list(flat(source_rig.matrix_world))}]
        for name in names:
            bone = source_rig.data.bones[name]
            packet_bones.append({
                'name': name, 'path': name, 'parent': indices[bone.parent.name] if bone.parent else 0,
                'restSource': 'bindpose', 'weighted': name in LEAVES,
                'rest': list(flat(source_rig.matrix_world @ bone.matrix_local)),
            })
        packet = {'clipName': 'Length Boundary', 'targetGuid': 'b' * 32,
                  'clipGuid': 'c' * 32, 'clipLocalId': 1234, 'bones': packet_bones}
        packet_path = folder / 'fixture.unity-animation.json'
        packet_path.write_text(json.dumps(packet), encoding='utf-8')
        packet['_sha256'] = hashlib.sha256(packet_path.read_bytes()).hexdigest()
        packet['_path'] = str(packet_path)
        link = {'_manifest_path': str(folder / 'fixture.link.json'),
                'modelFile': str(model_path), 'modelSha256': digest,
                'sourcePackage': str(packet_path), 'sourcePackageSha256': packet['_sha256'],
                'targetGuid': packet['targetGuid'], 'clipGuid': packet['clipGuid'],
                'clipLocalId': packet['clipLocalId']}

        # Load the production source importer without the add-on __init__.py.
        # Only Unity packet/action and Link identity dependencies are synthetic.
        package_name = 'length_test_source_package'
        package = ModuleType(package_name)
        package.__path__ = [str(ADDON_DIR)]
        link_module = ModuleType(package_name + '.animation_link')
        animation_module = ModuleType(package_name + '.unity_animation')
        package.animation_link = link_module
        package.unity_animation = animation_module
        package.bone_rest_lengths = lengths
        sys.modules[package_name] = package
        sys.modules[link_module.__name__] = link_module
        sys.modules[animation_module.__name__] = animation_module
        sys.modules[package_name + '.bone_rest_lengths'] = lengths
        calls = {'actions': 0, 'bound': 0}

        class PacketError(ValueError):
            pass

        animation_module.UnityAnimationError = PacketError
        animation_module.PACKAGE_HASH_KEY = 'length_fixture_packet_sha256'
        animation_module.load_package = lambda path: copy.deepcopy(packet)
        animation_module._playing = lambda context: False

        def set_playing(context, value):
            assert not value, 'The fixture must never start playback.'

        animation_module._set_playing = set_playing
        link_module.load_link = lambda path: copy.deepcopy(link)
        link_module._identity = lambda value: (value['modelSha256'], value['sourcePackageSha256'],
                                                value['clipGuid'], value['clipLocalId'])

        def import_action(context, rig, incoming, *, start_frame):
            calls['actions'] += 1
            assert set(rig.data.bones.keys()) == set(names)
            for name in LEAVES:
                measured = world_length(rig, rig.data.bones[name], context.scene.unit_settings.scale_length)
                assert abs(measured - expected_lengths[name]) <= 3e-7, (name, measured, expected_lengths[name])
            skin = next(obj for obj in context.scene.objects if obj.type == 'MESH')
            install_animation(rig, skin)
            action = rig.animation_data.action
            action[animation_module.PACKAGE_HASH_KEY] = incoming['_sha256']
            return SimpleNamespace(action=action, first_frame=start_frame, last_frame=start_frame + 12)

        def bind_action(rig, action, manifest_path, export_name, model_file):
            calls['bound'] += 1
            assert action[animation_module.PACKAGE_HASH_KEY] == packet['_sha256']
            assert export_name == RIG_NAME and Path(model_file) == model_path
            assert manifest_path == link['_manifest_path']

        animation_module._import_package_action = import_action
        link_module.bind_action = bind_action
        importer = module(package_name + '.animation_link_source', ADDON_DIR / 'animation_link_source.py')

        # Existing artist IDs have the same base name, so native FBX import must
        # create an independent .001 rig and retain the artist's Pose context.
        artist, artist_mesh = fixture()
        artist_scene = bpy.context.scene
        artist_scene.frame_set(23)
        bpy.context.view_layer.objects.active = artist
        bpy.ops.object.mode_set(mode='POSE')
        artist_context = context_snapshot()
        artist_data = (bone_snapshot(artist), mesh_snapshot(artist_mesh), action_snapshot(artist))
        existing_ids = id_snapshot()
        prepared_packet, export_name = importer._packet(link)
        prepared_path, prepared_digest = importer._source_model(link, None)
        result = importer._import_prepared_source(bpy.context, link, prepared_packet, export_name,
                                                  prepared_path, prepared_digest, start_frame=1)
        assert calls == {'actions': 1, 'bound': 1}, calls
        assert result.rig is not artist and result.scene is not artist_scene
        assert result.rig.name == RIG_NAME + '.001', result.rig.name
        assert bpy.context.window.scene == result.scene
        assert result.rig['character_designer_animation_source_model_sha256'] == digest
        assert existing_ids <= id_snapshot(), 'The successful import removed an artist ID.'
        for name in LEAVES:
            measured = world_length(result.rig, result.rig.data.bones[name], 1.0)
            assert abs(measured - expected_lengths[name]) <= 3e-7, name
        bpy.context.window.scene = artist_scene
        assert context_snapshot() == artist_context, 'Successful import changed the saved artist context.'
        assert artist_data == (bone_snapshot(artist), mesh_snapshot(artist_mesh), action_snapshot(artist))

        # The same production entry point must reject the metadata before any
        # Action/Link write, remove its new IDs, and restore all existing state.
        tampered = copy.deepcopy(payload)
        bone_row(tampered, 'eye.L')['head'][0] += .10
        write_manifest(model_path, digest, tampered)
        protected_ids = id_snapshot()
        protected_context = context_snapshot()
        try:
            importer._import_prepared_source(bpy.context, link, prepared_packet, export_name,
                                             prepared_path, prepared_digest, start_frame=1)
        except PacketError as error:
            assert 'bind frame' in str(error), error
        else:
            raise AssertionError('Production source import accepted a mismatched bone head.')
        assert calls == {'actions': 1, 'bound': 1}, 'Invalid metadata reached Action or Link writes.'
        assert id_snapshot() == protected_ids, 'Failed source import leaked IDs or removed existing IDs.'
        assert context_snapshot() == protected_context, 'Failed import did not restore artist context.'
        assert artist_data == (bone_snapshot(artist), mesh_snapshot(artist_mesh), action_snapshot(artist))
        assert hashlib.sha256(model_path.read_bytes()).hexdigest() == digest
    print('PASS production source import restores leaf tails before Action binding and rolls back invalid metadata')


test_real_roundtrip(1.0)
test_real_roundtrip(.01)
test_mismatched_metadata()
test_unsafe_dependencies()
test_capture_unsafe_flags()
test_production_source_import_boundary()
print('BONE_REST_LENGTHS_TESTS_OK')
