"""Pin the artist's own Action and slot alongside prepared character motions."""
import bpy
from bpy.props import CollectionProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup

from . import animation_worklist_edit_scene as edit_scene


class CharacterDesignerLocalMotionPin(PropertyGroup):
    workspace_identity: StringProperty()
    rig: PointerProperty(type=bpy.types.Object)
    action: PointerProperty(type=bpy.types.Action)
    slot_handle: IntProperty()

    @classmethod
    def register(cls):
        bpy.types.Scene.character_designer_local_motion_pins = CollectionProperty(type=cls)

    @classmethod
    def unregister(cls):
        del bpy.types.Scene.character_designer_local_motion_pins


def _home(context):
    scene = context.scene
    homes = edit_scene.home_scenes(scene)
    if homes:
        if len(homes) != 1:
            return None
        scene = homes[0]
    state = scene.character_designer_animation_worklist
    rig = scene.character_designer_setup.rig
    if (not state.workspace_identity or scene.library or scene.override_library or not rig
            or rig.library or rig.override_library or rig.name not in scene.objects
            or (state.rig and state.rig != rig) or rig.get('character_designer_worklist_main_session_v1') is not None
            or any(item.rig == rig for item in state.items)):
        return None
    return scene


def _active(rig):
    ad = rig.animation_data
    if ad and ad.action and not ad.action.library and not ad.action.override_library and ad.action_slot:
        return ad.action, ad.action_slot.handle
    return None


def _pins(home):
    state = home.character_designer_animation_worklist
    rig = home.character_designer_setup.rig
    for index, pin in enumerate(home.character_designer_local_motion_pins):
        if (pin.workspace_identity == state.workspace_identity and pin.rig == rig and pin.action
                and not pin.action.library and not pin.action.override_library):
            yield index, pin


def _slot(action, handle):
    return next((s for s in action.slots if s.handle == handle), None)


def assign_character_action(rig, action, slot_handle):
    """Assign an authenticated local character motion, retaining the prior work."""
    slot = _slot(action, slot_handle)
    if action.library or action.override_library or slot is None:
        raise ValueError('This local character Action or its original slot is unavailable.')
    ad = rig.animation_data
    if ad and ad.use_tweak_mode:
        raise ValueError('Finish editing the NLA strip first.')
    if _active(rig) == (action, slot_handle):
        return
    previous = ad.action if ad else None
    previous_slot = ad.action_slot if ad else None
    previous_fake_user = previous.use_fake_user if previous else None
    created_animation_data = ad is None
    ad = ad or rig.animation_data_create()
    try:
        ad.action, ad.action_slot = action, slot
        if ad.action != action or ad.action_slot != slot:
            raise ValueError('The original Action slot could not be assigned.')
        from .animation_cleanup import restore_defaults
        restore_defaults(rig, action, slot_handle)
        if previous and not previous.users:
            previous.use_fake_user = True
        from . import bone_display
        if bone_display.neutral_view(rig):
            bone_display.restore_view(rig)
    except Exception:
        if created_animation_data:
            rig.animation_data_clear()
        else:
            ad.action, ad.action_slot = previous, previous_slot
        if previous:
            previous.use_fake_user = previous_fake_user
        raise


class CHARACTERDESIGNER_OT_toggle_local_motion_pin(Operator):
    bl_idname = 'character_designer.toggle_local_motion_pin'
    bl_label = 'Pin or Unpin Character Action'
    bl_description = 'Keep this local character Action and its slot in the pinned list'
    bl_options = {'UNDO'}

    action_name: StringProperty(options={'HIDDEN'})
    slot_handle: IntProperty(options={'HIDDEN'})

    def execute(self, context):
        from .animation_motion_pins import redraw_lists
        home = _home(context)
        if home is None:
            return {'CANCELLED'}
        pins = home.character_designer_local_motion_pins
        for index, pin in _pins(home):
            if (pin.action.name, pin.slot_handle) == (self.action_name, self.slot_handle):
                # An unassigned Action can be kept alive solely by its pin.
                if pin.action.users == 1:
                    pin.action.use_fake_user = True
                pins.remove(index)
                redraw_lists(context)
                return {'FINISHED'}
        rig = home.character_designer_setup.rig
        current = _active(rig)
        if not current or (current[0].name, current[1]) != (self.action_name, self.slot_handle):
            self.report({'WARNING'}, 'The character Action changed. Open the list again.')
            return {'CANCELLED'}
        pin = pins.add()
        pin.rig = rig
        pin.action, pin.slot_handle = current
        pin.workspace_identity = home.character_designer_animation_worklist.workspace_identity
        redraw_lists(context)
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_open_local_motion(Operator):
    bl_idname = 'character_designer.open_local_motion'
    bl_label = 'Open Character Action'
    bl_description = 'Open the pinned Action on its original character and Action slot'
    bl_options = {'UNDO'}

    action_name: StringProperty(options={'HIDDEN'})
    slot_handle: IntProperty(options={'HIDDEN'})

    def execute(self, context):
        from .animation_worklist import _idle
        from .animation_original_inputs_ui import original_inputs_live
        home = _home(context)
        if home is None or not context.window:
            return {'CANCELLED'}
        rig = home.character_designer_setup.rig
        current = _active(rig)
        target = next((p.action for _, p in _pins(home) if p.action.name == self.action_name
                       and p.slot_handle == self.slot_handle), None)
        if target is None and current and current[0].name == self.action_name and current[1] == self.slot_handle:
            target = current[0]
        slot = _slot(target, self.slot_handle) if target else None
        if slot is None:
            self.report({'WARNING'}, 'This Action or its original slot is no longer available.')
            return {'CANCELLED'}
        previous_scene = context.scene
        ad = rig.animation_data
        if ad and ad.use_tweak_mode:
            self.report({'WARNING'}, 'Finish editing the NLA strip first.')
            return {'CANCELLED'}
        try:
            _idle(context)
            if original_inputs_live(context):
                raise ValueError('Finish editing Original Rigging Inputs first.')
            context.window.scene = home
            if original_inputs_live(context):
                raise ValueError('Finish editing Original Rigging Inputs first.')
            assign_character_action(rig, target, self.slot_handle)
        except (TypeError, ValueError, RuntimeError) as exc:
            if context.window.scene != previous_scene:
                context.window.scene = previous_scene
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


def draw_local_actions(layout, context, *, pinned_only):
    home = _home(context)
    if home is None:
        return False
    current = _active(home.character_designer_setup.rig)
    rows = [(p.action, p.slot_handle) for _, p in _pins(home)]
    pinned = set(rows)
    if current and current not in pinned and not pinned_only:
        rows.append(current)
    labels = [action.name.split(' · ', 1)[0] for action, _ in rows]
    for (action, handle), short in zip(rows, labels):
        row = layout.row(align=True)
        slot = _slot(action, handle)
        label = short if labels.count(short) == 1 else action.name
        if any(other == action and other_handle != handle for other, other_handle in rows) and slot:
            label += ' · ' + slot.name_display
        pick = row.row(align=True)
        pick.enabled = slot is not None
        open_op = pick.operator('character_designer.open_local_motion', text=label, icon='ACTION')
        open_op.action_name, open_op.slot_handle = action.name, handle
        marked = (action, handle) in pinned
        pin = row.operator('character_designer.toggle_local_motion_pin', text='',
                           icon='PINNED' if marked else 'UNPINNED', emboss=False)
        pin.action_name, pin.slot_handle = action.name, handle
    return bool(rows)


from .animation_neutral_motion import CHARACTERDESIGNER_OT_open_neutral_motion


CLASSES = (CharacterDesignerLocalMotionPin, CHARACTERDESIGNER_OT_toggle_local_motion_pin,
           CHARACTERDESIGNER_OT_open_local_motion, CHARACTERDESIGNER_OT_open_neutral_motion)
