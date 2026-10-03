"""Persistent native Body/Hair/Dress display with direct native body posing."""
import json
from functools import lru_cache

import bpy
from bpy.props import EnumProperty
from . import bone_display as display, bone_collections as groups, limb_ik, limb_ik_fk as match
from . import control_pose_assets as poses
from . import skirt_original_mode as dress_pose

SESSION = 'character_designer_body_original_mode_v1'
DISPLAY_REFS = 'character_designer_original_display_refs_v1'
DISPLAY_OWNER = 'character_designer_original_display_owner_v1'
_CHANNELS = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')
_LOCKS = ('lock_rotation', 'lock_rotation_w', 'lock_rotations_4d')


def active(rig):
    return bool(rig and rig.type == 'ARMATURE' and SESSION in rig)


@lru_cache(maxsize=1)
def _native_records(raw):
    """Cache only immutable names by exact saved text, never Blender objects."""
    saved = json.loads(raw)
    records = saved.get('native_groups')
    if records is None:
        # Previous sessions did not capture Hair or Dress display transactions.
        records = {'BODY': {'0': saved['names']}, 'HAIR': {}, 'DRESS': {}}
    return tuple((role, tuple((key, tuple(names)) for key, names in targets.items()))
                 for role, targets in records.items())


def _native_groups(context, rig):
    if active(rig):
        refs = rig.get(DISPLAY_REFS, {})
        result = {}
        for role, targets in _native_records(rig[SESSION]):
            result[role] = {}
            for key, names in targets:
                target = rig if key == '0' else refs.get(key)
                if target is None:
                    raise ValueError('An Original display rig is missing; restore it before switching to Controls.')
                result[role][target] = set(names)
        return result
    original = rig.data.collections_all.get('Original')
    return {'BODY': {rig: set(original.bones.keys())} if original else {},
            'HAIR': display._native_targets(context, rig, 'HAIR'),
            'DRESS': display._native_targets(context, rig, 'DRESS')}


def _targets_visible(targets):
    if any(not display._object_visible(rig) for rig, names in targets.items() if names):
        return False
    bones = [(rig, rig.data.bones.get(name)) for rig, names in targets.items() for name in names]
    return bool(bones) and all(bone is not None
        and not getattr(rig.pose.bones[bone.name], 'hide', bone.hide)
        and (not bone.collections or any(c.is_visible_effectively for c in bone.collections))
        for rig, bone in bones)


def group_visible(context, rig, role):
    return _targets_visible(_native_groups(context, rig)[role])


def _remember_object_visibility(rig, targets):
    """Extend an older active session only when it explicitly reveals a rig."""
    if not active(rig):
        return
    raw = rig[SESSION]
    saved = json.loads(raw)
    refs = rig.get(DISPLAY_REFS, {})
    changed = False
    for target in targets:
        if target == rig:
            view = saved['display']
        else:
            key = next((key for key, obj in refs.items() if obj == target), None)
            if key is None or target.get(DISPLAY_OWNER) != rig:
                raise ValueError('An Original display rig was removed or reassigned; undo that edit before returning.')
            view = saved.get('extra_display', {}).get(key)
            if view is None:
                raise ValueError('The saved Original dress display is missing; recover its saved file.')
        display._check_object_restore(target, view)
        if 'object_visibility' not in view:
            view['object_visibility'] = display._object_snapshot(target)
            changed = True
    if changed:
        rig[SESSION] = json.dumps(saved, separators=(',', ':'))


def _set_native_view(context, rig, native_groups, role='ALL', *, toggle=False, complete=True):
    targets = {}
    for part in native_groups.values() if role == 'ALL' else (native_groups[role],):
        for target, names in part.items():
            targets.setdefault(target, set()).update(names)
    for target, names in targets.items():
        display._editable(target)
        if set(names) - set(target.data.bones.keys()):
            raise ValueError('Original display bones were removed; undo that edit before switching views.')
    visible = not _targets_visible(targets) if toggle else True
    checkpoint = display._checkpoint(targets)
    raw = rig.get(SESSION)
    try:
        if visible:
            _remember_object_visibility(rig, targets)
        for target, names in targets.items():
            if visible and names:
                display._reveal_object(target)
            if target.data.show_bone_custom_shapes:
                target.data.show_bone_custom_shapes = False
            if target.data.display_type != 'OCTAHEDRAL':
                target.data.display_type = 'OCTAHEDRAL'
            visible_collections = set()
            if visible:
                for name in names:
                    for collection in target.data.bones[name].collections:
                        while collection is not None:
                            visible_collections.add(collection.name)
                            collection = collection.parent
            if role == 'ALL':
                for collection in target.data.collections_all:
                    if collection.is_solo:
                        collection.is_solo = False
                    show = collection.name in visible_collections
                    if collection.is_visible != show:
                        collection.is_visible = show
                for bone in target.data.bones:
                    display._set_hidden(target, bone, bone.name not in names or not visible)
            elif visible:
                for collection in target.data.collections_all:
                    if collection.name in visible_collections and not collection.is_visible:
                        collection.is_visible = True
            for name in names:
                bone = target.data.bones[name]
                if role != 'ALL':
                    display._set_hidden(target, bone, not visible)
                if visible:
                    if bone.hide_select:
                        bone.hide_select = False
        if complete:
            display._completed(rig)
    except Exception:
        if raw is not None:
            rig[SESSION] = raw
        display._rollback(checkpoint)
        raise


def show_group(context, rig, role):
    if not active(rig):
        raise ValueError('Choose Original in Bone Display first.')
    _require(context, rig)
    ensure_dress_editable(context, rig)
    native_groups = _native_groups(context, rig)
    if not any(native_groups[role].values()):
        raise ValueError('This character has no original bones in that group.')
    _set_native_view(context, rig, native_groups, role, toggle=True)


def _extra_views(rig, saved):
    result = []
    refs = rig.get(DISPLAY_REFS, {})
    for key, view in saved.get('extra_display', {}).items():
        target = refs.get(key)
        if target is None or target.get(DISPLAY_OWNER) != rig:
            raise ValueError('An Original display rig was removed or reassigned; undo that edit before returning.')
        display._editable(target)
        if saved.get('extra_bones', {}).get(key) != sorted(target.data.bones.keys()):
            raise ValueError('The skirt skeleton changed in Original; undo that edit before returning.')
        result.append((target, view))
    return result


def _update(context, rig):
    match._update(context, rig)


def _pose(rig, names=None):
    if names is None:
        names = poses.native_rest(rig)
    return {name: rig.pose.bones[name].matrix.copy() for name in names}


def _channels(rig):
    from . import skirt_rig
    # The Body transfer restores its saved controls. Shared Dress channels remain
    # independent artist edits, as they were on the former separate armature.
    return {pb.name: {'mode': pb.rotation_mode,
                     'channels': {key: list(getattr(pb, key)) for key in _CHANNELS},
                     'ik_fk': pb.get('ik_fk')}
            for pb in rig.pose.bones if not pb.bone.get(skirt_rig.OWNER_KEY)}


def _restore_channels(rig, state):
    for name, entry in state.items():
        pb = rig.pose.bones[name]
        if pb.rotation_mode != entry['mode']:
            pb.rotation_mode = entry['mode']
        for key, values in entry['channels'].items():
            if tuple(getattr(pb, key)) != tuple(values):
                setattr(pb, key, values)
        if entry['ik_fk'] is not None and pb.get('ik_fk') != entry['ik_fk']:
            pb['ik_fk'] = entry['ik_fk']


def _locks(rig, names):
    return {name: {key: list(value) if hasattr(value, '__len__') else value
                   for key in _LOCKS for value in (getattr(rig.pose.bones[name], key),)}
            for name in names}


def _restore_locks(rig, state):
    for name, fields in state.items():
        for key, value in fields.items():
            current = getattr(rig.pose.bones[name], key)
            same = tuple(current) == tuple(value) if hasattr(current, '__len__') else current == value
            if not same:
                setattr(rig.pose.bones[name], key, value)


def _owned_sources(rig, names):
    """Resolve exact validated ownership records, never prefixes or proximity."""
    from . import torso_controls, spine_ik_fk, eye_controls, root_control, foot_controls
    inventory = limb_ik._validate_inventory(rig)
    if any(match.VERSION_KEY not in rig.pose.bones[entry['target'].name].bone for entry in inventory['rigs'].values()):
        raise ValueError('Update this legacy Body setup before using Original mode.')
    # The inventory just validated these exact constraints and all extensions.
    # Reuse that result inside this read-only preflight, without a lifetime cache.
    entries = {(pb.name, con.name): con for pb, con, _record in inventory['records']
               if pb.name in names}
    records = [module.get_record(rig) for module in (torso_controls, spine_ik_fk, eye_controls, root_control)]
    records.extend(foot_controls.records(rig).values())
    for record in records:
        for entry in record['constraints'] if record else ():
            if entry['owner'] in names:
                entries[(entry['owner'], entry['name'])] = rig.pose.bones[entry['owner']].constraints[entry['name']]
    return [{'bone': bone, 'name': name, 'type': con.type, 'mute': con.mute}
            for (bone, name), con in sorted(entries.items())]


def _resolve(rig, entries):
    result = []
    for entry in entries:
        pb = rig.pose.bones.get(entry['bone'])
        con = pb.constraints.get(entry['name']) if pb else None
        if con is None or con.type != entry['type']:
            raise ValueError('An Original mode constraint was changed or removed; undo that edit before returning.')
        result.append((con, entry))
    return result


def _verify(rig, desired):
    errors = {name: poses._difference(matrix, rig.pose.bones[name].matrix) for name, matrix in desired.items()}
    worst = max(errors, key=errors.get)
    if not all(limb_ik._matrix_is_finite(rig.pose.bones[name].matrix) for name in desired) or errors[worst] > 4e-4:
        raise ValueError(f'Original mode could not preserve {worst} ({errors[worst]:.4g}); the switch was rolled back.')
    return errors[worst]


def _bake_sources(context, rig, desired, entries):
    changed = {entry['bone'] for entry in entries}
    # convert_local_to_pose respects unusual inheritance flags and connected parents;
    # the PoseBone.matrix setter does not reliably reproduce these native frames.
    # Every native parent already has a captured target frame. Compute all local
    # bases from those frames before assignment, instead of reevaluating the whole
    # character (and bound meshes) after every single bone. Non-native parents use
    # the current evaluated frame; bounded extra passes handle those dependencies.
    bones = [rig.pose.bones[name] for name in desired]
    for _attempt in range(4):
        bases = []
        for pb in bones:
            if pb.name not in changed and any(not con.mute for con in pb.constraints):
                continue
            kwargs = {'parent_matrix': desired.get(pb.parent.name, pb.parent.matrix),
                      'parent_matrix_local': pb.parent.bone.matrix_local} if pb.parent else {}
            basis = pb.bone.convert_local_to_pose(desired[pb.name], pb.bone.matrix_local, invert=True, **kwargs)
            if poses._difference(pb.matrix_basis, basis) > 1e-7:
                bases.append((pb, basis))
        if not bases:
            break
        for pb, basis in bases:
            pb.matrix_basis = basis
        _update(context, rig)
        if max(poses._difference(rig.pose.bones[name].matrix, matrix) for name, matrix in desired.items()) < 3e-5:
            break


def _require(context, rig):
    display._editable(rig)
    if context.mode not in {'OBJECT', 'POSE'}:
        raise ValueError('Finish the current edit or weight session, then switch Original in Object or Pose Mode.')
    if rig.override_library or rig.name not in context.view_layer.objects:
        raise ValueError('Choose a local character rig in this view layer.')
    if context.scene.get('character_designer_weight_workspace_v1', {}).get('rig') == rig:
        raise ValueError('Finish Edit Weights before choosing Controls in Bone Display.')
    if active(rig):
        saved = json.loads(rig[SESSION])
        display._check_object_restore(rig, saved['display'])
        for target, view in _extra_views(rig, saved):
            display._check_object_restore(target, view)


def enter(context, rig):
    from . import forearm_twist
    with forearm_twist.defer_runtime(context, flush_on_exit=False) as refresh:
        return _enter(context, rig, refresh)


def _enter(context, rig, refresh):
    _require(context, rig)
    if active(rig):
        raise ValueError('This rig is already in Original mode.')
    original = rig.data.collections_all.get('Original')
    if original is None or original.get(groups.GROUP_KEY) != 'Original':
        raise ValueError('Organize this character\'s Bone Collections first.')
    names = set(original.bones.keys())
    if not names or any(rig.data.bones[name].get(limb_ik.OWNER_KEY) in limb_ik.GENERATED_CONTROL_OWNERS for name in names):
        raise ValueError('Original must contain the native character bones only.')
    entries = _owned_sources(rig, names)
    native_groups = _native_groups(context, rig)
    rigs = list(dict.fromkeys([rig] + [target for part in native_groups.values() for target in part]))
    for target in rigs:
        display._editable(target)
        owner = target.get(DISPLAY_OWNER)
        if owner and active(owner):
            raise ValueError('An attached dress is already in another Original display session.')
    if rig.animation_data:
        prefixes = tuple(rig.pose.bones[name].path_from_id() + '.' + prop for name in names for prop in _CHANNELS)
        if any(not curve.mute and curve.data_path.startswith(prefixes) for curve in rig.animation_data.drivers):
            raise ValueError('A native transform driver still controls Original; preserve or adjust that driver before direct posing.')
    _update(context, rig)
    dress_entries = dress_pose.prepare(context, rig)
    dress_checkpoint = dress_pose.checkpoint(context, rig, dress_entries)
    rest = poses.native_rest(rig)
    desired = _pose(rig, rest)
    before = _channels(rig)
    checkpoint = display._checkpoint(display._affected(context, rig))
    ctx = limb_ik._capture_context(context, rig)
    locks = _locks(rig, names)
    saved = None
    try:
        # Recover a legacy display-only isolation before starting this explicit session.
        display.restore_view(rig)
        saved = {'version': 1, 'rest': rest, 'bones': sorted(rig.data.bones.keys()),
                 'names': sorted(names), 'constraints': entries, 'channels': before, 'locks': locks,
                 'dress_edit': dress_entries,
                 'display': display._snapshot(rig),
                 'native_groups': {role: {str(rigs.index(target)): sorted(bones)
                                          for target, bones in part.items()}
                                   for role, part in native_groups.items()},
                 'extra_display': {str(i): display._snapshot(target) for i, target in enumerate(rigs) if i},
                 'extra_bones': {str(i): sorted(target.data.bones.keys()) for i, target in enumerate(rigs) if i},
                 'pose': {name: [list(row) for row in matrix] for name, matrix in desired.items()}}
        rig[SESSION] = json.dumps(saved, separators=(',', ':'))
        rig[DISPLAY_REFS] = {str(i): target for i, target in enumerate(rigs) if i}
        for target in rigs[1:]:
            target[DISPLAY_OWNER] = rig
        if context.mode != 'POSE' or context.view_layer.objects.active != rig:
            limb_ik._mode_set(context, rig, 'POSE')
        for con, entry in _resolve(rig, entries):
            con.mute = True
        _update(context, rig)
        _bake_sources(context, rig, desired, entries)
        _verify(rig, desired)
        dress_pose.enter(context, rig, dress_entries)
        saved['entered_channels'] = _channels(rig)
        rig[SESSION] = json.dumps(saved, separators=(',', ':'))
        _set_native_view(context, rig, native_groups, complete=False)
        for bone in rig.data.bones:
            if bone.name in names:
                if bone.hide_select:
                    bone.hide_select = False
                pb = rig.pose.bones[bone.name]
                if any(pb.lock_rotation):
                    pb.lock_rotation = (False, False, False)
                if pb.lock_rotation_w:
                    pb.lock_rotation_w = False
                if pb.lock_rotations_4d:
                    pb.lock_rotations_4d = False
        groups._FRAME_CACHE.pop(rig.as_pointer(), None)
        display._completed(rig, update=False)
        refresh()
        dress_pose.verify(context, rig, dress_entries)
    except Exception:
        for con, entry in _resolve(rig, entries):
            con.mute = entry['mute']
        _restore_channels(rig, before)
        _restore_locks(rig, locks)
        dress_pose.rollback(context, dress_checkpoint)
        display._rollback(checkpoint)
        for target in rigs[1:]:
            target.pop(DISPLAY_OWNER, None)
        rig.pop(DISPLAY_REFS, None)
        rig.pop(SESSION, None)
        limb_ik._restore_context(context, rig, ctx)
        _update(context, rig)
        refresh()
        raise
    return {'bones': len(names), 'constraints': len(entries)}


def ensure_dress_editable(context, rig):
    """Explicitly upgrade a saved display-only Original session, never on draw."""
    _require(context, rig)
    if not active(rig):
        return False
    raw = rig[SESSION]
    saved = json.loads(raw)
    if 'dress_edit' in saved:
        dress_pose.validate_active(context, rig, saved['dress_edit'])
        return False
    _update(context, rig)
    entries = dress_pose.prepare(context, rig)
    checkpoint = dress_pose.checkpoint(context, rig, entries)
    try:
        dress_pose.enter(context, rig, entries)
        saved['dress_edit'] = entries
        rig[SESSION] = json.dumps(saved, separators=(',', ':'))
        display._completed(rig, update=False)
    except Exception:
        dress_pose.rollback(context, checkpoint)
        rig[SESSION] = raw
        raise
    return True


def leave(context, rig):
    from . import forearm_twist
    with forearm_twist.defer_runtime(context, flush_on_exit=False) as refresh:
        return _leave(context, rig, refresh)


def _leave(context, rig, refresh):
    _require(context, rig)
    if not active(rig):
        return False
    ensure_dress_editable(context, rig)
    saved = json.loads(rig[SESSION])
    rest = poses.native_rest(rig)
    if saved['bones'] != sorted(rig.data.bones.keys()) or saved['rest'] != rest:
        raise ValueError('The skeleton was structurally edited in Original mode; undo that edit before returning.')
    relations = _resolve(rig, saved['constraints'])
    extra_views = _extra_views(rig, saved)
    _update(context, rig)
    desired = _pose(rig, rest)
    current_channels = _channels(rig)
    dress_desired = dress_pose.capture(context, rig, saved['dress_edit'])
    dress_checkpoint = dress_pose.checkpoint(context, rig, saved['dress_edit'])
    edited = any(current_channels[name] != saved['entered_channels'][name] for name in desired)
    checkpoint = (current_channels, display._snapshot(rig), _locks(rig, saved['names']),
                  [(con, con.mute) for con, _entry in relations])
    extra_checkpoint = [(target, display._snapshot(target)) for target, _view in extra_views]
    try:
        _restore_channels(rig, saved['channels'])
        _restore_locks(rig, saved['locks'])
        for con, entry in relations:
            con.mute = entry['mute']
        _update(context, rig)
        limb_ik._validate_inventory(rig)
        changed = {name for name in desired if poses._difference(desired[name], rig.pose.bones[name].matrix) > 1e-7}
        if changed and edited:
            poses._match(context, rig, desired, changed, preserve_modes=True)
            _update(context, rig)
        _verify(rig, desired)
        dress_pose.leave(context, rig, saved['dress_edit'], desired=dress_desired)
        display._restore(rig, saved['display'])
        for target, view in extra_views:
            display._restore(target, view)
        rig.pop(SESSION)
        groups._FRAME_CACHE.pop(rig.as_pointer(), None)
        groups._frame_visibility(context.scene, objects=(rig,))
        display._completed(rig, update=False)
        refresh()
        dress_pose.verify(context, rig, saved['dress_edit'], desired=dress_desired)
        for target, _view in extra_views:
            target.pop(DISPLAY_OWNER, None)
        rig.pop(DISPLAY_REFS, None)
    except Exception:
        channels, view, locks, mutes = checkpoint
        _restore_channels(rig, channels)
        _restore_locks(rig, locks)
        for con, muted in mutes:
            con.mute = muted
        dress_pose.rollback(context, dress_checkpoint)
        display._restore(rig, view)
        for target, extra_view in extra_checkpoint:
            display._restore(target, extra_view)
        rig[SESSION] = json.dumps(saved, separators=(',', ':'))
        _update(context, rig)
        refresh()
        raise
    return True


class CHARACTERDESIGNER_OT_body_original_mode(bpy.types.Operator):
    bl_idname = 'character_designer.body_original_mode'
    bl_label = 'Original'
    bl_description = 'Switch Body, Hair and Dress between original bones and saved controls while preserving pose and Dress corrections'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('TOGGLE', 'Toggle', ''),
                               ('ORIGINAL', 'Original', 'Show native Body, Hair and Dress bones'),
                               ('CONTROLS', 'Controls', 'Return to the saved controls and preserve the pose')),
                         default='TOGGLE')

    @classmethod
    def poll(cls, context):
        try:
            return display.character_rig(context) is not None and context.mode in {'OBJECT', 'POSE'}
        except (ValueError, ReferenceError):
            return False

    def execute(self, context):
        try:
            rig = display.character_rig(context)
            running = active(rig)
            requested = self.action == 'ORIGINAL' or (self.action == 'TOGGLE' and not running)
            if requested:
                if not running:
                    enter(context, rig)
                else:
                    ensure_dress_editable(context, rig)
                self.report({'INFO'}, 'Original bones shown. Choose Controls in Bone Display to return.')
            elif running:
                leave(context, rig)
                self.report({'INFO'}, 'Controls restored; current pose kept.')
            elif display.view_mode(rig):
                display.restore_view(rig)
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def draw(layout, context):
    rig = display.character_rig(context)
    if rig is None:
        return False
    running = active(rig)
    native = running or display.view_mode(rig) is not None
    row = layout.row(align=True)
    row.enabled = context.mode in {'OBJECT', 'POSE'}
    original = row.row(align=True)
    original.enabled = running or bool(rig.data.collections_all.get('Original'))
    original.operator('character_designer.body_original_mode', text='Original',
                      depress=native).action = 'ORIGINAL'
    controls = row.row(align=True)
    controls.enabled = running or bool(groups.body_collection(rig)) or display.view_mode(rig) is not None
    controls.operator('character_designer.body_original_mode', text='Controls',
                      depress=not native).action = 'CONTROLS'
    return running


class CHARACTERDESIGNER_OT_clear_dress_corrections(bpy.types.Operator):
    bl_idname = 'character_designer.clear_dress_corrections'
    bl_label = 'Clear Dress Corrections'
    bl_description = 'Remove local Dress original pose corrections while retaining curve and physics controls'
    bl_options = {'REGISTER', 'UNDO'}
    scope: EnumProperty(items=(('SELECTED', 'Selected Bones', 'Clear selected Dress original bones'),
                               ('ALL', 'All Dress Bones', 'Clear all Dress local corrections')),
                        default='SELECTED')

    @classmethod
    def poll(cls, context):
        try:
            rig = display.character_rig(context)
            return rig is not None and not active(rig) and context.mode in {'OBJECT', 'POSE'}
        except (ValueError, ReferenceError):
            return False

    def execute(self, context):
        try:
            rig = display.character_rig(context)
            count = dress_pose.clear(context, rig, selected_only=self.scope == 'SELECTED')
            display._completed(rig, update=False)
            self.report({'INFO'}, f'Cleared {count} Dress local corrections.')
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


BODY_ORIGINAL_CLASSES = (CHARACTERDESIGNER_OT_body_original_mode,
                         CHARACTERDESIGNER_OT_clear_dress_corrections)
