"""Forearm export data, linear correspondence and FBX identity checks in Blender."""
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import forearm_twist as ft
from character_designer import forearm_twist_profile as profile
from character_designer import unity_forearm as portable
from character_designer import unity_export_worker as worker


def fixture():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    rig = bpy.data.objects.new('Rig', bpy.data.armatures.new('Native'))
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for side, x in (('L', 1), ('R', -1)):
        previous = None
        for name, start, end in (('Upper', 0, 1), ('Lower', 1, 2), ('Hand', 2, 2.4)):
            bone = rig.data.edit_bones.new(name + '.' + side)
            bone.head, bone.tail = (x, start, 0), (x, end, 0)
            bone.parent = previous
            previous = bone
    bpy.ops.object.mode_set(mode='OBJECT')
    vertices, faces = [], []
    for side, x in (('L', 1), ('R', -1)):
        start = len(vertices)
        for ring in range(9):
            for spoke in range(8):
                a = math.tau * spoke / 8
                vertices.append((x + .12 * math.cos(a), 1 + ring / 8, .12 * math.sin(a)))
        for ring in range(8):
            for spoke in range(8):
                a = start + 8 * ring + spoke
                b = start + 8 * ring + (spoke + 1) % 8
                faces.append((a, b, b + 8, a + 8))
    mesh = bpy.data.meshes.new('Body Mesh')
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new('Body', mesh)
    bpy.context.scene.collection.objects.link(obj)
    skin = obj.modifiers.new('Armature', 'ARMATURE')
    skin.object = rig
    sub = obj.modifiers.new('Subdivision', 'SUBSURF')
    sub.levels = 1
    uv = mesh.uv_layers.new(name='Artist UV')
    for loop in mesh.loops:
        uv.data[loop.index].uv = (.2 + .1 * (loop.vertex_index % 8), .25)
    basis = obj.shape_key_add(name='Basis')
    artist = obj.shape_key_add(name='SleeveShape')
    for i, point in enumerate(artist.data):
        point.co.z += .015 * math.sin(i)
    artist.value = .37
    mask = obj.vertex_groups.new(name='Artist Mask')
    mask.add(list(range(len(mesh.vertices))), .6, 'REPLACE')
    artist.vertex_group = mask.name
    records = {}
    for side, start in (('L', 0), ('R', 72)):
        lower = obj.vertex_groups.new(name='Lower.' + side)
        hand = obj.vertex_groups.new(name='Hand.' + side)
        for i in range(start, start + 72):
            t = ((i - start) // 8) / 8
            lower.add([i], 1. - .7 * t, 'REPLACE')
            hand.add([i], .7 * t, 'REPLACE')
        key = obj.shape_key_add(name='CD Forearm Twist.' + side)
        key.value = .6
        key.data[start + 32].co.x += .025
        chain = [name + '.' + side for name in ('Upper', 'Lower', 'Hand')]
        rings = [{'position': ring / 8, 'ratio': .15 + .7 * ring / 8,
                  'vertices': list(range(start + ring * 8, start + (ring + 1) * 8))}
                 for ring in range(9)]
        records[side] = {'key': key.name, 'enabled': side == 'L', 'armature': rig.name,
            'chain': chain, 'rest': ft._rest_signature(rig, chain), 'topology': ft._topology(mesh),
            'vertices': list(range(start, start + 72)),
            'positions': [((i - start) // 8) / 8 for i in range(start, start + 72)],
            'rings': rings, 'range_start': 1, 'range_end': 7, 'transition': .15}
    ft._write_records(obj, records)
    rig.pose.bones['Hand.L'].rotation_mode = 'XYZ'
    rig.pose.bones['Hand.L'].rotation_euler.y = .7
    bpy.context.view_layer.update()
    return rig, obj, records


def snapshot(rig, obj):
    return repr((obj[ft.RECORD_KEY],
        [(key.name, key.value, key.mute, key.relative_key.name, key.vertex_group,
          list(worker._coordinates(key.data))) for key in obj.data.shape_keys.key_blocks],
        [(pb.name, list(v for row in pb.matrix_basis for v in row)) for pb in rig.pose.bones],
        [[(g.group, g.weight) for g in v.groups] for v in obj.data.vertices],
        [layer.name for layer in obj.data.uv_layers],
        [tuple(item.uv) for item in obj.data.uv_layers[0].data],
        [(m.name, m.type, m.show_viewport) for m in obj.modifiers]))


def expect_error(callback, contains):
    try:
        callback()
    except ValueError as error:
        assert contains.lower() in str(error).lower(), str(error)
    else:
        raise AssertionError('Expected rejection: ' + contains)


def test_capture_range_shapes_no_mutation():
    rig, obj, records = fixture()
    before = snapshot(rig, obj)
    payload = portable.capture(obj)
    assert payload == portable.capture(obj)
    assert snapshot(rig, obj) == before
    assert len(payload['sources']) == 80
    assert [side['enabled'] for side in payload['sides']] == [True, False]
    assert [key['name'] for key in payload['shapes']] == ['SleeveShape']
    for sample, delta in zip(payload['sources'], payload['shapes'][0]['deltas']):
        i = sample['vertex']
        side = payload['sides'][sample['side']]['name']
        record = records[side]
        position = record['positions'][record['vertices'].index(i)]
        expected = profile.range_influence(position, record['rings'], 1, 7, .15)
        assert sample['influence'] == expected and 0. < expected <= 1.
        assert .15 < sample['ratio'] < .85
        assert abs(delta['z'] - .015 * math.sin(i) * .6) < 1e-7
        assert abs(sample['lowerWeight'] + sample['handWeight'] - 1.) < 1e-6
    print('PASS bounds gate exported samples; saved ratios, disabled side, artist mask and input state preserved')


def test_invalid_capture_rejected():
    rig, obj, records = fixture()
    original = obj[ft.RECORD_KEY]
    broken = copy.deepcopy(records)
    broken['R']['positions'].pop()
    obj[ft.RECORD_KEY] = json.dumps(broken)
    expect_error(lambda: portable.capture(obj), 'correspondence')
    obj[ft.RECORD_KEY] = original
    obj.data.vertices[0].co.x += .1
    # Coordinate edits are legal: correspondence is topology plus rest-chain based.
    assert portable.capture(obj)
    mirror = obj.modifiers.new('Geometry-dependent', 'MIRROR')
    expect_error(lambda: portable.capture(obj), 'Subdivision')
    obj.modifiers.remove(mirror)
    obj.scale.x = -1
    bpy.context.view_layer.update()
    expect_error(lambda: portable.capture(obj), 'transform')
    print('PASS malformed correspondence, unsupported modifier and reflected bind-space rejected')


def test_stencils_uv_and_fbx():
    rig, obj, records = fixture()
    payload = portable.capture(obj)
    folder = Path(tempfile.mkdtemp(prefix='cdesigner_forearm_test_'))
    result = worker.export_job({'objects': [rig.name, obj.name], 'rig': rig.name,
        'filename': 'Character.fbx', 'stage': str(folder), 'unit_scale': 1.,
        'owned_keys': {obj.name:[record['key'] for record in records.values()]},
        'forearm': {obj.name:payload}})
    assert result['ok'] and not any('omitted' in warning for warning in result['warnings'])
    path = folder / 'Character.forearm.json'
    sidecar = json.loads(path.read_text(encoding='utf8'))
    assert sidecar['schema'] == portable.SCHEMA
    assert sidecar['fbxSha256'] == hashlib.sha256((folder / 'Character.fbx').read_bytes()).hexdigest()
    baked = sidecar['meshes'][0]
    assert 0. <= baked['stencilCheckMaxError'] < 3e-6
    assert baked['idUvChannel'] == 1 and baked['stencils']
    assert set(obj.data.shape_keys.key_blocks.keys()) == {'Basis', 'SleeveShape'}
    assert obj.data.uv_layers.active.name == 'Artist UV'
    assert obj.data.uv_layers['Artist UV'].active_render
    for loop in obj.data.loops:
        assert tuple(obj.data.uv_layers[portable.UV_NAME].data[loop.index].uv) == (float(loop.vertex_index + 1), .375)
    # Real FBX import must retain identity even after artist keys were re-created.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module='io_scene_fbx')
    bpy.ops.import_scene.fbx(filepath=str(folder / 'Character.fbx'), use_anim=False)
    imported = next(item for item in bpy.context.scene.objects if item.type == 'MESH')
    assert set(imported.data.shape_keys.key_blocks.keys()) == {'Basis', 'SleeveShape'}
    assert len(imported.data.vertices) == baked['vertexCount']
    uv = imported.data.uv_layers[portable.UV_NAME]
    identities = {}
    for loop in imported.data.loops:
        value = tuple(uv.data[loop.index].uv)
        assert abs(value[1] - .375) < 1e-6
        identity = round(value[0]) - 1
        assert abs(value[0] - identity - 1) < 1e-6
        identities.setdefault(loop.vertex_index, set()).add(identity)
    assert all(len(ids) == 1 for ids in identities.values())
    assert {next(iter(ids)) for ids in identities.values()} == set(range(baked['vertexCount']))
    print('PASS real FBX/sidecar with exact vertex IDs, retained artist key/UV and validated Subdivision stencils')


test_capture_range_shapes_no_mutation()
test_invalid_capture_rejected()
test_stencils_uv_and_fbx()
print('UNITY_FOREARM_TESTS_OK')
