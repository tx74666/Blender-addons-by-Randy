"""Misc routing, per-rig persistence, and asynchronous export UI lifecycle.

The modal coordinator is replaced by a controllable fake; no Unity files or
external windows are opened. Real FBX/export tests live in the exporter suite.
"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
import character_designer
from character_designer import unity_export_ui as ui
from character_designer import unity_export
from character_designer.ui_constants import UI_PAGE_MISC, UI_PAGE_RIG


def rig(name):
    obj = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(obj)
    return obj


def mesh(name):
    data = bpy.data.meshes.new(name)
    data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


class Layout:
    def __init__(self):
        self.buttons, self.fields, self.labels = [], [], []
        self.boxes = 0
    def row(self, **_kwargs): return self
    def box(self):
        self.boxes += 1
        return self
    def separator(self): pass
    def label(self, **kwargs): self.labels.append(kwargs.get('text', ''))
    def prop(self, data, name, **_kwargs):
        assert name in data.bl_rna.properties, name
        self.fields.append(name)
    def operator(self, identifier, **kwargs):
        assert identifier != 'character_designer.unity_open_path', 'Report/folder buttons must not appear in this panel'
        category, name = identifier.split('.')
        prop = getattr(getattr(bpy.ops, category), name).get_rna_type()
        self.buttons.append((identifier, kwargs, self.__dict__.get('operator_context')))
        return SimpleNamespace()


class WindowManager:
    def __init__(self): self.timers, self.removed, self.handlers = [], [], []
    def event_timer_add(self, seconds, **_kwargs):
        timer = object()
        self.timers.append(timer)
        assert seconds == .2
        return timer
    def event_timer_remove(self, timer): self.removed.append(timer)
    def modal_handler_add(self, operator): self.handlers.append(operator)


class Event:
    """Only fields present on Blender's Event RNA, with no invented timer ID.

    The previous SimpleNamespace mock exposed ``timer`` because the modal code
    expected it. Blender events do not expose that member; a Timer returned by
    event_timer_add is a separate object used for removing the timer.
    """
    __slots__ = ('type', 'value')

    def __init__(self, event_type, value='NOTHING'):
        assert set(self.__slots__) <= set(bpy.types.Event.bl_rna.properties.keys())
        self.type = event_type
        self.value = value


class FakeExport:
    def __init__(self):
        self.running = False; self.cancelled = []; self.ready = False; self.polls = 0
        self.objects = [main, body, bound]
    def __getattr__(self, name): return getattr(unity_export, name)
    def export_running(self): return self.running
    def bound_meshes(self, *_args): return [body, bound]
    def _collection_scope(self, *_args):
        return {'rigs': {main}, 'helpers': set(), 'eligible': [body, bound]}
    def collect_character(self, *_args, **_kwargs): return {'objects': self.objects, 'warnings': []}
    def begin_export(self, *_args):
        if self.running: raise ValueError('Already running')
        self.running = True
        self.job = object()
        return self.job
    def poll_export(self, job):
        self.polls += 1
        assert job is self.job
        if not self.ready: return None
        self.running = False
        return {'filepath': 'Cosha.fbx', 'report_path': 'Cosha.cdesigner.json', 'warnings': []}
    def cancel_export(self, job): self.cancelled.append(job); self.running = False
    def stop_exports(self):
        if self.running: self.cancel_export(self.job)


class Modal:
    """Call actual operator methods with a controlled WM instead of a GUI loop."""
    _result = ui.CHARACTERDESIGNER_OT_unity_export._result
    _finish_timer = ui.CHARACTERDESIGNER_OT_unity_export._finish_timer
    invoke = ui.CHARACTERDESIGNER_OT_unity_export.invoke
    modal = ui.CHARACTERDESIGNER_OT_unity_export.modal
    cancel = ui.CHARACTERDESIGNER_OT_unity_export.cancel
    def __init__(self): self.reports = []
    def report(self, severity, text): self.reports.append((severity, text))


character_designer.register()
character_designer.register()
character_designer._validate_registration_integrity()
main, second = rig('ExportMain'), rig('ExportSecond')
body, extra, bound = mesh('Body'), mesh('UnboundAccessory'), mesh('BoundAccessory')
for obj in (body, bound):
    obj.modifiers.new('Armature', 'ARMATURE').object = main
bpy.context.scene.character_designer_setup.rig = main
bpy.context.scene.character_designer_setup.body = body
config = main.character_designer_unity_export
config.directory = '//UnityTarget/'
config.filename = 'Cosha'
assert second.character_designer_unity_export.directory == ''
assert not ui.CharacterDesignerUnityExport.bl_rna.properties['directory'].is_skip_save
for name in ('show_objects', 'show_warnings', 'show_materials'):
    prop = ui.CharacterDesignerUnityExport.bl_rna.properties[name]
    assert prop.default is False and not prop.is_skip_save, name
    assert getattr(config, name) is False, name

bpy.ops.object.select_all(action='DESELECT')
extra.select_set(True)
bpy.context.view_layer.objects.active = extra


def rejected(operation, expected):
    try:
        result = operation()
    except (RuntimeError, ValueError) as exc:
        assert expected in str(exc), str(exc)
    else:
        assert result == {'CANCELLED'}, result


# Legacy actions cannot bypass the live Armature qualification, even when a
# valid object is also selected before an invalid one in a batch.
bound.select_set(True)
rejected(lambda: bpy.ops.character_designer.unity_add_selected(), 'enabled Armature binding')
assert not config.extras
extra.select_set(False)
bpy.context.view_layer.objects.active = bound
assert bpy.ops.character_designer.unity_add_selected() == {'FINISHED'}
assert bpy.ops.character_designer.unity_add_selected() == {'FINISHED'}
assert len(config.extras) == 1 and config.extras[0].object == bound
bound.modifiers['Armature'].show_viewport = False
rejected(lambda: bpy.ops.character_designer.unity_add_selected(), 'enabled Armature binding')
assert len(config.extras) == 1 and config.extras[0].enabled
bound.modifiers['Armature'].show_viewport = True

assert bpy.ops.character_designer.unity_set_included(object_name=bound.name, include=False) == {'FINISHED'}
assert bound not in unity_export.collect_character(bpy.context, main, config)['objects']
assert bound.modifiers['Armature'].object == main
assert bpy.ops.character_designer.unity_remove_extra(index=0) == {'FINISHED'}
assert bound.name in bpy.data.objects and not config.extras
assert bound in unity_export.collect_character(bpy.context, main, config)['objects']
rejected(lambda: bpy.ops.character_designer.unity_set_included(object_name=extra.name, include=True),
         'enabled Armature binding')
rejected(lambda: bpy.ops.character_designer.unity_set_included(object_name=body.name, include=False),
         'main body cannot be excluded')
assert not config.extras
legacy = config.extras.add()
legacy.object = extra
legacy.enabled = True
assert extra not in unity_export.collect_character(bpy.context, main, config)['objects']
print('PASS bound-only legacy selection, exclusion restoration, and untouched scene bindings', flush=True)

# Stale entries must remain clearable even though manual export references are
# no longer shown. Exercise real collection errors, not a fake failure message.
config.show_objects = True
missing_index = len(config.extras)
config.extras.add().enabled = True
rejected(lambda: unity_export.collect_character(bpy.context, main, config), 'missing')
layout = Layout()
ui.CHARACTERDESIGNER_PT_unity_export.draw(SimpleNamespace(layout=layout), bpy.context)
assert 'Invalid saved references' in layout.labels
assert 'Missing saved reference' in layout.labels
assert any(identifier == 'character_designer.unity_remove_extra'
           and kwargs.get('text') == 'Clear Export Override'
           for identifier, kwargs, _context in layout.buttons)
assert bpy.ops.character_designer.unity_remove_extra(index=missing_index) == {'FINISHED'}
assert body in unity_export.collect_character(bpy.context, main, config)['objects']

# Helpers and foreign references use the same recovery path, without removing
# any object, modifier, or the valid (but skipped) unbound legacy reference.
helper = mesh('WidgetReference')
helper['character_designer_test_role'] = 'WIDGET'
foreign = mesh('OtherCharacterMesh')
foreign.modifiers.new('Armature', 'ARMATURE').object = second
for obj in (helper, foreign):
    index = len(config.extras)
    entry = config.extras.add(); entry.object = obj; entry.enabled = True
    layout = Layout()
    ui.CHARACTERDESIGNER_PT_unity_export.draw(SimpleNamespace(layout=layout), bpy.context)
    assert 'Invalid saved references' in layout.labels
    assert any(item[0] == 'character_designer.unity_remove_extra' for item in layout.buttons)
    assert bpy.ops.character_designer.unity_remove_extra(index=index) == {'FINISHED'}
    assert obj.name in bpy.data.objects
    assert body in unity_export.collect_character(bpy.context, main, config)['objects']
assert len(config.extras) == 1 and config.extras[0].object == extra
print('PASS missing/helper/foreign saved reference cleanup restores collection', flush=True)

# Registered material actions save an export choice without modifying either
# shader. A previously selected, now unused material must remain removable.
skin, cloth, stale = [bpy.data.materials.new(name) for name in ('Skin', 'Cloth', 'Stale Choice')]
for material in (skin, cloth, stale):
    material.use_nodes = True
    material.node_tree.nodes.new('ShaderNodeRGB').outputs[0].default_value = (.1, .3, .7, 1)
body.data.materials.append(skin)
bound.data.materials.append(cloth)


def shader_state(material):
    tree = material.node_tree
    return (tree.as_pointer(), tuple((node.as_pointer(), node.bl_idname) for node in tree.nodes),
            tuple((link.from_socket.as_pointer(), link.to_socket.as_pointer()) for link in tree.links),
            tuple(tuple(node.outputs[0].default_value) for node in tree.nodes if node.bl_idname == 'ShaderNodeRGB'))


shaders_before = [shader_state(material) for material in (skin, cloth, stale)]
for _repeat in range(2):
    assert bpy.ops.character_designer.unity_simple_material(material_name=cloth.name, enabled=True) == {'FINISHED'}
assert [entry.material for entry in config.simple_materials] == [cloth]
config.simple_materials.add().material = stale
config.show_materials = True
layout = Layout()
ui.CHARACTERDESIGNER_PT_unity_export.draw(SimpleNamespace(layout=layout), bpy.context)
material_buttons = [kwargs for identifier, kwargs, _context in layout.buttons
                    if identifier == 'character_designer.unity_simple_material']
assert {item['text'] for item in material_buttons if item.get('icon', '').startswith('CHECKBOX_')} == {
    'Skin', 'Cloth', 'Stale Choice'}
assert len([item for item in material_buttons if item.get('text') == 'Use Original']) == 2
assert bpy.ops.character_designer.unity_simple_material(material_name=stale.name, enabled=False) == {'FINISHED'}
assert bpy.ops.character_designer.unity_simple_material(material_name=cloth.name, enabled=False) == {'FINISHED'}
assert not config.simple_materials
assert [shader_state(material) for material in (skin, cloth, stale)] == shaders_before
assert body.data.materials[0] == skin and bound.data.materials[0] == cloth
assert bpy.ops.character_designer.unity_simple_material(material_name=cloth.name, enabled=True) == {'FINISHED'}
config.show_materials = False
print('PASS registered per-material choices, stale removal, and unchanged source shaders', flush=True)

fake = FakeExport()
with patch.object(ui, '_exporter', return_value=fake):
    bpy.context.window_manager.character_designer.ui_page = UI_PAGE_MISC
    assert ui.CHARACTERDESIGNER_PT_unity_export.poll(bpy.context)
    for expanded in (False, True):
        config.show_objects = expanded
        layout = Layout()
        ui.CHARACTERDESIGNER_PT_unity_export.draw(SimpleNamespace(layout=layout), bpy.context)
        export_button = next(item for item in layout.buttons if item[0] == 'character_designer.unity_export')
        assert export_button[2] == 'INVOKE_DEFAULT'
        assert 'rig' in layout.fields and 'directory' in layout.fields
        assert not any(item[0] == 'character_designer.unity_add_selected' for item in layout.buttons)
        assert 'object' not in layout.fields and 'enabled' not in layout.fields
        assert extra.name not in layout.labels
        if expanded:
            assert bound.name in layout.labels
            assert any(item[0] == 'character_designer.unity_set_included' for item in layout.buttons)
    fake.objects = [main, body]
    layout = Layout()
    ui.CHARACTERDESIGNER_PT_unity_export.draw(SimpleNamespace(layout=layout), bpy.context)
    assert 'Excluded' in layout.labels and bound.name in layout.labels
    assert extra.name not in layout.labels
    fake.objects = [main, body, bound]
    bpy.context.window_manager.character_designer.ui_page = UI_PAGE_RIG
    assert not ui.CHARACTERDESIGNER_PT_unity_export.poll(bpy.context)

    manager = WindowManager()
    context = SimpleNamespace(scene=bpy.context.scene, mode='OBJECT',
                              window_manager=manager, window=object(), screen=None)
    operator = Modal()
    assert operator.invoke(context, None) == {'RUNNING_MODAL'}
    timer = operator._timer
    assert fake.running and operator in ui._MODAL_EXPORTS
    assert operator.modal(context, Event('MOUSEMOVE')) == {'PASS_THROUGH'}
    assert fake.polls == 0
    assert operator.modal(context, Event('TIMER')) == {'PASS_THROUGH'}
    assert fake.polls == 1
    fake.ready = True
    assert operator.modal(context, Event('TIMER')) == {'FINISHED'}
    assert timer in manager.removed and not ui._MODAL_EXPORTS
    assert config.last_report == 'Cosha.cdesigner.json'
    assert 'Exported Cosha.fbx' in operator.reports[-1][1]
    assert 'Unity import not verified' not in operator.reports[-1][1]

    operator = Modal(); fake.ready = False
    operator.invoke(context, None)
    timer = operator._timer
    assert operator.modal(context, Event('ESC', 'PRESS')) == {'CANCELLED'}
    assert fake.cancelled and timer in manager.removed and not ui._MODAL_EXPORTS

    operator = Modal(); operator.invoke(context, None)
    timer = operator._timer
    ui.stop_export_ui()
    assert timer in manager.removed and not ui._MODAL_EXPORTS and not fake.running
    assert operator.modal(context, Event('TIMER')) == {'CANCELLED'}
print('PASS Misc panel, asynchronous completion, Esc, and refresh timer cleanup', flush=True)

with tempfile.TemporaryDirectory(prefix='cd-export-message-ui-') as temporary:
    report_path = Path(temporary) / 'Character.cdesigner.json'
    skips = [f'{name}: skipped; no enabled Armature binding to this character.'
             for name in ('Hair', 'Dress', 'Jacket', 'Shoes')]
    warnings = ['Cosha: 2 exported vertices have no weight.',
                'Material "Skin" uses a custom shader; configure it in Unity.',
                'Blender-only forearm calibration corrections are not exported.']
    notices = ['Body: Blender Preserve Volume skinning is exported as standard FBX skin weights; review joint deformation in Unity.',
               'Old files retained for reference safety: OldBody.png, PreviousCloth.png']
    legacy = {'ok': True, 'warnings': skips + notices + warnings}
    report_path.write_text(json.dumps(legacy), encoding='utf-8')
    config.last_report = str(report_path)
    config.last_status = 'Exported with 9 warning(s)'
    config.show_objects = True
    config.show_warnings = True
    with patch.object(ui, '_exporter', return_value=fake):
        layout = Layout()
        ui.CHARACTERDESIGNER_PT_unity_export.draw(SimpleNamespace(layout=layout), bpy.context)
        assert not any(label.startswith('Exported') for label in layout.labels), layout.labels
        shown = ' '.join(layout.labels)
        assert 'Cosha: 2 vertices need skin weights.' in shown, shown
        assert 'Skin: set up its shader in Unity.' in shown, shown
        assert 'Forearm correction is Blender-only; not included in Unity.' in shown, shown
        assert 'skipped' not in shown and '9 warning' not in shown, shown
        assert 'Preserve Volume' not in shown and 'Old files retained' not in shown, shown
        assert 'show_warnings' in layout.fields
        actions = {identifier for identifier, _kwargs, _context in layout.buttons}
        assert 'character_designer.unity_locate_unweighted' in actions
        assert 'character_designer.unity_simple_material' in actions
        assert 'character_designer.unity_open_path' not in actions
        assert json.loads(report_path.read_text(encoding='utf-8')) == legacy
        for status in ('Export cancelled', 'Export failed: Worker stopped', 'Preparing Unity export...'):
            config.last_status = status
            layout = Layout()
            ui.CHARACTERDESIGNER_PT_unity_export.draw(SimpleNamespace(layout=layout), bpy.context)
            assert status in layout.labels, layout.labels
            assert 'show_warnings' not in layout.fields, layout.fields
            assert 'Exported · 3 warning(s)' not in layout.labels
        operator = Modal()
        operator._result(config, {'filepath': 'Cosha.fbx', 'report_path': str(report_path),
                                 'warnings': skips + notices + warnings})
        assert config.last_status == 'Exported · 3 warning(s)'
        assert operator.reports[-1][0] == {'WARNING'}
        config.show_objects = config.show_materials = False
        # A saved expanded flag must not create an empty disclosure or box.
        assert config.show_warnings
        for report_only in ([], skips, notices, skips + notices):
            document = {'ok': True, 'warnings': report_only}
            result = {'filepath': 'Cosha.fbx', 'report_path': str(report_path), 'warnings': report_only.copy()}
            original = json.loads(json.dumps(result))
            report_path.write_text(json.dumps(document), encoding='utf-8')
            operator._result(config, result)
            assert result == original, 'UI filtering changed the complete export result'
            assert config.last_status == 'Exported successfully'
            assert operator.reports[-1][0] == {'INFO'}
            layout = Layout()
            ui.CHARACTERDESIGNER_PT_unity_export.draw(SimpleNamespace(layout=layout), bpy.context)
            assert 'Exported successfully' not in layout.labels
            assert 'show_warnings' not in layout.fields
            assert not any(label.startswith('Warnings') for label in layout.labels)
            assert layout.boxes == 0, 'Notice-only report created an empty warning area'
            assert not any(identifier == 'character_designer.unity_open_path'
                           for identifier, _kwargs, _context in layout.buttons)
            assert json.loads(report_path.read_text(encoding='utf-8')) == document
        report_path.write_text('broken JSON', encoding='utf-8')
        layout = Layout()
        ui.CHARACTERDESIGNER_PT_unity_export.draw(SimpleNamespace(layout=layout), bpy.context)
        assert 'Exported successfully' not in layout.labels
        assert 'show_warnings' not in layout.fields and layout.boxes == 0
print('PASS actionable mixed warnings, unchanged full reports, hidden empty warnings, no path buttons and failure/cancel precedence', flush=True)

with tempfile.TemporaryDirectory(prefix='cd-export-ui-') as temporary:
    path = str(Path(temporary) / 'profiles.blend')
    config.show_objects, config.show_materials, config.show_warnings = False, True, True
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    saved = bpy.data.objects['ExportMain'].character_designer_unity_export
    other = bpy.data.objects['ExportSecond'].character_designer_unity_export
    assert saved.directory == '//UnityTarget/' and other.directory == ''
    assert not saved.show_objects and saved.show_materials and saved.show_warnings
    assert [entry.material.name for entry in saved.simple_materials] == ['Cloth']
    assert not other.simple_materials
    assert not any(getattr(other, name) for name in ('show_objects', 'show_materials', 'show_warnings'))
print('PASS save/reopen preserves separate rig destinations, foldouts and material choices', flush=True)
character_designer.unregister()
assert not hasattr(bpy.types.Object, 'character_designer_unity_export')
assert not hasattr(bpy.types, 'CHARACTERDESIGNER_PT_unity_export')
print('UNITY_EXPORT_UI_PASSED', flush=True)
