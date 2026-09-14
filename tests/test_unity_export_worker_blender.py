"""Real FBX export/reimport checks, run with Blender --factory-startup -b."""
import importlib.util
import json
from pathlib import Path
import tempfile
from array import array

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('unity_export_worker', ROOT / 'addons/character_designer/unity_export_worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def fixture():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm = bpy.data.armatures.new('NativeSkeleton')
    rig = bpy.data.objects.new('CharacterRig', arm)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    root = arm.edit_bones.new('CTRL_master')
    root.head, root.tail = (0, 0, 0), (0, 0, 0.2)
    root.use_deform = False
    hip = arm.edit_bones.new('Hips')
    hip.head, hip.tail, hip.parent = (0, 0, 0), (0, 0, 1), root
    hand = arm.edit_bones.new('Hand')
    hand.head, hand.tail, hand.parent = (0, 0, 1), (0, 0, 2), hip
    helper = arm.edit_bones.new('CTRL_hand')
    helper.head, helper.tail = (0, 0, 1), (0, 0.2, 1)
    helper.use_deform = False
    bpy.ops.object.mode_set(mode='OBJECT')
    for name in ('CTRL_master', 'CTRL_hand'):
        arm.bones[name]['character_designer_owner'] = 'limb_ik'
    rig.location = (3, -2, 1)
    rig.pose.bones['Hand'].rotation_mode = 'XYZ'
    rig.pose.bones['Hand'].rotation_euler.y = 0.6
    data = bpy.data.meshes.new('BodyData')
    data.from_pydata([(0.1, -0.2, 0), (0.5, -0.2, 0), (0.5, 0.2, 1), (0.1, 0.2, 1)], [], [(0, 1, 2, 3)])
    data.update()
    mesh = bpy.data.objects.new('Body', data)
    bpy.context.scene.collection.objects.link(mesh)
    mesh.parent = rig
    mesh.matrix_parent_inverse = Matrix.Identity(4)
    group = mesh.vertex_groups.new(name='Hand')
    group.add([0, 1, 2, 3], 1.0, 'REPLACE')
    mask = mesh.vertex_groups.new(name='Smile Mask')
    mask.add([0, 1], 0.25, 'REPLACE')
    mask.add([2, 3], 0.75, 'REPLACE')
    mirror = mesh.modifiers.new('Mirror', 'MIRROR')
    mirror.use_mirror_merge = False
    subdiv = mesh.modifiers.new('Subsurf', 'SUBSURF')
    subdiv.subdivision_type = 'SIMPLE'
    subdiv.levels = 1
    skin = mesh.modifiers.new('Rig', 'ARMATURE')
    skin.object = rig
    basis = mesh.shape_key_add(name='Basis')
    smile = mesh.shape_key_add(name='Smile')
    for point in smile.data:
        point.co.y += 0.2
    smile.vertex_group = mask.name
    smile.value = 0.35
    extra = mesh.shape_key_add(name='Extra')
    extra.relative_key = smile
    for index, point in enumerate(extra.data):
        point.co = smile.data[index].co + Vector((0, 0, 0.1))
    owned = mesh.shape_key_add(name='CD Forearm Twist.L')
    for point in owned.data:
        point.co.x += 0.3
    owned.value = 1.0
    image = bpy.data.images.new('Test Texture', width=2, height=2)
    image.generated_color = (0.1, 0.2, 0.3, 1)
    material = bpy.data.materials.new('Body Material')
    material.use_nodes = True
    texture = material.node_tree.nodes.new('ShaderNodeTexImage')
    texture.image = image
    shader = material.node_tree.nodes.get('Principled BSDF')
    material.node_tree.links.new(texture.outputs['Color'], shader.inputs['Base Color'])
    mesh.data.materials.append(material)
    bpy.context.view_layer.update()
    return rig, mesh


def test_export_import():
    rig, mesh = fixture()
    folder = Path(tempfile.mkdtemp(prefix='cdesigner_worker_test_'))
    result = worker.export_job({'objects': [rig.name, mesh.name], 'rig': rig.name,
                               'filename': 'Character.fbx', 'stage': str(folder), 'unit_scale': 1.0,
                               'owned_keys': {mesh.name: ['CD Forearm Twist.L']}})
    assert result['ok']
    assert result['rigs']['CharacterRig'] == ['Hand', 'Hips'], result
    assert result['shape_keys']['Body'] == ['Smile', 'Extra']
    assert result['meshes']['Body']['vertices'] == 18, result['meshes']
    assert result['meshes']['Body']['modifiers_baked'] == ['MIRROR', 'SUBSURF']
    assert result['meshes']['Body']['unweighted_vertices'] == 0
    assert len(result['files']) == 2 and (folder / result['files'][1]).is_file(), result
    expected = {key.name: worker._coordinates(key.data) for key in mesh.data.shape_keys.key_blocks}
    expected_basis = expected['Basis']
    # Chained relative keys retain their own delta, not their relative parent's.
    assert abs((expected['Extra'][2] - expected_basis[2]) - 0.1) < 2e-6
    for vertex in mesh.data.vertices:
        assert any(group.group == mesh.vertex_groups['Hand'].index and abs(group.weight - 1) < 1e-6
                   for group in vertex.groups)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module='io_scene_fbx')
    bpy.ops.import_scene.fbx(filepath=str(folder / 'Character.fbx'), use_anim=False)
    imported_mesh = next(obj for obj in bpy.context.scene.objects if obj.type == 'MESH')
    imported_rig = next(obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE')
    assert set(imported_rig.data.bones.keys()) == {'Hand', 'Hips'}
    assert imported_rig.data.bones['Hips'].parent is None
    assert set(imported_mesh.data.shape_keys.key_blocks.keys()) == {'Basis', 'Smile', 'Extra'}
    assert len(imported_mesh.data.vertices) == 18
    for name, coords in expected.items():
        imported = worker._coordinates(imported_mesh.data.shape_keys.key_blocks[name].data)
        assert max(abs(a - b) for a, b in zip(imported, coords)) < 3e-6, name
    assert len(imported_mesh.modifiers) == 1 and imported_mesh.modifiers[0].type == 'ARMATURE'
    assert max(abs(value) for value in imported_rig.matrix_world.translation) < 1e-6
    assert all(not bone.constraints for bone in imported_rig.pose.bones)
    print('PASS real FBX export/reimport with mirrors, subdiv, masked/chained artist shapes, skin weights and texture')


def test_owned_dependency_refused():
    rig, mesh = fixture()
    mesh.data.shape_keys.key_blocks['Extra'].relative_key = mesh.data.shape_keys.key_blocks['CD Forearm Twist.L']
    try:
        worker._shape_inputs(mesh, ['CD Forearm Twist.L'])
    except worker.ExportError as error:
        assert 'depends' in str(error)
    else:
        raise AssertionError('Artist dependency on owned key was not rejected.')
    print('PASS owned calibration dependency is rejected')


def test_topology_mismatch_refused():
    rig, mesh = fixture()
    mirror = mesh.modifiers['Mirror']
    mirror.use_mirror_merge = True
    mirror.merge_threshold = 0.02
    shape = mesh.data.shape_keys.key_blocks['Smile']
    shape.vertex_group = ''
    for point in shape.data:
        point.co.x = 0
    try:
        worker._bake_mesh(bpy.context, mesh, ['CD Forearm Twist.L'], [])
    except worker.ExportError as error:
        assert 'topology' in str(error), error
    else:
        raise AssertionError('Shape-dependent mirror topology was not rejected.')
    print('PASS changing modifier topology is rejected')


def test_skirt_attachment_and_solidify():
    main, body = fixture()
    data = bpy.data.armatures.new('SkirtData')
    rig = bpy.data.objects.new('SkirtRig', data)
    bpy.context.scene.collection.objects.link(rig)
    rig['character_designer_skirt_owner'] = 'fixture-skirt'
    bpy.ops.object.select_all(action='DESELECT')
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    waist = data.edit_bones.new('Skirt_Waist')
    waist.head, waist.tail = (0, 0, 0), (0, 0, 0.2)
    deform = data.edit_bones.new('Skirt_DEF_01')
    deform.head, deform.tail, deform.parent = (0, 0, 0), (0, 0, -0.5), waist
    for name in ('Skirt_Mid', 'Skirt_MCH_01', 'Skirt_PHYS_01'):
        bone = data.edit_bones.new(name)
        bone.head, bone.tail, bone.parent = (0, 0, 0), (0, 0, -0.5), waist
        bone.use_deform = False
    bpy.ops.object.mode_set(mode='OBJECT')
    rig.parent, rig.parent_type, rig.parent_bone = main, 'BONE', 'Hips'
    rig.matrix_parent_inverse = Matrix.Identity(4)
    rig.location = (0.2, 0.3, 0.4)
    mesh_data = bpy.data.meshes.new('SkirtMeshData')
    mesh_data.from_pydata([(-0.2, 0, 0), (0.2, 0, 0), (0.2, 0, -0.5), (-0.2, 0, -0.5)], [], [(0, 1, 2, 3)])
    mesh = bpy.data.objects.new('SkirtMesh', mesh_data)
    bpy.context.scene.collection.objects.link(mesh)
    mesh.parent = rig
    mesh.vertex_groups.new(name='Skirt_DEF_01').add([0, 1, 2, 3], 1, 'REPLACE')
    mesh.modifiers.new('Rig', 'ARMATURE').object = rig
    mesh.modifiers.new('Thickness', 'SOLIDIFY').thickness = 0.05
    mesh.shape_key_add(name='Basis')
    key = mesh.shape_key_add(name='Skirt Width')
    for point in key.data:
        point.co.x *= 1.2
    bpy.context.view_layer.update()
    relative = main.matrix_world.inverted() @ rig.matrix_world
    folder = Path(tempfile.mkdtemp(prefix='cdesigner_worker_skirt_'))
    result = worker.export_job({'objects': [main.name, body.name, rig.name, mesh.name], 'rig': main.name,
                               'filename': 'Character.fbx', 'stage': str(folder), 'unit_scale': 0.01,
                               'owned_keys': {body.name: ['CD Forearm Twist.L']}})
    assert result['source_rigs']['SkirtRig'] == ['Skirt_DEF_01', 'Skirt_Waist']
    assert result['rigs']['CharacterRig'] == ['Hand', 'Hips', 'Skirt_DEF_01', 'Skirt_Waist']
    assert result['meshes']['SkirtMesh']['vertices'] == 8
    actual_relative = main.matrix_world.inverted() @ rig.matrix_world
    assert max(abs(relative[row][col] - actual_relative[row][col]) for row in range(4) for col in range(4)) < 2e-6
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module='io_scene_fbx')
    bpy.ops.import_scene.fbx(filepath=str(folder / 'Character.fbx'), use_anim=False)
    imported = bpy.data.objects['CharacterRig']
    assert imported.data.bones['Skirt_Waist'].parent == imported.data.bones['Hips']
    assert 'SkirtRig' not in bpy.data.objects
    assert len(bpy.data.objects['SkirtMesh'].data.vertices) == 8
    assert set(bpy.data.objects['SkirtMesh'].data.shape_keys.key_blocks.keys()) == {'Basis', 'Skirt Width'}
    print('PASS source skirt skeleton flattened only for export, Hips attachment, Solidify shapes and non-default units')


def test_normal_nodes_and_disabled_skinning():
    rig, mesh = fixture()
    mesh.modifiers['Rig'].show_viewport = False
    tree = bpy.data.node_groups.new('Normal Attributes', 'GeometryNodeTree')
    tree.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    entry = tree.nodes.new('NodeGroupInput')
    exit_node = tree.nodes.new('NodeGroupOutput')
    smooth = tree.nodes.new('GeometryNodeSetShadeSmooth')
    tree.links.new(entry.outputs['Geometry'], smooth.inputs['Geometry'])
    tree.links.new(smooth.outputs['Geometry'], exit_node.inputs['Geometry'])
    modifier = mesh.modifiers.new('Not Name Whitelisted', 'NODES')
    modifier.node_group = tree
    warnings = []
    result = worker._bake_mesh(bpy.context, mesh, ['CD Forearm Twist.L'], warnings)
    assert result['skinned'] is False and result['disabled_skinning'] == ['Rig']
    assert not mesh.modifiers
    assert result['modifiers_baked'] == ['MIRROR', 'SUBSURF', 'NODES']
    assert any('disabled Armature' in warning for warning in warnings)
    # A nodes graph that changes actual vertex positions is refused regardless
    # of a reassuring modifier name.
    rig, mesh = fixture()
    tree = bpy.data.node_groups.new('Geometry Change', 'GeometryNodeTree')
    tree.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    entry = tree.nodes.new('NodeGroupInput')
    exit_node = tree.nodes.new('NodeGroupOutput')
    deform = tree.nodes.new('GeometryNodeSetPosition')
    deform.inputs['Offset'].default_value = (0, 0, 0.2)
    tree.links.new(entry.outputs['Geometry'], deform.inputs['Geometry'])
    tree.links.new(deform.outputs['Geometry'], exit_node.inputs['Geometry'])
    mesh.modifiers.new('Smooth by Angle', 'NODES').node_group = tree
    try:
        worker._bake_mesh(bpy.context, mesh, ['CD Forearm Twist.L'], [])
    except worker.ExportError as error:
        assert 'changes geometry or skin weights' in str(error)
    else:
        raise AssertionError('A misleadingly named Geometry Nodes deformation was accepted.')
    print('PASS normal-only Geometry Nodes, geometry-change rejection and disabled skinning remains disabled')


def test_unsaved_image_buffer():
    rig, mesh = fixture()
    folder = Path(tempfile.mkdtemp(prefix='cdesigner_worker_painted_'))
    image = bpy.data.images['Test Texture']
    pixels = array('f', [0.07, 0.21, 0.82, 1.0] * 4)
    raw = folder / 'painted_pixels.f32'
    raw.write_bytes(pixels.tobytes())
    # The .blend snapshot's generated_color is intentionally unrelated. The
    # coordinator's captured pixel buffer must win over that stale backing.
    image.generated_color = (1, 0, 0, 1)
    result = worker.export_job({'objects': [rig.name, mesh.name], 'rig': rig.name,
                               'filename': 'Character.fbx', 'stage': str(folder),
                               'owned_keys': {mesh.name: ['CD Forearm Twist.L']},
                               'image_buffers': {'Test Texture': {'path': str(raw), 'width': 2, 'height': 2, 'count': 16}}})
    texture = next(folder / name for name in result['files'] if name.endswith('.png'))
    imported = bpy.data.images.load(str(texture), check_existing=False)
    actual = list(imported.pixels[:4])
    # Eight-bit sRGB PNG has the same small quantization as Blender Image.save.
    assert max(abs(a - b) for a, b in zip(actual, pixels[:4])) < 0.008, actual
    print('PASS unsaved painted pixel buffer survives export instead of reverting to generated/file backing')


def test_unweighted_vertices_diagnostic():
    rig, mesh = fixture()
    mesh.vertex_groups['Hand'].remove([0])
    folder = Path(tempfile.mkdtemp(prefix='cdesigner_worker_unweighted_'))
    result = worker.export_job({'objects': [rig.name, mesh.name], 'rig': rig.name,
                               'filename': 'Character.fbx', 'stage': str(folder),
                               'owned_keys': {mesh.name: ['CD Forearm Twist.L']}})
    # Mirror duplicates the unweighted original corner; subdivision does not
    # give either corner a new weight. Diagnostics must not invent skinning.
    assert result['meshes']['Body']['unweighted_vertices'] == 2, result['meshes']
    assert any('2 exported vertices have no weight' in warning for warning in result['warnings'])
    assert worker._unweighted_vertices(mesh) == 2
    print('PASS unweighted skin vertices are reported without changing weights')


def test_removed_forearm_emits_removal_marker():
    rig, mesh = fixture()
    mesh.shape_key_remove(mesh.data.shape_keys.key_blocks['CD Forearm Twist.L'])
    folder = Path(tempfile.mkdtemp(prefix='cdesigner_worker_removed_forearm_'))
    result = worker.export_job({'objects': [rig.name, mesh.name], 'rig': rig.name,
                               'filename': 'Character.fbx', 'stage': str(folder),
                               'owned_keys': {}, 'forearm': {}, 'had_forearm': True})
    assert result['ok']
    assert 'Character.forearm.json' in result['files']
    marker = json.loads((folder / 'Character.forearm.json').read_text(encoding='utf8'))
    assert marker['schema'] == 'cdesigner.forearm/1'
    assert marker['fbx'] == 'Character.fbx'
    assert marker['meshes'] == [], marker
    import hashlib
    assert marker['fbxSha256'] == hashlib.sha256((folder / 'Character.fbx').read_bytes()).hexdigest()
    assert result['forearm_correction']['meshes'] == []
    assert 'removed' in result['forearm_correction']['status'].lower()
    assert result['shape_keys']['Body'] == ['Smile', 'Extra']
    assert not any('correction' in warning.lower() for warning in result['warnings'])
    print('PASS removed forearm emits an empty matching-FBX sidecar to clear prior runtime correction')


test_export_import()
test_owned_dependency_refused()
test_topology_mismatch_refused()
test_skirt_attachment_and_solidify()
test_normal_nodes_and_disabled_skinning()
test_unsaved_image_buffer()
test_unweighted_vertices_diagnostic()
test_removed_forearm_emits_removal_marker()
print('UNITY_WORKER_TESTS_OK')
