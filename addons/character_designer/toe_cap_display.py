"""Wrap the generated Toe Bend wire around a cross-section of its shoe at bind."""
import hashlib
import math
import struct

from mathutils import Vector

from . import foot_controls as feet, foot_sole_display as sole, limb_ik as limb


PROFILE = 'character_designer_toe_profile'


def section(points, edges, to_rig, station, length):
    """Intersect the shoe with the armature XZ plane at the toe bone's head."""
    y_axis = to_rig[1].xyz
    offset = to_rig[1][3]
    cut = []
    for a, b in edges:
        ay = y_axis.dot(a) + offset
        by = y_axis.dot(b) + offset
        if min(ay, by) <= station <= max(ay, by):
            if ay == by:
                cut.extend((to_rig @ a, to_rig @ b))
            else:
                cut.append(to_rig @ a.lerp(b, (station - ay) / (by - ay)))
    if len(cut) < 3:
        cut = [to_rig @ p for p in points if abs(y_axis.dot(p) + offset - station) <= 0.025 * length]
    return [Vector((p.x, station, p.z)) for p in cut]


def _coordinates(mesh):
    return [tuple(v.co) for v in mesh.vertices]


def _fingerprint(coords):
    return hashlib.sha256(b''.join(struct.pack('<3f', *p) for p in coords)).hexdigest()


def _outline(section_points, count):
    """Intersect adjacent support lines of the shoe outline with picking clearance."""
    if len(section_points) < 3:
        return None
    lo_x, hi_x = min(p.x for p in section_points), max(p.x for p in section_points)
    lo_z, hi_z = min(p.z for p in section_points), max(p.z for p in section_points)
    width, height = hi_x - lo_x, hi_z - lo_z
    if min(width, height) < 1.e-5:
        return None
    center = Vector(((hi_x + lo_x) / 2, section_points[0].y, (hi_z + lo_z) / 2))
    points = [((p.x - center.x) / width, (p.z - center.z) / height) for p in section_points]
    # Measure support in a normalized section so wide, low shoes retain detail.
    clearance = 0.035 * min(width, height)
    lines = []
    for i in range(count):
        angle = (i + 0.5) * math.tau / count
        nx, nz = -math.sin(angle), math.cos(angle)
        support = max(nx * x + nz * z for x, z in points)
        lines.append((nx, nz, support + clearance * math.hypot(nx / width, nz / height)))
    outline = []
    for i, (x2, z2, d2) in enumerate(lines):
        x1, z1, d1 = lines[i - 1]
        det = x1 * z2 - x2 * z1
        outline.append(((d1 * z2 - d2 * z1) / det, 0., (x1 * d2 - x2 * d1) / det))
    return center, (width, 1., height), outline


def _write_vertices(mesh, coords):
    for vertex, co in zip(mesh.vertices, coords):
        vertex.co = co
    mesh.update()


def restore(rig, record, saved):
    target = rig.pose.bones[record['toe_control']]
    mesh = target.custom_shape.data
    visual, use_bone_size, coords, marker = saved
    sole.restore(target, visual)
    target.use_custom_shape_bone_size = use_bone_size
    _write_vertices(mesh, coords)
    if marker is None:
        mesh.pop(PROFILE, None)
    else:
        mesh[PROFILE] = marker


def fit(rig, record, rotation, section_points):
    """Update only the owned ring. Return its rollback state when it was fitted."""
    target = rig.pose.bones[record['toe_control']]
    toe = rig.pose.bones[record['toe']]
    shape = target.custom_shape
    asset = record['widgets']['TOE_BEND']
    if (not shape or shape.type != 'MESH' or shape.name != asset['object']
            or shape.data.name != asset['mesh'] or shape.modifiers or shape.data.shape_keys
            or shape.get(feet.ID_KEY) != record['id'] or shape.data.get(feet.ID_KEY) != record['id']
            or shape.data.users != 1 or shape.data.polygons
            or target.custom_shape_transform not in (None, target, toe)):
        return None
    mesh = shape.data
    coords = _coordinates(mesh)
    stock, edges = limb._widget_geometry('FINGER')
    marker = mesh.get(PROFILE)
    if (len(coords) != len(stock) or len(mesh.edges) != len(edges)
            or {tuple(sorted(e.vertices)) for e in mesh.edges} != {tuple(sorted(e)) for e in edges}
            or (_fingerprint(coords) != marker and coords != [tuple(Vector(p)) for p in stock])):
        return None
    outline = _outline(section_points, len(coords))
    if outline is None:
        return None
    try:
        sole._guard_offsets(target)
    except limb.LimbIKError:
        return None  # Keep the animator's display curves and their original frame.
    center, scale, wire = outline
    local = toe.bone.matrix_local.inverted()
    visual = dict(version=1, translation=list(local @ center),
                  rotation=list(local.to_3x3().to_euler()), scale=list(scale))
    saved = sole.capture(target), target.use_custom_shape_bone_size, coords, marker
    try:
        # The native toe includes the evaluated leg, foot roll and toe bend.
        # A custom-shape donor affects only drawing, including in Natural Bind.
        target.custom_shape_transform = toe
        target.use_custom_shape_bone_size = False
        limb._apply_control_visual_state(target, visual)
        target.bone[limb.CONTROL_VISUAL_DEFAULT_KEY] = limb._canonical_json(visual)
        _write_vertices(mesh, wire)
        mesh[PROFILE] = _fingerprint(_coordinates(mesh))
    except Exception:
        restore(rig, record, saved)
        raise
    return saved
