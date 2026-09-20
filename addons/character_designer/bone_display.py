"""Character display presets; real weighting bones without changing the rig."""
import json
import bpy
from bpy.props import EnumProperty
from bpy.types import Operator, Panel
from . import bone_collections as groups, character_setup, foot_controls, hair_bones_rig as hair, skirt_rig
from .ui_constants import SIDEBAR_CATEGORY, active_ui_page, UI_PAGE_RIG, UI_PAGE_WEIGHT

VIEW_KEY = 'character_designer_bone_display_view_v1'
REFS_KEY = 'character_designer_bone_display_refs_v1'


def _completed(main):
    from . import bone_display_sync
    bpy.context.view_layer.update()
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {'VIEW_3D', 'PROPERTIES'}:
                area.tag_redraw()
    bone_display_sync.completed(main)


def _skirt_source(rig):
    source = rig.get(skirt_rig.SOURCE_KEY)
    if source is None or source.get(skirt_rig.RIG_KEY) != rig:
        raise ValueError('The dress rig has lost its source mesh reference.')
    return source


def character_rig(context):
    obj = context.object
    rig = obj if obj and obj.type == 'ARMATURE' else None
    if obj and obj.type == 'MESH':
        candidates = {m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object}
        if len(candidates) == 1:
            rig = candidates.pop()
        elif len(candidates) > 1:
            raise ValueError('Select the character armature; this mesh uses multiple rigs.')
    if rig and rig.get(skirt_rig.OWNER_KEY):
        attachment = skirt_rig.attachment_status(_skirt_source(rig))
        if attachment['attached']:
            rig = attachment['character']
    return rig or character_setup.preferred_rig(context)


def _editable(rig):
    if (rig is None or rig.type != 'ARMATURE' or rig.mode == 'EDIT'
            or rig.library or rig.data.library or not rig.is_editable or rig.data.users != 1):
        raise ValueError('Choose a local, single-user character armature outside Edit Mode.')


def dress_rigs(context, main):
    result = []
    for rig in context.scene.objects:
        if rig.type != 'ARMATURE' or not rig.get(skirt_rig.OWNER_KEY):
            continue
        if rig != main and rig.parent != main:
            continue
        source = _skirt_source(rig)
        attachment = skirt_rig.attachment_status(source)
        if rig == main or (attachment['attached'] and attachment['character'] == main):
            record = skirt_rig.read_record(source)
            if rig.get(skirt_rig.OWNER_KEY) != record['owner'] or rig.data.get(skirt_rig.OWNER_KEY) != record['owner']:
                raise ValueError('The dress rig ownership does not match its source.')
            result.append((rig, record))
    return result


def _affected(context, main):
    previous = list(main.data.get(REFS_KEY, {}).values()) if VIEW_KEY in main.data else []
    return list(dict.fromkeys([main] + [rig for rig, _record in dress_rigs(context, main)] + previous))


def _snapshot(rig):
    return {'shapes': rig.data.show_bone_custom_shapes, 'display': rig.data.display_type,
            'collections': {c.name: [c.is_visible, c.is_solo] for c in rig.data.collections_all},
            'hidden': {b.name: [b.hide, b.hide_select] for b in rig.data.bones},
            'pose_hidden': {pb.name: pb.hide for pb in rig.pose.bones if hasattr(pb, 'hide')}}


def _set_hidden(rig, bone, hidden):
    bone.hide = hidden
    # Recent Blender versions store Object/Pose visibility on the pose channel;
    # Bone.hide alone no longer controls the displayed bone outside Edit Mode.
    pose_bone = rig.pose.bones.get(bone.name)
    if pose_bone is not None and hasattr(pose_bone, 'hide'):
        pose_bone.hide = hidden


def _restore(rig, saved):
    rig.data.show_bone_custom_shapes = saved['shapes']
    rig.data.display_type = saved['display']
    for name, flags in saved['collections'].items():
        collection = rig.data.collections_all.get(name)
        if collection is not None:
            collection.is_visible, collection.is_solo = flags
    for name, flags in saved['hidden'].items():
        bone = rig.data.bones.get(name)
        if bone is not None:
            bone.hide, bone.hide_select = flags
    # Older saved views did not change or record the separate pose flags.
    for name, hidden in saved.get('pose_hidden', {}).items():
        pose_bone = rig.pose.bones.get(name)
        if pose_bone is not None and hasattr(pose_bone, 'hide'):
            pose_bone.hide = hidden


def _checkpoint(rigs):
    return [(rig, _snapshot(rig), rig.data.get(VIEW_KEY), dict(rig.data.get(REFS_KEY, {}))) for rig in rigs]


def _rollback(checkpoint):
    for rig, state, view, refs in checkpoint:
        _restore(rig, state)
        if view is None:
            rig.data.pop(VIEW_KEY, None)
            rig.data.pop(REFS_KEY, None)
        else:
            rig.data[VIEW_KEY] = view
            rig.data[REFS_KEY] = refs
    groups._FRAME_CACHE.clear()


def view_mode(main):
    if not main or VIEW_KEY not in main.data:
        return None
    try:
        return json.loads(main.data[VIEW_KEY])['mode']
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError('The saved bone display view is invalid; recover its saved file.') from exc


def restore_view(main):
    """Restore display only; artist weight and pose edits made in this view survive."""
    _editable(main)
    if VIEW_KEY not in main.data:
        return False
    saved = json.loads(main.data[VIEW_KEY])
    references = main.data.get(REFS_KEY, {})
    targets = []
    for key, state in saved['rigs'].items():
        rig = references.get(key)
        if rig is None:
            raise ValueError('A rig used by the saved display view was removed; restore it first.')
        _editable(rig)
        targets.append((rig, state))
    before = [(rig, _snapshot(rig)) for rig, _state in targets]
    try:
        for rig, state in targets:
            _restore(rig, state)
    except Exception:
        for rig, state in before:
            _restore(rig, state)
        raise
    for rig, _state in targets:
        rig.data.pop(VIEW_KEY, None)
        rig.data.pop(REFS_KEY, None)
    # Refresh Body membership to the current keyed IK/FK mode after leaving isolation.
    groups._FRAME_CACHE.clear()
    groups._frame_visibility(bpy.context.scene)
    for rig, _state in targets:
        _completed(rig)
    return True


def _native_targets(context, main, mode):
    if mode == 'DRESS':
        result = {}
        for rig, record in dress_rigs(context, main):
            _controls, deform, _mechanism = skirt_rig._bone_collection_layout(record)
            names = deform | {record['controls']['waist']}
            result[rig] = {name for name in names if rig.data.bones[name].use_deform}
        return result
    if main.get(skirt_rig.OWNER_KEY):
        return {}
    if mode == 'HAIR':
        names = {b.name for _rig, c in _control_collections(context, main, 'HAIR') for b in c.bones}
        return {main: {name for name in names if main.data.bones[name].use_deform}}
    original = main.data.collections_all.get('Original')
    if original is None or original.get(groups.GROUP_KEY) != 'Original':
        raise ValueError('Organize Bone Collections before opening the Original view.')
    return {main: set(original.bones.keys())}


def show_native(context, main, mode):
    if mode not in {'ORIGINAL', 'HAIR', 'DRESS'}:
        raise ValueError('Choose Original, Hair Bones or Dress Bones.')
    _editable(main)
    targets = _native_targets(context, main, mode)
    if not any(targets.values()):
        raise ValueError('This character has no bound bones in that group yet.')
    rigs = _affected(context, main)
    for rig in rigs:
        _editable(rig)
    checkpoint = _checkpoint(rigs)
    try:
        restore_view(main)
        snapshots = {str(i): _snapshot(rig) for i, rig in enumerate(rigs)}
        for rig in rigs:
            names = targets.get(rig, set())
            rig.data.show_bone_custom_shapes = False
            rig.data.display_type = 'OCTAHEDRAL'
            # Bone hide flags isolate the exact targets even when a bone belongs
            # to several collections. All unrelated mechanisms stay hidden.
            for collection in rig.data.collections_all:
                collection.is_solo = False
                collection.is_visible = False
            # Show only target groups, with ancestors where required. The
            # collection eyes now describe the same view as the bone flags.
            if names:
                target_groups = ({rig.data.collections_all.get('Original')} if mode == 'ORIGINAL'
                                 else {c for c in rig.data.collections_all if any(b.name in names for b in c.bones)})
                for collection in target_groups:
                    while collection is not None:
                        collection.is_visible = True
                        collection = collection.parent
            for bone in rig.data.bones:
                _set_hidden(rig, bone, bone.name not in names)
                if bone.name in names:
                    bone.hide_select = False
        payload = json.dumps({'version': 1, 'mode': mode, 'rigs': snapshots}, separators=(',', ':'))
        refs = {str(i): rig for i, rig in enumerate(rigs)}
        # Mark every affected armature so a frame change or rig lifecycle cannot
        # overwrite this temporary visibility, including independent dress rigs.
        for rig in rigs:
            rig.data[VIEW_KEY] = payload
            rig.data[REFS_KEY] = refs
    except Exception:
        _rollback(checkpoint)
        raise
    _completed(main)


def _control_collections(context, main, role):
    if role == 'BODY':
        collection = groups.body_collection(main)
        return [(main, collection)] if collection else []
    if role == 'HAIR':
        return [(main, c) for c in main.data.collections_all
                if c.get(hair.OWNER_KEY) == hair.OWNER_VALUE or c.get(groups.GROUP_KEY) == 'Hair']
    result = []
    for rig, record in dress_rigs(context, main):
        result.extend((rig, c) for c in rig.data.collections_all if c.get(skirt_rig.OWNER_KEY) == record['owner'])
    return result


def _hide_registered_foot_mechanisms(rig):
    # A helper can also belong to an artist-visible collection. Its ownership
    # record, rather than the collection name or bone prefix, identifies it.
    roles = {'HEEL_PIVOT', 'TOE_TIP_PIVOT', 'BALL_PIVOT', 'ANKLE_SOLVER',
             'IK_TOE_REF', 'FK_TOE_REF', 'TOE_SPACE'}
    for side, record in foot_controls.records(rig).items():
        if side not in {'L', 'R'} or record.get('side') != side or not record.get('id'):
            continue
        for role in roles:
            bone = rig.data.bones.get(record.get('bones', {}).get(role, ''))
            if (bone is not None and not bone.use_deform
                    and bone.get(foot_controls.OWNER_KEY) == foot_controls.OWNER_VALUE
                    and bone.get(foot_controls.ID_KEY) == record.get('id')
                    and bone.get(foot_controls.ROLE_KEY) == role
                    and bone.get(foot_controls.SIDE_KEY) == side):
                _set_hidden(rig, bone, True)


def _hide_registered_wrist_mechanisms(rig):
    from . import limb_ik
    for _owner, _constraint, record in limb_ik._owned_constraint_records(rig):
        if record.get('rotation_space') != 'PARENT_DELTA':
            continue
        target = rig.data.bones.get(record.get('target', ''))
        if target is None:
            continue
        for bone in target.children:
            if (not bone.use_deform and limb_ik._owned(
                    bone, target.get(limb_ik.ARMATURE_ID_KEY),
                    role='HAND_ROTATION', rig_id=target.get(limb_ik.RIG_ID_KEY))):
                _set_hidden(rig, bone, True)


def show_controls(context, main, role='ALL', *, toggle=False):
    _editable(main)
    roles = ('BODY', 'HAIR', 'DRESS') if role == 'ALL' else (role,)
    collections = [pair for part in roles for pair in _control_collections(context, main, part)]
    if not collections:
        raise ValueError('Organize or create the controls in this group first.')
    rigs = _affected(context, main)
    for rig in rigs:
        _editable(rig)
    checkpoint = _checkpoint(rigs)
    was_native_view = view_mode(main) is not None
    try:
        restore_view(main)
        visible = not all(c.is_visible for _rig, c in collections) if toggle and not was_native_view else True
        for rig in rigs:
            for collection in rig.data.collections_all:
                collection.is_solo = False
                if collection.get(groups.GROUP_KEY) in {'Original', '_Internal', '_Other'}:
                    collection.is_visible = False
        for rig, collection in collections:
            rig.data.show_bone_custom_shapes = True
            collection.is_visible = visible
            if visible:
                if rig.get(skirt_rig.OWNER_KEY):
                    record = skirt_rig.read_record(_skirt_source(rig))
                    names, _deform, _mechanism = skirt_rig._bone_collection_layout(record)
                else:
                    names = set(collection.bones.keys())
                for name in names:
                    _set_hidden(rig, rig.data.bones[name], False)
        for rig in rigs:
            _hide_registered_foot_mechanisms(rig)
            _hide_registered_wrist_mechanisms(rig)
    except Exception:
        _rollback(checkpoint)
        raise
    _completed(main)


class CHARACTERDESIGNER_OT_bone_display(Operator):
    bl_idname = 'character_designer.bone_display'
    bl_label = 'Bone Display'
    bl_description = 'Change bone display without changing poses, bindings or custom shape assignments'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('ALL', 'Show All Controls', ''), ('BODY', 'Body', ''),
        ('HAIR', 'Hair', ''), ('DRESS', 'Dress', ''), ('ORIGINAL', 'Original', ''),
        ('HAIR_BONES', 'Hair Bones', 'Show the real weighted hair bones'),
        ('DRESS_BONES', 'Dress Bones', 'Show the real weighted dress bones'),
        ('RESTORE', 'Restore Display', 'Return to the display before the bone view')))

    def execute(self, context):
        try:
            main = character_rig(context)
            if self.action == 'RESTORE':
                restore_view(main)
            elif self.action in {'ORIGINAL', 'HAIR_BONES', 'DRESS_BONES'}:
                show_native(context, main, self.action.removesuffix('_BONES'))
            else:
                show_controls(context, main, self.action, toggle=self.action != 'ALL')
            for area in context.screen.areas:
                area.tag_redraw()
            return {'FINISHED'}
        except (ValueError, RuntimeError, TypeError, KeyError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_PT_bone_display(Panel):
    bl_idname = 'CHARACTERDESIGNER_PT_bone_display'
    bl_label = 'Bone Display'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = SIDEBAR_CATEGORY
    bl_parent_id = 'CHARACTERDESIGNER_PT_main'
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) in {UI_PAGE_RIG, UI_PAGE_WEIGHT}

    def draw(self, context):
        layout = self.layout
        try:
            main = character_rig(context)
            if main is None:
                layout.label(text='Select the character or set Main Rig.', icon='INFO')
                return
            mode = view_mode(main)
            from . import bone_display_sync
            error = bone_display_sync.last_error(main)
            if error:
                layout.label(text=error, icon='ERROR')
            layout.operator('character_designer.bone_display', text='Show All Controls', icon='HIDE_OFF').action = 'ALL'
            for role, label in (('BODY', 'Body'), ('HAIR', 'Hair'), ('DRESS', 'Dress')):
                row = layout.row(align=True)
                members = _control_collections(context, main, role)
                row.enabled = bool(members)
                visible = bool(members) and all(c.is_visible for _rig, c in members) and mode is None
                row.operator('character_designer.bone_display', text=label,
                    icon='HIDE_OFF' if visible else 'HIDE_ON', depress=visible).action = role
                if role != 'BODY':
                    row.operator('character_designer.bone_display', text='Bones', icon='BONE_DATA',
                        depress=mode == role).action = role + '_BONES'
            row = layout.row()
            row.enabled = bool(main.data.collections_all.get('Original'))
            row.operator('character_designer.bone_display', text='Original · Native Bones',
                icon='ARMATURE_DATA', depress=mode == 'ORIGINAL').action = 'ORIGINAL'
            if mode:
                layout.operator('character_designer.bone_display', text='Restore Display', icon='LOOP_BACK').action = 'RESTORE'
            elif main == context.object and main.mode != 'EDIT':
                layout.operator('character_designer.simplify_bone_collections', text='Organize Bone Collections', icon='GROUP_BONE')
        except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as exc:
            layout.label(text=str(exc), icon='ERROR')


BONE_DISPLAY_CLASSES = (CHARACTERDESIGNER_OT_bone_display, CHARACTERDESIGNER_PT_bone_display)
