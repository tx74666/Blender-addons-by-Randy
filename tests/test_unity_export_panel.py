"""Draw the real Unity Export panel without Blender, workers or external apps.

The fake layout records containers, inherited button state and operator arguments.
Exporter scope is a fixture boundary; warning parsing and UI/operator code are real.
"""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class ID:
    def __init__(self, name, **fields):
        self.name = name
        self.__dict__.update(fields)


class IDs(list):
    def get(self, name):
        return next((item for item in self if item.name == name), None)

    def __contains__(self, value):
        return any(item.name == value for item in self) if isinstance(value, str) else super().__contains__(value)


class Entries(list):
    def add(self):
        value = SimpleNamespace(material=None, object=None, enabled=True)
        self.append(value)
        return value

    def remove(self, index):
        del self[index]


class Operator:
    def report(self, severity, message):
        self.reports = getattr(self, 'reports', []) + [(severity, message)]


class Layout:
    def __init__(self, kind='root', parent=None):
        self.kind, self.parent = kind, parent
        self.items = []
        self.enabled = True

    def container(self, kind):
        child = Layout(kind, self)
        self.items.append(child)
        return child

    def row(self, **_kwargs): return self.container('row')
    def column(self, **_kwargs): return self.container('column')
    def box(self): return self.container('box')
    def separator(self, **_kwargs): pass

    def inherited(self, name, default=None):
        return self.__dict__.get(name, self.parent.inherited(name, default) if self.parent else default)

    def active(self):
        return self.enabled and (self.parent.active() if self.parent else True)

    def record(self, kind, **values):
        item = SimpleNamespace(kind=kind, layout=self, **values)
        self.items.append(item)
        return item

    def label(self, **kwargs):
        self.record('label', kwargs=kwargs)

    def prop(self, data, name, **kwargs):
        if not hasattr(data, name):
            raise AssertionError('Panel requested a nonexistent field: ' + name)
        self.record('prop', data=data, name=name, kwargs=kwargs)

    def operator(self, identifier, **kwargs):
        properties = SimpleNamespace()
        self.record('operator', identifier=identifier, kwargs=kwargs, properties=properties,
                    enabled=self.active(), operator_context=self.inherited('operator_context'))
        return properties

    def events(self, kind=None):
        for item in self.items:
            if isinstance(item, Layout):
                yield from item.events(kind)
            elif kind is None or item.kind == kind:
                yield item


class PanelTests(unittest.TestCase):
    def test_export_report_retains_truthful_unity_status_and_prefab_guidance(self):
        for correction in (False, True):
            with self.subTest(correction=correction):
                folder = self.root / ('correction' if correction else 'ordinary')
                stage, output = folder / 'stage', folder / 'output'
                stage.mkdir(parents=True)
                filename = 'Character.fbx'
                (stage / filename).write_bytes(b'isolated exporter fixture' * 4)
                job = dict(directory=output, stage=stage, filename=filename, asset_id='fixture',
                           manifest_hash=None, rig=self.rig, objects=[])
                worker_result = dict(ok=True, files=[filename], warnings=[],
                                     forearm_correction={'meshes': ['Body'] if correction else []})
                with patch.object(self.bpy, 'utils', create=True,
                                  new=SimpleNamespace(user_resource=lambda *_args, **_kwargs: str(folder / 'backups'))):
                    result = self.exporter._publish(job, worker_result)
                report = json.loads(Path(result['report_path']).read_text(encoding='utf-8'))
                self.assertEqual(report['unity_status'], 'Exported; Unity import has not been verified.')
                if correction:
                    self.assertIn('Character.Runtime.prefab', report['unity_runtime_usage'])
                    self.assertIn('after the companion Unity importer completes', report['unity_runtime_usage'])
                else:
                    self.assertNotIn('unity_runtime_usage', report)

    def setUp(self):
        name = '_unity_export_panel_' + uuid.uuid4().hex
        self.package = ModuleType(name)
        self.package.__path__ = [str(SOURCE)]
        bpy = ModuleType('bpy')
        props, types = ModuleType('bpy.props'), ModuleType('bpy.types')
        for kind in ('BoolProperty', 'CollectionProperty', 'IntProperty', 'PointerProperty', 'StringProperty'):
            setattr(props, kind, lambda _kind=kind, **kwargs: SimpleNamespace(kind=_kind, keywords=kwargs))
        types.Operator, types.Panel, types.PropertyGroup = Operator, type('Panel', (), {}), type('PropertyGroup', (), {})
        types.Object = types.Material = ID
        bpy.props, bpy.types = props, types
        bpy.app = SimpleNamespace(version=(5, 2, 0))
        bpy.path = SimpleNamespace(abspath=lambda value: value)
        bpy.data = SimpleNamespace(materials=IDs())
        setup = ModuleType(name + '.character_setup')
        setup.settings = lambda context: context.scene.character_designer_setup
        setup.preferred_rig = lambda context: setup.settings(context).rig
        constants = ModuleType(name + '.ui_constants')
        constants.SIDEBAR_CATEGORY, constants.UI_PAGE_MISC = 'Character Designer', 'MISC'
        constants.active_ui_page = lambda context: context.page
        self.module_patch = patch.dict(sys.modules, {name: self.package, 'bpy': bpy,
                                                   'bpy.props': props, 'bpy.types': types,
                                                   setup.__name__: setup, constants.__name__: constants})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        self.bpy = bpy
        self.exporter, self.ui = self.load('unity_export'), self.load('unity_export_ui')
        self.temporary = tempfile.TemporaryDirectory(prefix='unity-export-panel-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = SimpleNamespace(directory=str(self.root), filename='Character', asset_id='',
                                      extras=Entries(), simple_materials=Entries(), last_report='', last_status='')
        for key in ('show_objects', 'show_warnings', 'show_materials'):
            definition = self.ui.CharacterDesignerUnityExport.__annotations__.get(key)
            setattr(self.config, key, definition.keywords.get('default', False) if definition else False)
        self.rig = ID('CharacterRig', type='ARMATURE', character_designer_unity_export=self.config)
        self.skin, self.cloth, self.old, self.foreign = [ID(name, node_tree=object())
                                                       for name in ('Skin', 'Cloth', 'Old Selection', 'Foreign')]
        self.bpy.data.materials.extend([self.skin, self.cloth, self.old, self.foreign])
        def mesh(label, *materials):
            return ID(label, type='MESH', material_slots=[SimpleNamespace(material=value) for value in materials])
        self.body = mesh('Body', self.skin, self.skin)
        self.dress = mesh('Dress', self.cloth, self.skin)
        self.excluded = mesh('Excluded Coat', self.foreign)
        self.objects = [self.rig, self.body, self.dress]
        self.preflight = []
        self.collect_error = None
        self.running = False
        self.context = SimpleNamespace(
            page='MISC', mode='OBJECT', screen=None, region=SimpleNamespace(width=1000),
            preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=1.0)),
            scene=SimpleNamespace(objects=IDs([*self.objects, self.excluded]),
                                  character_designer_setup=SimpleNamespace(rig=self.rig, body=self.body)))
        for attribute, value in {
            'export_running': lambda: self.running,
            'bound_meshes': lambda *_args: [self.body, self.dress, self.excluded],
            '_bound_meshes': lambda *_args: [self.body, self.dress, self.excluded],
            'collect_character': self.collect,
            '_character_armatures': lambda *_args: {self.rig},
            '_helpers': lambda *_args: set(),
            '_binding_armatures': lambda *_args: {self.rig},
        }.items():
            patcher = patch.object(self.exporter, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def load(self, name):
        qualified = self.package.__name__ + '.' + name
        spec = importlib.util.spec_from_file_location(qualified, SOURCE / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
        setattr(self.package, name, module)
        return module

    def collect(self, *_args, **_kwargs):
        if self.collect_error:
            raise ValueError(self.collect_error)
        return {'objects': self.objects, 'warnings': self.preflight}

    def draw(self):
        layout = Layout()
        self.ui.CHARACTERDESIGNER_PT_unity_export.draw(SimpleNamespace(layout=layout), self.context)
        return layout

    def labels(self, layout):
        return [item.kwargs.get('text', '') for item in layout.events('label')]

    def buttons(self, layout, identifier):
        return [item for item in layout.events('operator') if item.identifier == identifier]

    def prop(self, layout, name):
        matches = [item for item in layout.events('prop') if item.name == name]
        self.assertEqual(len(matches), 1, (name, matches))
        return matches[0]

    def report_file(self, warnings):
        path = self.root / 'Character.cdesigner.json'
        path.write_text(json.dumps({'ok': True, 'warnings': warnings}), encoding='utf8')
        self.config.last_report, self.config.last_status = str(path), 'Exported successfully'
        self.ui._read_report.cache_clear()
        return path

    def material_operator(self, material, enabled):
        operator = self.ui.CHARACTERDESIGNER_OT_unity_simple_material()
        operator.material_name, operator.enabled = material.name, enabled
        return operator, operator.execute(self.context)

    def test_saved_defaults_and_collapsed_panel(self):
        for key in ('show_objects', 'show_warnings', 'show_materials'):
            definition = self.ui.CharacterDesignerUnityExport.__annotations__[key]
            self.assertIs(definition.keywords['default'], False)
            self.assertNotIn('SKIP_SAVE', definition.keywords.get('options', ()))
        layout = self.draw()
        header = self.prop(layout, 'show_objects').kwargs['text']
        self.assertTrue(header.startswith('Objects'))
        self.assertIn('2', header)
        self.assertIn('1', header)
        self.assertFalse([item for item in layout.events('prop') if item.name == 'show_warnings'])
        self.prop(layout, 'show_materials')
        self.assertFalse(self.buttons(layout, 'character_designer.unity_open_path'))
        self.assertFalse(self.buttons(layout, 'character_designer.unity_simple_material'))
        self.assertFalse(self.buttons(layout, 'character_designer.unity_set_included'))
        self.assertNotIn('Body', self.labels(layout))
        self.assertNotIn('Only meshes with an enabled Armature binding', self.labels(layout))
        export = self.buttons(layout, 'character_designer.unity_export')
        self.assertEqual(len(export), 1)
        self.assertEqual(export[0].operator_context, 'INVOKE_DEFAULT')
        self.assertTrue(self.ui.CHARACTERDESIGNER_PT_unity_export.poll(self.context))
        self.context.page = 'RIG'
        self.assertFalse(self.ui.CHARACTERDESIGNER_PT_unity_export.poll(self.context))

    def test_one_fresh_scope_per_draw_and_no_collapsed_material_scan(self):
        with patch.object(self.exporter, '_helpers', wraps=self.exporter._helpers) as helpers, \
                patch.object(self.exporter, '_character_armatures', wraps=self.exporter._character_armatures) as rigs, \
                patch.object(self.ui, '_material_choices', side_effect=AssertionError('Collapsed material traversal')):
            self.draw()
            self.config.show_objects = True
            self.draw()
        self.assertEqual(helpers.call_count, 2)
        self.assertEqual(rigs.call_count, 2)

    def test_normal_notices_hide_warning_row_and_keep_full_report(self):
        message = ('Dress: Blender Preserve Volume skinning is exported as standard FBX skin weights; '
                   'review joint deformation in Unity.')
        self.config.show_warnings = True
        notices = [message, 'Old files retained for reference safety: old_texture.png',
                   'Hair: skipped; no enabled Armature binding to this character.']
        for messages in ([], notices, [message]):
            report = self.report_file(messages)
            layout = self.draw()
            self.assertFalse([item for item in layout.events('prop') if item.name == 'show_warnings'])
            self.assertFalse(self.buttons(layout, 'character_designer.unity_open_path'))
            self.assertNotIn('Preserve Volume', ' '.join(self.labels(layout)))
            self.assertEqual(json.loads(report.read_text(encoding='utf8'))['warnings'], messages)
        warning = 'Body: 2 exported vertices have no weight.'
        report = self.report_file(notices + [warning, 'Unknown diagnostic requiring review.'])
        layout = self.draw()
        self.assertEqual(self.prop(layout, 'show_warnings').kwargs['text'], 'Warnings · 2')
        self.assertIn('Body: 2 vertices need skin weights.', self.labels(layout))
        self.assertIn('Unknown diagnostic requiring review.', self.labels(layout))
        self.assertEqual(json.loads(report.read_text(encoding='utf8'))['warnings'], notices +
                         [warning, 'Unknown diagnostic requiring review.'])

    def test_objects_expand_with_safe_inclusion_arguments(self):
        self.config.show_objects = True
        layout = self.draw()
        self.assertIn('Body', self.labels(layout))
        self.assertIn('Dress', self.labels(layout))
        self.assertIn('Excluded Coat', self.labels(layout))
        actions = {item.properties.object_name: item.properties.include
                   for item in self.buttons(layout, 'character_designer.unity_set_included')}
        self.assertEqual(actions, {'Dress': False, 'Excluded Coat': True})
        self.assertFalse(self.buttons(layout, 'character_designer.unity_add_selected'))
        self.assertFalse(self.buttons(layout, 'character_designer.unity_open_path'))

    def test_export_feedback_uses_real_warnings_without_changing_result(self):
        notice = ('Dress: Blender Preserve Volume skinning is exported as standard FBX skin weights; '
                  'review joint deformation in Unity.')
        result = {'filepath': 'Character.fbx', 'report_path': str(self.root / 'Character.cdesigner.json'),
                  'warnings': [notice, 'Old files retained for reference safety: old.png']}
        original = json.loads(json.dumps(result))
        operator = self.ui.CHARACTERDESIGNER_OT_unity_export()
        operator._result(self.config, result)
        self.assertEqual(self.config.last_status, 'Exported successfully')
        self.assertEqual(operator.reports[-1][0], {'INFO'})
        self.assertNotIn('see Warnings', operator.reports[-1][1])
        self.assertEqual(result, original)
        result['warnings'].append('Body: 2 exported vertices have no weight.')
        operator._result(self.config, result)
        self.assertEqual(self.config.last_status, 'Exported · 1 warning(s)')
        self.assertEqual(operator.reports[-1][0], {'WARNING'})
        self.assertIn('1 warning(s); see Warnings', operator.reports[-1][1])

    def test_real_warnings_keep_actions_without_path_buttons(self):
        warning = 'Body: 2 exported vertices have no weight.'
        self.preflight = [warning, 'Jacket: skipped; no enabled Armature binding to this character.']
        self.config.show_warnings = True
        preflight = self.draw()
        self.assertEqual(self.prop(preflight, 'show_warnings').kwargs['text'], 'Warnings · 1')
        self.assertEqual(self.labels(preflight).count('Body: 2 vertices need skin weights.'), 1)
        self.assertNotIn('skipped', ' '.join(self.labels(preflight)))
        self.config.show_warnings = False
        self.report_file([warning, 'Material "Skin" uses a custom shader; configure it in Unity.',
                          'Hair: skipped; no enabled Armature binding to this character.'])
        closed = self.draw()
        self.assertFalse(self.buttons(closed, 'character_designer.unity_open_path'))
        self.assertNotIn('Body: 2 vertices need skin weights.', self.labels(closed))
        self.assertIn('2', self.prop(closed, 'show_warnings').kwargs['text'])
        self.config.show_warnings = True
        layout = self.draw()
        labels = self.labels(layout)
        self.assertEqual(labels.count('Body: 2 vertices need skin weights.'), 1)
        self.assertIn('Skin: set up its shader in Unity.', labels)
        self.assertNotIn('skipped', ' '.join(labels))
        paths = self.buttons(layout, 'character_designer.unity_open_path')
        self.assertFalse(paths)
        locate = self.buttons(layout, 'character_designer.unity_locate_unweighted')
        self.assertEqual(len(locate), 1)
        self.assertEqual(locate[0].properties.object_name, 'Body')
        self.config.show_objects = True
        self.assertEqual(self.labels(self.draw()).count('Body: 2 vertices need skin weights.'), 1)

    def test_success_is_quiet_and_fail_cancel_progress_remain_visible(self):
        path = self.report_file(['Material "Skin" uses a custom shader; configure it in Unity.'])
        for status in ('Exported successfully', 'Exported with 9 warning(s)'):
            self.config.last_status = status
            labels = self.labels(self.draw())
            self.assertFalse(any(text.startswith('Exported') for text in labels))
            self.assertNotIn('Unity import not verified', labels)
        for status in ('Export cancelled', 'Export failed: Worker stopped', 'Preparing Unity export...'):
            self.config.last_status = status
            self.config.show_warnings = True
            labels = self.labels(self.draw())
            self.assertIn(status, labels)
            self.assertNotIn('Skin: set up its shader in Unity.', labels)
        self.config.last_status = 'Exported successfully'
        path.write_text('invalid JSON', encoding='utf8')
        self.ui._read_report.cache_clear()
        self.assertFalse(any(text.startswith('Exported') for text in self.labels(self.draw())))
        self.running = True
        export = self.buttons(self.draw(), 'character_designer.unity_export')[0]
        self.assertFalse(export.enabled)
        self.assertEqual(export.kwargs['text'], 'Exporting...')

    def test_materials_include_used_and_selected_stale_once_without_touching_shaders(self):
        for material in (self.skin, self.old):
            self.config.simple_materials.add().material = material
        trees = [material.node_tree for material in self.bpy.data.materials]
        self.config.show_materials = True
        layout = self.draw()
        self.assertEqual(self.prop(layout, 'show_materials').kwargs['text'], 'Use Simplified Materials · 2')
        buttons = self.buttons(layout, 'character_designer.unity_simple_material')
        toggles = [item for item in buttons if item.kwargs.get('icon', '').startswith('CHECKBOX_')]
        by_name = {item.properties.material_name: item for item in toggles}
        self.assertEqual(len(toggles), 3)
        self.assertEqual(set(by_name), {'Skin', 'Cloth', 'Old Selection'})
        self.assertEqual({name: item.properties.enabled for name, item in by_name.items()},
                         {'Skin': False, 'Cloth': True, 'Old Selection': False})
        self.assertEqual(by_name['Skin'].kwargs['icon'], 'CHECKBOX_HLT')
        self.assertEqual(by_name['Cloth'].kwargs['icon'], 'CHECKBOX_DEHLT')
        originals = [item for item in buttons if item.kwargs.get('text', '').startswith('Use Original')]
        self.assertEqual({item.properties.material_name for item in originals}, {'Skin', 'Old Selection'})
        self.assertTrue(all(item.properties.enabled is False for item in originals))
        self.assertEqual([material.node_tree for material in self.bpy.data.materials], trees)
        self.assertEqual([entry.material for entry in self.config.simple_materials], [self.skin, self.old])
        self.running = True
        self.assertTrue(all(not item.enabled for item in
                            self.buttons(self.draw(), 'character_designer.unity_simple_material')))

    def test_material_toggle_changes_only_saved_export_choice(self):
        trees = [material.node_tree for material in self.bpy.data.materials]
        _, result = self.material_operator(self.cloth, True)
        self.assertEqual(result, {'FINISHED'})
        self.assertEqual([entry.material for entry in self.config.simple_materials], [self.cloth])
        self.assertEqual(self.material_operator(self.cloth, True)[1], {'FINISHED'})
        self.assertEqual(len(self.config.simple_materials), 1)
        self.config.simple_materials.add().material = self.old
        self.assertEqual(self.material_operator(self.old, False)[1], {'FINISHED'})
        self.assertEqual(self.material_operator(self.cloth, False)[1], {'FINISHED'})
        self.assertFalse(self.config.simple_materials)
        self.assertEqual([material.node_tree for material in self.bpy.data.materials], trees)
        self.assertEqual(self.objects, [self.rig, self.body, self.dress])

    def test_material_choice_rejects_foreign_missing_and_busy_without_mutation(self):
        operator, result = self.material_operator(self.foreign, True)
        self.assertEqual(result, {'CANCELLED'})
        self.assertIn('not used by an included', operator.reports[-1][1])
        operator, result = self.material_operator(ID('Deleted'), True)
        self.assertEqual(result, {'CANCELLED'})
        self.assertIn('no longer exists', operator.reports[-1][1])
        self.running = True
        self.assertEqual(self.material_operator(self.skin, True)[1], {'CANCELLED'})
        self.assertFalse(self.config.simple_materials)

    def test_preflight_failure_and_missing_rig_remain_actionable(self):
        self.collect_error = 'A saved export reference is missing; clear it under Objects.'
        self.assertTrue(any('saved export reference is missing' in text for text in self.labels(self.draw())))
        self.context.scene.character_designer_setup.rig = None
        layout = self.draw()
        self.assertIn('Choose your character rig to begin.', self.labels(layout))
        self.assertFalse(self.buttons(layout, 'character_designer.unity_export'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
