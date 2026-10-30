"""Pose-library interaction adapters; no playback handlers or polling service."""
import hashlib
import json
import os
import sys
import time
from types import SimpleNamespace

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty

_MENU_HOOK = None
_SHELF_HOOK = None
_ACTIVE_OPERATORS = []


def _poses():
    from . import control_pose_assets
    return control_pose_assets


def _core():
    from . import control_pose_blend
    return control_pose_blend


def _controlled(context, asset=None):
    obj = context.object
    asset = asset if asset is not None else getattr(context, 'asset', None)
    if not (obj and obj.type == 'ARMATURE' and obj.mode == 'POSE' and asset
            and asset.id_type == 'ACTION'):
        return False
    from . import body_setup
    local = getattr(asset, 'local_id', None)
    return body_setup.has_generated(obj) or bool(local and _poses().ASSET_METADATA in local)


def _signature(values, metadata):
    raw = json.dumps({'values': values, 'metadata': metadata}, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode('utf8')
    return hashlib.sha256(raw).hexdigest()


def _remember(operator, context):
    asset = getattr(context, 'asset', None)
    if not asset or asset.id_type != 'ACTION':
        raise ValueError('Choose a Pose asset first.')
    operator.asset_name = asset.name
    local = getattr(asset, 'local_id', None)
    local_input = local is not None and local.library is None
    operator.asset_local_name = local.name if local_input else ''
    operator.asset_library_path = '' if local_input else asset.full_library_path
    operator.target_name = context.object.name
    return _load(operator, context, verify=False)


def _load(operator, context, *, verify=True):
    rig = context.object
    if rig is None or rig.type != 'ARMATURE' or rig.name != operator.target_name:
        raise ValueError('The current character changed. Choose its Pose again.')
    if operator.asset_local_name:
        local = bpy.data.actions.get(operator.asset_local_name)
        if local is None or local.library or not local.asset_data:
            raise ValueError('The original local Pose asset is no longer available.')
    else:
        local = None
        if not operator.asset_library_path:
            raise ValueError('The original Pose library is no longer available.')
    asset = SimpleNamespace(name=operator.asset_name, local_id=local,
                            full_library_path=operator.asset_library_path, id_type='ACTION')
    values = _poses()._asset_channels(asset, rig)
    metadata = getattr(values, 'metadata', None)
    digest = _signature(values, metadata)
    if verify and operator.asset_digest and digest != operator.asset_digest:
        raise ValueError('The Pose asset changed. Choose it again before repeating this operation.')
    operator.asset_digest = digest
    operator._asset, operator._values, operator._metadata = asset, values, metadata
    return asset, values, metadata


def _begin(operator, context):
    if operator._values is None:
        raise _core().NativeBlendRequired('This Pose uses native controller channels.')
    return _core().begin(context, rig=context.object, values=operator._values,
                         metadata=operator._metadata, flipped=operator.flipped)


def _native(operator, context, invoke):
    asset = getattr(context, 'asset', None)
    if not asset or asset.name != operator.asset_name:
        raise ValueError('Choose the original Pose asset again to use native blending.')
    local = getattr(asset, 'local_id', None)
    if operator.asset_local_name:
        expected = bpy.data.actions.get(operator.asset_local_name)
        same = local is expected and expected is not None
        if not same and local is not None and expected is not None:
            same = (hasattr(local, 'as_pointer') and hasattr(expected, 'as_pointer')
                    and local.as_pointer() == expected.as_pointer())
        if not same:
            raise ValueError('The current local Pose asset is different; choose the original Pose again.')
    else:
        actual_path = getattr(asset, 'full_library_path', '')
        if (not actual_path or not operator.asset_library_path
                or os.path.normcase(os.path.realpath(actual_path))
                != os.path.normcase(os.path.realpath(operator.asset_library_path))):
            raise ValueError('The current Pose library is different; choose the original Pose again.')
    return bpy.ops.poselib.blend_pose_asset('INVOKE_DEFAULT' if invoke else 'EXEC_DEFAULT',
        blend_factor=operator.blend_factor, flipped=operator.flipped)


def _end_ui(operator):
    if operator in _ACTIVE_OPERATORS:
        _ACTIVE_OPERATORS.remove(operator)
    for attribute, method, args in (('_area', 'header_text_set', (None,)),
                                    ('_workspace', 'status_text_set', (None,)),
                                    ('_window', 'cursor_modal_restore', ())):
        try:
            getattr(getattr(operator, attribute), method)(*args)
        except (AttributeError, ReferenceError, RuntimeError):
            pass


def _fail(operator, context, error):
    recovery = None
    if getattr(operator, '_session', None) is not None:
        try:
            _core().cancel(context, operator._session)
        except Exception as problem:
            recovery = str(problem)
        operator._session = None
    _end_ui(operator)
    text = str(error)
    if recovery:
        text += ' Recovery needs review: ' + recovery
    operator.report({'ERROR'}, text)
    return {'CANCELLED'}


def _status(operator):
    suffix = ' | Mirrored' if operator.flipped else ''
    operator._area.header_text_set(f'Blend Pose: {operator.blend_factor:.3f} ({operator.blend_factor * 100:.1f}%){suffix}')


class CHARACTERDESIGNER_OT_blend_control_pose(bpy.types.Operator):
    bl_idname = 'character_designer.blend_control_pose'
    bl_label = 'Blend Pose'
    bl_description = 'Blend this Pose through its controls; type 0.5 for half, Enter to keep, Esc to restore'
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    blend_factor: FloatProperty(name='Strength', default=0.0, min=0.0, max=1.0,
                               description='0 keeps the starting pose; 1 applies the complete asset')
    flipped: BoolProperty(name='Mirrored', default=False)
    asset_name: StringProperty(options={'HIDDEN'})
    asset_local_name: StringProperty(options={'HIDDEN'})
    asset_library_path: StringProperty(options={'HIDDEN'})
    asset_digest: StringProperty(options={'HIDDEN'})
    target_name: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        obj = context.object
        return bool(obj and obj.type == 'ARMATURE' and obj.mode == 'POSE')

    def execute(self, context):
        self._session = None
        try:
            _load(self, context) if self.asset_name else _remember(self, context)
            try:
                self._session = _begin(self, context)
            except _core().NativeBlendRequired:
                return _native(self, context, False)
            _core().preview(context, self._session, self.blend_factor)
            _core().confirm(context, self._session)
            self._session = None
            return {'FINISHED'}
        except Exception as error:
            return _fail(self, context, error)

    def invoke(self, context, event):
        self._session = None
        try:
            _load(self, context) if self.asset_name else _remember(self, context)
            try:
                self._session = _begin(self, context)
            except _core().NativeBlendRequired:
                return _native(self, context, True)
            self._area, self._window, self._workspace = context.area, context.window, context.workspace
            self._mouse_x, self._mouse_factor = event.mouse_x, float(self.blend_factor)
            self._numeric, self._last_preview = '', 0.0
            _core().preview(context, self._session, self.blend_factor)
            self._applied_factor = float(self.blend_factor)
            context.window.cursor_modal_set('SCROLL_X')
            context.workspace.status_text_set('Move mouse | Type 0–1 | Arrows adjust | Ctrl mirrors | Enter keeps | Esc restores')
            _status(self)
            context.window_manager.modal_handler_add(self)
            _ACTIVE_OPERATORS.append(self)
            return {'RUNNING_MODAL'}
        except Exception as error:
            return _fail(self, context, error)

    def modal(self, context, event):
        try:
            if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                _core().cancel(context, self._session)
                self._session = None
                _end_ui(self)
                return {'CANCELLED'}
            if event.type in {'RET', 'NUMPAD_ENTER', 'LEFTMOUSE', 'SPACE'} and event.value == 'PRESS':
                _core().preview(context, self._session, self.blend_factor)
                _core().confirm(context, self._session)
                self._session = None
                _end_ui(self)
                return {'FINISHED'}
            if event.type in {'LEFT_CTRL', 'RIGHT_CTRL'} and event.value == 'PRESS':
                _core().cancel(context, self._session)
                self._session = None
                self.flipped = not self.flipped
                self._session = _begin(self, context)
                _core().preview(context, self._session, self.blend_factor)
                self._applied_factor = float(self.blend_factor)
                _status(self)
                return {'RUNNING_MODAL'}
            changed = False
            if event.type == 'MOUSEMOVE' and not self._numeric:
                speed = 0.1 if event.shift else 1.0
                self.blend_factor = max(0.0, min(1.0,
                    self._mouse_factor + (event.mouse_x - self._mouse_x) * speed / 300.0))
                changed = True
            elif event.value == 'PRESS':
                char = getattr(event, 'unicode', '')
                if char and char in '0123456789.' and len(self._numeric) < 12:
                    candidate = self._numeric + char
                    if candidate.count('.') <= 1:
                        self._numeric = candidate
                        try:
                            self.blend_factor = max(0.0, min(1.0, float(candidate)))
                            changed = True
                        except ValueError:
                            pass
                elif event.type == 'BACK_SPACE':
                    self._numeric = self._numeric[:-1]
                    raw = self._numeric if self._numeric not in {'', '.'} else '0'
                    self.blend_factor = max(0.0, min(1.0, float(raw)))
                    changed = True
                elif event.type in {'LEFT_ARROW', 'RIGHT_ARROW'}:
                    self._numeric = ''
                    step = .001 if event.shift else .01
                    self.blend_factor = max(0.0, min(1.0,
                        self.blend_factor + (step if event.type == 'RIGHT_ARROW' else -step)))
                    self._mouse_x, self._mouse_factor = event.mouse_x, float(self.blend_factor)
                    changed = True
            if changed:
                now = time.monotonic()
                if event.type != 'MOUSEMOVE' or now - self._last_preview >= 1.0 / 30.0:
                    _core().preview(context, self._session, self.blend_factor)
                    self._applied_factor, self._last_preview = float(self.blend_factor), now
                _status(self)
            return {'RUNNING_MODAL'}
        except Exception as error:
            return _fail(self, context, error)

    def cancel(self, context):
        if getattr(self, '_session', None) is not None:
            try:
                _core().cancel(context, self._session)
            finally:
                self._session = None
                _end_ui(self)


def _offset_names(context, rig, scope):
    if scope == 'ASSET':
        return None
    from . import limb_ik, control_pose_capture
    inventory = limb_ik._validate_inventory(rig)
    if scope == 'SELECTED':
        return control_pose_capture._resolve_names(rig, inventory, 'SELECTED', False)
    names = {name for key, entry in inventory['rigs'].items() if key[0] == 'ARM'
             for name in entry['chain']}
    if not names:
        raise ValueError('This character has no validated arm controls.')
    return sorted(names)


class CHARACTERDESIGNER_OT_offset_animation_pose(bpy.types.Operator):
    bl_idname = 'character_designer.offset_animation_pose'
    bl_label = 'Offset Animation with Pose'
    bl_description = 'Create a new editable animation with a fixed Rest-to-Pose offset; keep the source animation'
    bl_options = {'REGISTER', 'UNDO'}

    strength: FloatProperty(name='Strength', default=.5, min=0.0, max=1.0)
    scope: EnumProperty(name='Region', items=(
        ('ARMS', 'Arms', 'Both arm chains, without fingers'),
        ('SELECTED', 'Selected Region', 'The selected native bones or their controls'),
        ('ASSET', 'Pose Bones', 'All supported bones recorded in the Pose asset'),
    ), default='ARMS')
    flipped: BoolProperty(name='Mirrored', default=False)
    asset_name: StringProperty(options={'HIDDEN'})
    asset_local_name: StringProperty(options={'HIDDEN'})
    asset_library_path: StringProperty(options={'HIDDEN'})
    asset_digest: StringProperty(options={'HIDDEN'})
    target_name: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        obj = context.object
        return bool(obj and obj.type == 'ARMATURE' and obj.mode in {'OBJECT', 'POSE'})

    def invoke(self, context, event):
        try:
            _remember(self, context)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        self.layout.prop(self, 'strength', slider=True)
        self.layout.prop(self, 'scope')
        self.layout.prop(self, 'flipped')
        self.layout.label(text='Creates a new Custom animation; keeps the original.')

    def execute(self, context):
        try:
            asset, _values, _metadata = _load(self, context) if self.asset_name else _remember(self, context)
            from . import control_pose_offset
            result = control_pose_offset.create(context, context.object, asset,
                strength=self.strength, bone_names=_offset_names(context, context.object, self.scope),
                flipped=self.flipped, activate=True)
            name = getattr(result, 'name', '') if not isinstance(result, dict) else result.get('name', '')
            self.report({'INFO'}, 'Pose offset animation created' + (': ' + name if name else '.'))
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class _RoutedLayout:
    def __init__(self, layout):
        object.__setattr__(self, '_layout', layout)

    def __getattr__(self, name):
        return getattr(self._layout, name)

    def __setattr__(self, name, value):
        setattr(self._layout, name, value)

    def operator(self, identifier, *args, **kwargs):
        routes = {'poselib.apply_pose_asset': 'character_designer.apply_control_pose',
                  'poselib.blend_pose_asset': 'character_designer.blend_control_pose'}
        return self._layout.operator(routes.get(identifier, identifier), *args, **kwargs)


def draw_extra_menu(self, context):
    if _controlled(context):
        self.layout.separator()
        self.layout.operator('character_designer.offset_animation_pose', icon='POSE_HLT')


def route_native_menu():
    """Route known Pose Library menus, preserving their original draw functions."""
    global _MENU_HOOK, _SHELF_HOOK
    gui = sys.modules.get('pose_library.gui')
    if gui is None:
        return False
    original = getattr(gui, 'pose_library_asset_browser_context_menu', None)
    menu = bpy.types.ASSETBROWSER_MT_context_menu
    callbacks = getattr(menu.draw, '_draw_funcs', ())
    if _MENU_HOOK is None and original is not None and isinstance(callbacks, list) and original in callbacks:
        def routed(self, context):
            if _controlled(context):
                original(SimpleNamespace(layout=_RoutedLayout(self.layout)), context)
            else:
                original(self, context)
        # Replace exactly the owned callback slot, preserving every other menu
        # callback and its order. The original callable itself remains unchanged.
        callbacks[callbacks.index(original)] = routed
        _MENU_HOOK = (menu, original, routed)
    shelf = getattr(gui, 'VIEW3D_AST_pose_library', None)
    if _SHELF_HOOK is None and shelf is not None:
        descriptor = shelf.__dict__.get('draw_context_menu')
        if isinstance(descriptor, classmethod):
            original_shelf = descriptor.__func__
            def routed_shelf(cls, context, asset, layout):
                controlled = _controlled(context, asset)
                original_shelf(cls, context, asset, _RoutedLayout(layout) if controlled else layout)
                if controlled:
                    layout.separator()
                    layout.operator('character_designer.offset_animation_pose', icon='POSE_HLT')
            wrapped = classmethod(routed_shelf)
            shelf.draw_context_menu = wrapped
            _SHELF_HOOK = (shelf, descriptor, wrapped)
    return _MENU_HOOK is not None


def _release_hooks(*, menu=True, shelf=True):
    global _MENU_HOOK, _SHELF_HOOK
    if menu and _MENU_HOOK is not None:
        menu_type, original, routed = _MENU_HOOK
        callbacks = getattr(menu_type.draw, '_draw_funcs', ())
        if isinstance(callbacks, list) and routed in callbacks:
            if original in callbacks:
                menu_type.remove(routed)
            else:
                callbacks[callbacks.index(routed)] = original
        _MENU_HOOK = None
    if shelf and _SHELF_HOOK is not None:
        shelf_type, descriptor, wrapped = _SHELF_HOOK
        if shelf_type.__dict__.get('draw_context_menu') is wrapped:
            shelf_type.draw_context_menu = descriptor
        _SHELF_HOOK = None


def unregister():
    if _ACTIVE_OPERATORS:
        raise RuntimeError('Finish or cancel the active Pose blend before disabling Character Designer.')
    _release_hooks()
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)


def _registration_state():
    return tuple(cls for cls in CLASSES if cls.is_registered), _MENU_HOOK, _SHELF_HOOK


def _rollback_registration(before):
    classes, menu, shelf = before
    _release_hooks(menu=menu is None, shelf=shelf is None)
    for cls in reversed(CLASSES):
        if cls not in classes and cls.is_registered:
            bpy.utils.unregister_class(cls)


def register():
    registered = []
    before = _registration_state()
    try:
        for cls in CLASSES:
            if not cls.is_registered:
                bpy.utils.register_class(cls)
                registered.append(cls)
        route_native_menu()
    except Exception:
        _rollback_registration(before)
        raise


CLASSES = (CHARACTERDESIGNER_OT_blend_control_pose, CHARACTERDESIGNER_OT_offset_animation_pose)
