"""Restore the selected native body root and owned gaze before matching the limbs."""
from mathutils import Matrix

from . import body_pose_reset, eye_controls, limb_ik_fk, natural_limb_inventory, root_control


def _protect(rig, sources):
    names = {p.name for p in sources}
    body_pose_reset._guard_drivers(rig, names)
    prefixes = tuple(c.path_from_id() + '.' for p in sources for c in p.constraints)
    if any(curve.data_path.startswith(prefixes) for curve in getattr(rig.animation_data, 'drivers', ())):
        raise ValueError('An artist driver controls a constraint on the selected body reference.')


def prepare_body(context, rig, inventory):
    """Only the full character selection may include the selected hips and gaze."""
    selected = {p.name for p in context.selected_pose_bones or ()}
    root = root_control.get_record(rig)
    entries = inventory['rigs']
    if (len(entries) != 4 or not root or root['master'] not in selected):
        return
    if not all({e['target'].name, e['pole'].name} <= selected for e in entries.values()):
        from . import natural_pose_clear
        if not natural_pose_clear.body_selected(context):
            return
    ancestors = [
        {b.name for b in rig.data.bones[e['chain'][0]].parent_recursive}
        for e in entries.values()
    ]
    hips = [rig.pose.bones[name] for name in root['sources']
            if name in selected and all(name in chain for chain in ancestors)]
    if hips:
        root_control.validate(rig, inventory, pose_only=True)
        master = rig.pose.bones[root['master']]
        if master[root_control.SCALE_PROPERTY] != 1.0 or master.matrix_basis != Matrix.Identity(4):
            raise ValueError('Restore the Root Control location, rotation and uniform scale before resetting to the bind pose.')
        for hip in hips:
            natural_limb_inventory._check_locks(hip, location=True, rotation=True, scale=True)
    eyes = []
    eye_record = eye_controls.get_record(rig)
    if eye_record and set(eye_record['bones'].values()) <= selected:
        eye_controls.validate(rig, pose_only=True)
        body_pose_reset._guard_drivers(rig, eye_record['bones'].values())
        eyes = [rig.pose.bones[name] for name in eye_record['sources']]
        for eye in eyes:
            natural_limb_inventory._check_locks(eye, location=True, rotation=True, scale=True)
    _protect(rig, hips + eyes)
    for eye in eyes:
        eye.matrix_basis = Matrix.Identity(4)
    if eyes:
        limb_ik_fk._update(context, rig)
