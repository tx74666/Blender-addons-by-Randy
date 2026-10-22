"""Read-only inventory validation for forearm calibration in Original mode.

Only the exact owned source constraints recorded by the active Original
transaction expose their preceding mute value. RNA stays muted throughout;
ordinary inventory callers retain their strict live constraint checks.
"""
import json
from functools import lru_cache
from . import validation_json, validation_native


@lru_cache(maxsize=1)
def _session_entries(raw):
    saved = json.loads(raw)
    if not isinstance(saved, dict) or saved.get('version') != 1:
        raise ValueError('The Original session record is invalid; recover its saved file.')
    names = saved['names']
    if not isinstance(names, list) or any(not isinstance(n, str) for n in names) or len(set(names)) != len(names):
        raise ValueError('The Original source bone list is invalid.')
    entries = []
    for entry in saved['constraints']:
        if (not isinstance(entry, dict) or type(entry.get('mute')) is not bool
                or any(not isinstance(entry.get(k), str) for k in ('bone', 'name', 'type'))):
            raise ValueError('The Original constraint record is invalid.')
        entries.append((entry['bone'], entry['name'], entry['type'], entry['mute']))
    if len({(bone, name) for bone, name, _kind, _muted in entries}) != len(entries):
        raise ValueError('The Original constraint record contains duplicates.')
    return tuple(names), tuple(entries), saved


class _MuteView:
    def __init__(self, entries):
        self.entries = {(bone, name): (kind, muted) for bone, name, kind, muted in entries}
        self.used = set()

    def mute(self, pose_bone, constraint):
        key = (pose_bone.name, constraint.name)
        if key not in self.entries:
            return bool(constraint.mute)
        kind, muted = self.entries[key]
        if constraint.type != kind or not constraint.mute:
            raise ValueError(f"Original constraint '{constraint.name}' on '{pose_bone.name}' was changed; undo that edit before calibrating.")
        self.used.add(key)
        return muted

    def finish(self):
        if self.used != set(self.entries):
            raise ValueError('Original contains an unrecognized source constraint; its ownership cannot be verified.')


@validation_json.read_scope()
@validation_native.read_scope()
def validate(armature, *, original_rest=None):
    from . import body_original_mode as original, limb_ik
    if not original.active(armature):
        if original_rest is not None:
            raise ValueError('A saved Rest validation view requires an active Original session.')
        return limb_ik._validate_inventory(armature)
    raw = armature[original.SESSION]
    try:
        names, entries, saved = _session_entries(raw)
        if original_rest is None:
            # Re-read the current exact session text through the raw-only
            # immutable schema plan. A cached mutable decoded dictionary is
            # never used as evidence that today's saved Rest is valid.
            native = original._validate_session_rest(armature)
        else:
            original_rest.validate(armature, saved)
            native = saved['rest']
        if set(names) - set(native):
            raise ValueError('Original source bones changed; restore their structure before calibrating.')
        # Keep the same ownership manifest used when Original was entered,
        # without asking the strict inventory to accept every disabled relation.
        expected = {(pb.name, con.name): con.type
                    for pb, con, _record in limb_ik._owned_constraint_records(armature, strict=True)
                    if pb.name in names}
        from . import torso_controls, spine_ik_fk, eye_controls, root_control, foot_controls
        records = [module.get_record(armature) for module in
                   (torso_controls, spine_ik_fk, eye_controls, root_control)]
        records.extend(foot_controls.records(armature).values())
        for record in records:
            for entry in record['constraints'] if record else ():
                if entry['owner'] in names:
                    key = (entry['owner'], entry['name'])
                    if key in expected:
                        raise ValueError('Original source constraint ownership is duplicated.')
                    expected[key] = entry['type']
        if expected != {(bone, name): kind for bone, name, kind, _muted in entries}:
            raise ValueError('Original source constraints changed; their saved ownership no longer matches.')
        view = _MuteView(entries)
        inventory = limb_ik._validate_inventory(armature, original_mutes=view, original_rest=original_rest)
        view.finish()
        return inventory
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('The Original constraint record is incomplete; recover its saved file.') from exc


def validate_forearm(armature, *, sides=None, dependencies=()):
    """Prove forearm inputs without blessing unrelated Original Rest geometry.

    The ordinary validator remains the global Original/return contract. This
    explicit forearm route authenticates the old Direct graph through a local
    read-only Rest view, then requires its actual arm and deform dependencies
    to retain the session Rest. No saved baseline or source RNA is changed.
    """
    from . import body_original_mode as original
    if not original.active(armature):
        return validate(armature)
    required = set(('L', 'R') if sides is None else sides)
    if not required or required - {'L', 'R'}:
        raise ValueError('Forearm validation needs an explicit left or right arm scope.')
    try:
        return validate(armature)
    except ValueError:
        # Only a proven Original geometry change can earn the scoped retry.
        # Healthy callers keep the existing complete validation path.
        pass
    return _validate_forearm_rest_fallback(armature, sides=required, dependencies=dependencies)


def _validate_forearm_rest_fallback(armature, *, sides=None, dependencies=()):
    """Complete the local proof after this call's strict Original rejection.

    Forearm's mesh resolver already attempted the same strict validation before
    reading its support. Reuse that control flow, not a persistent success or a
    caller-provided bypass flag. Geometry, native relations, local dependencies
    and the complete owned graph are still read and validated here. The Rest
    view retains its independent fresh validation before the graph is admitted.
    """
    from . import body_original_mode as original, body_rest_resync as resync, limb_ik
    if not original.active(armature):
        return validate(armature)
    required = set(('L', 'R') if sides is None else sides)
    if not required or required - {'L', 'R'}:
        raise ValueError('Forearm validation needs an explicit left or right arm scope.')
    try:
        _names, _entries, saved = _session_entries(armature[original.SESSION])
        current, changed = resync._geometry_changes(armature, saved)
        if not changed:
            return validate(armature)
        if not limb_ik._is_direct_preroll_schema(armature.data.get(limb_ik.SCHEMA_KEY, limb_ik.LEGACY_SCHEMA)):
            raise ValueError('Scoped Forearm Rest validation requires Direct controls; keep Original and reconcile the edited rig first.')
        protected = set(dependencies)
        if any(not isinstance(name, str) or name not in current for name in protected):
            raise ValueError('A Forearm deform dependency is not an unchanged native bone.')

        def check_rest(names):
            for name in tuple(names):
                names.update(parent.name for parent in armature.data.bones[name].parent_recursive
                             if parent.name in current)
            local_changes = changed & names
            if local_changes:
                raise ValueError('Forearm Twist Rest changed in its arm or deform dependency: '
                                 + ', '.join(sorted(local_changes)[:4])
                                 + '. Return to Controls to reconcile that Rest edit before recapturing the forearm.')

        # An edited arm must receive a local error before source-widget Rest
        # comparisons reject it. These strict owner records only add refusals;
        # the complete inventory/mute/driver proof below is still mandatory.
        for _pb, _con, entry in limb_ik._owned_constraint_records(armature, strict=True):
            chain = entry.get('chain', ())
            if (entry.get('kind') == 'ARM' and entry.get('side') in required
                    and len(chain) == 3 and all(isinstance(name, str) and name in current for name in chain)):
                protected.update(chain)
        check_rest(protected)
        view = resync.OriginalRestView(armature, saved, current, changed)
        inventory = validate(armature, original_rest=view)
        discovery = None
        for side in sorted(required):
            route = inventory['rigs'].get(('ARM', side))
            if route is not None:
                chain = route['chain']
            else:
                if discovery is None:
                    discovery = limb_ik.analyze_armature(armature)
                found = discovery['limbs']['ARM'][side]
                if found['status'] not in {'READY', 'WARNING'}:
                    raise ValueError(f'Forearm validation could not identify the {side} native arm chain.')
                chain = [found[role] for role in ('upper', 'lower', 'end')]
            if any(name not in current for name in chain):
                raise ValueError(f'Forearm validation found a non-native {side} arm source.')
            protected.update(chain)
        check_rest(protected)
        return inventory
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('The Original forearm scope is incomplete; recover its saved file.') from exc
