"""Saved per-character Unity destination and a compact export panel."""

import json
import os
import re
import textwrap
import uuid
from functools import lru_cache

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from . import character_setup
from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_MISC, active_ui_page

_MODAL_EXPORTS = []
_SIMPLE_MATERIAL_SEARCH = {}


def stop_export_ui():
    """Refresh/unregister removes timers as well as stopping the worker."""
    _exporter().stop_exports()
    for operator in list(_MODAL_EXPORTS):
        operator._finish_timer()
    _SIMPLE_MATERIAL_SEARCH.clear()


def _redraw(context):
    for area in context.screen.areas if context.screen else ():
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _exporter():
    # Keep panel registration independent of the FBX exporter being enabled.
    from . import unity_export
    return unity_export


def _mesh_only(_self, obj):
    return obj.type == 'MESH'


def _rig(context):
    rig = character_setup.preferred_rig(context)
    return rig if rig is not None and rig.type == 'ARMATURE' else None


def _config(context):
    rig = _rig(context)
    if rig is None:
        raise ValueError('Choose the character Main Rig first.')
    if rig.name not in context.scene.objects:
        raise ValueError('The character Main Rig is not in this scene.')
    return rig, rig.character_designer_unity_export


def _object_mode_required(context):
    if context.mode not in {'OBJECT', 'POSE'}:
        raise ValueError('Finish editing and switch to Object or Pose Mode before exporting.')


def _short(message, limit=56):
    message = str(message).replace('\n', ' ')
    return message if len(message) <= limit else message[:limit - 3] + '...'


@lru_cache(maxsize=16)
def _read_report(path, modified, size):
    with open(path, encoding='utf-8') as stream:
        report = json.load(stream)
    if (not isinstance(report, dict) or report.get('ok') is not True
            or not isinstance(report.get('warnings', []), list)
            or not all(isinstance(item, str) for item in report.get('warnings', []))):
        return None
    return report


def _last_report(config):
    if not config.last_report or not config.last_status.startswith('Exported'):
        return None
    try:
        path = bpy.path.abspath(config.last_report)
        stat = os.stat(path)
        return _read_report(path, stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, TypeError):
        return None


def _success_status(warnings):
    return f'Exported · {len(warnings)} warning(s)' if warnings else 'Exported successfully'


def _panel_warnings(report):
    """Show actionable diagnostics while preserving the complete export report."""
    warnings, _notices = _exporter().report_messages(report)
    return [message for message in warnings
            if not message.startswith('Old files retained for reference safety: ')
            and not re.fullmatch(
                r'.+: Blender Preserve Volume skinning is exported as standard FBX skin weights; '
                r'review joint deformation in Unity\.', message)]


def _warning_summary(message):
    match = re.match(r'(.+): (\d+) exported vertices have no weight', message)
    if match:
        return f'{match[1]}: {match[2]} vertices need skin weights.'
    match = re.match(r'Material "(.+)" uses a custom shader;', message)
    if match:
        return f'{match[1]}: set up its shader in Unity.'
    if message.startswith('Blender-only forearm calibration corrections'):
        return 'Forearm correction is Blender-only; not included in Unity.'
    return message


def _simple_material_entries(config):
    return [entry for entry in config.simple_materials if entry.material is not None]


def _material_choices(objects, config):
    """Keep original choices discoverable and retained overrides removable."""
    chosen = {entry.material for entry in _simple_material_entries(config)}
    used = {slot.material for obj in objects if obj.type == 'MESH'
            for slot in obj.material_slots if slot.material is not None}
    return sorted(used | chosen, key=lambda material: material.name.casefold()), chosen


def _simple_material_search_items(operator, _context):
    # Blender passes OperatorProperties here, not the Python Operator instance.
    # Keep strings alive for this popup only; acceptance validates independently.
    return _SIMPLE_MATERIAL_SEARCH.get(getattr(operator, 'search_token', ''), ())


def _visible_collection_path(layer, obj):
    if layer.exclude or layer.hide_viewport or layer.collection.hide_viewport:
        return False
    return obj.name in layer.collection.objects or any(_visible_collection_path(child, obj) for child in layer.children)


def _warning_actions(layout, context, config, message):
    match = re.match(r'(.+): (\d+) exported vertices have no weight', message)
    if match:
        action = layout.operator('character_designer.unity_locate_unweighted',
                                 text='Locate Unweighted Vertices', icon='VIEWZOOM')
        action.object_name = match[1]
        return
    match = re.match(r'Material "(.+)" uses a custom shader;', message)
    if match:
        material = bpy.data.materials.get(match[1])
        if material is not None and not any(entry.material == material for entry in _simple_material_entries(config)):
            action = layout.operator('character_designer.unity_simple_material',
                                     text='Use Simplified', icon='MATERIAL')
            action.material_name = material.name
            action.enabled = True


def _invalid_overrides(context, rig, config, *, _scope=None):
    """Expose cleanup for stale references without offering unbound inclusion."""
    exporter = _exporter()
    rigs = exporter._character_armatures(context.scene, rig) if _scope is None else _scope['rigs']
    helpers = exporter._helpers(context.scene) if _scope is None else _scope['helpers']
    setup = character_setup.settings(context)
    invalid = []
    for index, entry in enumerate(config.extras):
        obj = entry.object
        if obj is None or obj.name not in context.scene.objects:
            reason = 'Missing saved reference'
        elif obj.type != 'MESH' or obj in helpers:
            reason = f'{obj.name}: controller or helper reference'
        elif exporter._binding_armatures(obj) - rigs:
            reason = f'{obj.name}: belongs to another character'
        elif (not entry.enabled and setup is not None and obj == setup.body
              and exporter._binding_armatures(obj) & rigs):
            reason = f'{obj.name}: main body cannot be excluded'
        else:
            # Valid but unbound legacy mesh references are skipped by the
            # exporter; do not present them as exportable objects here.
            continue
        invalid.append((index, reason))
    return invalid


class CharacterDesignerUnityExtra(PropertyGroup):
    object: PointerProperty(name='Object', type=bpy.types.Object, poll=_mesh_only)
    enabled: BoolProperty(name='Include', default=True)


class CharacterDesignerUnitySimpleMaterial(PropertyGroup):
    material: PointerProperty(name='Material', type=bpy.types.Material)


class CharacterDesignerUnityExport(PropertyGroup):
    directory: StringProperty(
        name='Unity Folder', subtype='DIR_PATH',
        description='Destination folder, normally inside your Unity project Assets folder',
        options={'PATH_SUPPORTS_BLEND_RELATIVE'} if bpy.app.version >= (5, 2, 0) else set(),
    )
    filename: StringProperty(
        name='File Name',
        description='FBX name; leave empty to use the character name',
    )
    asset_id: StringProperty(options={'HIDDEN'})
    extras: CollectionProperty(type=CharacterDesignerUnityExtra)
    simple_materials: CollectionProperty(type=CharacterDesignerUnitySimpleMaterial)
    show_objects: BoolProperty(
        name='Objects', default=False,
        description='Review meshes with an enabled character Armature binding and change export inclusion',
    )
    show_materials: BoolProperty(
        name='Use Simplified Materials', default=False,
        description='Choose a temporary Principled BSDF approximation per material for the next export; originals are kept',
    )
    show_warnings: BoolProperty(
        name='Warnings', default=False,
        description='Review export warnings and open the last completed export report',
    )
    last_status: StringProperty(name='Last Export', options={'HIDDEN'})
    last_report: StringProperty(name='Export Report', subtype='FILE_PATH', options={'HIDDEN'})


class CHARACTERDESIGNER_OT_unity_export(Operator):
    bl_idname = 'character_designer.unity_export'
    bl_label = 'Export / Update to Unity'
    bl_description = 'Export this character to its saved folder; update the same asset on later exports'
    # Files written to Unity cannot be undone by Blender's scene Undo.
    bl_options = {'REGISTER'}

    def _result(self, config, result):
        report = result.get('report_path') or result.get('report')
        if isinstance(report, str):
            config.last_report = report
        warnings = _panel_warnings(result)
        config.last_status = _success_status(warnings)
        self.report({'WARNING'} if warnings else {'INFO'},
                    f"Exported {os.path.basename(result['filepath'])}."
                    + (f' {len(warnings)} warning(s); see Warnings.' if warnings else ''))

    def _finish_timer(self):
        timer = getattr(self, '_timer', None)
        if timer is not None:
            try:
                self._window_manager.event_timer_remove(timer)
            except (ReferenceError, RuntimeError):
                pass
            self._timer = None
        if self in _MODAL_EXPORTS:
            _MODAL_EXPORTS.remove(self)

    def invoke(self, context, _event):
        self._timer = None
        self._job = None
        self._config = None
        try:
            _object_mode_required(context)
            rig, self._config = _config(context)
            self._job = _exporter().begin_export(context, rig, self._config)
            self._config.last_status = 'Preparing Unity export...'
            self._window_manager = context.window_manager
            self._timer = context.window_manager.event_timer_add(0.2, window=context.window)
            context.window_manager.modal_handler_add(self)
            _MODAL_EXPORTS.append(self)
            _redraw(context)
            return {'RUNNING_MODAL'}
        except (ValueError, RuntimeError, OSError) as exc:
            if self._job is not None:
                _exporter().cancel_export(self._job)
            self._finish_timer()
            if self._config is not None:
                self._config.last_status = 'Export failed: ' + str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def modal(self, context, event):
        if self._timer is None:
            return {'CANCELLED'}
        if event.type == 'ESC' and event.value == 'PRESS':
            self.cancel(context)
            self.report({'INFO'}, 'Unity export cancelled; existing files kept.')
            return {'CANCELLED'}
        # Blender Event has no timer identity field. Polling another window
        # timer is harmless; publication only occurs after this job completes.
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        try:
            result = _exporter().poll_export(self._job)
            if result is None:
                return {'PASS_THROUGH'}
            self._result(self._config, result)
            self._finish_timer()
            _redraw(context)
            return {'FINISHED'}
        except (ValueError, RuntimeError, OSError, ReferenceError) as exc:
            _exporter().cancel_export(self._job)
            self._finish_timer()
            try:
                self._config.last_status = 'Export failed: ' + str(exc)
            except ReferenceError:
                pass
            self.report({'ERROR'}, str(exc))
            _redraw(context)
            return {'CANCELLED'}

    def cancel(self, context):
        if getattr(self, '_job', None) is not None:
            _exporter().cancel_export(self._job)
        self._finish_timer()
        try:
            if getattr(self, '_config', None) is not None:
                self._config.last_status = 'Export cancelled'
        except ReferenceError:
            pass
        _redraw(context)

    def execute(self, context):
        config = None
        try:
            _object_mode_required(context)
            rig, config = _config(context)
            result = _exporter().export_character(context, rig, config)
            self._result(config, result)
            return {'FINISHED'}
        except (ValueError, RuntimeError, OSError) as exc:
            if config is not None:
                config.last_status = 'Export failed: ' + str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_unity_locate_unweighted(Operator):
    bl_idname = 'character_designer.unity_locate_unweighted'
    bl_label = 'Locate Unweighted Vertices'
    bl_description = 'Check the current source mesh, select vertices without deform-bone weights, and frame them; does not assign weights'
    bl_options = {'REGISTER', 'UNDO'}
    object_name: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        try:
            if _exporter().export_running():
                raise ValueError('Wait for the current export to finish.')
            if context.mode not in {'OBJECT', 'POSE', 'EDIT_MESH', 'PAINT_WEIGHT'}:
                raise ValueError('Finish the current operation before locating vertices.')
            rig, _settings = _config(context)
            obj = context.scene.objects.get(self.object_name)
            if obj is None or obj.type != 'MESH':
                raise ValueError('The reported mesh no longer exists in this scene.')
            if obj.name not in context.view_layer.objects:
                raise ValueError('Enable this mesh collection in the current View Layer first.')
            if not _visible_collection_path(context.view_layer.layer_collection, obj):
                raise ValueError('Show this mesh collection before locating its vertices.')
            if obj.library or obj.data.library:
                raise ValueError('The reported mesh is linked and cannot enter Edit Mode; open its source file to edit weights.')
            if obj not in _exporter().bound_meshes(context, rig):
                raise ValueError('This mesh is no longer bound to the character Main Rig.')
            if obj.mode == 'EDIT':
                obj.update_from_editmode()
            from .unity_diagnostics import unweighted_vertex_indices
            indices = unweighted_vertex_indices(obj)
            if not indices:
                self.report({'INFO'}, 'The current source mesh has no unweighted vertices. Re-export to update the report; modifier-generated vertices may need separate inspection.')
                return {'FINISHED'}
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            for selected in tuple(context.selected_objects):
                selected.select_set(False)
            obj.hide_viewport = False
            obj.hide_set(False)
            obj.hide_select = False
            obj.select_set(True)
            context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='EDIT')
            import bmesh
            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            for face in bm.faces:
                face.select_set(False)
            for edge in bm.edges:
                edge.select_set(False)
            for vertex in bm.verts:
                vertex.select_set(False)
            bm.select_mode = {'VERT'}
            context.tool_settings.mesh_select_mode = (True, False, False)
            bm.select_history.clear()
            for index in indices:
                vertex = bm.verts[index]
                vertex.hide_set(False)
                vertex.select_set(True)
                bm.select_history.add(vertex)
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            area = context.area if context.area and context.area.type == 'VIEW_3D' else None
            if area is None and context.screen:
                area = next((area for area in context.screen.areas if area.type == 'VIEW_3D'), None)
            if area:
                region = next((region for region in area.regions if region.type == 'WINDOW'), None)
                if region:
                    with context.temp_override(area=area, region=region):
                        area.spaces.active.shading.show_xray = True
                        bpy.ops.view3d.view_selected(use_all_regions=False)
            self.report({'INFO'}, f'Selected {len(indices)} current unweighted vertices on {obj.name}; weights unchanged.')
            return {'FINISHED'}
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_unity_export_section(Operator):
    bl_idname = 'character_designer.unity_export_section'
    bl_label = 'Expand Export Section'
    bl_description = 'Expand or collapse this export section'
    bl_options = {'INTERNAL'}
    section: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        try:
            _rig_obj, config = _config(context)
            name = {'OBJECTS': 'show_objects', 'MATERIALS': 'show_materials',
                    'WARNINGS': 'show_warnings'}.get(self.section)
            if name is None:
                raise ValueError('Unknown export section.')
            setattr(config, name, not getattr(config, name))
            _redraw(context)
            return {'FINISHED'}
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_unity_choose_simple_material(Operator):
    bl_idname = 'character_designer.unity_choose_simple_material'
    bl_label = 'Add Simplified Material'
    bl_description = 'Choose an included character material to simplify on export'
    bl_options = {'INTERNAL'}
    bl_property = 'material_name'
    material_name: EnumProperty(items=_simple_material_search_items)
    search_token: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, _event):
        try:
            self.cancel(context)
            exporter = _exporter()
            if exporter.export_running():
                raise ValueError('Wait for the current export to finish.')
            rig, config = _config(context)
            collected = exporter.collect_character(context, rig, config)
            materials, chosen = _material_choices(collected['objects'], config)
            self._material_items = tuple((material.name, material.name, '')
                                         for material in materials if material not in chosen)
            if not self._material_items:
                self.report({'INFO'}, 'No other included character materials to add.')
                return {'CANCELLED'}
            self.search_token = uuid.uuid4().hex
            _SIMPLE_MATERIAL_SEARCH[self.search_token] = self._material_items
            self._rig_identity = rig.as_pointer()
            context.window_manager.invoke_search_popup(self)
            return {'RUNNING_MODAL'}
        except (ValueError, RuntimeError) as exc:
            self.cancel(context)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def cancel(self, _context):
        _SIMPLE_MATERIAL_SEARCH.pop(getattr(self, 'search_token', ''), None)

    def execute(self, context):
        try:
            rig, _config_data = _config(context)
            if getattr(self, '_rig_identity', rig.as_pointer()) != rig.as_pointer():
                raise ValueError('The character changed; choose a material again.')
            # The existing undoable action freshly checks included bindings and
            # material use, even if the scene changed while the popup was open.
            return bpy.ops.character_designer.unity_simple_material(
                material_name=self.material_name, enabled=True)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            self.cancel(context)


class CHARACTERDESIGNER_OT_unity_simple_material(Operator):
    bl_idname = 'character_designer.unity_simple_material'
    bl_label = 'Simple Export Material'
    bl_description = 'Toggle a temporary Principled BSDF approximation for this material on the next export; preserve the original shader'
    bl_options = {'REGISTER', 'UNDO'}
    material_name: StringProperty(options={'HIDDEN'})
    enabled: BoolProperty(default=True, options={'HIDDEN'})

    @classmethod
    def description(cls, _context, properties):
        if properties.enabled:
            return 'Use a temporary Principled BSDF approximation for this material on the next export; keep the original unchanged'
        return 'Use this material\'s original shader on the next export; keep other material choices unchanged'

    def execute(self, context):
        try:
            if _exporter().export_running():
                raise ValueError('Wait for the current export to finish.')
            rig, config = _config(context)
            material = bpy.data.materials.get(self.material_name)
            if material is None:
                raise ValueError('This material no longer exists.')
            if self.enabled:
                collected = _exporter().collect_character(context, rig, config)
                used = {slot.material for obj in collected['objects'] if obj.type == 'MESH' for slot in obj.material_slots}
                if material not in used:
                    raise ValueError('This material is not used by an included character mesh.')
                if not any(entry.material == material for entry in _simple_material_entries(config)):
                    config.simple_materials.add().material = material
            else:
                for index in reversed(range(len(config.simple_materials))):
                    if config.simple_materials[index].material == material:
                        config.simple_materials.remove(index)
            _redraw(context)
            self.report({'INFO'}, 'Export again to apply the material choice. The original material is unchanged.')
            return {'FINISHED'}
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_unity_add_selected(Operator):
    bl_idname = 'character_designer.unity_add_selected'
    bl_label = 'Include Selected Bound Meshes'
    bl_description = 'Restore inclusion of selected meshes with an enabled character Armature binding'
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    def execute(self, context):
        try:
            _object_mode_required(context)
            rig, config = _config(context)
            selected = [obj for obj in context.selected_objects if obj.type == 'MESH']
            if not selected:
                raise ValueError('Select at least one bound mesh to include.')
            eligible = set(_exporter().bound_meshes(context, rig))
            invalid = [obj.name for obj in selected if obj not in eligible]
            if invalid:
                raise ValueError('Only meshes with an enabled Armature binding to this character can be included: '
                                 + ', '.join(invalid))
            included = {entry.object for entry in config.extras if entry.object is not None}
            for obj in selected:
                if obj in included:
                    for entry in config.extras:
                        if entry.object == obj:
                            entry.enabled = True
                    continue
                entry = config.extras.add()
                entry.object = obj
                entry.enabled = True
                included.add(obj)
            config.show_objects = True
            self.report({'INFO'}, f'Included {len(selected)} bound mesh(es) for {rig.name}.')
            return {'FINISHED'}
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_unity_remove_extra(Operator):
    bl_idname = 'character_designer.unity_remove_extra'
    bl_label = 'Clear Export Override'
    bl_description = 'Return this reference to automatic bound-mesh inclusion; keep the scene object'
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}
    index: IntProperty(default=-1, options={'HIDDEN'})

    def execute(self, context):
        try:
            _rig_obj, config = _config(context)
            if not 0 <= self.index < len(config.extras):
                raise ValueError('This extra export reference no longer exists.')
            config.extras.remove(self.index)
            return {'FINISHED'}
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_unity_set_included(Operator):
    bl_idname = 'character_designer.unity_set_included'
    bl_label = 'Change Export Inclusion'
    bl_description = 'Include or exclude this bound mesh in the export; leave its scene binding unchanged'
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}
    object_name: StringProperty(options={'HIDDEN'})
    include: BoolProperty(default=True, options={'HIDDEN'})

    def execute(self, context):
        try:
            rig, config = _config(context)
            obj = context.scene.objects.get(self.object_name)
            if obj is None or obj not in _exporter().bound_meshes(context, rig):
                raise ValueError('This mesh no longer has an enabled Armature binding to this character.')
            setup = character_setup.settings(context)
            if not self.include and setup is not None and obj == setup.body:
                raise ValueError('The main body cannot be excluded.')
            matches = [entry for entry in config.extras if entry.object == obj]
            if not matches:
                entry = config.extras.add()
                entry.object = obj
                matches = [entry]
            for entry in matches:
                entry.enabled = self.include
            _redraw(context)
            return {'FINISHED'}
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_unity_open_path(Operator):
    bl_idname = 'character_designer.unity_open_path'
    bl_label = 'Open Export Folder'
    bl_description = 'Open the export folder or the last export report'
    bl_options = {'INTERNAL'}
    open_report: BoolProperty(default=False, options={'HIDDEN'})

    @classmethod
    def description(cls, _context, properties):
        return ('Open the last completed export report, including Unity verification status and runtime details'
                if properties.open_report else 'Open the saved export folder')

    def execute(self, context):
        try:
            _rig_obj, config = _config(context)
            saved = config.last_report if self.open_report else config.directory
            if not saved:
                raise ValueError('No export report yet.' if self.open_report else 'Choose a Unity folder first.')
            path = os.path.abspath(bpy.path.abspath(saved))
            if not (os.path.isfile(path) if self.open_report else os.path.isdir(path)):
                raise ValueError('The export report no longer exists.' if self.open_report
                                 else 'The Unity folder does not exist yet.')
            return bpy.ops.wm.path_open(filepath=path)
        except (ValueError, RuntimeError, OSError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_PT_unity_export(Panel):
    bl_label = 'Unity Export'
    bl_parent_id = 'CHARACTERDESIGNER_PT_main'
    bl_idname = 'CHARACTERDESIGNER_PT_unity_export'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = SIDEBAR_CATEGORY
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) == UI_PAGE_MISC

    def draw(self, context):
        layout = self.layout
        setup = character_setup.settings(context)
        if setup is None:
            layout.label(text='Character settings unavailable', icon='ERROR')
            return
        layout.prop(setup, 'rig', text='Main Rig')
        rig = _rig(context)
        if rig is None:
            layout.label(text='Choose your character rig to begin.', icon='INFO')
            return
        config = rig.character_designer_unity_export
        # DIR_PATH supplies the single directory picker.
        layout.prop(config, 'directory', text='Folder')
        layout.prop(config, 'filename', text='Name')

        objects, eligible, warnings, error, scope = [], [], [], '', None
        try:
            exporter = _exporter()
            scope = exporter._collection_scope(
                context, rig, references=exporter._scope_references(config, setup))
            eligible = scope['eligible']
            collected = _exporter().collect_character(context, rig, config, _scope=scope)
            objects = collected['objects']
            warnings = collected.get('warnings', [])
        except (ValueError, RuntimeError) as exc:
            error = str(exc)

        row = layout.row()
        row.scale_y = 1.35
        running = _exporter().export_running()
        row.enabled = not running
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator('character_designer.unity_export', icon='EXPORT',
                     text='Exporting...' if running else 'Export / Update to Unity')
        if running:
            layout.label(text='Esc cancels; existing files are kept.', icon='INFO')
        if context.mode not in {'OBJECT', 'POSE'}:
            layout.label(text='Use Object or Pose Mode to export.', icon='INFO')
        if error:
            layout.label(text=_short(error), icon='ERROR')
        if config.last_status and not config.last_status.startswith('Exported'):
            layout.label(text=_short(config.last_status),
                         icon='ERROR' if config.last_status.startswith('Export failed') else 'INFO')

        row = layout.row()
        meshes = sum(obj.type == 'MESH' for obj in objects)
        rigs = sum(obj.type == 'ARMATURE' for obj in objects)
        row.operator('character_designer.unity_export_section',
                     text='Objects' if error else f'Objects · {meshes} Meshes · {rigs} Armatures', emboss=False,
                     icon='TRIA_DOWN' if config.show_objects else 'TRIA_RIGHT').section = 'OBJECTS'
        if config.show_objects:
            CHARACTERDESIGNER_PT_unity_export._draw_objects(
                layout, context, rig, config, setup, objects, eligible, error, scope)

        chosen = {entry.material for entry in _simple_material_entries(config)}
        row = layout.row()
        row.operator('character_designer.unity_export_section', text=f'Use Simplified Materials · {len(chosen)}', emboss=False,
                     icon='TRIA_DOWN' if config.show_materials else 'TRIA_RIGHT').section = 'MATERIALS'
        if config.show_materials:
            box = layout.box()
            box.enabled = not running
            row = box.row()
            row.operator_context = 'INVOKE_DEFAULT'
            row.operator('character_designer.unity_choose_simple_material', text='Add Material', icon='ADD')
            for material in sorted(chosen, key=lambda material: material.name.casefold()):
                row = box.row(align=True)
                row.label(text=_short(material.name, 30), icon='MATERIAL')
                action = row.operator('character_designer.unity_simple_material',
                                      text='Use Original', icon='LOOP_BACK')
                action.material_name = material.name
                action.enabled = False
            if not chosen:
                box.label(text='No simplified materials selected.')

        report = _last_report(config)
        reported_warnings = _panel_warnings(report or {'warnings': warnings})
        if reported_warnings:
            row = layout.row()
            row.operator('character_designer.unity_export_section', text=f'Warnings · {len(reported_warnings)}', emboss=False,
                         icon='TRIA_DOWN' if config.show_warnings else 'TRIA_RIGHT').section = 'WARNINGS'
        if reported_warnings and config.show_warnings:
            box = layout.box()
            scale = context.preferences.system.ui_scale or 1.0
            width = max(24, int(getattr(context.region, 'width', 300) / (7 * scale)) - 6)
            for warning in reported_warnings:
                for index, line in enumerate(textwrap.wrap(_warning_summary(warning), width)):
                    box.label(text=line, icon='ERROR' if index == 0 else 'BLANK1')
                _warning_actions(box, context, config, warning)

    @staticmethod
    def _draw_objects(layout, context, rig, config, setup, objects, eligible, error, scope=None):
        box = layout.box()
        for obj in sorted(objects, key=lambda obj: (obj.type != 'ARMATURE', obj != setup.body)):
            row = box.row(align=True)
            if obj.type == 'MESH' and obj != setup.body:
                action = row.operator('character_designer.unity_set_included', text='',
                                      icon='CHECKBOX_HLT', emboss=False)
                action.object_name = obj.name
                action.include = False
            row.label(text=obj.name, icon='ARMATURE_DATA' if obj.type == 'ARMATURE' else 'MESH_DATA')
        excluded = [obj for obj in eligible if obj not in objects
                    and (not error or any(entry.object == obj and not entry.enabled
                                          for entry in config.extras))]
        if excluded:
            box.separator()
            box.label(text='Excluded')
            for obj in excluded:
                row = box.row(align=True)
                action = row.operator('character_designer.unity_set_included', text='',
                                      icon='CHECKBOX_DEHLT', emboss=False)
                action.object_name = obj.name
                action.include = True
                row.label(text=obj.name, icon='MESH_DATA')
        try:
            invalid = _invalid_overrides(context, rig, config, _scope=scope)
        except (ValueError, RuntimeError):
            invalid = []
        if invalid:
            box.separator()
            box.label(text='Invalid saved references', icon='ERROR')
            for index, reason in invalid:
                box.label(text=_short(reason))
                box.operator('character_designer.unity_remove_extra',
                             text='Clear Export Override', icon='X').index = index


UNITY_EXPORT_CLASSES = (
    CharacterDesignerUnityExtra,
    CharacterDesignerUnitySimpleMaterial,
    CharacterDesignerUnityExport,
    CHARACTERDESIGNER_OT_unity_export,
    CHARACTERDESIGNER_OT_unity_locate_unweighted,
    CHARACTERDESIGNER_OT_unity_export_section,
    CHARACTERDESIGNER_OT_unity_choose_simple_material,
    CHARACTERDESIGNER_OT_unity_simple_material,
    CHARACTERDESIGNER_OT_unity_add_selected,
    CHARACTERDESIGNER_OT_unity_remove_extra,
    CHARACTERDESIGNER_OT_unity_set_included,
    CHARACTERDESIGNER_OT_unity_open_path,
    CHARACTERDESIGNER_PT_unity_export,
)
