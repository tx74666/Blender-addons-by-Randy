"""Read-only inventory validation for forearm calibration in Original mode.

Only the exact owned source constraints recorded by the active Original
transaction expose their preceding mute value. RNA stays muted throughout;
ordinary inventory callers retain their strict live constraint checks.
"""
import json
from functools import lru_cache


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
            native = original._validate_session_rest(armature, saved)
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
