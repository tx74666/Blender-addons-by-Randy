"""Source skin diagnostic regressions; run with Blender --factory-startup -b."""

import importlib.util
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'unity_diagnostics', ROOT / 'addons/character_designer/unity_diagnostics.py')
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


def rig_fixture(name='Rig'):
    armature = bpy.data.armatures.new(name + 'Bones')
    rig = bpy.data.objects.new(name, armature)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for index, (bone_name, deform) in enumerate([
            ('Forearm', True), ('Native', True), ('Inert', False),
            ('CTRL_native_deform', True), ('namespace:MCH_hidden', False),
            ('OwnedControl', True)]):
        bone = armature.edit_bones.new(bone_name)
        bone.head = (index, 0, 0)
        bone.tail = (index, 0, 1)
        bone.use_deform = deform
    bpy.ops.object.mode_set(mode='OBJECT')
    armature.bones['OwnedControl']['character_designer_owner'] = 'limb_ik'
    return rig


def fixture():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    rig = rig_fixture()
    data = bpy.data.meshes.new('Source')
    data.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                      (2, 0, 0), (2, 1, 0)], [], [(0, 1, 2, 3)])
    data.update()
    mesh = bpy.data.objects.new('Body', data)
    bpy.context.scene.collection.objects.link(mesh)
    skin = mesh.modifiers.new('Skin', 'ARMATURE')
    skin.object = rig
    for name, vertices, weight in [
            ('Forearm', [0], 1), ('Inert', [1], 1),
            ('MissingBone', [2], 1), ('OwnedControl', [3], 1),
            ('CTRL_native_deform', [4], .25), ('Native', [5], 1e-10)]:
        group = mesh.vertex_groups.new(name=name)
        group.add(vertices, weight, 'REPLACE')
    mesh.shape_key_add(name='Basis')
    key = mesh.shape_key_add(name='Smile')
    key.data[0].co.z += .3
    key.value = .4
    return rig, mesh, skin


def snapshot(mesh):
    return (
        tuple(tuple(vertex.co) for vertex in mesh.data.vertices),
        tuple(tuple((entry.group, entry.weight) for entry in vertex.groups)
              for vertex in mesh.data.vertices),
        tuple((key.name, key.value, tuple(tuple(point.co) for point in key.data))
              for key in mesh.data.shape_keys.key_blocks),
        tuple(vertex.select for vertex in mesh.data.vertices),
        tuple((edge.select, tuple(edge.vertices)) for edge in mesh.data.edges),
        tuple((polygon.select, tuple(polygon.vertices)) for polygon in mesh.data.polygons),
        tuple((modifier.name, modifier.type, modifier.show_viewport)
              for modifier in mesh.modifiers),
        bpy.context.mode,
    )


def test_readonly_and_actual_deform_weights():
    rig, mesh, _ = fixture()
    before = snapshot(mesh)
    assert diagnostics.unweighted_vertex_indices(mesh) == [1, 2, 3, 5]
    assert diagnostics.unweighted_vertex_indices(mesh, retained_only=False) == [1, 2, 5]
    assert snapshot(mesh) == before
    assert diagnostics.unweighted_vertex_indices(None) is None
    assert diagnostics.unweighted_vertex_indices(rig) is None
    # A control-like name alone must not discard a real, native deform bone.
    assert not diagnostics.is_generated_control(rig.data.bones['CTRL_native_deform'])
    assert diagnostics.is_generated_control(rig.data.bones['namespace:MCH_hidden'])
    print('PASS source indices, actual deform weights, generated-control filtering, no mutation')


def test_changes_are_recomputed():
    rig, mesh, _ = fixture()
    forearm = mesh.vertex_groups['Forearm']
    forearm.add([1, 2, 3, 5], .25, 'REPLACE')
    assert diagnostics.unweighted_vertex_indices(mesh) == []
    forearm.remove([1])
    assert diagnostics.unweighted_vertex_indices(mesh) == [1]
    rig.data.bones['Forearm'].use_deform = False
    assert diagnostics.unweighted_vertex_indices(mesh) == [0, 1, 2, 3, 5]
    mesh.vertex_groups.clear()
    assert diagnostics.unweighted_vertex_indices(mesh) == list(range(6))
    print('PASS weight repair/removal, bone changes, and absent groups recomputed')


def test_enabled_rigs_and_no_skin():
    rig, mesh, skin = fixture()
    skin.show_viewport = False
    assert diagnostics.unweighted_vertex_indices(mesh) is None
    skin.show_viewport = True
    skin.object = None
    assert diagnostics.unweighted_vertex_indices(mesh) is None
    skin.object = rig
    other = rig_fixture('SecondRig')
    other.data.bones['Native'].use_deform = False
    another_skin = mesh.modifiers.new('OtherSkin', 'ARMATURE')
    another_skin.object = other
    # A group is valid when any enabled target rig really deforms that bone.
    rig.data.bones['Forearm'].use_deform = False
    assert diagnostics.unweighted_vertex_indices(mesh) == [1, 2, 3, 5]
    another_skin.show_viewport = False
    assert diagnostics.unweighted_vertex_indices(mesh) == [0, 1, 2, 3, 5]
    print('PASS disabled/missing/multiple Armature targets')


def test_source_indices_before_modifier_expansion():
    _, mesh, _ = fixture()
    mirror = mesh.modifiers.new('Mirror', 'MIRROR')
    mirror.use_mirror_merge = False
    subdiv = mesh.modifiers.new('Subdivision', 'SUBSURF')
    subdiv.levels = 2
    bpy.context.view_layer.update()
    evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
    assert len(evaluated.data.vertices) > len(mesh.data.vertices)
    assert diagnostics.unweighted_vertex_indices(mesh) == [1, 2, 3, 5]
    assert len(mesh.data.vertices) == 6
    print('PASS source topology preserved despite Mirror and Subdivision')


test_readonly_and_actual_deform_weights()
test_changes_are_recomputed()
test_enabled_rigs_and_no_skin()
test_source_indices_before_modifier_expansion()
print('UNITY_DIAGNOSTICS_TESTS_PASSED')
