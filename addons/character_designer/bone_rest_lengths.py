"""Hash-bound terminal bone lengths for Blender/FBX model round trips.

FBX joints retain bind frames, but not Blender's terminal bone tails. This
optional manifest block restores lengths only on a freshly imported model,
after checking its complete skeleton in export-world meters. It never uses an
artist rig as a replacement bind skeleton.
"""

import json
import math
from pathlib import Path
import re


SCHEMA = 'cdesigner.bone-rest-lengths/1'
SPACE = 'export-world-meters'
POSITION_TOLERANCE = 2e-5
AXIS_TOLERANCE = 2e-5


class BoneLengthError(ValueError):
    pass


def _number(value):
    if type(value) not in (float, int):
        raise BoneLengthError('Bone length metadata contains a non-finite number.')
    try:
        value = float(value)
    except OverflowError as error:
        raise BoneLengthError('Bone length metadata contains a non-finite number.') from error
    if not math.isfinite(value):
        raise BoneLengthError('Bone length metadata contains a non-finite number.')
    return value


def _vector(value):
    if type(value) is not list or len(value) != 3:
        raise BoneLengthError('Bone length metadata contains an invalid vector.')
    return tuple(_number(v) for v in value)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _unit_scale(rig, meters_per_unit):
    unit = _number(meters_per_unit)
    if unit <= 0:
        raise BoneLengthError('Bone length recovery requires positive scene units.')
    basis = rig.matrix_world.to_3x3()
    lengths = [axis.length for axis in basis.col]
    scale = sum(lengths) / 3.0
    if (not math.isfinite(scale) or scale <= 1e-12 or basis.determinant() <= 0
            or max(abs(length - scale) for length in lengths) > scale * 2e-5):
        raise BoneLengthError('Bone length recovery requires a positive uniform rig scale.')
    axes = [axis.normalized() for axis in basis.col]
    if any(abs(axes[i].dot(axes[j])) > AXIS_TOLERANCE
           for i in range(3) for j in range(i + 1, 3)):
        raise BoneLengthError('Bone length recovery does not support a sheared rig.')
    return unit, unit * scale


def _frame(rig, bone, unit):
    head = rig.matrix_world @ bone.head_local
    basis = rig.matrix_world.to_3x3() @ bone.matrix_local.to_3x3()
    return ([float(value * unit) for value in head],
            [[float(value) for value in axis.normalized()] for axis in basis.col])


def _bone_attachments(rig):
    return {obj.parent_bone for obj in rig.children
            if obj.parent_type == 'BONE' and obj.parent_bone}


def capture(rig, fbx_sha256, meters_per_unit=1.0):
    """Capture the final, origin-normalized export rig after helper removal."""
    unit, scale = _unit_scale(rig, meters_per_unit)
    attachments = _bone_attachments(rig)
    records = []
    for bone in rig.data.bones:
        head, axes = _frame(rig, bone, unit)
        terminal = not bone.children
        records.append({'name': bone.name, 'parent': bone.parent.name if bone.parent else None,
                        'head': head, 'axes': axes, 'length': float(bone.length * scale),
                        'terminal': terminal,
                        'restore': terminal and bone.bbone_segments == 1 and bone.name not in attachments})
    payload = {'schema': SCHEMA, 'space': SPACE, 'fbx_sha256': fbx_sha256,
               'rig_name': rig.name, 'bones': records}
    _validate(payload, fbx_sha256, rig.name)
    return payload


def _validate(payload, fbx_sha256, rig_name):
    if (type(payload) is not dict or payload.get('schema') != SCHEMA
            or payload.get('space') != SPACE):
        raise BoneLengthError('Unsupported bone length metadata.')
    digest = payload.get('fbx_sha256')
    if (type(digest) is not str or not re.fullmatch('[0-9a-f]{64}', digest)
            or digest != fbx_sha256.lower() or payload.get('rig_name') != rig_name):
        raise BoneLengthError('Bone length metadata belongs to a different model FBX or rig.')
    bones = payload.get('bones')
    if type(bones) is not list or not bones:
        raise BoneLengthError('Bone length metadata has no valid skeleton.')
    records = {}
    for record in bones:
        if type(record) is not dict:
            raise BoneLengthError('Invalid bone length record.')
        name, parent = record.get('name'), record.get('parent')
        if (type(name) is not str or not name or name in records
                or 'parent' not in record
                or (parent is not None and (type(parent) is not str or not parent))
                or type(record.get('terminal')) is not bool or type(record.get('restore')) is not bool
                or (record['restore'] and not record['terminal'])):
            raise BoneLengthError('Bone length metadata has ambiguous names or relationships.')
        _vector(record.get('head'))
        axes = record.get('axes')
        if type(axes) is not list or len(axes) != 3:
            raise BoneLengthError('Invalid bone length rest axes.')
        axes = [_vector(axis) for axis in axes]
        if (any(abs(_dot(axis, axis) - 1.0) > AXIS_TOLERANCE for axis in axes)
                or any(abs(_dot(axes[i], axes[j])) > AXIS_TOLERANCE
                       for i in range(3) for j in range(i + 1, 3))):
            raise BoneLengthError('Bone length metadata has invalid rest axes.')
        cross = (axes[0][1] * axes[1][2] - axes[0][2] * axes[1][1],
                 axes[0][2] * axes[1][0] - axes[0][0] * axes[1][2],
                 axes[0][0] * axes[1][1] - axes[0][1] * axes[1][0])
        if _dot(cross, axes[2]) < 1.0 - AXIS_TOLERANCE:
            raise BoneLengthError('Bone length metadata has reflected rest axes.')
        if _number(record.get('length')) <= 0:
            raise BoneLengthError('Bone length metadata has a non-positive length.')
        records[name] = record
    children = {name: [] for name in records}
    for name, record in records.items():
        parent = record['parent']
        if parent is not None:
            if parent not in records or parent == name:
                raise BoneLengthError('Bone length metadata has an invalid parent.')
            children[parent].append(name)
    if any(record['terminal'] != (not children[name]) for name, record in records.items()):
        raise BoneLengthError('Bone length metadata has an inconsistent terminal inventory.')
    return records


def load(model_path, fbx_sha256, rig_name):
    """Legacy exports have no block and retain their existing import behavior."""
    model_path = Path(model_path)
    manifest = model_path.with_suffix('.cdesigner.json')
    if not manifest.is_file():
        return None
    try:
        report = json.loads(manifest.read_text(encoding='utf-8'))
    except (ValueError, OSError) as error:
        raise BoneLengthError('The character export manifest cannot be read.') from error
    if type(report) is not dict:
        raise BoneLengthError('The character export manifest is invalid.')
    if 'bone_rest_lengths' not in report:
        return None
    if (report.get('schema') != 'cdesigner.unity-character.v1'
            or report.get('filename') != model_path.name
            or type(report.get('files')) is not dict
            or report['files'].get(model_path.name) != fbx_sha256.lower()):
        raise BoneLengthError('The bone length manifest does not identify this model FBX.')
    payload = report['bone_rest_lengths']
    _validate(payload, fbx_sha256, rig_name)
    return payload


def restore(context, rig, payload, fbx_sha256, rig_name, meters_per_unit=1.0):
    """Restore safe terminal lengths without changing any native bind frame.

    Call only on this operation's freshly imported, independent rig. An error
    before or after native editing leaves the imported skeleton unchanged;
    the import caller then discards its new scene.
    """
    if payload is None:
        return 0
    import bpy

    records = _validate(payload, fbx_sha256, rig_name)
    unit, scale = _unit_scale(rig, meters_per_unit)
    if set(records) != set(rig.data.bones.keys()):
        raise BoneLengthError('Bone length metadata does not match the complete imported skeleton.')
    plan = {}
    attachments = _bone_attachments(rig)
    for bone in rig.data.bones:
        record = records[bone.name]
        head, axes = _frame(rig, bone, unit)
        if (record['parent'] != (bone.parent.name if bone.parent else None)
                or record['terminal'] != (not bone.children)
                or _distance(record['head'], head) > POSITION_TOLERANCE
                or any(_distance(expected, actual) > AXIS_TOLERANCE
                       for expected, actual in zip(record['axes'], axes))):
            raise BoneLengthError('Bone length metadata has a different bind frame: ' + bone.name)
        length = record['length'] / scale
        if not math.isfinite(length) or length <= 0:
            raise BoneLengthError('Bone length metadata cannot be converted to this rig scale.')
        if record['restore'] and abs(length - bone.length) > max(1e-7, length * 1e-6):
            if bone.bbone_segments != 1 or bone.name in attachments:
                raise BoneLengthError('Bone length recovery would affect a B-Bone or bone attachment: ' + bone.name)
            plan[bone.name] = length
    if not plan:
        return 0
    if rig.data.users != 1 or rig.library or rig.data.library:
        raise BoneLengthError('Bone length recovery requires an independent local imported rig.')
    if context.object and context.object.mode != 'OBJECT':
        raise BoneLengthError('Bone length recovery requires Object Mode.')
    if any(mod.type == 'ARMATURE' and mod.object == rig and mod.use_bone_envelopes
           for obj in context.scene.objects for mod in obj.modifiers):
        raise BoneLengthError('Bone length recovery cannot change an envelope-deformed rig.')
    before = {bone.name: (bone.matrix_local.copy(), bone.tail_local.copy(),
                         bone.parent.name if bone.parent else None,
                         rig.pose.bones[bone.name].matrix_basis.copy(),
                         _frame(rig, bone, unit)) for bone in rig.data.bones}
    active, selected = context.view_layer.objects.active, tuple(context.selected_objects)

    def edit_tails(tails=None):
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            for name, length in plan.items():
                bone = rig.data.edit_bones[name]
                if tails is None:
                    bone.length = length
                else:
                    bone.tail = tails[name]
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
        context.view_layer.update()

    try:
        for obj in selected:
            obj.select_set(False)
        rig.select_set(True)
        context.view_layer.objects.active = rig
        edit_tails()
        for bone in rig.data.bones:
            matrix, _tail, parent, pose, (old_head, old_axes) = before[bone.name]
            head, axes = _frame(rig, bone, unit)
            # Native EditBone tails are float32. Shortening a terminal tail can
            # quantize its normalized axes by a few millionths, even with the
            # same head and roll. Validate axes separately from head/pose;
            # never loosen the positional or animation checks to absorb this.
            if (bone.parent.name if bone.parent else None) != parent or (
                    _distance(old_head, head) > 2e-6) or any(
                    _distance(a, b) > AXIS_TOLERANCE for a, b in zip(old_axes, axes)) or any(
                    abs(matrix[i][j] - bone.matrix_local[i][j]) > AXIS_TOLERANCE
                    for i in range(3) for j in range(3)) or any(
                    abs(a - b) > 2e-6 for row_a, row_b in zip(pose, rig.pose.bones[bone.name].matrix_basis)
                    for a, b in zip(row_a, row_b)):
                raise BoneLengthError('Bone length recovery changed a native bind or pose frame: ' + bone.name)
        if any(abs(rig.data.bones[name].length - length) > max(1e-7, length * 2e-6)
               for name, length in plan.items()):
            raise BoneLengthError('Blender did not retain the recovered terminal bone lengths.')
    except Exception:
        edit_tails({name: before[name][1] for name in plan})
        raise
    finally:
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in selected:
            obj.select_set(True)
        context.view_layer.objects.active = active
    return len(plan)
