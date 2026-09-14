"""Unity export isolation, character scope, FBX round trip and safe publication.

Run with Blender --background --factory-startup --python-exit-code 1 --python ...
All generated assets live in a temporary directory; no Unity project is used.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import character_setup, unity_export as exporter


def fail(call, fragment=''):
    try:
        call()
    except (exporter.ExportError, OSError) as error:
        assert fragment.casefold() in str(error).casefold(), str(error)
        return
    raise AssertionError('Expected rejected export: ' + fragment)


def mesh(name, rig=None, *, mirror=False, shape=False):
    data = bpy.data.meshes.new(name + 'Mesh')
    data.from_pydata([(0.1, 0, 0), (.4, 0, 0), (.4, 0, 1), (.1, 0, 1)], [], [(0, 1, 2, 3)])
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    if rig:
        obj.vertex_groups.new(name='Hips').add([0, 1], 1, 'REPLACE')
        obj.vertex_groups.new(name='Hand').add([2, 3], 1, 'REPLACE')
    if shape:
        obj.shape_key_add(name='Basis')
        key = obj.shape_key_add(name='ArtistSmile')
        key.data[2].co.y += .15
        key.value = .35
        key.keyframe_insert('value', frame=1)
        key.value = .6
        key.keyframe_insert('value', frame=10)
    if mirror:
        obj.modifiers.new('Artist Mirror', 'MIRROR').use_clip = True
    if rig:
        obj.modifiers.new('Artist Armature', 'ARMATURE').object = rig
    return obj


def armature(name):
    data = bpy.data.armatures.new(name + 'Data')
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    hips = data.edit_bones.new('Hips')
    hips.head, hips.tail = (0, 0, 0), (0, 0, .5)
    hand = data.edit_bones.new('Hand')
    hand.head, hand.tail = (0, 0, .5), (0, 0, 1)
    hand.parent = hips
    control = data.edit_bones.new('CTRL_test')
    control.head, control.tail = (0, 0, 0), (0, .2, 0)
    control.use_deform = False
    bpy.ops.object.mode_set(mode='OBJECT')
    data.bones['CTRL_test']['character_designer_owner'] = 'limb_ik'
    obj.select_set(False)
    return obj


@contextmanager
def fixture(directory):
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    rig = armature('TestRig')
    rig.location = (2, 3, 0)
    body = mesh('Body', rig, mirror=True, shape=True)
    material = bpy.data.materials.new('ArtistMaterial')
    material.use_nodes = True
    image = bpy.data.images.new('ArtistSkin', width=4, height=4)
    image.generated_color = (.2, .5, .8, 1)
    image.pixels[0:4] = (.7, .2, .1, 1)
    image_node = material.node_tree.nodes.new('ShaderNodeTexImage')
    image_node.image = image
    shader = next(node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
    material.node_tree.links.new(image_node.outputs['Color'], shader.inputs['Base Color'])
    body.data.materials.append(material)
    hidden = mesh('HiddenClothes', rig)
    hidden.hide_set(True)
    other = armature('OtherRig')
    other_body = mesh('OtherBody', other)
    widget = mesh('ArtistWidget', rig)
    rig.pose.bones['CTRL_test'].custom_shape = widget
    extra = mesh('UnboundAccessory')
    source = SimpleNamespace(rig=rig, body=body, assets=[])
    config = SimpleNamespace(directory=str(directory), filename='Character',
                             asset_id='', extras=[])
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    rig.pose.bones['Hand'].rotation_mode = 'XYZ'
    rig.pose.bones['Hand'].rotation_euler.y = .25
    rig.pose.bones['Hand'].keyframe_insert('rotation_euler', frame=1)
    bpy.context.scene.frame_set(1)
    with patch.object(character_setup, 'settings', return_value=source):
        yield SimpleNamespace(rig=rig, body=body, hidden=hidden, other=other,
                              other_body=other_body, widget=widget, extra=extra,
                              source=source, config=config)


def digest():
    def coords(items):
        return tuple(tuple(item.co) for item in items)
    def matrix(value):
        return tuple(tuple(row) for row in value)
    result = {'frame': bpy.context.scene.frame_current,
              'active': bpy.context.view_layer.objects.active.name,
              'selected': sorted(obj.name for obj in bpy.context.selected_objects),
              'filepath': bpy.data.filepath, 'mode': bpy.context.mode,
              'images': [(image.name, image.filepath, image.file_format, image.source,
                          tuple(image.size), tuple(image.pixels)) for image in bpy.data.images],
              'objects': {}}
    for obj in bpy.data.objects:
        entry = {'world': matrix(obj.matrix_world), 'parent': obj.parent.name if obj.parent else None,
                 'hidden': (obj.hide_get(), obj.hide_viewport, obj.hide_render),
                 'modifiers': [(m.name, m.type, m.show_viewport, m.show_render,
                                getattr(getattr(m, 'object', None), 'name', None))
                               for m in obj.modifiers],
                 'action': obj.animation_data.action.as_pointer() if obj.animation_data and obj.animation_data.action else None}
        if obj.type == 'MESH':
            entry['vertices'] = coords(obj.data.vertices)
            entry['faces'] = tuple(tuple(face.vertices) for face in obj.data.polygons)
            entry['weights'] = tuple(tuple((g.group, g.weight) for g in v.groups) for v in obj.data.vertices)
            entry['groups'] = tuple(g.name for g in obj.vertex_groups)
            entry['keys'] = [(k.name, k.value, k.mute, k.relative_key.name, coords(k.data))
                             for k in obj.data.shape_keys.key_blocks] if obj.data.shape_keys else None
        if obj.type == 'ARMATURE':
            entry['bones'] = [(b.name, matrix(b.matrix_local), b.parent.name if b.parent else None, b.use_deform)
                              for b in obj.data.bones]
            entry['pose'] = [(b.name, matrix(b.matrix_basis), b.custom_shape.name if b.custom_shape else None,
                              tuple((c.name, c.type) for c in b.constraints)) for b in obj.pose.bones]
            entry['position'] = obj.data.pose_position
        result['objects'][obj.name] = entry
    return result


def test_character_scope(directory):
    with fixture(directory / 'scope') as f:
        def selected():
            result = exporter.collect_character(bpy.context, f.rig, f.config)
            return {obj.name for obj in result['objects']}, result['warnings']

        expected = {'TestRig', 'Body', 'HiddenClothes'}
        assert selected()[0] == expected, selected()[0]
        # Hiding a bound garment is not an instruction to omit it from export.
        f.hidden.hide_viewport = True
        f.hidden.hide_render = True
        assert selected()[0] == expected

        disabled = mesh('DisabledArmature', f.rig)
        disabled.modifiers['Artist Armature'].show_viewport = False
        disabled.parent = f.rig
        disabled.modifiers['Artist Armature'].show_render = True
        parent_only = mesh('ParentOnly')
        parent_only.parent = f.rig
        parent_only.parent_type = 'BONE'
        parent_only.parent_bone = 'Hips'
        inactive_binding = mesh('NoBindingChannels', f.rig)
        inactive_binding.modifiers['Artist Armature'].use_vertex_groups = False
        inactive_binding.modifiers['Artist Armature'].use_bone_envelopes = False
        assert selected()[0] == expected

        # Shared setup metadata, parenting and old export overrides must never
        # promote an unbound or disabled mesh into the exported character.
        f.source.body = f.extra
        f.source.assets = [SimpleNamespace(object=obj) for obj in (f.extra, parent_only, disabled)]
        f.config.extras.append(SimpleNamespace(object=f.extra, enabled=True))
        f.config.extras.append(SimpleNamespace(object=parent_only, enabled=True))
        f.config.extras.append(SimpleNamespace(object=disabled, enabled=True))
        included, warnings = selected()
        assert included == expected, included
        assert any('UnboundAccessory' in text for text in warnings), warnings
        assert any('DisabledArmature' in text for text in warnings), warnings
        f.source.body = f.other_body
        assert selected()[0] == expected, 'Another character Body Weight Source changed export scope'
        f.source.body = f.body

        # Either active Armature binding mode is accepted, including envelopes.
        enveloped = mesh('EnvelopeBound', f.rig)
        enveloped.modifiers['Artist Armature'].use_vertex_groups = False
        enveloped.modifiers['Artist Armature'].use_bone_envelopes = True
        expected.add('EnvelopeBound')
        assert selected()[0] == expected

        # A separately generated rig attached to this character can own its mesh.
        accessory_rig = armature('AttachedAccessoryRig')
        accessory_rig.parent = f.rig
        accessory = mesh('AttachedAccessory', accessory_rig)
        expected.update({accessory_rig.name, accessory.name})
        assert selected()[0] == expected

        # An explicitly disabled old entry can still omit an otherwise bound part.
        exclusion = SimpleNamespace(object=f.hidden, enabled=False)
        f.config.extras.append(exclusion)
        assert selected()[0] == expected - {'HiddenClothes'}
        f.config.extras.remove(exclusion)
        cross_rig = f.body.modifiers.new('Foreign active Armature', 'ARMATURE')
        cross_rig.object = f.other
        fail(lambda: selected())
        f.body.modifiers.remove(cross_rig)
        assert selected()[0] == expected

        f.config.extras.append(SimpleNamespace(object=None, enabled=True))
        fail(lambda: exporter.collect_character(bpy.context, f.rig, f.config), 'missing')
        f.config.extras[-1].object = f.other_body
        fail(lambda: exporter.collect_character(bpy.context, f.rig, f.config), 'another rig')
        f.config.extras[-1].object = f.widget
        fail(lambda: exporter.collect_character(bpy.context, f.rig, f.config), 'helper')
    print('PASS active Armature-only scope: hidden, disabled, parenting, shared setup, legacy extras, envelopes, attached rigs and foreign/helper rejection', flush=True)


def fake_job(directory, stage, *, asset='profile-a', rig=None):
    stage.mkdir(parents=True, exist_ok=True)
    manifest = directory / 'Character.cdesigner.json'
    return {'directory': directory, 'stage': stage, 'filename': 'Character.fbx',
            'asset_id': asset, 'manifest_hash': exporter._hash(manifest) if manifest.exists() else None,
            'rig': rig or SimpleNamespace(name='TestRig'), 'objects': ['TestRig', 'Body']}


def publish(job, version, *, texture=True):
    (job['stage'] / 'Character.fbx').write_bytes((f'FBX fixture {version} ' * 10).encode())
    files = ['Character.fbx']
    if texture:
        (job['stage'] / 'Textures').mkdir(exist_ok=True)
        (job['stage'] / 'Textures' / 'skin.png').write_bytes(f'PNG fixture {version}'.encode())
        files.append('Textures/skin.png')
    return exporter._publish(job, {'ok': True, 'files': files, 'warnings': []})


def folder_bytes(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob('*') if path.is_file()} if directory.exists() else {}


def test_publication(directory):
    target = directory / 'publish'
    for index in range(3):
        job = fake_job(target, directory / f'publish-stage-{index}')
        publish(job, index)
        if not index:
            (target / 'Character.fbx.meta').write_bytes(b'guid: original-model-guid')
            (target / 'Textures' / 'skin.png.meta').write_bytes(b'guid: original-texture-guid')
        assert (target / 'Character.fbx.meta').read_bytes() == b'guid: original-model-guid'
        assert (target / 'Textures' / 'skin.png.meta').read_bytes() == b'guid: original-texture-guid'
    before = folder_bytes(target)
    fail(lambda: publish(fake_job(target, directory / 'wrong-profile', asset='other-profile'), 10), 'different')
    assert folder_bytes(target) == before
    (target / 'Character.fbx').write_bytes(b'Artist external FBX edit')
    edited = folder_bytes(target)
    fail(lambda: publish(fake_job(target, directory / 'externally-edited'), 11), 'outside')
    assert folder_bytes(target) == edited
    foreign = directory / 'foreign'
    foreign.mkdir()
    (foreign / 'Character.fbx').write_bytes(b'Unowned asset')
    fail(lambda: publish(fake_job(foreign, directory / 'foreign-stage'), 12), 'without')
    assert (foreign / 'Character.fbx').read_bytes() == b'Unowned asset'
    racing = directory / 'racing'
    publish(fake_job(racing, directory / 'racing-first'), 0)
    old_job = fake_job(racing, directory / 'racing-stale-job')
    publish(fake_job(racing, directory / 'racing-later-job'), 1)
    after_later = folder_bytes(racing)
    fail(lambda: publish(old_job, 2), 'changed during')
    assert folder_bytes(racing) == after_later
    for path in ('../escape.fbx', '../../out.txt', str(directory / 'absolute.fbx'),
                 'Character.fbx.meta', 'Character.fbx.META'):
        fail(lambda path=path: exporter._safe_file(target, path))
    for name in ('../escape', 'foo/bar', 'NUL', 'test:stream', 'broken?'):
        fail(lambda name=name: exporter._filename(name, SimpleNamespace(name='Rig')))
    print('PASS three updates preserve .meta; foreign/profile/external edit/path rejection', flush=True)


def test_publication_rollback(directory):
    target = directory / 'rollback'
    publish(fake_job(target, directory / 'rollback-initial'), 1)
    (target / 'Character.fbx.meta').write_bytes(b'guid: never-change')
    before = folder_bytes(target)
    original = exporter.os.replace
    calls = []
    def broken_replace(source, destination):
        calls.append(str(destination))
        if len(calls) == 2:
            raise OSError('Injected second-file publication failure')
        return original(source, destination)
    with patch.object(exporter.os, 'replace', broken_replace):
        fail(lambda: publish(fake_job(target, directory / 'rollback-stage'), 2), 'Injected')
    assert len(calls) == 2
    assert folder_bytes(target) == before
    assert not list(target.rglob('.cdesigner-*'))
    # A new destination must also roll back the already published first file.
    fresh = directory / 'rollback-fresh'
    calls.clear()
    with patch.object(exporter.os, 'replace', broken_replace):
        fail(lambda: publish(fake_job(fresh, directory / 'rollback-fresh-stage'), 3), 'Injected')
    assert folder_bytes(fresh) == {}
    print('PASS injected second-file failure restores old outputs and removes new outputs', flush=True)


def test_report_messages(directory):
    skipped = 'Hair: skipped; no enabled Armature binding to this character.'
    weight_warning = 'Body: 2 exported vertices have no weight.'
    shader_warning = 'Material "ArtistMaterial" uses a custom shader; configure it in Unity.'
    legacy = {'warnings': [skipped, weight_warning, shader_warning, skipped]}
    assert exporter.report_messages(legacy) == ([weight_warning, shader_warning], [skipped])
    assert legacy['warnings'] == [skipped, weight_warning, shader_warning, skipped]
    job = fake_job(directory / 'messages', directory / 'messages-stage')
    (job['stage'] / 'Character.fbx').write_bytes(b'Fixture export with classified messages' * 10)
    result = exporter._publish(job, {'ok': True, 'files': ['Character.fbx'],
                                    **legacy, 'notices': [skipped]})
    report = json.loads(Path(result['report_path']).read_text(encoding='utf-8'))
    for record in (result, report):
        assert record['warnings'] == [weight_warning, shader_warning], record
        assert record['notices'] == [skipped], record
    assert exporter.report_messages(report) == ([weight_warning, shader_warning], [skipped])
    assert exporter.report_messages({'warnings': [skipped]}) == ([], [skipped])
    print('PASS legacy skip classification and published manifest preserve actionable warnings and notices', flush=True)


def test_worker_roundtrip(directory):
    with fixture(directory / 'roundtrip') as f:
        f.config.extras.append(SimpleNamespace(object=f.extra, enabled=True))
        f.config.simple_materials = [SimpleNamespace(material=f.body.data.materials[0])]
        bpy.ops.object.mode_set(mode='POSE')
        before = digest()
        result = exporter.export_character(bpy.context, f.rig, f.config)
        assert digest() == before, 'Export changed live pose, mesh, keys, rig, animation or selection'
        assert not exporter.export_running()
        report = json.loads(Path(result['report_path']).read_text(encoding='utf8'))
        assert [item['material'] for item in report['simple_materials']] == [f.body.data.materials[0].name]
        assert set(report['objects']) == {'TestRig', 'Body', 'HiddenClothes'}, report['objects']
        assert any('UnboundAccessory' in notice for notice in report['notices'])
        assert not any(': skipped;' in warning for warning in report['warnings'])
        assert result['warnings'] == report['warnings']
        assert result['notices'] == report['notices']
        assert report['shape_keys']['Body'] == ['ArtistSmile'], report['shape_keys']
        assert report['rigs']['TestRig'] == ['Hand', 'Hips'], report['rigs']
        textures = [name for name in report['files'] if name.startswith('Textures/')]
        assert len(textures) == 1 and textures[0].endswith('.png'), report['files']
        assert (Path(result['filepath']).parent / textures[0]).read_bytes().startswith(b'\x89PNG')
        for index in range(2):
            model_meta = Path(result['filepath'] + '.meta')
            model_meta.write_bytes(b'guid: fixture-roundtrip-stable')
            result = exporter.export_character(bpy.context, f.rig, f.config)
            assert model_meta.read_bytes() == b'guid: fixture-roundtrip-stable'
            assert digest() == before
        filepath = result['filepath']
        previous_files = folder_bytes(Path(filepath).parent)
        job = exporter.begin_export(bpy.context, f.rig, f.config)
        exporter.cancel_export(job)
        assert not exporter.export_running()
        assert digest() == before
        assert folder_bytes(Path(filepath).parent) == previous_files
        bad = f.body.modifiers.new('Unsupported artist effect', 'WAVE')
        failed_before = digest()
        fail(lambda: exporter.export_character(bpy.context, f.rig, f.config), 'modifier')
        assert digest() == failed_before
        assert folder_bytes(Path(filepath).parent) == previous_files
        assert not exporter.export_running()
        f.body.modifiers.remove(bad)
        assert digest() == before
        bpy.ops.object.mode_set(mode='OBJECT')
    # Reimport into a disposable empty scene; assertions examine actual FBX data.
    bpy.ops.object.select_all(action='SELECT')
    for obj in bpy.context.scene.objects:
        obj.hide_set(False)
        obj.select_set(True)
    bpy.ops.object.delete(use_global=False)
    bpy.ops.preferences.addon_enable(module='io_scene_fbx')
    assert 'FINISHED' in bpy.ops.import_scene.fbx(filepath=filepath)
    objects = {obj.name: obj for obj in bpy.context.scene.objects}
    assert set(objects) == {'TestRig', 'Body', 'HiddenClothes'}, set(objects)
    body = objects['Body']
    assert len(body.data.vertices) == 8, 'Mirror geometry was not exported'
    assert set(objects['TestRig'].data.bones.keys()) == {'Hand', 'Hips'}
    assert body.data.shape_keys is not None
    keys = body.data.shape_keys.key_blocks
    assert 'ArtistSmile' in keys.keys(), keys.keys()
    assert max((a.co - b.co).length for a, b in zip(keys['ArtistSmile'].data, keys[0].data)) > .1
    assert {'Hips', 'Hand'} <= set(body.vertex_groups.keys())
    for vertex in body.data.vertices:
        assert abs(sum(entry.weight for entry in vertex.groups) - 1) < 1e-5
    assert any(mod.type == 'ARMATURE' and mod.object == objects['TestRig'] for mod in body.modifiers)
    assert not any(obj.animation_data and obj.animation_data.action for obj in objects.values())
    imported_images = [node.image for material in body.data.materials if material.use_nodes
                       for node in material.node_tree.nodes if node.type == 'TEX_IMAGE' and node.image]
    assert imported_images and all(Path(bpy.path.abspath(image.filepath)).is_file() for image in imported_images)
    print('PASS isolated worker three exports, cancellation/failure preservation; FBX skeleton, weights, Mirror, artist Shape Key and texture round trip', flush=True)


def main():
    with tempfile.TemporaryDirectory(prefix='cdesigner-unity-test-') as temporary:
        directory = Path(temporary)
        backups = directory / 'backups'
        backups.mkdir()
        original = bpy.utils.user_resource
        def test_resource(kind, *, path='', create=False):
            if kind == 'DATAFILES' and path == 'character_designer/export_backups':
                return str(backups)
            return original(kind, path=path, create=create)
        with patch.object(bpy.utils, 'user_resource', test_resource):
            test_character_scope(directory)
            test_publication(directory)
            test_publication_rollback(directory)
            test_report_messages(directory)
            test_worker_roundtrip(directory)
    print('UNITY_EXPORT_TESTS_PASS 5', flush=True)


if __name__ == '__main__':
    main()
