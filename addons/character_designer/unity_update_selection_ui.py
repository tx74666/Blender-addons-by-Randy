"""Saved choices for a future Unity update, separate from FBX inclusion.

``get_selection`` is the read-only boundary consumed by an update pipeline.
Unchecked items mean "leave the Unity version unchanged"; they never represent
deletion or an instruction to omit an object from a replacement FBX. A caller
must implement that preservation contract before passing ``pipeline_ready=True``
to ``draw_selection`` or allowing a partial selection to reach export.

Only explicit operators write the per-rig settings. Drawing synchronizes a
discardable WindowManager cache so Blender's native Boolean drag paint can be
used without materializing every default choice into the saved artist data.
"""

import bpy
from types import SimpleNamespace
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup

from . import character_setup


SETTINGS_PROPERTY = 'character_designer_unity_update_selection'
VIEW_PROPERTY = 'character_designer_unity_update_selection_views'
_DEFAULT_SETTINGS = SimpleNamespace(meshes=(), update_rig=True, show_meshes=True, show_rig=False)
_SELECTION_WRITE_MODES = frozenset({'OBJECT', 'POSE'})
_SELECTION_MODE_HINT = 'Use Object or Pose Mode to select updates.'


def _exporter():
    from . import unity_export
    return unity_export


def _token(data):
    if data is None:
        return ''
    return str(getattr(data, 'session_uid', None) or data.as_pointer())


def _editable(data):
    return (data is not None and getattr(data, 'is_editable', True)
            and getattr(data, 'library', None) is None
            and getattr(data, 'override_library', None) is None)


def _read_settings(rig):
    # Reading a PointerProperty-to-PropertyGroup may create its empty ID group.
    # First-time drawing must leave the artist's persistent data untouched.
    return (getattr(rig, SETTINGS_PROPERTY) if rig.is_property_set(SETTINGS_PROPERTY)
            else _DEFAULT_SETTINGS)


def _validate_rig(context, rig, *, allow_running=False, for_write=True):
    if rig is None or getattr(rig, 'type', None) != 'ARMATURE':
        raise ValueError('Choose the character Main Rig first.')
    if context.scene.objects.get(rig.name) != rig:
        raise ValueError('The character Main Rig is no longer in this scene.')
    if character_setup.preferred_rig(context) != rig:
        raise ValueError('The active character changed; use its current update selection.')
    if for_write and not _editable(rig):
        raise ValueError('Linked or overridden character rigs cannot store update choices here.')
    if not allow_running and _exporter().export_running():
        raise ValueError('Wait for the current Unity export to finish.')
    if not hasattr(bpy.types.Object, SETTINGS_PROPERTY):
        raise ValueError('Unity update selection settings are unavailable.')
    return _read_settings(rig)


def _eligible(context, rig):
    return tuple(_exporter().bound_meshes(context, rig))


def _selected(settings, obj):
    # Duplicate legacy records are conservative; an explicit false wins until
    # the next explicit action synchronizes every matching record.
    return all(entry.selected for entry in settings.meshes if entry.object == obj)


def selection_counts(rig, eligible):
    """Return (selected, total, partial) without changing settings or scene data."""
    settings = _read_settings(rig)
    objects = tuple(dict.fromkeys(eligible))
    selected = sum(_selected(settings, obj) for obj in objects)
    return selected, len(objects), 0 < selected < len(objects)


def get_selection(context, rig, eligible=None):
    """Read current update choices after a fresh rig/binding inspection.

    ``eligible`` may restrict a caller's inspected scope, but cannot authorize a
    stale, foreign or unbound object. Missing saved references remain untouched.
    No record means selected by default. Reading is permitted for linked source
    data and while an export is running; it never grants permission to edit it.
    """
    settings = _validate_rig(context, rig, allow_running=True, for_write=False)
    current = _eligible(context, rig)
    if eligible is not None:
        requested = tuple(dict.fromkeys(eligible))
        if any(obj not in current for obj in requested):
            raise ValueError('The update object list is stale; refresh the current character.')
        current = requested
    return {'meshes': tuple(obj for obj in current if _selected(settings, obj)),
            'update_rig': bool(settings.update_rig), 'eligible': current}


def _append_mesh_choice(settings, obj, selected):
    """Append one explicit saved choice; the surrounding operation owns rollback."""
    entry = settings.meshes.add()
    entry.object = obj
    entry.selected = selected
    return entry


def _require_selection_mode(context):
    # Edit Mode Undo records its edited geometry, not settings on another ID.
    # An apparently undoable checkbox must not consume the artist's mesh Undo.
    if context.mode not in _SELECTION_WRITE_MODES:
        raise ValueError(_SELECTION_MODE_HINT)


def set_selection(context, rig, *, selected, objects=None, include_rig=False):
    """Atomically set choices for current bound meshes and optionally the Rig.

    ``objects=None`` means all current eligible meshes. Pass ``objects=()`` and
    ``include_rig=True`` for the independent Rig checkbox. This changes update
    intent only: no legacy ``extras``, source mesh, binding, bone or Unity asset
    is modified. Failed writes restore previous flags and remove only entries
    appended by this call; unrelated and invalid saved references are retained.
    """
    _require_selection_mode(context)
    if not isinstance(selected, bool):
        raise ValueError('Update selection requires a Boolean value.')
    _validate_rig(context, rig)
    eligible = _eligible(context, rig)
    targets = eligible if objects is None else tuple(dict.fromkeys(objects))
    for obj in targets:
        if obj is None or context.scene.objects.get(obj.name) != obj or obj not in eligible:
            raise ValueError('This mesh no longer has an enabled binding to the current character.')
        if not _editable(obj) or not _editable(obj.data):
            raise ValueError('Linked or overridden meshes cannot change update choices here.')
    if not targets and not include_rig:
        settings = _read_settings(rig)
        return {'meshes': tuple(obj for obj in eligible if _selected(settings, obj)),
                'update_rig': bool(settings.update_rig), 'eligible': eligible}
    had_settings = rig.is_property_set(SETTINGS_PROPERTY)
    settings = getattr(rig, SETTINGS_PROPERTY)
    original_keys = set(settings.keys())
    original_length = len(settings.meshes)
    original_rig = bool(settings.update_rig)
    old_flags = [(entry, bool(entry.selected), entry.is_property_set('selected')) for entry in settings.meshes
                 if entry.object in targets]
    try:
        for obj in targets:
            matches = [entry for entry in settings.meshes if entry.object == obj]
            if matches:
                for entry in matches:
                    entry.selected = selected
            else:
                _append_mesh_choice(settings, obj, selected)
        if include_rig:
            settings.update_rig = selected
    except Exception:
        for entry, previous, was_set in old_flags:
            if was_set:
                entry.selected = previous
            else:
                entry.property_unset('selected')
        settings.update_rig = original_rig
        while len(settings.meshes) > original_length:
            settings.meshes.remove(len(settings.meshes) - 1)
        for key in set(settings.keys()) - original_keys:
            del settings[key]
        if not had_settings and rig.is_property_set(SETTINGS_PROPERTY):
            rig.property_unset(SETTINGS_PROPERTY)
        raise
    _redraw(context)
    return {'meshes': tuple(obj for obj in eligible if _selected(settings, obj)),
            'update_rig': bool(settings.update_rig), 'eligible': eligible}


def _redraw(context):
    for window in context.window_manager.windows:
        if window.screen:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()


def _view_valid(view, context=None):
    context = context or bpy.context
    rig, scene = view.rig, view.scene
    if (rig is None or scene is None or view.rig_token != _token(rig)
            or view.scene_token != _token(scene) or context.scene != scene):
        raise ValueError('The update selection view is stale; use the current character.')
    return rig


def _call_set(view, selected, target, obj=None):
    context = bpy.context
    rig = _view_valid(view, context)
    result = bpy.ops.character_designer.unity_update_selection_set(
        'EXEC_DEFAULT', True, target=target, selected=bool(selected),
        rig_name=rig.name, rig_token=_token(rig),
        scene_token=_token(context.scene),
        object_name=obj.name if obj is not None else '',
        object_token=_token(obj))
    if result != {'FINISHED'}:
        raise ValueError('The update selection could not be changed.')


def _mesh_get(row):
    try:
        if (row.rig is None or row.object is None
                or row.rig_token != _token(row.rig)
                or row.object_token != _token(row.object)):
            return False
        return _selected(_read_settings(row.rig), row.object)
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def _mesh_set(row, value):
    if row.object is None or row.object_token != _token(row.object):
        raise ValueError('The update mesh reference is stale.')
    _call_set(row, value, 'OBJECT', row.object)


def _group_mesh_get(view):
    rows = [row for row in view.mesh_rows if row.present]
    return bool(rows) and all(_mesh_get(row) for row in rows)


def _group_mesh_set(view, value):
    _call_set(view, value, 'MESH')


def _group_rig_get(view):
    try:
        return bool(view.rig is not None and view.rig_token == _token(view.rig)
                    and _read_settings(view.rig).update_rig)
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def _group_rig_set(view, value):
    _call_set(view, value, 'RIG')


class CharacterDesignerUnityUpdateMeshChoice(PropertyGroup):
    object: PointerProperty(name='Mesh', type=bpy.types.Object)
    selected: BoolProperty(name='Update Mesh', default=True)


class CharacterDesignerUnityUpdateSelection(PropertyGroup):
    meshes: CollectionProperty(type=CharacterDesignerUnityUpdateMeshChoice)
    update_rig: BoolProperty(name='Update Rig', default=True)
    show_meshes: BoolProperty(name='Mesh', default=True)
    show_rig: BoolProperty(name='Rig', default=False)


class CharacterDesignerUnityUpdateMeshView(PropertyGroup):
    rig: PointerProperty(type=bpy.types.Object, options={'HIDDEN', 'SKIP_SAVE'})
    scene: PointerProperty(type=bpy.types.Scene, options={'HIDDEN', 'SKIP_SAVE'})
    object: PointerProperty(type=bpy.types.Object, options={'HIDDEN', 'SKIP_SAVE'})
    rig_token: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    scene_token: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    object_token: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    present: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    selected: BoolProperty(name='Update Mesh', get=_mesh_get, set=_mesh_set,
                           description='Update this mesh; unchecked keeps its Unity version unchanged',
                           options={'SKIP_SAVE'})


class CharacterDesignerUnityUpdateView(PropertyGroup):
    rig: PointerProperty(type=bpy.types.Object, options={'HIDDEN', 'SKIP_SAVE'})
    scene: PointerProperty(type=bpy.types.Scene, options={'HIDDEN', 'SKIP_SAVE'})
    rig_token: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    scene_token: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    mesh_rows: CollectionProperty(type=CharacterDesignerUnityUpdateMeshView, options={'SKIP_SAVE'})
    mesh_selected: BoolProperty(name='Update Meshes', get=_group_mesh_get, set=_group_mesh_set,
                                description='Select or deselect every mesh update', options={'SKIP_SAVE'})
    rig_selected: BoolProperty(name='Update Rig', get=_group_rig_get, set=_group_rig_set,
                               description='Update the character skeleton independently of mesh choices',
                               options={'SKIP_SAVE'})


class CHARACTERDESIGNER_OT_unity_update_selection_set(Operator):
    bl_idname = 'character_designer.unity_update_selection_set'
    bl_label = 'Change Unity Update Selection'
    bl_description = 'Choose which mesh and rig changes will be sent to Unity'
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}
    target: EnumProperty(items=(('OBJECT', 'Mesh', ''), ('MESH', 'Meshes', ''),
                                ('RIG', 'Rig', ''), ('ALL', 'All', '')))
    selected: BoolProperty(default=True, options={'HIDDEN'})
    rig_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    rig_token: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    scene_token: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    object_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    object_token: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        try:
            _require_selection_mode(context)
            rig = context.scene.objects.get(self.rig_name)
            if (rig is None or self.rig_token != _token(rig)
                    or self.scene_token != _token(context.scene)):
                raise ValueError('The update selection belongs to an earlier character view.')
            objects, include_rig = None, self.target in {'RIG', 'ALL'}
            if self.target == 'RIG':
                objects = ()
            elif self.target == 'OBJECT':
                obj = context.scene.objects.get(self.object_name)
                if obj is None or self.object_token != _token(obj):
                    raise ValueError('The update mesh reference is stale.')
                objects = (obj,)
            set_selection(context, rig, selected=self.selected, objects=objects,
                          include_rig=include_rig)
            return {'FINISHED'}
        except (ValueError, RuntimeError, ReferenceError, TypeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_unity_update_selection_expand(Operator):
    bl_idname = 'character_designer.unity_update_selection_expand'
    bl_label = 'Expand Unity Update Group'
    bl_description = 'Expand or collapse this group without changing its update selection'
    bl_options = {'INTERNAL'}
    section: EnumProperty(items=(('MESH', 'Mesh', ''), ('RIG', 'Rig', '')))
    rig_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    rig_token: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    scene_token: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        try:
            rig = context.scene.objects.get(self.rig_name)
            if (rig is None or self.rig_token != _token(rig)
                    or self.scene_token != _token(context.scene)):
                raise ValueError('The update selection belongs to an earlier character view.')
            _validate_rig(context, rig, allow_running=True)
            settings = getattr(rig, SETTINGS_PROPERTY)
            field = 'show_meshes' if self.section == 'MESH' else 'show_rig'
            setattr(settings, field, not getattr(settings, field))
            _redraw(context)
            return {'FINISHED'}
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


def _sync_view(context, rig, eligible):
    """Update disposable WM rows only; identities are never reassigned in place."""
    views = getattr(context.window_manager, VIEW_PROPERTY)
    rig_token, scene_token = _token(rig), _token(context.scene)
    view = next((item for item in views if item.rig == rig and item.scene == context.scene
                 and item.rig_token == rig_token and item.scene_token == scene_token), None)
    if view is None:
        view = views.add()
        view.rig, view.scene = rig, context.scene
        view.rig_token, view.scene_token = rig_token, scene_token
    objects = sorted(dict.fromkeys(eligible), key=lambda obj: (obj.name.casefold(), obj.name))
    rows = []
    for row in view.mesh_rows:
        if row.present:
            row.present = False
    for obj in objects:
        token = _token(obj)
        row = next((item for item in view.mesh_rows
                    if item.object == obj and item.object_token == token), None)
        if row is None:
            row = view.mesh_rows.add()
            row.rig, row.scene, row.object = rig, context.scene, obj
            row.rig_token, row.scene_token, row.object_token = rig_token, scene_token, token
        row.present = True
        rows.append(row)
    return view, rows


def _pin(operator, context, rig):
    operator.rig_name = rig.name
    operator.rig_token = _token(rig)
    operator.scene_token = _token(context.scene)
    return operator


def required_rigs(context, rig, eligible, scope=None):
    """List only actual mesh-binding rigs and required rig ancestors.

    The Main Rig is always shown. Merely parenting an unused armature below the
    character does not make it an update dependency. The roster uses all source
    meshes so independent Mesh checkbox changes do not move any UI rows.
    """
    exporter = _exporter()
    scope = scope or {}
    allowed = scope.get('rigs')
    if allowed is None:
        allowed = exporter._character_armatures(context.scene, rig)
    bindings = scope.get('bindings', {})
    required = {rig}
    for obj in eligible:
        bound = bindings.get(obj)
        if bound is None:
            bound = exporter._binding_armatures(obj)
        for armature in bound:
            if armature not in allowed:
                continue
            current = armature
            while current is not None:
                if current in allowed:
                    required.add(current)
                current = current.parent
    return (rig, *sorted(required - {rig}, key=lambda obj: (obj.name.casefold(), obj.name)))


def _header(layout, context, rig, view, section, total, *, enabled):
    row = layout.row(align=True)
    check = row.row(align=True)
    check.enabled = enabled
    check.prop(view, 'mesh_selected' if section == 'MESH' else 'rig_selected', text='')
    label = row.row(align=True)
    label.enabled = _editable(rig)
    label.alignment = 'LEFT'
    action = label.operator('character_designer.unity_update_selection_expand',
                            text=f'{"Mesh" if section == "MESH" else "Rig"} ({total})',
                            emboss=False)
    _pin(action, context, rig).section = section


def draw_selection(layout, context, rig, eligible, scope=None, *, pipeline_ready=False):
    """Draw independent Mesh/Rig choices with stable native drag-paint rows.

    Only the WindowManager view cache is synchronized. The header's large label
    expands a group; its separate checkbox changes selection. Mesh children
    never move when checked. ``scope`` may supply an already inspected ``rigs``
    set for the small Rig information section. Until the preserving update
    pipeline is connected, the default notice explicitly says export is pending.
    """
    settings = _read_settings(rig)
    eligible = tuple(dict.fromkeys(eligible))
    view, rows = _sync_view(context, rig, eligible)
    running = _exporter().export_running()
    selection_mode = context.mode in _SELECTION_WRITE_MODES
    can_write = _editable(rig) and not running and selection_mode
    if not selection_mode:
        layout.label(text=_SELECTION_MODE_HINT, icon='INFO')
    all_meshes_editable = all(_editable(obj) and _editable(obj.data) for obj in eligible)
    actions = layout.row(align=True)
    actions.enabled = can_write and all_meshes_editable
    for text, selected in (('Select All', True), ('Deselect All', False)):
        action = actions.operator('character_designer.unity_update_selection_set', text=text)
        _pin(action, context, rig)
        action.target, action.selected = 'ALL', selected
    tree = layout.column(align=True)
    rigs = required_rigs(context, rig, eligible, scope)
    _header(tree, context, rig, view, 'RIG', len(rigs), enabled=can_write)
    if settings.show_rig:
        column = tree.column(align=True)
        for armature in rigs:
            row = column.row(align=True)
            row.separator(factor=3.0)
            row.label(text=armature.name, icon='ARMATURE_DATA')
    total = len(eligible)
    _header(tree, context, rig, view, 'MESH', total,
            enabled=can_write and bool(total) and all_meshes_editable)
    if settings.show_meshes:
        column = tree.column(align=True)
        for item in rows:
            row = column.row(align=True)
            row.separator(factor=3.0)
            check = row.row(align=True)
            check.enabled = can_write and _editable(item.object) and _editable(item.object.data)
            check.prop(item, 'selected', text='')
            row.label(text=item.object.name, icon='MESH_DATA')
            if not _editable(item.object) or not _editable(item.object.data):
                row.label(text='Linked', icon='LINKED')
        if not rows:
            column.label(text='No bound meshes.', icon='INFO')
    layout.label(text=('Unchecked items stay unchanged in Unity.' if pipeline_ready
                       else 'Selective export is not enabled yet.'), icon='INFO')


CLASSES = (
    CharacterDesignerUnityUpdateMeshChoice,
    CharacterDesignerUnityUpdateSelection,
    CharacterDesignerUnityUpdateMeshView,
    CharacterDesignerUnityUpdateView,
    CHARACTERDESIGNER_OT_unity_update_selection_set,
    CHARACTERDESIGNER_OT_unity_update_selection_expand,
)


def _owns_property(owner, name, kind, target):
    prop = owner.bl_rna.properties.get(name)
    return bool(prop is not None and prop.type == kind and prop.fixed_type == target.bl_rna)


def register_properties():
    """Register after CLASSES, safely retaining exact-own repeated definitions."""
    definitions = (
        (bpy.types.Object, SETTINGS_PROPERTY, 'POINTER', CharacterDesignerUnityUpdateSelection),
        (bpy.types.WindowManager, VIEW_PROPERTY, 'COLLECTION', CharacterDesignerUnityUpdateView),
    )
    for owner, name, kind, target in definitions:
        if owner.bl_rna.properties.get(name) is not None and not _owns_property(owner, name, kind, target):
            raise RuntimeError('An unrelated RNA property already uses ' + name + '.')
    created_settings = not _owns_property(*definitions[0])
    if created_settings:
        setattr(bpy.types.Object, SETTINGS_PROPERTY, PointerProperty(type=CharacterDesignerUnityUpdateSelection))
    try:
        if not _owns_property(*definitions[1]):
            setattr(bpy.types.WindowManager, VIEW_PROPERTY, CollectionProperty(
                type=CharacterDesignerUnityUpdateView, options={'SKIP_SAVE'}))
    except Exception:
        if created_settings:
            delattr(bpy.types.Object, SETTINGS_PROPERTY)
        raise


def unregister_properties():
    """Remove RNA definitions, retaining saved per-rig ID data for re-registration."""
    for owner, name, kind, target in (
            (bpy.types.WindowManager, VIEW_PROPERTY, 'COLLECTION', CharacterDesignerUnityUpdateView),
            (bpy.types.Object, SETTINGS_PROPERTY, 'POINTER', CharacterDesignerUnityUpdateSelection)):
        if _owns_property(owner, name, kind, target):
            delattr(owner, name)
