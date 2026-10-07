"""Unregistered, model-only Direct omission adapter; no public exporter uses it.

This preserves plain native skin data, not the Body-attached/Cloth final surface.
It neither exports FBX nor authorizes skeletal animation or a physics bake.
Only the existing Direct hooks may change a disposable model snapshot.
"""

import inspect
import json
from pathlib import Path


SURFACE = "PLAIN_NATIVE_SKIN_V1"
BACKEND = "DIRECT_MAIN_CLOTH_V1"
SCHEMA = "cdesigner.plain-native-skin.model.v1"
VERSION = 1
_HOOKS = {"export_capture": ("source",), "validate_snapshot": ("source", "proof"),
          "strip_export_snapshot": ("source", "proof")}
_CHANNELS = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale")
_OMISSION = {"surface": SURFACE, "simulation_baked": False, "physics_omitted": True,
             "manual_original_preserved": True, "body_attachment_omitted": True,
             "final_surface_equivalent": False}


class PlainNativeSkinError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise PlainNativeSkinError("Plain Dress model snapshot: " + message)


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise PlainNativeSkinError("The model receipt must contain finite JSON data.") from error


def _service():
    # Importing this adapter alone must not import bpy, register handlers or load
    # the physical backend. The caller explicitly invokes an API before this.
    from . import skirt_surface_direct

    return skirt_surface_direct


def _signature(service):
    _require(getattr(service, "BACKEND", None) == BACKEND
             and type(getattr(service, "VERSION", None)) is int and service.VERSION == VERSION,
             "the authoritative Direct backend/version is unknown.")
    result = {}
    for name, expected in _HOOKS.items():
        function = getattr(service, name, None)
        _require(callable(function), "restore the authoritative Direct hook: " + name)
        try:
            parameters = tuple(inspect.signature(function).parameters.values())
        except (TypeError, ValueError) as error:
            raise PlainNativeSkinError("The authoritative Direct hook has no supported signature.") from error
        _require(tuple(p.name for p in parameters) == expected
                 and all(p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
                         and p.default is inspect.Parameter.empty for p in parameters),
                 "the authoritative Direct hook signature changed: " + name)
        result[name] = list(expected)
    return result


def _source_guard(source):
    _require(getattr(source, "type", None) == "MESH" and getattr(source, "data", None) is not None,
             "choose the actual Direct source Mesh.")
    _require(source.data.shape_keys is None, "author Dress Shape Keys remain unsupported; preserve them.")
    _require(not (source.library or source.override_library or source.data.library or source.data.override_library),
             "linked/overridden artist data is unsupported.")


def _author_channels(source, service):
    """Read native data, not evaluated physical matrices or invented poses."""
    rig = source.get(service.skirt.RIG_KEY)
    _require(rig is not None and rig.type == "ARMATURE" and rig.data.pose_position == "POSE",
             "preserve the actual Main Rig in Pose Position before capture/strip.")
    try:
        bones = [{"bone": bone.name, "rotation_mode": bone.rotation_mode,
                  "channels": {name: list(getattr(bone, name)) for name in _CHANNELS}}
                 for bone in sorted(rig.pose.bones, key=lambda item: item.name)]
        result = {"rig": rig.name, "rig_frame": service.shared._frame(rig),
                  "pose_position": rig.data.pose_position, "bones": bones}
    except (AttributeError, TypeError, ValueError) as error:
        raise PlainNativeSkinError("The native Manual/Original channels cannot be proved.") from error
    _require(bool(bones) and len({row["bone"] for row in bones}) == len(bones),
             "the native Main Rig channel inventory is incomplete.")
    return json.loads(_json(result))


def _direct_guard(source, proof):
    _require(type(proof) is dict and type(proof.get("version")) is int
             and proof["version"] == VERSION and proof.get("backend") == BACKEND
             and proof.get("source") == source.name and proof.get("private_snapshot_api_only") is True
             and proof.get("export_verified") is False,
             "the complete authoritative Direct capture is required.")
    _require(_json(proof.get("omission")) == _json(_OMISSION),
             "the authoritative omission semantics are unknown or different.")
    state = proof.get("state")
    _require(type(state) is dict and state.get("mode") == "MANUAL"
             and state.get("editing") is False and type(state.get("pending")) is bool
             and proof.get("mode_value") is False
             and _json(proof.get("cloth_flags")) == "[false,false]",
             "only Manual input with finished Original editing and paused Cloth is supported.")
    rows = proof.get("deform_contract")
    _require(type(rows) is list and rows
             and all(type(row) is dict and type(row.get("bone")) is str and row["bone"]
                     and type(row.get("manual")) is dict and row["manual"].get("mute") is False
                     and type(row.get("physical")) is dict and row["physical"].get("mute") is True for row in rows)
             and len({row["bone"] for row in rows}) == len(rows),
             "Manual/Original input must remain enabled and every old physical output muted.")


def capture(source):
    """Read-only capture; animation and final-surface equivalence are unsupported."""
    _source_guard(source)
    service = _service()
    signature = _signature(service)
    direct_proof = service.export_capture(source)  # Full native graph/driver/Rest proof.
    _direct_guard(source, direct_proof)
    return json.loads(_json({"schema": SCHEMA, "version": VERSION, "scope": "MODEL_SNAPSHOT_ONLY",
                            "surface": SURFACE, "backend": BACKEND,
                            "direct_signature": signature, "direct_proof": direct_proof,
                            "author_channels": _author_channels(source, service),
                            "omission": direct_proof["omission"],
                            "animation_supported": False, "FBX_verified": False,
                            "Unity_verified": False, "export_authorized": False}))


def validate(source, receipt):
    """Reject unknown/tampered/changed capture before any snapshot writes."""
    _require(type(receipt) is dict and receipt.get("schema") == SCHEMA
             and type(receipt.get("version")) is int and receipt["version"] == VERSION
             and receipt.get("scope") == "MODEL_SNAPSHOT_ONLY"
             and receipt.get("surface") == SURFACE and receipt.get("backend") == BACKEND
             and receipt.get("animation_supported") is False and receipt.get("export_authorized") is False,
             "a complete model-only receipt is required; skeletal animation is unsupported.")
    _source_guard(source)
    service = _service()
    _require(_json(receipt.get("direct_signature")) == _json(_signature(service)),
             "the captured Direct hook signature changed.")
    _direct_guard(source, receipt.get("direct_proof"))
    service.validate_snapshot(source, receipt["direct_proof"])
    _require(_json(capture(source)) == _json(receipt),
             "the source graph, omission, native pose or captured state changed.")


def _model_context(source):
    import bpy

    current = Path(bpy.data.filepath).resolve() if bpy.data.filepath else None
    _require(bpy.app.background and current is not None and current.name == "character.blend"
             and current.parent.name.startswith("cdesigner-unity-")
             and bpy.data.objects.get(source.name) == source,
             "prepare/strip only the local disposable model snapshot; animation/artist sessions are forbidden.")


def prepare(source, receipt):
    """Read-only private-model preflight; no timeline seek, mode switch or bake."""
    _model_context(source)
    validate(source, receipt)
    return json.loads(_json(receipt))


def strip(source, receipt):
    """Omit owned absolute output via Direct; preserve plain skin author channels.

This is a disposable component. It does not set Rest Pose, clean a skeleton,
sample an Action, publish a file or establish native FBX/Unity acceptance.
"""
    prepared = prepare(source, receipt)
    service = _service()
    result = service.strip_export_snapshot(source, prepared["direct_proof"])
    expected = dict(prepared["omission"], source=source.name,
                    owner=prepared["direct_proof"]["owner"], backend=BACKEND,
                    private_snapshot_api_only=True, export_verified=False)
    _require(_json(result) == _json(expected), "the authoritative strip returned different omission semantics.")
    _require(_json(_author_channels(source, service)) == _json(prepared["author_channels"]),
             "the private strip changed native Manual/Original channels or rig placement.")
    return dict(result, scope="MODEL_SNAPSHOT_ONLY", animation_supported=False,
                manual_pose_retained_at_strip=True, FBX_verified=False, Unity_verified=False,
                export_authorized=False)
