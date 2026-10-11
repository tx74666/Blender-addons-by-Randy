"""Persistent real-worker material export vectors from a disposable fixture.

Example (append arguments after Blender's ``--``):
  blender --background --factory-startup --threads 2 --python-exit-code 1 \
    --python tests/test_unity_material_match_export_blender.py -- \
    --projection PATH_TO_RELEASE --output PATH_TO_HIDDEN_STAGING

Only a fresh factory process is accepted. Output must be inside .codex/task-
staging and must not already contain files. Each export uses the real worker
and coordinator, has a maximum 60-second deadline, and emits bounded progress.
The fake .mat/.meta validate recipe identity only; they prove no Unity remap.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import traceback
from unittest.mock import patch

import bpy


def options():
    arguments = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projection', type=Path, help='Release root, addons folder, or Character Designer folder')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-version', default='0.77.20')
    parser.add_argument('--deadline', type=float, default=55.0)
    args = parser.parse_args(arguments)
    if not 0 < args.deadline <= 60:
        parser.error('--deadline must be greater than zero and no more than 60 seconds')
    return args


def addon_directory(projection):
    projection = (projection or Path(__file__).resolve().parents[1]).resolve()
    for candidate in (projection / 'addons', projection, projection.parent):
        if (candidate / 'character_designer/__init__.py').is_file():
            return candidate
    raise ValueError('The chosen projection has no Character Designer source package.')


def output_directory(path):
    path = path.expanduser().resolve()
    names = tuple(part.casefold() for part in path.parts)
    assert any(names[i:i + 2] == ('.codex', 'task-staging') for i in range(len(names) - 1)), \
        'Use a task-owned hidden .codex/task-staging output folder.'
    assert not path.exists() or (path.is_dir() and not any(path.iterdir())), \
        'Choose a fresh output directory; existing evidence will not be overwritten.'
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fixture():
    rig_data = bpy.data.armatures.new('Cosha Export Vector Skeleton')
    rig = bpy.data.objects.new('CoshaRig', rig_data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bone = rig_data.edit_bones.new('Root')
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bone.use_deform = True
    bpy.ops.object.mode_set(mode='OBJECT')
    assert rig.pose is not None and 'Root' in rig.pose.bones

    material = bpy.data.materials.new('Crystal_Blue')
    material.use_nodes = True
    material.diffuse_color = (.08, .24, .65, 1)
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    toon = nodes.new('ShaderNodeBsdfToon')
    toon.inputs['Color'].default_value = (.08, .24, .65, 1)
    material.node_tree.links.new(toon.outputs['BSDF'], output.inputs['Surface'])

    data = bpy.data.meshes.new('Cosha Export Vector Mesh')
    data.from_pydata([(-.3, 0, 0), (.3, 0, 0), (.3, 0, 1), (-.3, 0, 1)], [], [(0, 1, 2, 3)])
    data.update()
    data.uv_layers.new(name='UVMap')
    data.materials.append(material)
    body = bpy.data.objects.new('Cosha Export Vector Body', data)
    bpy.context.scene.collection.objects.link(body)
    body.parent = rig
    body.vertex_groups.new(name='Root').add(list(range(4)), 1.0, 'REPLACE')
    modifier = body.modifiers.new('Character Armature', 'ARMATURE')
    modifier.object, modifier.use_vertex_groups, modifier.use_bone_envelopes = rig, True, False
    bpy.context.scene.character_designer_setup.rig = rig
    bpy.context.scene.character_designer_setup.body = body
    bpy.context.view_layer.update()
    return rig, body, material


def graph(material):
    return {'name': material.name, 'diffuse_color': list(material.diffuse_color),
            'nodes': [{'name': node.name, 'type': node.bl_idname,
                       'inputs': {socket.name: list(socket.default_value)
                                  if hasattr(socket.default_value, '__len__') else socket.default_value
                                  for socket in node.inputs if hasattr(socket, 'default_value')}}
                      for node in material.node_tree.nodes],
            'links': [[link.from_node.name, link.from_socket.name, link.to_node.name, link.to_socket.name]
                      for link in material.node_tree.links]}


def author_state(rig, body, material):
    return {'graph': graph(material), 'vertices': [list(vertex.co) for vertex in body.data.vertices],
            'weights': [[[group.group, group.weight] for group in vertex.groups] for vertex in body.data.vertices],
            'material_slots': [slot.material.name if slot.material else None for slot in body.material_slots],
            'bones': [[bone.name, list(bone.head_local), list(bone.tail_local), bone.use_deform]
                      for bone in rig.data.bones],
            'frame': bpy.context.scene.frame_current, 'mode': bpy.context.mode,
            'active': bpy.context.view_layer.objects.active.name,
            'selected': sorted(obj.name for obj in bpy.context.selected_objects), 'filepath': bpy.data.filepath}


def recipe(entry):
    return {name: getattr(entry, name) for name in
            ('source_material', 'material_guid', 'material_local_id', 'material_path',
             'material_name', 'project_root', 'export_asset_id')}


def fbx_material_names(filepath):
    # Inspect actual serialized FBX identifiers without importing a second
    # scene or changing the author's Material datablock.
    bpy.ops.preferences.addon_enable(module='io_scene_fbx')
    from io_scene_fbx import parse_fbx
    document, _version = parse_fbx.parse(str(filepath))
    objects = next(element for element in document.elems if element.id == b'Objects')
    names = []
    for element in objects.elems:
        if element.id == b'Material':
            raw = element.props[1]
            name = raw.split(b'\x00\x01', 1)[0].decode('utf-8')
            names.append(name.removeprefix('Material::'))
    return sorted(names)


def persist_worker(job, output, label):
    logs = output / 'logs'
    logs.mkdir(exist_ok=True)
    for source, name in ((job['root'] / 'worker.log', label + '-worker.log'),
                         (job['root'] / 'job.json', label + '-job.json'),
                         (job['stage'] / 'result.json', label + '-worker-result.json')):
        if source.is_file():
            shutil.copy2(source, logs / name)


def real_export(exporter, rig, body, material, config, output, label, deadline):
    before = author_state(rig, body, material)
    started = time.monotonic()
    print('STAGE ' + label + ': preparing real export snapshot', flush=True)
    job = exporter.begin_export(bpy.context, rig, config)
    next_update = started
    try:
        while True:
            persist_worker(job, output, label)
            elapsed = time.monotonic() - started
            if elapsed >= deadline:
                raise TimeoutError(label + ': real worker exceeded the bounded export deadline')
            if time.monotonic() >= next_update:
                print('STAGE ' + label + ': worker/publication pending; elapsed ' + format(elapsed, '.1f') + 's', flush=True)
                next_update = time.monotonic() + 2
            result = exporter.poll_export(job)
            if result is not None:
                break
            time.sleep(.1)
    finally:
        if exporter.export_running():
            persist_worker(job, output, label)
            exporter.cancel_export(job)
    assert not exporter.export_running()
    assert author_state(rig, body, material) == before, label + ': exporter changed live author data'
    model, report_file = Path(result['filepath']), Path(result['report_path'])
    report = json.loads(report_file.read_text(encoding='utf-8'))
    assert report['ok'] is True and report['schema'] == 'cdesigner.unity-character.v1'
    assert report['asset_id'] == config.asset_id and report['filename'] == model.name
    assert report['materials'] == [material.name]
    assert model.stat().st_size > 32 and fbx_material_names(model) == [material.name]
    destination = output / 'versions' / label
    destination.mkdir(parents=True)
    for relative, expected in report['files'].items():
        source = (model.parent / relative).resolve()
        assert source.is_relative_to(model.parent.resolve()) and digest(source) == expected
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copy2(report_file, destination / report_file.name)
    record = {'stage': label, 'elapsed_seconds': time.monotonic() - started,
              'fbx': str(destination / model.name), 'report': str(destination / report_file.name),
              'fbx_sha256': digest(model), 'manifest_sha256': digest(report_file),
              'materials': report['materials'], 'simple_materials': report['simple_materials'],
              'unity_material_matches': report['unity_material_matches'], 'author': before,
              'warnings': report['warnings'], 'coordinator_backup': result['backup']}
    write_json(destination / 'vector.json', record)
    print('STAGE ' + label + ': real export and author preservation PASS', flush=True)
    return record


def run(args):
    assert bpy.app.background and not bpy.data.filepath, 'Use --background --factory-startup; never an artist .blend.'
    source = addon_directory(args.projection)
    sys.path.insert(0, str(source))
    import character_designer
    from character_designer import unity_export as exporter
    assert Path(character_designer.__file__).resolve() == (source / 'character_designer/__init__.py').resolve()
    version = '.'.join(str(part) for part in character_designer.bl_info['version'])
    assert version == args.expected_version, ('Unexpected projection version', version, args.expected_version)
    output = output_directory(args.output)
    records = []
    summary = {'schema': 'cdesigner.material-match-real-export-vectors/1', 'projection': str(source),
               'version': version, 'blender_version': bpy.app.version_string,
               'limits': ['Fake .mat/.meta validate export recipe identity only.',
                          'No Unity remap, receipt, native Unity import or interactive picker is proven.'],
               'records': records, 'passed': False}
    write_json(output / 'vectors.json', summary)
    character_designer.register()
    original_resource = bpy.utils.user_resource

    def task_resource(resource_type, *, path='', create=False):
        if resource_type == 'DATAFILES' and path == 'character_designer/export_backups':
            directory = output / 'coordinator-backups'
            if create:
                directory.mkdir(parents=True, exist_ok=True)
            return str(directory)
        return original_resource(resource_type, path=path, create=create)

    try:
        rig, body, material = fixture()
        root = output / 'FakeUnityProject'
        (root / 'Assets').mkdir(parents=True)
        (root / 'ProjectSettings').mkdir()
        config = rig.character_designer_unity_export
        config.directory, config.filename, config.asset_id = str(root / 'Assets/Characters/Cosha'), 'Cosha', 'a' * 32
        original_graph = graph(material)
        with patch.object(bpy.utils, 'user_resource', side_effect=task_resource):
            records.append(real_export(exporter, rig, body, material, config, output, '01-original', args.deadline))
            assert records[-1]['simple_materials'] == [] and records[-1]['unity_material_matches'] == []
            assert any('Material "Crystal_Blue" uses a custom shader;' in warning for warning in records[-1]['warnings'])
            write_json(output / 'vectors.json', summary)

            fake_material = root / 'Assets/Materials/ExportValidationOnly.mat'
            fake_material.parent.mkdir(parents=True)
            fake_material.write_text('Fixture Material identity only; not a verified Unity Material.', encoding='utf-8')
            fake_material.with_name(fake_material.name + '.meta').write_text('guid: ' + 'b' * 32 + '\n', encoding='utf-8')
            entry = config.unity_material_matches.add()
            entry.material, entry.source_material = material, material.name
            entry.material_guid, entry.material_local_id = 'b' * 32, '2100000'
            entry.material_path, entry.material_name = fake_material.relative_to(root).as_posix(), 'Export Validation Only'
            entry.project_root, entry.export_asset_id = root.as_posix(), config.asset_id
            saved_recipe = recipe(entry)
            body.data.vertices[2].co.x += .125
            body.data.update()
            records.append(real_export(exporter, rig, body, material, config, output, '02-paired-changed-model', args.deadline))
            assert records[1]['fbx_sha256'] != records[0]['fbx_sha256']
            assert records[1]['simple_materials'] == [] and len(records[1]['unity_material_matches']) == 1
            assert records[1]['unity_material_matches'][0]['source_material'] == 'Crystal_Blue'
            assert records[1]['unity_material_matches'][0]['material_guid'] == 'b' * 32
            assert graph(material) == original_graph and recipe(entry) == saved_recipe
            write_json(output / 'vectors.json', summary)

            assert bpy.ops.character_designer.unity_simple_material(material_name=material.name, enabled=True) == {'FINISHED'}
            records.append(real_export(exporter, rig, body, material, config, output, '03-simplified', args.deadline))
            assert [item['material'] for item in records[2]['simple_materials']] == ['Crystal_Blue']
            assert records[2]['unity_material_matches'] == []
            assert not any('Material "Crystal_Blue" uses a custom shader;' in warning for warning in records[2]['warnings'])
            assert graph(material) == original_graph and recipe(entry) == saved_recipe
            write_json(output / 'vectors.json', summary)

            assert bpy.ops.character_designer.unity_simple_material(material_name=material.name, enabled=False) == {'FINISHED'}
            records.append(real_export(exporter, rig, body, material, config, output, '04-original-restored', args.deadline))
            assert records[3]['simple_materials'] == []
            assert records[3]['unity_material_matches'] == records[1]['unity_material_matches']
            assert any('Material "Crystal_Blue" uses a custom shader;' in warning for warning in records[3]['warnings'])
            assert graph(material) == original_graph and recipe(entry) == saved_recipe
            summary['saved_recipe'] = saved_recipe
            summary['passed'] = True
            write_json(output / 'vectors.json', summary)
    except BaseException as error:
        summary['failure'] = {'type': type(error).__name__, 'message': str(error), 'traceback': traceback.format_exc()}
        write_json(output / 'vectors.json', summary)
        raise
    finally:
        exporter.stop_exports()
        character_designer.unregister()
    print('UNITY_MATERIAL_MATCH_REAL_EXPORT_PASSED ' + str(output / 'vectors.json'), flush=True)


if __name__ == '__main__':
    run(options())
