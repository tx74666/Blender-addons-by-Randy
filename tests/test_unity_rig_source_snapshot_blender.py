"""Tiny native source-definition checks; run only in an owned factory Blender.

The package is loaded without registration. All mutation constructs private test
fixtures; rig_source_snapshot itself must leave their native author data intact.
Pass --result to a new evidence JSON path through the trusted launcher.
"""
import argparse
import ast
import datetime
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
import traceback
import types

import bpy

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'addons' / 'character_designer'
package = types.ModuleType('character_designer')
package.__path__ = [str(PACKAGE)]
sys.modules['character_designer'] = package
exporter = importlib.import_module('character_designer.unity_export')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
        separators=(',', ':')).encode('utf8')).hexdigest()


def fixture(*, nodes=False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    rig = bpy.data.objects.new('Snapshot Rig', bpy.data.armatures.new('Snapshot Bones'))
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bone = rig.data.edit_bones.new('Root')
    bone.head, bone.tail = (0, 0, 0), (0, 1, 0)
    bpy.ops.object.mode_set(mode='OBJECT')
    mesh = bpy.data.meshes.new('Snapshot Mesh')
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new('Snapshot Hair', mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = rig
    modifier = obj.modifiers.new('Native Binding', 'ARMATURE')
    modifier.object = rig
    obj.vertex_groups.new(name='Root').add([0, 1, 2, 3], 1, 'REPLACE')
    obj.shape_key_add(name='Basis')
    obj.shape_key_add(name='Artist Shape')
    mesh.uv_layers.new(name='First UV')
    mesh.uv_layers.new(name='Second UV')
    mesh.color_attributes.new(name='First Color', type='FLOAT_COLOR', domain='CORNER')
    mesh.color_attributes.new(name='Second Color', type='FLOAT_COLOR', domain='CORNER')
    rig[exporter.UPDATE_RIG_ID] = '1' * 32
    obj[exporter.UPDATE_PART_ID] = '2' * 32
    obj[exporter.UPDATE_PART_OWNER] = '1' * 32
    obj['character_designer_hair_strands_v1'] = '{"native_fixture":1}'
    if nodes:
        tree = bpy.data.node_groups.new('Snapshot Geometry', 'GeometryNodeTree')
        tree.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        knob = tree.interface.new_socket(name='Amount', in_out='INPUT', socket_type='NodeSocketFloat')
        incoming = tree.nodes.new('NodeGroupInput')
        outgoing = tree.nodes.new('NodeGroupOutput')
        tree.links.new(incoming.outputs['Geometry'], outgoing.inputs['Geometry'])
        modifier = obj.modifiers.new('Native Geometry Inputs', 'NODES')
        modifier.node_group = tree
        getattr(modifier.properties.inputs, knob.identifier).value = .5
        return rig, obj, tree, knob.identifier
    return rig, obj


def author(rig, obj):
    """Independent fixture witness, including state intentionally not admitted."""
    def primitive(raw):
        if isinstance(raw, bpy.types.ID): return (raw.bl_rna.identifier, raw.name_full, raw.as_pointer())
        if raw is None or type(raw) in (str, int, bool, float): return raw
        if hasattr(raw, 'items'): return {str(key): primitive(value) for key, value in raw.items()}
        if hasattr(raw, '__len__') and hasattr(raw, '__getitem__'): return [primitive(raw[i]) for i in range(len(raw))]
        raise TypeError(type(raw).__name__)
    def properties(owner):
        try: return primitive(dict(owner.items()))
        except TypeError as error:
            if "doesn't support IDProperties" not in str(error): raise
            return None
    def node_inputs(modifier):
        if modifier.type != 'NODES' or modifier.node_group is None: return None
        result = []
        for item in modifier.node_group.interface.items_tree:
            if getattr(item, 'in_out', None) != 'INPUT': continue
            socket = getattr(modifier.properties.inputs, item.identifier, None)
            if socket is not None:
                result.append((item.identifier, socket.type,
                    primitive(socket.value) if hasattr(socket, 'value') else None,
                    getattr(socket, 'attribute_name', None), getattr(socket, 'layer_name', None)))
        return result
    keys = obj.data.shape_keys
    return repr((
        primitive(dict(obj.items())), primitive(dict(rig.items())),
        tuple(value for item in (rig, obj) for row in item.matrix_basis for value in row),
        [(bone.name, tuple(bone.head_local), tuple(bone.tail_local), primitive(dict(bone.items()))) for bone in rig.data.bones],
        [(bone.name, tuple(value for row in bone.matrix_basis for value in row)) for bone in rig.pose.bones],
        [tuple(vertex.co) for vertex in obj.data.vertices],
        [tuple(edge.vertices) for edge in obj.data.edges],
        [(group.name, group.lock_weight) for group in obj.vertex_groups],
        [[(item.group, item.weight) for item in vertex.groups] for vertex in obj.data.vertices],
        [(layer.name, layer.active_render, [tuple(item.uv) for item in layer.data]) for layer in obj.data.uv_layers],
        [(layer.name, [tuple(item.color) for item in layer.data]) for layer in obj.data.color_attributes],
        obj.data.color_attributes.render_color_index,
        [(key.name, key.value, key.mute, key.slider_min, key.slider_max,
          [tuple(point.co) for point in key.data]) for key in keys.key_blocks] if keys else None,
        [(modifier.name, modifier.type, modifier.show_viewport,
          primitive(getattr(modifier, 'object', None)), primitive(getattr(modifier, 'node_group', None)),
          properties(modifier), node_inputs(modifier)) for modifier in obj.modifiers],
        bpy.context.scene.frame_current, bpy.context.mode,
        tuple(item.as_pointer() for item in bpy.context.selected_objects),
        bpy.context.view_layer.objects.active.as_pointer() if bpy.context.view_layer.objects.active else None,
    ))


def read(rig, obj):
    before = author(rig, obj)
    try:
        return exporter.rig_source_snapshot(rig, [rig, obj])
    finally:
        assert author(rig, obj) == before, 'Snapshot mutated native author data/context'


def changed(mutation):
    rig, obj = fixture()
    before = digest(read(rig, obj))
    mutation(rig, obj)
    assert digest(read(rig, obj)) != before, 'Definition change was not detected'


def main(result):
    if result.exists(): raise RuntimeError('Use a new result path')
    source = PACKAGE / 'unity_export.py'
    report = {'schema': 'x.rig-source-snapshot-native/1', 'script': str(Path(__file__).resolve()),
        'script_sha256': sha(__file__), 'source': str(source), 'source_sha256': sha(source),
        'blender_version': bpy.app.version_string, 'config': bpy.utils.user_resource('CONFIG'),
        'author_witness_scope': ['fixture object/bone custom properties and object bases',
            'Rest head/tail, raw pose channels, geometry/edges, groups/weights/locks',
            'UV/color data and render choices, key definitions/values/coordinates',
            'modifier bindings/custom properties/typed Geometry Nodes values',
            'frame/mode/selection/active object'],
        'scope': 'Factory-owned tiny fixtures only; no artist, Unity, live add-on registration or release',
        'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'checks': []}

    def check(name, callback):
        entry = {'name': name}
        try:
            callback()
            entry['passed'] = True
        except Exception:
            entry.update(passed=False, error=traceback.format_exc())
        report['checks'].append(entry)

    def stable_and_context():
        rig, obj = fixture()
        before = digest(read(rig, obj))
        assert digest(read(rig, obj)) == before
        rig.pose.bones['Root'].rotation_mode = 'XYZ'
        rig.pose.bones['Root'].rotation_euler.z = .3
        bpy.context.scene.frame_current = 19
        rig.show_in_front = True
        obj.hide_render = True
        rig.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        assert digest(read(rig, obj)) == before, 'Pose/frame/display/selection affected definition'
    check('repeat_and_pose_frame_display_selection_independent', stable_and_context)

    def rest(rig, obj):
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode='EDIT')
        rig.data.edit_bones['Root'].tail.y += .1
        bpy.ops.object.mode_set(mode='OBJECT')
    check('rest_change', lambda: changed(rest))
    check('geometry_change', lambda: changed(lambda rig, obj: setattr(obj.data.vertices[0].co, 'z', .1)))
    check('weight_change', lambda: changed(lambda rig, obj: obj.vertex_groups['Root'].add([0], .4, 'REPLACE')))
    check('group_lock_change', lambda: changed(lambda rig, obj: setattr(obj.vertex_groups['Root'], 'lock_weight', True)))
    check('artist_key_coordinates_change', lambda: changed(lambda rig, obj: setattr(obj.data.shape_keys.key_blocks['Artist Shape'].data[0].co, 'z', .2)))
    check('artist_key_definition_change', lambda: changed(lambda rig, obj: setattr(obj.data.shape_keys.key_blocks['Artist Shape'], 'slider_max', 2)))
    check('modifier_binding_change', lambda: changed(lambda rig, obj: setattr(obj.modifiers[0], 'object', None)))
    check('native_hair_registry_change', lambda: changed(lambda rig, obj: obj.__setitem__('character_designer_hair_strands_v1', '{"native_fixture":2}')))
    check('active_render_uv_change', lambda: changed(lambda rig, obj: setattr(obj.data.uv_layers[1], 'active_render', True)))
    check('render_color_change', lambda: changed(lambda rig, obj: setattr(obj.data.color_attributes, 'render_color_index', 1)))

    def geometry_nodes_inputs():
        rig, obj, tree, identifier = fixture(nodes=True)
        before = digest(read(rig, obj))
        getattr(obj.modifiers[-1].properties.inputs, identifier).value = .75
        assert digest(read(rig, obj)) != before, 'Geometry Nodes modifier input was omitted'
    check('geometry_nodes_typed_modifier_input_change', geometry_nodes_inputs)

    def geometry_nodes_display_and_time():
        rig, obj, tree, identifier = fixture(nodes=True)
        before = digest(read(rig, obj))
        tree.nodes['Group Input'].location.x += 10
        assert digest(read(rig, obj)) == before, 'Node canvas display affected definition'
        tree.nodes.new('GeometryNodeInputSceneTime')
        try:
            read(rig, obj)
        except exporter.ExportError as error:
            assert 'Time-dependent' in str(error), str(error)
        else: raise AssertionError('Scene Time node was admitted')
    check('geometry_nodes_display_and_time_refusal', geometry_nodes_display_and_time)

    def forearm_exclusion():
        path = ROOT / 'tests' / 'test_unity_forearm_blender.py'
        tree = ast.parse(path.read_text(encoding='utf8'))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'fixture')
        scope = {'bpy': bpy, 'math': math, 'ft': importlib.import_module('character_designer.forearm_twist')}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
        rig, obj, records = scope['fixture']()
        before = digest(read(rig, obj))
        key = obj.data.shape_keys.key_blocks[records['L']['key']]
        key.data[0].co.z += .01
        assert digest(read(rig, obj)) == before, 'Authenticated derived coordinate was included'
        obj.data.shape_keys.key_blocks['SleeveShape'].data[0].co.z += .01
        assert digest(read(rig, obj)) != before, 'Unowned artist key coordinate was excluded'
    check('authenticated_forearm_exclusion_is_exact', forearm_exclusion)

    def library_roundtrip():
        rig, obj, tree, identifier = fixture(nodes=True)
        before = digest(read(rig, obj))
        with tempfile.TemporaryDirectory(prefix='rig-source-snapshot-', dir=result.parent) as directory:
            library = Path(directory) / 'owned_snapshot.blend'
            bpy.data.libraries.write(str(library), {rig, obj}, path_remap='ABSOLUTE', fake_user=False, compress=False)
            bpy.ops.wm.open_mainfile(filepath=str(library), use_scripts=True)
            assert digest(read(bpy.data.objects['Snapshot Rig'], bpy.data.objects['Snapshot Hair'])) == before, 'Library definition differs after native reopen'
    check('private_library_save_reopen_definition_equal', library_roundtrip)
    report['source_unchanged'] = sha(source) == report['source_sha256']
    report['passed'] = report['source_unchanged'] and all(row['passed'] for row in report['checks'])
    report['completed_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'result': str(result)}))
    if not report['passed']: raise RuntimeError('Native source-snapshot checks failed; see preserved result')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--result', required=True, type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    main(args.result.resolve())
