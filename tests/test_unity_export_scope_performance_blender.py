"""Fresh Unity-export scope regression and bounded source-level draw benchmark.

Run only in disposable Blender --background --factory-startup, with
--python-exit-code 1 --python THIS_FILE -- --report ABSOLUTE_REPORT_JSON.
No FBX worker, renderer, saved blend, Unity instance, or user asset is used.
The baseline zip is read into unregistered private modules, never extracted.
Counts are acceptance criteria; timings describe Python panel drawing through
a recording layout, not actual GPU/UI presentation latency.
"""

import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
import traceback
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import zipfile

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
import character_designer
from character_designer import character_setup, unity_export as exporter, unity_export_ui as ui


class Layout:
    """Same lightweight layout for both real panel methods."""
    def __init__(self, records=None):
        self.records = records if records is not None else []

    def row(self, **_kwargs): return Layout(self.records)
    box = column = row
    def separator(self, **_kwargs): pass
    def label(self, **kwargs): self.records.append(('label', kwargs))
    def prop(self, data, name, **kwargs):
        assert name in data.bl_rna.properties, name
        self.records.append(('prop', name, kwargs))
    def operator(self, identifier, **kwargs):
        value = SimpleNamespace()
        self.records.append(('operator', identifier, kwargs, value))
        return value


def draw(module=ui):
    layout = Layout()
    module.CHARACTERDESIGNER_PT_unity_export.draw(SimpleNamespace(layout=layout), bpy.context)
    return layout


def signature(result):
    return {'objects': [obj.name for obj in result['objects']], 'warnings': result['warnings']}


def hash_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextmanager
def baseline(path):
    """Share unchanged registered dependencies; do not register baseline RNA."""
    names, loaded = [], []
    with zipfile.ZipFile(path) as archive:
        for leaf in ('unity_export', 'unity_export_ui'):
            name = 'character_designer._scope_baseline_' + leaf
            module = ModuleType(name)
            module.__package__ = 'character_designer'
            member = 'character_designer/' + leaf + '.py'
            module.__file__ = str(path) + '::' + member
            sys.modules[name] = module
            names.append(name)
            loaded.append(module)
            exec(compile(archive.read(member), module.__file__, 'exec'), module.__dict__)
    old_exporter, old_ui = loaded
    old_ui._exporter = lambda: old_exporter
    try:
        yield old_exporter, old_ui
    finally:
        for name in names:
            sys.modules.pop(name, None)


@contextmanager
def fixture():
    """Own one scene and explicitly remove only this test's new datablocks."""
    original_scene = bpy.context.window.scene
    kinds = ('objects', 'meshes', 'armatures', 'materials', 'collections', 'scenes')
    original_ids = {kind: {item.as_pointer() for item in getattr(bpy.data, kind)} for kind in kinds}
    scene = bpy.data.scenes.new('Export Scope Performance Fixture')
    bpy.context.window.scene = scene

    def armature(name, count):
        data = bpy.data.armatures.new(name + 'Data')
        rig = bpy.data.objects.new(name, data)
        scene.collection.objects.link(rig)
        bpy.ops.object.select_all(action='DESELECT')
        rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode='EDIT')
        for index in range(count):
            bone = data.edit_bones.new(f'Bone{index:03}')
            bone.head, bone.tail = (0, 0, index * .02), (0, 0, index * .02 + .01)
        bpy.ops.object.mode_set(mode='OBJECT')
        return rig

    def mesh(name, rig=None):
        data = bpy.data.meshes.new(name + 'Data')
        data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        data.update()
        obj = bpy.data.objects.new(name, data)
        scene.collection.objects.link(obj)
        if rig is not None:
            obj.modifiers.new('Character Binding', 'ARMATURE').object = rig
            obj.vertex_groups.new(name='Bone000').add([0, 1, 2], 1.0, 'REPLACE')
        return obj

    try:
        rig = armature('ScopeMainRig', 180)
        other = armature('ScopeForeignRig', 1)
        meshes = [mesh(f'ScopeMesh{index:02}', rig) for index in range(8)]
        for index in range(300):
            obj = bpy.data.objects.new(f'Unrelated{index:03}', None)
            scene.collection.objects.link(obj)
        material = bpy.data.materials.new('ScopeMaterial')
        material.use_nodes = True
        for obj in meshes:
            obj.data.materials.append(material)
        setup = character_setup.settings(bpy.context)
        setup.rig, setup.body = rig, meshes[0]
        config = rig.character_designer_unity_export
        config.directory, config.filename = '', 'ScopeFixture'
        bpy.ops.object.select_all(action='DESELECT')
        rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
        yield SimpleNamespace(scene=scene, rig=rig, other=other, meshes=meshes,
                              setup=setup, config=config, material=material, mesh=mesh)
    finally:
        if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.context.window.scene = original_scene
        for kind in kinds:
            bank = getattr(bpy.data, kind)
            for item in tuple(bank):
                if item.as_pointer() not in original_ids[kind]:
                    bank.remove(item, do_unlink=True)


def fingerprint(f):
    """Check draw/collection purity independently of expected scope contents."""
    records = []
    for obj in f.scene.objects:
        value = [obj.name, obj.type, obj.parent.name if obj.parent else None,
                 tuple(tuple(row) for row in obj.matrix_world), obj.select_get(),
                 sorted((str(key), repr(obj.get(key))) for key in obj.keys()),
                 [(mod.name, mod.type, mod.show_viewport, getattr(mod, 'use_vertex_groups', None),
                   getattr(mod, 'use_bone_envelopes', None), getattr(getattr(mod, 'object', None), 'name', None))
                  for mod in obj.modifiers]]
        if obj.type == 'MESH':
            value.extend(([(tuple(v.co), [(g.group, g.weight) for g in v.groups]) for v in obj.data.vertices],
                          [tuple(p.vertices) for p in obj.data.polygons],
                          [slot.material.name if slot.material else None for slot in obj.material_slots]))
        elif obj.type == 'ARMATURE':
            value.append([(b.name, tuple(tuple(row) for row in b.matrix_local), b.use_deform,
                           obj.pose.bones[b.name].custom_shape.name if obj.pose.bones[b.name].custom_shape else None)
                          for b in obj.data.bones])
        records.append(value)
    records.extend(([(entry.object.name if entry.object else None, entry.enabled) for entry in f.config.extras],
                    [entry.material.name if entry.material else None for entry in f.config.simple_materials],
                    (f.config.directory, f.config.filename, f.config.show_objects,
                     f.config.show_materials, f.config.show_warnings),
                    f.scene.frame_current, bpy.context.mode, bpy.data.filepath))
    return hashlib.sha256(repr(records).encode()).hexdigest()


def benchmark(f, backend, panel, expected_scans, iterations):
    result = {}
    for expanded, expected in ((False, expected_scans[0]), (True, expected_scans[1])):
        f.config.show_objects = expanded
        f.config.show_materials = False
        before = fingerprint(f)
        with ExitStack() as stack:
            helpers = stack.enter_context(patch.object(backend, '_helpers', wraps=backend._helpers))
            rigs = stack.enter_context(patch.object(backend, '_character_armatures', wraps=backend._character_armatures))
            bindings = stack.enter_context(patch.object(backend, '_binding_armatures', wraps=backend._binding_armatures))
            materials = stack.enter_context(patch.object(panel, '_material_choices', wraps=panel._material_choices))
            if panel is ui:
                stack.enter_context(patch.object(backend, '_capture_hair_motion',
                                                side_effect=AssertionError('Draw captured Hair export proof')))
            draw(panel)
            counts = {'helpers': helpers.call_count, 'character_armatures': rigs.call_count,
                      'material_choices': materials.call_count, 'binding_queries': bindings.call_count}
            if panel is ui:
                counts['helper_role_candidates'] = len(helpers.call_args.kwargs['candidates'])
        assert counts['helpers'] == counts['character_armatures'] == expected, counts
        assert counts['material_choices'] == (0 if panel is ui else 1), counts
        if panel is ui:
            assert counts['binding_queries'] == len(f.meshes), counts
            assert counts['helper_role_candidates'] == len(f.meshes), counts
        samples = []
        for _ in range(iterations):
            started = time.perf_counter()
            draw(panel)
            samples.append((time.perf_counter() - started) * 1000)
        assert fingerprint(f) == before, 'Panel draw mutated character data'
        result['expanded' if expanded else 'collapsed'] = dict(
            counts=counts, samples_ms=samples, median_ms=statistics.median(samples))
    return result


def check_freshness(f, old_backend):
    checks = []
    f.config.show_objects = True
    f.config.show_materials = False

    def observe(label, *, excluded=(), error=''):
        before = fingerprint(f)
        captured = []
        original = exporter.collect_character
        def collecting(*args, **kwargs):
            result = original(*args, **kwargs)
            captured.append(signature(result))
            return result
        with patch.object(exporter, 'collect_character', side_effect=collecting):
            layout = draw()
        labels = [record[1].get('text', '') for record in layout.records if record[0] == 'label']
        if error:
            for backend in (exporter, old_backend):
                try:
                    backend.collect_character(bpy.context, f.rig, f.config)
                except backend.ExportError as exc:
                    assert error.casefold() in str(exc).casefold(), str(exc)
                else:
                    raise AssertionError('Expected scope rejection: ' + error)
            assert any(error.casefold() in text.casefold() for text in labels), labels
        else:
            current = signature(exporter.collect_character(bpy.context, f.rig, f.config))
            assert captured == [current], (label, captured, current)
            assert current == signature(old_backend.collect_character(bpy.context, f.rig, f.config))
            assert not set(excluded) & set(current['objects']), (label, current)
        assert fingerprint(f) == before, label + ': draw/collection changed source data'
        checks.append(label)
        return layout

    observe('baseline')
    changed = f.meshes[-1]
    modifier = changed.modifiers['Character Binding']
    modifier.show_viewport = False
    observe('direct modifier disabled', excluded=[changed.name])
    modifier.show_viewport = True
    observe('direct modifier reenabled')
    modifier.use_vertex_groups = False
    modifier.use_bone_envelopes = False
    observe('both binding channels disabled', excluded=[changed.name])
    modifier.use_bone_envelopes = True
    observe('envelope binding enabled')
    modifier.use_vertex_groups, modifier.use_bone_envelopes = True, False
    changed['character_designer_fixture_role'] = 'GUIDE'
    observe('direct helper role added', excluded=[changed.name])
    del changed['character_designer_fixture_role']
    observe('direct helper role removed')
    container = bpy.data.collections.new('Bound Widget Container')
    f.scene.collection.children.link(container)
    container.objects.link(changed)
    container['character_designer_widget_container_role'] = 'WIDGET'
    observe('direct widget container role added', excluded=[changed.name])
    del container['character_designer_widget_container_role']
    observe('direct widget container role removed')
    container.objects.unlink(changed)
    f.rig.pose.bones[0].custom_shape = changed
    observe('direct custom shape assigned', excluded=[changed.name])
    f.rig.pose.bones[0].custom_shape = None
    observe('direct custom shape cleared')
    f.other.pose.bones[0].custom_shape = changed
    observe('foreign rig custom shape assigned', excluded=[changed.name])
    f.other.pose.bones[0].custom_shape = None
    observe('foreign rig custom shape cleared')
    modifier.object = f.other
    observe('direct binding changed to foreign rig', excluded=[changed.name])
    f.other.parent = f.rig
    observe('direct rig ancestry attached')
    assert changed.name in signature(exporter.collect_character(bpy.context, f.rig, f.config))['objects']
    f.other.parent = None
    observe('direct rig ancestry detached', excluded=[changed.name])
    modifier.object = f.rig
    observe('direct character binding restored')
    f.scene.collection.objects.unlink(changed)
    observe('direct scene member removed', excluded=[changed.name])
    f.scene.collection.objects.link(changed)
    observe('direct scene member restored')
    changed.hide_viewport = changed.hide_render = True
    observe('hidden bound clothing retained')
    assert changed.name in signature(exporter.collect_character(bpy.context, f.rig, f.config))['objects']
    changed.hide_viewport = changed.hide_render = False
    entry = f.config.extras.add()
    entry.object, entry.enabled = changed, False
    layout = observe('saved exclusion retained', excluded=[changed.name])
    assert any(record[0] == 'label' and record[1].get('text') == 'Excluded' for record in layout.records)
    assert bpy.ops.character_designer.unity_remove_extra(index=0) == {'FINISHED'}
    observe('clear exclusion restores automatic binding')
    foreign = changed.modifiers.new('Foreign Binding', 'ARMATURE')
    foreign.object = f.other
    observe('foreign binding rejected', error='linked to more than this character rig')
    changed.modifiers.remove(foreign)
    observe('foreign binding removed')
    entry = f.config.extras.add()
    entry.object = None
    layout = observe('missing saved reference rejected', error='saved export reference is missing')
    assert any(record[0] == 'operator' and record[1] == 'character_designer.unity_remove_extra'
               and record[3].index == 0 for record in layout.records), 'Missing-reference cleanup unavailable'
    assert bpy.ops.character_designer.unity_remove_extra(index=0) == {'FINISHED'}
    observe('missing reference cleanup recovers')
    entry = f.config.extras.add()
    entry.object, entry.enabled = f.meshes[0], False
    observe('main body exclusion rejected', error='main rig and body cannot be excluded')
    assert bpy.ops.character_designer.unity_remove_extra(index=0) == {'FINISHED'}
    observe('main body cleanup recovers')

    # Role checks must include saved references and setup assets even when
    # those meshes have no eligible Armature modifier.
    unbound = f.mesh('Saved Unbound Helper')
    entry = f.config.extras.add()
    entry.object, entry.enabled = unbound, True
    observe('saved unbound reference remains skipped')
    unbound['character_designer_fixture_role'] = 'GUIDE'
    observe('unbound saved helper role rejected', error='controller or helper')
    del unbound['character_designer_fixture_role']
    assert bpy.ops.character_designer.unity_remove_extra(index=0) == {'FINISHED'}
    asset = f.setup.assets.add()
    asset.object = unbound
    observe('unbound setup asset remains skipped')
    unbound['character_designer_fixture_role'] = 'GUIDE'
    observe('unbound setup helper warning suppressed')
    assert not any(unbound.name in warning for warning in exporter.collect_character(
        bpy.context, f.rig, f.config)['warnings'])
    del unbound['character_designer_fixture_role']
    f.setup.assets.remove(len(f.setup.assets) - 1)
    observe('unbound setup helper removed')

    # Even an expanded material section lists only saved choices. Reading
    # current material slots belongs to the explicit searchable chooser.
    entry = f.config.simple_materials.add()
    entry.material = f.material
    class SlotGuard:
        def __init__(self, obj): self.object = obj
        def __getattr__(self, name):
            if name == 'material_slots':
                raise AssertionError('Main panel read a mesh material slot')
            return getattr(self.object, name)
    collecting = exporter.collect_character
    def guarded_collection(*args, **kwargs):
        result = collecting(*args, **kwargs)
        return dict(result, objects=[SlotGuard(obj) if obj.type == 'MESH' else obj
                                     for obj in result['objects']])
    f.config.show_objects = False
    for expanded in (False, True):
        f.config.show_materials = expanded
        before = fingerprint(f)
        with patch.object(ui, '_material_choices', side_effect=AssertionError('Main material scan')) as choices, \
             patch.object(exporter, '_capture_hair_motion', side_effect=AssertionError('Draw captured Hair proof')), \
             patch.object(exporter, 'collect_character', side_effect=guarded_collection):
            layout = draw()
            assert choices.call_count == 0, ('material-expanded' if expanded else 'material-collapsed')
        assert fingerprint(f) == before, 'Material body draw changed source data'
        if expanded:
            assert any(record[0] == 'operator' and record[1] == 'character_designer.unity_choose_simple_material'
                       for record in layout.records)
    f.config.simple_materials.remove(len(f.config.simple_materials) - 1)
    checks.append('collapsed and expanded material bodies never enumerate slots or capture Hair')
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path)
    parser.add_argument('--baseline-zip', type=Path, default=ROOT / 'dist/character_designer-0.69.0.zip')
    parser.add_argument('--iterations', type=int, default=6, choices=range(1, 21))
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    if not bpy.app.background or bpy.data.filepath:
        raise RuntimeError('Use a disposable --background --factory-startup process without a blend file.')
    if args.report and not args.report.is_absolute():
        raise ValueError('--report must be an absolute owned output path')
    report = {'ok': False, 'blender_version': bpy.app.version_string,
              'baseline_zip': str(args.baseline_zip.resolve()),
              'baseline_sha256': hash_file(args.baseline_zip),
              'source_sha256': {name: hash_file(ROOT / 'addons/character_designer' / name)
                                for name in ('unity_export.py', 'unity_export_ui.py')},
              'timing_scope': 'Python draw with recording layout and real bpy data; no viewport/GPU timing',
              'fixture': {'unrelated_objects': 300, 'main_bones': 180, 'bound_triangle_meshes': 8}}
    registered = False
    try:
        character_designer.register()
        registered = True
        with fixture() as f, baseline(args.baseline_zip) as (old_exporter, old_ui):
            report['baseline'] = benchmark(f, old_exporter, old_ui, (3, 4), args.iterations)
            report['current'] = benchmark(f, exporter, ui, (1, 1), args.iterations)
            report['checks'] = check_freshness(f, old_exporter)
        report['ok'] = True
    except Exception:
        report['error'] = traceback.format_exc()
        raise
    finally:
        try:
            if registered:
                character_designer.unregister()
        except Exception:
            report['ok'] = False
            report['cleanup_error'] = traceback.format_exc()
            raise
        finally:
            report['baseline_zip_unchanged'] = hash_file(args.baseline_zip) == report['baseline_sha256']
            if not report['baseline_zip_unchanged']:
                report['ok'] = False
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
            print('UNITY_EXPORT_SCOPE_PERFORMANCE ' + json.dumps(report), flush=True)
    assert report['baseline_zip_unchanged'], 'Immutable baseline zip changed during test'


if __name__ == '__main__':
    main()
