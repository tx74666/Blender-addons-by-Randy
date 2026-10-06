"""Prove Dress ownership before isolated model/Action snapshot changes.

The host helpers are read-only. ``prepare_animation_snapshot`` is reserved for
the disposable skeletal worker: it omits surface simulation, keeps the native
manual/Original contribution, and never rewrites an artist physics property.
"""

import ast
import json
from pathlib import Path

import bpy


BACKEND = 'ACTUAL_SURFACE_DELTA_V1'
RECORD_KEY = 'character_designer_skirt_v1'
RIG_KEY = 'character_designer_skirt_armature'


def _record(source):
    raw = source.get(RECORD_KEY)
    if raw is None:
        return None
    try:
        if source.type != 'MESH' or type(raw) is not str:
            raise ValueError()
        record = json.loads(raw)
        if type(record) is not dict:
            raise ValueError()
        physics = record.get('physics')
        if physics is None:
            return None
        if type(physics) is not dict:
            raise ValueError()
        backend = physics.get('backend', 'LEGACY_CAGE')
        if backend == 'LEGACY_CAGE' and 'surface' not in physics:
            return None
        if backend != BACKEND:
            raise ValueError()
        return record
    except (ValueError, TypeError) as error:
        raise ValueError(source.name + ': restore the saved Dress backend before export.') from error


def _service():
    from . import skirt_surface
    if skirt_surface.BACKEND != BACKEND:
        raise ValueError('Restore the validated Dress surface export service.')
    return skirt_surface


def capture_animation_surfaces(context, rig, selected_action=None):
    """Capture only actual surfaces whose native skinning rig is the selected rig."""
    result = []
    service = None
    # Rig ID properties/owned bones may reference another linked Scene's Dress
    # source too. Audit the same selected-rig closure the library writer keeps.
    for source in sorted(bpy.data.objects, key=lambda item: item.name):
        if source.type != 'MESH' or source.get(RIG_KEY) != rig:
            continue
        if _record(source) is None:
            continue
        service = service or _service()
        proof = json.loads(json.dumps(service.export_capture(source), allow_nan=False))
        _influence_animation_guard(source, selected_action)
        _physical_animation_guard(source, _record(source), selected_action)
        result.append(proof)
    return result


def _proved_sources(proofs):
    if type(proofs) is not list:
        raise ValueError('The captured Dress surface inventory must be a list.')
    if not proofs:
        return []
    service = _service()
    result, names = [], set()
    for proof in proofs:
        if type(proof) is not dict or type(proof.get('source')) is not str:
            raise ValueError('The captured Dress surface identity is invalid.')
        source = bpy.data.objects.get(proof['source'])
        if source is None or source.name in names:
            raise ValueError('A captured Dress surface is missing or duplicated.')
        names.add(source.name)
        record = _record(source)
        if record is None:
            raise ValueError(source.name + ': the captured Dress backend changed.')
        service.validate_snapshot(source, proof)
        result.append((source, record, proof))
    return result


def snapshot_scene_roots(proofs):
    """Retain the proven native Scene membership, a reverse library dependency.

An Object-only library write cannot preserve its home Scene or the parent of
its collision Collection. Include those original Scene IDs as snapshot roots;
do not create a replacement Scene or alter artist memberships in the host.
"""
    roots = set()
    for source, record, _proof in _proved_sources(proofs):
        scene = bpy.data.scenes.get(record['physics']['surface']['home_scene'])
        if scene is None:
            raise ValueError(source.name + ': the Dress installation Scene is missing.')
        roots.add(scene)
    return roots


def _curves(action):
    if action is None:
        return ()
    if action.is_action_layered:
        return (curve for layer in action.layers for strip in layer.strips
                for bag in getattr(strip, 'channelbags', ()) for curve in bag.fcurves)
    return iter(getattr(action, 'fcurves', ()))


def _actions(identifier, selected_action):
    actions = {selected_action} if selected_action is not None else set()
    animation = getattr(identifier, 'animation_data', None)
    if animation:
        if animation.action is not None:
            actions.add(animation.action)
        pending = [strip for track in animation.nla_tracks for strip in track.strips]
        while pending:
            strip = pending.pop()
            if strip.action is not None:
                actions.add(strip.action)
            pending.extend(getattr(strip, 'strips', ()))
    return actions


def _influence_animation_guard(source, selected_action):
    from . import skirt_rig
    _holder, identifier, path = skirt_rig.physics_control(source)
    animation = getattr(identifier, 'animation_data', None)
    expected = _path_identity(path)
    if (animation and any(_path_identity(curve.data_path) == expected for curve in animation.drivers)
            or any(_path_identity(curve.data_path) == expected for action in _actions(identifier, selected_action)
                   for curve in _curves(action))):
        raise ValueError(source.name + ': physics_influence has author animation or a driver. '
                         'Preserve that mode animation before a skeletal-only export.')


def _path_identity(path):
    # Blender RNA paths may use either quote style for the same custom property.
    # Parsing compares the literal path structure; it never executes the text.
    try:
        return ast.dump(ast.parse(path, mode='eval'), include_attributes=False)
    except (SyntaxError, TypeError, ValueError):
        return ('unparsed', path)


def _targets_constraint(path, aliases):
    """Compare literal RNA owner paths, including named/indexed and quote aliases."""
    identities = {_path_identity(alias) for alias in aliases}
    try:
        node = ast.parse(path, mode='eval').body
    except (SyntaxError, TypeError, ValueError):
        return path in aliases
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        if ast.dump(ast.Expression(body=node), include_attributes=False) in identities:
            return True
        node = node.value
    return False


def _generated_influence_driver(curve, aliases, identifier, path):
    # Native surface proof already verifies these installed drivers. Only this
    # exact generated input remains legal; a mute/target driver is never exempt.
    if _path_identity(curve.data_path) not in {_path_identity(alias + '.influence') for alias in aliases}:
        return False
    driver = getattr(curve, 'driver', None)
    variables = tuple(getattr(driver, 'variables', ()))
    if curve.mute or getattr(driver, 'type', None) != 'AVERAGE' or len(variables) != 1:
        return False
    variable = variables[0]
    targets = tuple(getattr(variable, 'targets', ()))
    return (variable.type == 'SINGLE_PROP' and len(targets) == 1
            and targets[0].id == identifier
            and _path_identity(targets[0].data_path) == _path_identity(path))


def _physical_animation_guard(source, record, selected_action):
    """Prove frame evaluation cannot undo the worker's owned physical mutes.

Selected Actions may differ from the currently active one. Check both, every
native NLA Action and driver before stripping or muting any native endpoint.
All properties beneath the planned constraints are guarded, not just ``mute``.
"""
    from . import skirt_rig
    rig = source.get(RIG_KEY)
    roles = record['physics']['surface']['roles']
    cloth = bpy.data.objects[roles['CLOTH_PROXY'][0]].modifiers.get(record['physics']['surface']['cloth_modifier'])
    neutral = bpy.data.objects[roles['NEUTRAL_RIG'][0]]
    _holder, driver_identifier, influence_path = skirt_rig.physics_control(source)
    owned = []
    for identifier in (rig, neutral):
        for chain in record['chains']:
            for names, deform in ((chain['def'], True), (chain['phys'], False)):
                for name in names:
                    bone = identifier.pose.bones[name]
                    constraint = (bone.constraints.get('Skirt physics delta') if deform else bone.constraints[0])
                    if constraint is None:
                        raise ValueError(source.name + ': the proved Dress physical endpoint is missing.')
                    index = next(index for index, item in enumerate(bone.constraints) if item == constraint)
                    aliases = (constraint.path_from_id(), bone.path_from_id() + '.constraints[' + str(index) + ']')
                    owned.append((identifier, constraint, aliases, deform))
    if cloth is None or cloth.type != 'CLOTH':
        raise ValueError(source.name + ': the proved Dress physical endpoint is missing.')
    for identifier, _constraint, aliases, deform in owned:
        for action in _actions(identifier, selected_action if identifier == rig else None):
            if any(_targets_constraint(curve.data_path, aliases) for curve in _curves(action)):
                raise ValueError(source.name + ': a physical constraint has author Action/NLA channels. '
                                 'Preserve that animation before a skeletal-only export.')
        animation = getattr(identifier, 'animation_data', None)
        allowed = 0
        for curve in animation.drivers if animation else ():
            if not _targets_constraint(curve.data_path, aliases):
                continue
            if deform and _generated_influence_driver(curve, aliases, driver_identifier, influence_path):
                allowed += 1
                continue
            raise ValueError(source.name + ': a physical constraint has an author or edited driver. '
                             'Preserve that driver before a skeletal-only export.')
        if deform and allowed != 1:
            raise ValueError(source.name + ': restore the unique generated physical influence driver before export.')
    return cloth, [constraint for _identifier, constraint, _aliases, _deform in owned]


def prepare_animation_snapshot(rig, proofs, selected_action, *, private_snapshot=None):
    """Omit only proven owned physical output before any worker evaluation.

Every surface and property-animation guard is proved first. Mutation occurs
only after that full preflight, inside the unsaved worker session. Do not call
this function in the artist's Blender process.
"""
    if proofs:
        expected = Path(private_snapshot).resolve() if isinstance(private_snapshot, str) else None
        current = Path(bpy.data.filepath).resolve() if bpy.data.filepath else None
        if (not bpy.app.background or expected is None or current != expected
                or expected.name != 'animation.blend'
                or not expected.parent.name.startswith('cdesigner-action-')):
            raise ValueError('Dress physical output may be omitted only in its isolated background Action snapshot.')
    surfaces = _proved_sources(proofs)
    actual = {source.name for source in bpy.data.objects
              if source.type == 'MESH' and source.get(RIG_KEY) == rig and _record(source) is not None}
    if actual != {source.name for source, _record_value, _proof in surfaces}:
        raise ValueError('The skeletal snapshot Dress inventory differs from its captured proof.')
    plans = []
    for source, record, proof in surfaces:
        if source.get(RIG_KEY) != rig or record['rig'] != rig.name:
            raise ValueError('A captured Dress surface has a different skeletal rig.')
        _influence_animation_guard(source, selected_action)
        cloth, constraints = _physical_animation_guard(source, record, selected_action)
        plans.append((source, record, proof, cloth, constraints))
    service = _service() if plans else None
    # Strip before muting: each strip validates its unchanged source graph again.
    # Multiple independent Dress subsets on one rig have all passed preflight.
    for source, _record_value, proof, _cloth, _constraints in plans:
        service.strip_export_snapshot(source, proof)
    for _source, _record_value, _proof, cloth, constraints in plans:
        cloth.show_viewport = cloth.show_render = False
        for constraint in constraints:
            constraint.mute = True
    return [{'source': source.name, 'owner': record['owner'], 'backend': BACKEND,
             'simulation_baked': False, 'physics_omitted': True,
             'manual_original_preserved': True} for source, record, _proof, _cloth, _constraints in plans]
