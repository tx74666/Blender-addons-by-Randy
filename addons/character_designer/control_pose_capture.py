"""Save one internal native Pose asset from the evaluated Body pose.

Capture is read-only for the artist rig: no mode switches, temporary keying,
constraint changes, Action assignment, or alterations to an Original session.
Generated controls resolve through the validated Body inventory. The resulting
Action contains native transform channels, not generated controller names.
"""

import json
import math

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from mathutils import Matrix

from . import control_pose_assets as poses, limb_ik, limb_ik_fk

METADATA = 'character_designer_pose_asset_v1'
_SCOPES = {'SELECTED', 'ALL_BODY'}


def _require(context, rig):
    from . import bone_display, forearm_twist
    bone_display._editable(rig)
    if context.mode not in {'OBJECT', 'POSE'} or rig.override_library:
        raise ValueError('Save a Pose on a local character in Object or Pose Mode.')
    if rig.name not in context.view_layer.objects:
        raise ValueError('The character must be in this view layer.')
    if context.scene.get('character_designer_weight_workspace_v1', {}).get('rig') == rig:
        raise ValueError('Finish Edit Weights before saving a Pose.')
    preview = forearm_twist._SESSION
    if preview is not None and preview.get('armature') == rig:
        raise ValueError('Confirm or cancel the Forearm preview before saving a Pose.')


def _body_names(rig, inventory):
    """Reuse the authored Body/Hair boundary used by Bone Collections."""
    from . import bone_collections, hair_bones_rig as hair
    hair_names = {bone.name for bone in rig.data.bones
                  if bone.get(hair.OWNER_KEY) == hair.OWNER_VALUE}
    group = rig.data.collections_all.get('Hair')
    stack = [group] if group else []
    while stack:
        group = stack.pop()
        hair_names.update(group.bones.keys())
        stack.extend(group.children)
    generated = {bone.name for bone in rig.data.bones
                 if bone.get(limb_ik.OWNER_KEY) in limb_ik.GENERATED_CONTROL_OWNERS}
    native = bone_collections._native_body_names(rig, generated, hair_names)
    original = rig.data.collections_all.get('Original')
    if original is None or original.get(bone_collections.GROUP_KEY) != 'Original':
        raise ValueError('Update or organize this Character Designer Body setup before saving a Pose.')
    return native.intersection(original.bones.keys())


def _finger_names(rig, inventory):
    """Native fingers retain their exact decorated source identity and side."""
    registry = inventory.get('source_widgets') or {}
    result = {name: entry['side'] for name, entry in registry.get('bones', {}).items()
              if entry['kind'] == 'FINGER'}
    # A user may deliberately omit custom shapes. Reuse the generator's native
    # hand-descendant proof; this never maps an unregistered generated control.
    chains = [limb_ik.LimbChain(key[0], key[1], *entry['chain'])
              for key, entry in inventory['rigs'].items() if key[0] == 'ARM']
    result.update(limb_ik._source_widget_candidates(rig, chains)['FINGER'])
    return result


def _source_maps(rig, inventory, body):
    """Map only public, validated controls; mechanisms intentionally stay out."""
    from . import torso_controls, spine_ik_fk, eye_controls, root_control
    mapping, arms = {}, {}
    for key, entry in inventory['rigs'].items():
        names = set(limb_ik_fk.pose_names(entry))
        for name in names:
            mapping[name] = names
        for field in ('target', 'pole', 'heel'):
            bone = entry.get(field)
            if bone is not None:
                mapping[bone.name] = names
        foot = entry.get('foot_controls')
        if foot:
            for field in ('roll', 'toe_control'):
                name = foot.get(field)
                if name:
                    mapping[name] = names
        if key[0] == 'ARM':
            arms[key[1]] = names
    torso = torso_controls.get_record(rig)
    if torso:
        for source, control in torso['controls'].items():
            mapping[control] = {source}
        mapping[torso['bend']] = set(torso['sources'])
    spine = spine_ik_fk.get_record(rig)
    if spine:
        for name in (spine['chest'], spine['shape']):
            mapping[name] = set(spine['sources'])
    eyes = eye_controls.get_record(rig)
    if eyes:
        mapping[eyes['master']] = set(eyes['sources'])
        for source, side in zip(eyes['sources'], ('L', 'R')):
            mapping[eyes['targets'][side]] = {source}
    root = root_control.get_record(rig)
    master = root['master'] if root else inventory.get('master')
    master = master if isinstance(master, str) else master.name if master else None
    if master:
        mapping[master] = set(body)
    return mapping, arms


def _resolve_names(rig, inventory, scope, include_fingers):
    body = _body_names(rig, inventory)
    fingers = _finger_names(rig, inventory)
    if scope == 'ALL_BODY':
        names = set(body)
    else:
        selected = {pb.name for pb in rig.pose.bones
                    if (pb if hasattr(pb, 'select') else pb.bone).select}
        if not selected:
            raise ValueError('Select native Body bones or Body controls, or choose All Body.')
        mapping, arms = _source_maps(rig, inventory, body)
        names = set()
        for name in sorted(selected):
            if name in mapping:
                names.update(mapping[name])
            elif name in body:
                names.add(name)
            else:
                raise ValueError(f'{name}: select a native Body bone or its public control; Hair, Dress and mechanisms are not supported.')
        if include_fingers:
            sides = {side for side, chain in arms.items() if names.intersection(chain)}
            names.update(name for name, side in fingers.items() if side in sides)
    if not include_fingers:
        names.difference_update(fingers)
    if not names:
        raise ValueError('This selection has no supported Body bones; enable Include Fingers for a hand gesture.')
    if names - body:
        raise ValueError('The selected controls do not resolve to this character\'s native Body bones.')
    return sorted(names)


def _owned_constraints(rig, inventory):
    from . import torso_controls, spine_ik_fk, eye_controls, root_control, foot_controls
    result = {(pb.name, con.name) for pb, con, _record in inventory['records']}
    records = [module.get_record(rig) for module in
               (torso_controls, spine_ik_fk, eye_controls, root_control)]
    records.extend(foot_controls.records(rig).values())
    for record in records:
        if record:
            result.update((entry['owner'], entry['name']) for entry in record['constraints'])
    return result


def _validate_channels(rig, names, inventory):
    owned = _owned_constraints(rig, inventory)
    for name in names:
        pb = rig.pose.bones[name]
        if any((name, con.name) not in owned for con in pb.constraints):
            raise ValueError(f'{name}: a non-Body constraint cannot be preserved in this Pose.')
    if rig.animation_data:
        paths = {rig.pose.bones[name].path_from_id(prop) for name in names
                 for prop in poses._FIELDS}
        if any(curve.data_path in paths for curve in rig.animation_data.drivers):
            raise ValueError('A driver controls the selected native Pose channels; preserve that setup before saving.')


def _modes(rig, inventory):
    from . import body_original_mode, spine_ik_fk, root_control
    result = {'BODY': {'mode': 'ORIGINAL' if body_original_mode.active(rig) else 'CONTROLS', 'value': None}}
    for key, entry in sorted(inventory['rigs'].items()):
        value = float(rig.pose.bones[entry['target'].name].get(limb_ik_fk.PROPERTY, 1.0))
        result['/'.join(key)] = {'mode': limb_ik_fk.mode_for_rig(rig, entry), 'value': value}
    spine = spine_ik_fk.get_record(rig)
    if spine:
        result['SPINE'] = {'mode': spine_ik_fk.mode_for_rig(rig),
                           'value': float(rig.pose.bones[spine['chest']].get(spine_ik_fk.PROPERTY, 0.0))}
    root = root_control.get_record(rig)
    if root:
        result['ROOT'] = {'mode': 'ROOT', 'value': float(rig.pose.bones[root['master']].get(root_control.SCALE_PROPERTY, 1.0))}
    return result


def _capture_fields(rig, evaluated, names):
    matrices = {pb.name: pb.matrix.copy() for pb in evaluated.pose.bones}
    result = {}
    for name in names:
        pb, bone = rig.pose.bones[name], rig.data.bones[name]
        matrix = matrices[name]
        parent = matrices[bone.parent.name] if bone.parent else Matrix.Identity(4)
        if not limb_ik._matrix_is_finite(matrix) or not limb_ik._matrix_is_finite(parent):
            raise ValueError(f'{name}: the evaluated Pose contains a non-finite transform.')
        basis = poses._convert(bone, matrix, parent, invert=True)
        location, rotation, scale = basis.decompose()
        rebuilt = Matrix.LocRotScale(location, rotation, scale)
        tolerance = 2e-6 * max(1.0, max(abs(value) for row in basis for value in row))
        if (not limb_ik._matrix_is_finite(basis) or min(abs(value) for value in scale) < 1e-8
                or poses._difference(basis, rebuilt) > tolerance
                or poses._difference(matrix, poses._convert(bone, rebuilt, parent)) > tolerance):
            raise ValueError(f'{name}: this evaluated Pose has shear or a singular transform; it cannot be saved exactly as native Pose channels.')
        # Complete quaternions make the Action independent of later changes to
        # the destination Euler order or PoseBone.rotation_mode.
        fields = {'location': tuple(location), 'rotation_quaternion': tuple(rotation.normalized()), 'scale': tuple(scale)}
        source = evaluated.pose.bones[name]
        for prop, count in poses._BBONE.items():
            value = getattr(source, prop)
            fields[prop] = (float(value),) if count == 1 else tuple(value)
        if any(not math.isfinite(value) for values in fields.values() for value in values):
            raise ValueError(f'{name}: the Pose contains non-finite channels.')
        result[name] = fields
    return result


def save_pose(context, rig, name, *, scope='SELECTED', include_fingers=False):
    """Return a new local Action asset without assigning it to artist animation."""
    from . import forearm_original_inventory
    _require(context, rig)
    if scope not in _SCOPES:
        raise ValueError('Choose Selected Region or All Body.')
    name = name.strip() if isinstance(name, str) else ''
    if not name:
        raise ValueError('Give the Pose a name.')
    inventory = forearm_original_inventory.validate(rig)
    if not inventory['rigs']:
        raise ValueError('Generate Character Designer Body controls before saving this Pose.')
    names = _resolve_names(rig, inventory, scope, include_fingers)
    _validate_channels(rig, names, inventory)
    # This merely finishes pending native evaluation; capture never rewrites
    # matrix_basis or an artist controller, even with Auto Key enabled.
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    depsgraph.update()
    evaluated = rig.evaluated_get(depsgraph)
    fields = _capture_fields(rig, evaluated, names)
    rest = poses.native_rest(rig)
    metadata = {'version': 1, 'source_object': rig.name, 'source_slot': 'OB' + rig.name,
                'rest': rest, 'scope': scope, 'names': names,
                'include_fingers': bool(include_fingers), 'control_modes': _modes(rig, inventory)}
    action = bpy.data.actions.new(name)
    try:
        slot = action.slots.new(id_type='OBJECT', name=rig.name)
        metadata['source_slot'] = slot.identifier
        bag = action.layers.new(name='Pose').strips.new(type='KEYFRAME').channelbags.new(slot)
        for bone_name, properties in fields.items():
            for prop, values in properties.items():
                path = rig.pose.bones[bone_name].path_from_id(prop)
                for index, value in enumerate(values):
                    curve = bag.fcurves.new(data_path=path, index=index)
                    point = curve.keyframe_points.insert(1.0, float(value))
                    point.interpolation = 'CONSTANT'
        action[METADATA] = json.dumps(metadata, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        action.use_fake_user = True
        action.asset_mark()
        action.asset_data.description = 'Character Designer native Body pose. Apply through Character Designer to match current controls.'
    except Exception:
        bpy.data.actions.remove(action)
        raise
    # Preview is optional presentation work. The saved Pose remains valid if a
    # background Blender instance does not provide an asset preview renderer.
    try:
        action.asset_generate_preview()
    except RuntimeError:
        pass
    return action


def _show_current_file(context, action=None):
    """Show local Poses only in the current window's visible asset browsers.

    Hidden workspace browsers can have an uninitialized native runtime even
    when params exists. Blender 5.2's deferred asset activation dereferences
    that runtime without a null check; never call it or visit hidden screens.
    """
    window = getattr(context, 'window', None)
    if window is None or window.screen is None:
        return
    for area in window.screen.areas:
        if area.type != 'FILE_BROWSER':
            continue
        space = area.spaces.active
        if space.browse_mode != 'ASSETS':
            continue
        params = space.params
        if params is None or not hasattr(params, 'asset_library_reference'):
            continue
        try:
            params.asset_library_reference = 'LOCAL'
            params.filter_search = ''
            params.filter_asset_id.filter_action = True
            params.catalog_id = '00000000-0000-0000-0000-000000000000'
            params.asset_catalog_visibility = 'ALL'
            area.tag_redraw()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            # Presentation cannot invalidate an already completed Pose.
            continue


class CHARACTERDESIGNER_OT_save_pose_asset(bpy.types.Operator):
    bl_idname = 'character_designer.save_pose_asset'
    bl_label = 'Save Pose'
    bl_description = 'Save the visible Body pose as one internal asset and match controls when applying it'
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(name='Name', default='Pose')
    scope: EnumProperty(name='Save', items=(
        ('SELECTED', 'Selected Region', 'Selected native bones or their Body controls; a limb saves its full chain'),
        ('ALL_BODY', 'All Body', 'Save every supported native Body bone'),
    ), default='SELECTED')
    include_fingers: BoolProperty(name='Include Fingers', default=False,
                                 description='Include the selected arm\'s fingers, or selected native finger bones')

    @classmethod
    def poll(cls, context):
        from . import bone_display
        if context.mode not in {'OBJECT', 'POSE'}:
            return False
        try:
            rig = bone_display.character_rig(context)
            return bool(rig and rig.type == 'ARMATURE' and not rig.library and not rig.data.library)
        except ValueError:
            return False

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'name')
        layout.prop(self, 'scope')
        layout.prop(self, 'include_fingers')
        layout.label(text='Saved in Current File', icon='FILE_BLEND')

    def execute(self, context):
        from . import bone_display
        try:
            rig = bone_display.character_rig(context)
            action = save_pose(context, rig, self.name, scope=self.scope, include_fingers=self.include_fingers)
        except (ValueError, RuntimeError, KeyError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _show_current_file(context, action)
        self.report({'INFO'}, f'Saved Pose "{action.name}" in Current File; save the .blend to keep it.')
        return {'FINISHED'}


def draw(layout, context):
    row = layout.row()
    row.operator_context = 'INVOKE_DEFAULT'
    row.operator(CHARACTERDESIGNER_OT_save_pose_asset.bl_idname, text='Save Pose', icon='ASSET_MANAGER')


def register():
    if not CHARACTERDESIGNER_OT_save_pose_asset.is_registered:
        bpy.utils.register_class(CHARACTERDESIGNER_OT_save_pose_asset)


def unregister():
    if CHARACTERDESIGNER_OT_save_pose_asset.is_registered:
        bpy.utils.unregister_class(CHARACTERDESIGNER_OT_save_pose_asset)
