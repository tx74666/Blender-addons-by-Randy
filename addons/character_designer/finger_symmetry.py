"""Strict existing L/R pairs and shared rest-axis safety for finger tools."""
import bpy
from mathutils import Matrix, Vector


def reflect(vector):
    """Polar vector reflected in the armature's local X=0 plane."""
    return Vector((-vector.x, vector.y, vector.z))


def opposite(collection, bone):
    name = bpy.utils.flip_name(bone.name)
    other = collection.get(name) if name != bone.name else None
    if other is None or bpy.utils.flip_name(other.name) != bone.name:
        raise ValueError(f"'{bone.name}' has no matching left/right bone. Nothing was changed; create or name the existing counterpart first.")
    return other


def validate_pair(collection, bone, other):
    from .finger_bones import _head, _tail, _bone_direction
    if getattr(bone, 'lock', False) or getattr(other, 'lock', False):
        raise ValueError(f"'{bone.name}' or '{other.name}' is locked. Unlock the pair before calibrating either side.")
    length = max((_tail(bone)-_head(bone)).length, (_tail(other)-_head(other)).length)
    if length < 1e-8 or _bone_direction(other).dot(reflect(_bone_direction(bone))) < .9:
        raise ValueError(f"'{bone.name}' and '{other.name}' do not have compatible mirrored directions.")
    tolerance = max(length*.25, 1e-5)
    if any((reflect(point)-target).length > tolerance for point, target in
           ((_head(bone), _head(other)), (_tail(bone), _tail(other)))):
        raise ValueError(f"'{bone.name}' / '{other.name}' are not a reliable pair about Armature Local X. Nothing was changed.")
    if (bone.parent is None) != (other.parent is None):
        raise ValueError(f"'{bone.name}' and '{other.name}' have different parent connections.")
    if bone.parent and other.parent != bone.parent and bpy.utils.flip_name(bone.parent.name) != other.parent.name:
        raise ValueError(f"'{bone.name}' and '{other.name}' have different parent connections.")
    if bone.use_connect != other.use_connect:
        raise ValueError(f"'{bone.name}' and '{other.name}' have different connected-joint settings.")


def guard_rig(rig):
    if rig.data.users != 1 or rig.library or rig.data.library or rig.override_library:
        raise ValueError('Use a local, single-user armature for rest-axis calibration.')
    world = rig.matrix_world.to_3x3()
    scales = [v.length for v in world.col]
    unit = [v.normalized() for v in world.col]
    if min(scales) < 1e-8 or max(scales)-min(scales) > max(scales)*1e-5 or world.determinant() <= 0 or any(
        abs(unit[i].dot(unit[j])) > 1e-5 for i, j in ((0, 1), (0, 2), (1, 2))):
        raise ValueError('Apply non-uniform or mirrored armature scale before calibrating finger roll.')


def guard_pose(rig, names):
    guard_rig(rig)
    if rig.animation_data and (rig.animation_data.action or rig.animation_data.nla_tracks or rig.animation_data.drivers):
        raise ValueError('This rig has animation or drivers; calibrate its rest axes before animation.')
    affected = set(names)
    for name in names:
        affected.update(b.name for b in rig.data.edit_bones[name].children_recursive)
    for name in affected:
        pb = rig.pose.bones.get(name)
        if pb is not None and (pb.constraints or any(
            abs(pb.matrix_basis[i][j]-Matrix.Identity(4)[i][j]) > 2e-6
            for i in range(4) for j in range(4))):
            raise ValueError(f"'{name}' needs neutral pose transforms and no constraints before changing rest axes on either side.")
