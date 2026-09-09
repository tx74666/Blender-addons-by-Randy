"""Soft, semantic pose-controller colors with persistent per-bone restoration.

Native Blender normal/select/active colors handle selection; no draw or frame
handler changes colors, so later artist edits remain authoritative.
"""
import json
import re

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator


BACKUP_KEY = '_cd_control_color_before_v1'
DISPLAY_KEY = '_cd_control_color_display_before_v1'
ENABLED_KEY = '_cd_control_colors_enabled'
OWNER_KEY = 'character_designer_owner'
OWNERS = {'limb_ik', 'foot_controls', 'torso_controls', 'eye_controls', 'spine_ik_fk', 'root_control', 'limb_fk_visuals', 'head_neck_visuals', 'body_detail_visuals'}

# RGB values are Blender's display colors, not material/shader colors.
# Normal stays colored against a dark viewport; selection increases brightness
# while retaining hue, and the active control receives a soft pastel highlight.
PALETTES = {
    'MINT': ((.30, .57, .53), (.46, .88, .78), (.77, 1.00, .91)),
    'SKY': ((.35, .51, .65), (.52, .77, .98), (.79, .91, 1.00)),
    'ROSE': ((.65, .40, .52), (.96, .62, .77), (1.00, .85, .92)),
    'PEACH': ((.67, .47, .40), (.98, .74, .60), (1.00, .91, .81)),
    'LILAC': ((.53, .44, .68), (.78, .67, .98), (.93, .86, 1.00)),
    'IRIS': ((.43, .47, .68), (.66, .72, .98), (.85, .89, 1.00)),
    'HONEY': ((.66, .56, .34), (.94, .83, .53), (1.00, .96, .78)),
    'MAUVE': ((.58, .43, .62), (.87, .67, .91), (.98, .86, 1.00)),
}


def _hair(pb):
    return pb.bone.get('character_designer_hair_bones_owner') == 'hair_bones_v1'


def is_control(pb, *, owned_only=False):
    if _hair(pb):
        return True
    shape = pb.custom_shape
    if shape is None:
        return False
    if not owned_only:
        return True
    return (pb.bone.get(OWNER_KEY) in OWNERS or shape.get(OWNER_KEY) in OWNERS
            or bool(pb.id_data.get('character_designer_skirt_owner')))


def palette_for(pb):
    rig, bone = pb.id_data, pb.bone
    if pb.custom_shape and pb.custom_shape.get(OWNER_KEY) == 'body_detail_visuals':
        from . import body_detail_visuals
        role = pb.custom_shape.get(body_detail_visuals.ROLE_KEY)
        return {'BREAST_L': 'MINT', 'BREAST_R': 'ROSE', 'HIPS': 'LILAC'}.get(role, 'LILAC')
    if pb.custom_shape and pb.custom_shape.get(OWNER_KEY) == 'head_neck_visuals':
        from . import head_neck_visuals
        return 'IRIS' if pb.custom_shape.get(head_neck_visuals.ROLE_KEY) == 'NECK' else 'LILAC'
    role = str(bone.get('character_designer_limb_ik_role', '')).upper()
    torso_role = str(bone.get('character_designer_torso_role', '')).upper()
    foot_role = str(bone.get('character_designer_foot_role', '')).upper()
    if role == 'MASTER' or bone.get(OWNER_KEY) == 'root_control':
        return 'HONEY'
    if _hair(pb):
        return 'MAUVE'
    if rig.get('character_designer_skirt_owner'):
        return 'LILAC' if 'waist' in pb.name.lower() else 'MAUVE'
    if bone.get(OWNER_KEY) == 'torso_controls':
        return 'LILAC' if torso_role == 'BEND' else 'IRIS'
    if bone.get(OWNER_KEY) == 'spine_ik_fk':
        return 'IRIS' if 'shape' in pb.name.lower() else 'LILAC'
    side = bone.get('character_designer_limb_ik_side', bone.get('character_designer_foot_side', ''))
    if side not in {'L', 'R'}:
        tokens = re.split(r'[_.:\-\s]+', pb.name.upper())
        side = next((t[0] for t in reversed(tokens) if t in {'L', 'R', 'LEFT', 'RIGHT'}), '')
    detail = (role in {'POLE', 'POLE_LINE'} or 'TOE' in foot_role
              or any(t in pb.name.lower() for t in ('toe', 'finger', 'thumb', 'index', 'middle', 'ring', 'pinky', 'little')))
    if side == 'L':
        return 'SKY' if detail else 'MINT'
    if side == 'R':
        return 'PEACH' if detail else 'ROSE'
    return 'LILAC'


def _color_state(pb):
    color = pb.color
    return {'palette': color.palette, 'constraints': color.custom.show_colored_constraints,
            **{name: list(getattr(color.custom, name)) for name in ('normal', 'select', 'active')}}


def _set_color(pb, state):
    pb.color.palette = state['palette']
    pb.color.custom.show_colored_constraints = state.get('constraints', False)
    for name in ('normal', 'select', 'active'):
        setattr(pb.color.custom, name, state[name])


def capture_bone(pb):
    return {'color': _color_state(pb), 'backup': pb.get(BACKUP_KEY)}


def restore_bone_state(pb, state):
    _set_color(pb, state['color'])
    if state.get('backup') is None:
        if BACKUP_KEY in pb:
            del pb[BACKUP_KEY]
    else:
        pb[BACKUP_KEY] = state['backup']


def _saved_color(pb):
    try:
        state = json.loads(pb[BACKUP_KEY])
        if not isinstance(state, dict) or state.get('palette') not in {
            'DEFAULT', 'CUSTOM', *(f'THEME{i:02}' for i in range(1, 21))
        }:
            raise ValueError()
        for name in ('normal', 'select', 'active'):
            values = state[name]
            if len(values) != 3 or any(not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in values):
                raise ValueError()
        return state
    except (ValueError, TypeError, KeyError):
        raise ValueError(f"The saved color for '{pb.name}' is invalid; it was left unchanged.") from None


def restore_bone(pb):
    if BACKUP_KEY not in pb:
        return False
    _set_color(pb, _saved_color(pb))
    del pb[BACKUP_KEY]
    return True


def _enable_display(rig):
    if DISPLAY_KEY not in rig.data:
        rig.data[DISPLAY_KEY] = bool(rig.data.show_bone_colors)
    rig.data.show_bone_colors = True


def style(pb, *, force=False):
    """Use on newly created controls; explicit Apply may replace artist colors."""
    if not hasattr(pb, 'color'):
        return False
    if not force and (pb.id_data.get(ENABLED_KEY) == 0
                      or BACKUP_KEY in pb or pb.color.palette != 'DEFAULT'):
        return False
    if BACKUP_KEY not in pb:
        pb[BACKUP_KEY] = json.dumps(_color_state(pb))
    colors = PALETTES[palette_for(pb)]
    pb.color.palette = 'CUSTOM'
    pb.color.custom.show_colored_constraints = False
    for name, value in zip(('normal', 'select', 'active'), colors):
        setattr(pb.color.custom, name, value)
    _enable_display(pb.id_data)
    return True


def has_backup(rig):
    return bool(rig and rig.type == 'ARMATURE' and any(BACKUP_KEY in pb for pb in rig.pose.bones))


def _restore_display_if_unused(rig):
    # Armature data may be shared by several independently colored Pose objects.
    if DISPLAY_KEY in rig.data and not any(
        has_backup(obj) for obj in bpy.data.objects if obj.type == 'ARMATURE' and obj.data == rig.data
    ):
        rig.data.show_bone_colors = bool(rig.data[DISPLAY_KEY])
        del rig.data[DISPLAY_KEY]


def cleanup(rig):
    """Surviving source bones recover their old color when their shape is removed."""
    for pb in rig.pose.bones:
        if BACKUP_KEY in pb and not is_control(pb):
            restore_bone(pb)
    _restore_display_if_unused(rig)


def sync(rig):
    cleanup(rig)
    if rig.get(ENABLED_KEY) == 0:
        return 0
    return sum(style(pb) for pb in rig.pose.bones if is_control(pb, owned_only=True))


def apply(rig):
    targets = [pb for pb in rig.pose.bones if is_control(pb)]
    if not targets:
        raise ValueError('This armature has no bone controllers to color.')
    # Validate retained backups before changing any visible color.
    for pb in targets:
        if BACKUP_KEY in pb:
            _saved_color(pb)
    count = sum(style(pb, force=True) for pb in targets)
    rig[ENABLED_KEY] = True
    return count


def restore(rig):
    targets = [pb for pb in rig.pose.bones if BACKUP_KEY in pb]
    for pb in targets:
        _saved_color(pb)
    for pb in targets:
        restore_bone(pb)
    rig[ENABLED_KEY] = False
    _restore_display_if_unused(rig)
    return len(targets)


class CHARACTERDESIGNER_OT_control_colors(Operator):
    bl_idname = 'character_designer.control_colors'
    bl_label = 'Controller Colors'
    bl_description = 'Soft colors by side and function, with brighter selection; restore the colors saved before applying'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('APPLY', 'Apply Soft Colors', ''), ('RESTORE', 'Restore Colors', '')))

    @classmethod
    def poll(cls, context):
        obj = context.object
        return bool(obj and obj.type == 'ARMATURE' and obj.mode != 'EDIT'
                    and obj.is_editable and not obj.library and not obj.data.library)

    def execute(self, context):
        try:
            count = apply(context.object) if self.action == 'APPLY' else restore(context.object)
        except (ValueError, RuntimeError, TypeError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        for area in context.screen.areas if context.screen else ():
            area.tag_redraw()
        self.report({'INFO'}, f"{'Colored' if self.action == 'APPLY' else 'Restored'} {count} bone controllers.")
        return {'FINISHED'}


CONTROL_COLOR_CLASSES = (CHARACTERDESIGNER_OT_control_colors,)
