"""Saved Material recipes and a cancellable Unity picker request."""
import time
import os
from pathlib import Path

import bpy
from bpy.props import PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup

from . import unity_material_match as exchange

_ACTIVE = []


def _profile(rig, config):
    from . import unity_export
    return (os.path.normcase(str(Path(bpy.path.abspath(config.directory)).resolve())),
            unity_export._filename(config.filename, rig), config.asset_id)


class CharacterDesignerUnityMaterialMatch(PropertyGroup):
    material: PointerProperty(name='Source Material', type=bpy.types.Material)
    source_material: StringProperty(options={'HIDDEN'})
    material_guid: StringProperty(options={'HIDDEN'})
    material_local_id: StringProperty(options={'HIDDEN'})
    material_path: StringProperty(options={'HIDDEN'})
    material_name: StringProperty(options={'HIDDEN'})
    project_root: StringProperty(options={'HIDDEN'})
    export_asset_id: StringProperty(options={'HIDDEN'})


def _entry(entry):
    return {**{key: getattr(entry, key) for key in
               ('material_guid', 'material_path', 'material_name', 'project_root', 'export_asset_id')},
            'source_material': entry.material.name if entry.material is not None else entry.source_material,
            'material_local_id': int(entry.material_local_id or '0')}


def capture(config, objects, directory, asset_id):
    used = {slot.material for obj in objects if obj.type == 'MESH'
            for slot in obj.material_slots if slot.material is not None}
    entries = [_entry(entry) for entry in getattr(config, 'unity_material_matches', [])
               if entry.material is not None and entry.material in used]
    simple = {entry.material.name for entry in config.simple_materials if entry.material is not None}
    return exchange.export_bindings(entries, directory, asset_id,
                                    {material.name for material in used}, simple)


def saved_entry(config, material):
    return next((entry for entry in config.unity_material_matches if entry.material == material), None)


def status(rig, config, entry):
    from . import unity_export
    if entry.material is None:
        return 'Review Match', 'Source Material is missing.'
    if any(item.material == entry.material for item in config.simple_materials):
        return 'Using Simplified', 'The saved Unity pairing is retained for Use Original.'
    if unity_export.export_running():
        return 'Awaiting Unity', 'Export is running.'
    try:
        return exchange.binding_status(bpy.path.abspath(config.directory),
            unity_export._filename(config.filename, rig), config.asset_id, entry.material.name, _entry(entry))
    except (ValueError, TypeError):
        return 'Review Match', 'Check the saved material reference.'


def warning_matched(rig, config, message):
    import re
    match = re.match(r'Material "(.+)" uses a custom shader;', message)
    if not match:
        return False
    material = bpy.data.materials.get(match[1])
    entry = saved_entry(config, material) if material is not None else None
    return entry is not None and status(rig, config, entry)[0] == 'Matched'


def draw_matches(layout, context, rig, config, running):
    from . import unity_export_ui as ui
    if not config.unity_material_matches and not any(op._rig == rig for op in _ACTIVE):
        return
    layout.separator()
    layout.label(text='Unity Materials', icon='MATERIAL')
    box = layout.box()
    for entry in config.unity_material_matches:
        state, detail = status(rig, config, entry)
        name = entry.material.name if entry.material is not None else entry.source_material
        row = box.row(align=True)
        row.label(text=ui._short(name, 28), icon='MATERIAL')
        row.enabled = not running and entry.material is not None
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator('character_designer.unity_match_material', text='Match').material_name = name
        box.label(text=state + (' · ' + ui._short(detail, 28) if state == 'Matched' else ''),
                  icon='CHECKMARK' if state == 'Matched' else 'INFO')
    for operator in _ACTIVE:
        if operator._rig == rig:
            box.label(text=ui._short(operator.material_name, 30) + ': Waiting for Unity...', icon='TIME')
            box.label(text='Choose a Material in Unity; Esc cancels.', icon='INFO')


class CHARACTERDESIGNER_OT_unity_match_material(Operator):
    bl_idname = 'character_designer.unity_match_material'
    bl_label = 'Match Unity Material'
    bl_description = 'Search and preview an existing Material in Unity, then save this character pairing'
    bl_options = {'REGISTER', 'UNDO'}
    material_name: StringProperty(options={'HIDDEN'})

    def invoke(self, context, _event):
        from . import unity_export_ui as ui
        self._timer = None
        self._request = None
        try:
            rig, config = ui._config(context)
            if rig.library:
                raise ValueError('Use a local character rig to save a Material pairing.')
            if ui._exporter().export_running():
                raise ValueError('Wait for the current export to finish.')
            material = bpy.data.materials.get(self.material_name)
            objects = ui._exporter().collect_character(context, rig, config)['objects']
            if material is None or not any(slot.material == material for obj in objects
                                          if obj.type == 'MESH' for slot in obj.material_slots):
                raise ValueError('This source material is no longer used by the exported character.')
            if any(entry.material == material for entry in ui._simple_material_entries(config)):
                raise ValueError('Choose Use Original before matching this material in Unity.')
            if any(op._rig == rig and op.material_name == self.material_name for op in _ACTIVE):
                raise ValueError('This Material is already waiting for Unity.')
            self._rig, self._material = rig, material
            self._profile = _profile(rig, config)
            self._request = exchange.create_request(bpy.path.abspath(config.directory),
                ui._exporter()._filename(config.filename, rig), config.asset_id, material.name)
            self._started = time.monotonic()
            self._last_poll = 0.0
            self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
            _ACTIVE.append(self)
            context.window_manager.modal_handler_add(self)
            ui._redraw(context)
            self.report({'INFO'}, 'Choose a Material in the Unity Match Unity Material window. Esc cancels.')
            return {'RUNNING_MODAL'}
        except (OSError, ValueError, RuntimeError) as error:
            self.cancel(context)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

    def _finish(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if self in _ACTIVE:
            _ACTIVE.remove(self)
        from . import unity_export_ui as ui
        ui._redraw(context)

    def cancel(self, context):
        try:
            if self._request is not None:
                exchange.cancel_request(self._request)
        except OSError as error:
            self.report({'WARNING'}, 'Could not notify Unity of cancellation: ' + str(error))
        finally:
            self._finish(context)

    def modal(self, context, event):
        from . import unity_export_ui as ui
        if event.type == 'ESC':
            self.cancel(context)
            self.report({'INFO'}, 'Material matching cancelled; the saved pairing is kept.')
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        now = time.monotonic()
        if now - self._last_poll < 0.2:
            return {'PASS_THROUGH'}
        self._last_poll = now
        try:
            if time.monotonic() - self._started > exchange.TIMEOUT:
                raise ValueError('Unity did not complete the match. Open the project and its companion importer, then retry.')
            rig, config = ui._config(context)
            if _profile(rig, config) != self._profile:
                raise ValueError('The export destination changed; match the material again.')
            if rig != self._rig or self._material.name != self.material_name:
                raise ValueError('The character or source material changed; match it again.')
            if ui._exporter().export_running():
                raise ValueError('The model export started while matching; retry after it finishes.')
            if any(entry.material == self._material for entry in config.simple_materials):
                raise ValueError('The export material choice changed; match it again after choosing Use Original.')
            result = exchange.read_result(self._request)
            if result is None:
                return {'PASS_THROUGH'}
            if result['status'] != 'matched':
                self._finish(context)
                self.report({'ERROR'} if result['status'] == 'failed' else {'INFO'},
                            result.get('error') or 'Material matching cancelled.')
                return {'CANCELLED'}
            entry = saved_entry(config, self._material)
            if entry is None:
                entry = config.unity_material_matches.add()
            entry.material = self._material
            entry.source_material = self._material.name
            for key in ('material_guid', 'material_path', 'material_name', 'project_root', 'export_asset_id'):
                setattr(entry, key, result[key])
            entry.material_local_id = str(result['material_local_id'])
            self._finish(context)
            self.report({'INFO'}, self._material.name + ': Matched to ' + result['material_name'] + '.')
            return {'FINISHED'}
        except (OSError, ValueError, RuntimeError, ReferenceError, KeyError) as error:
            self.cancel(context)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


def stop_matches():
    for operator in tuple(_ACTIVE):
        operator.cancel(bpy.context)


UNITY_MATERIAL_MATCH_CLASSES = (CharacterDesignerUnityMaterialMatch, CHARACTERDESIGNER_OT_unity_match_material)
