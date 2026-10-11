"""Private copied-rig flag preparation; no exporter admission or registration.

The caller must retain its original native ID inventory and full author guards.
This prepares an explicitly different export target, not source Manual fidelity.
"""
import math


FLAGS = ("inherit_scale", "use_inherit_rotation", "use_local_location")
STANDARD = ("FULL", True, True)


class StandardParentCandidateError(ValueError):
    def __init__(self, message, rollback_errors=()):
        self.rollback_errors = tuple(rollback_errors)
        super().__init__(message + ("; rollback failed: " + "; ".join(rollback_errors)
                                   if rollback_errors else ""))


def _require(value, message):
    if not value:
        raise StandardParentCandidateError(message)


def _state(rig):
    result = {}
    for bone in rig.data.bones:
        matrix = tuple(tuple(float(x) for x in row) for row in bone.matrix_local)
        head, tail = tuple(bone.head_local), tuple(bone.tail_local)
        _require(all(math.isfinite(x) for row in matrix for x in row)
                 and all(math.isfinite(x) for x in head + tail), "Nonfinite Rest: " + bone.name)
        flags = tuple(getattr(bone, field) for field in FLAGS)
        _require(type(flags[0]) is str and all(type(x) is bool for x in flags[1:]),
                 "Unknown native inheritance flags: " + bone.name)
        _require(bone.name not in result, "Duplicate bone name: " + bone.name)
        result[bone.name] = {"parent": bone.parent.name if bone.parent else None,
                             "matrix": matrix, "head": head, "tail": tail,
                             "connected": bone.use_connect, "deform": bone.use_deform,
                             "flags": flags}
    return result


def _geometry(state):
    return {name: {key: value for key, value in row.items() if key != "flags"}
            for name, row in state.items()}


def standardize_export_copy(bpy, source, copied, original_ids, expected_names):
    """Only a registered, local, new Object AND Armature may receive flags.

    ``original_ids`` is the pre-copy native inventory, not a names allowlist.
    Native evaluation, TRS projection, FBX and final-surface acceptance are
    deliberately outside this helper. Failure restores every copied flag.
    """
    _require(bpy.app.background, "Use only a disposable background component.")
    _require(source is not copied and source.type == copied.type == "ARMATURE"
             and source.data is not copied.data, "An independent export Object and Armature are required.")
    for rig in (source, copied):
        _require(not (rig.library or rig.override_library or rig.data.library or rig.data.override_library)
                 and rig.mode != "EDIT" and rig.parent is None,
                 "Local unparented rigs outside Edit Mode are required.")
        _require(bpy.data.objects.get(rig.name) == rig and bpy.data.armatures.get(rig.data.name) == rig.data,
                 "Native rig identities are not registered.")
    for kind, old, new in (("objects", source, copied), ("armatures", source.data, copied.data)):
        _require(old.as_pointer() in original_ids[kind] and new.as_pointer() not in original_ids[kind]
                 and new.as_pointer() != old.as_pointer(), "Refuse original or unproved copied " + kind)
    _require(copied.data.users == 1
             and bpy.data.user_map(subset={copied.data}).get(copied.data, set()) == {copied},
             "The copied Armature has outside native users.")
    _require(copied.animation_data is None and copied.data.animation_data is None
             and not copied.constraints and all(not bone.constraints for bone in copied.pose.bones),
             "Clear animation/drivers and constraints only on the export copy first.")
    names = tuple(expected_names)
    before_source, before = _state(source), _state(copied)
    _require(bool(names) and len(set(names)) == len(names) and set(before) == set(names)
             and set(names) <= set(before_source), "The complete retained bone inventory differs.")
    for name in names:
        bone, original = copied.data.bones[name], source.data.bones[name]
        _require(bone.as_pointer() != original.as_pointer()
                 and before[name]["matrix"] == before_source[name]["matrix"]
                 and all(before[name][field] == before_source[name][field]
                         for field in ("head", "tail", "deform"))
                 and before[name]["flags"] == before_source[name]["flags"],
                 "The copied bone is not an exact independent Rest/flags input: " + name)
        parent, seen = original.parent, {name}
        while parent is not None and parent.name not in before:
            _require(parent.name not in seen, "Source parent cycle: " + name)
            seen.add(parent.name)
            parent = parent.parent
        _require(before[name]["parent"] == (parent.name if parent else None),
                 "The copied retained parent chain differs: " + name)
    try:
        for name in names:
            for field, value in zip(FLAGS, STANDARD):
                setattr(copied.data.bones[name], field, value)
        after = _state(copied)
        _require(all(row["flags"] == STANDARD for row in after.values()), "Native flag writes did not stick.")
        _require(_geometry(after) == _geometry(before), "Flag writes changed copied parents or geometric Rest.")
        _require(_state(source) == before_source, "Flag preparation changed the source rig.")
    except Exception as error:
        failures = []
        for name in names:
            for field, value in zip(FLAGS, before[name]["flags"]):
                try:
                    setattr(copied.data.bones[name], field, value)
                except Exception as restore_error:
                    failures.append(name + "." + field + ": " + str(restore_error))
        try:
            _require(_state(copied) == before, "Copied Rest/flags rollback is incomplete.")
            _require(_state(source) == before_source, "Source preservation could not be proved.")
        except Exception as restore_error:
            failures.append(str(restore_error))
        raise StandardParentCandidateError("Standard-parent candidate failed: " + str(error), failures) from error
    return {"bone_count": len(names), "original_flags": {name: dict(zip(FLAGS, before[name]["flags"])) for name in names},
            "standard_flags": {name: dict(zip(FLAGS, after[name]["flags"])) for name in names},
            "source_retained_flags": {name: dict(zip(FLAGS, before_source[name]["flags"])) for name in names},
            "parents_and_geometric_Rest_preserved": True, "source_bone_Rest_and_flags_preserved": True,
            "TRS_projection_verified": False, "source_Manual_equivalent": False,
            "FBX_verified": False, "Unity_verified": False, "export_authorized": False}
