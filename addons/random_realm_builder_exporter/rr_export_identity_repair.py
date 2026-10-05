"""Explicit, metadata-only rebinding to a verified existing Unity package.

This module has no Blender dependency and never writes Unity files. Callers
provide the real scene/root lookups; a file picker alone grants no ownership.
"""

import copy
import hashlib
import json
import ntpath
from pathlib import Path
import re
import uuid


STABLE_PROP = "rr_export_stable_id"
LAST_PROP = "rr_export_last_id"
PREVIOUS_PROP = "rr_export_previous_ids"
ASSET_PROP = "rr_export_asset_id_override"
CORE_PROPS = ("rr_reference_marked", "rr_reference_stable_id")
IDENTITY_PROPS = (STABLE_PROP, LAST_PROP, PREVIOUS_PROP, ASSET_PROP) + CORE_PROPS
_ID_PATTERN = re.compile(r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*\Z")


def _valid_id(value):
    return isinstance(value, str) and bool(_ID_PATTERN.fullmatch(value))


def _previous(values, current_id):
    if not isinstance(values, (list, tuple)) or any(not _valid_id(value) for value in values):
        raise ValueError("The package contains invalid previous asset names.")
    result, seen = [], {current_id.casefold()}
    for value in values:
        if value.casefold() not in seen:
            result.append(value)
            seen.add(value.casefold())
    return result


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_export_package(selection):
    """Accept a package folder, manifest, model, or their Unity .meta path.

    The returned fingerprint includes the model even for legacy manifests
    without a hash contract, so applying a preview can detect later edits.
    """
    selected = Path(str(selection)).expanduser()
    if selected.name.lower().endswith(".meta"):
        selected = selected.with_name(selected.name[:-5])
    if selected.is_symlink():
        raise ValueError("Choose a local asset package rather than a symbolic link.")
    selected = selected.resolve()
    if selected.is_dir():
        package = selected
    elif selected.is_file() and (selected.name == "manifest.json" or selected.suffix.lower() == ".fbx"):
        package = selected.parent
    else:
        raise ValueError("Choose an asset folder, manifest.json, or its model.fbx.")
    if not _valid_id(package.name):
        raise ValueError("The asset folder must use an exported ASCII asset name.")
    marker = package.parent / f".rr-{package.name}.publishing"
    if marker.exists():
        raise ValueError("This asset is being published or awaiting recovery; try again after it finishes.")
    manifest_path = package / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("The package manifest must be a local file inside its asset folder.")
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("The selected folder has no readable RR Helper manifest.json.") from error
    if not isinstance(manifest, dict) or manifest.get("id") != package.name:
        raise ValueError("The manifest asset name does not match its folder.")
    if manifest.get("group") or manifest.get("variantGroupId"):
        raise ValueError("This is a managed group or Variant package; use its group workflow to repair it.")
    stable_id = manifest.get("stableId", "")
    if not isinstance(stable_id, str) or not stable_id.strip() or any(ord(char) < 32 for char in stable_id):
        raise ValueError("This package has no valid persistent asset identity; export it with RR Helper first.")
    model_file = manifest.get("modelFile", "")
    if not isinstance(model_file, str) or not model_file or ntpath.isabs(model_file):
        raise ValueError("This package has no safe model file path.")
    model_parts = model_file.replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} or ":" in part for part in model_parts):
        raise ValueError("The model file path must remain inside the selected package.")
    model_path = package.joinpath(*model_parts).resolve()
    if not model_path.is_relative_to(package) or not model_path.is_file() or not model_path.stat().st_size:
        raise ValueError("The model file is missing, empty, or outside its package.")
    if selected.is_file() and selected.suffix.lower() == ".fbx" and selected != model_path:
        raise ValueError("The selected FBX does not match this package's manifest.")
    contract = manifest.get("uvExport") or {}
    if not isinstance(contract, dict):
        raise ValueError("The package's model contract is invalid.")
    digest = _sha256(model_path)
    for expected in (manifest.get("modelSha256"), contract.get("modelSha256")):
        if expected is not None and (not isinstance(expected, str)
                or not re.fullmatch(r"[a-fA-F0-9]{64}", expected) or expected.lower() != digest):
            raise ValueError("The model does not match its manifest SHA-256; export or recover this package first.")
    if marker.exists():
        raise ValueError("The asset changed while being inspected; wait for publishing to finish.")
    return {
        "asset_id": package.name, "stable_id": stable_id.strip(),
        "previous_ids": _previous(manifest.get("previousIds", []), package.name),
        "package_dir": str(package), "manifest_path": str(manifest_path),
        "model_path": str(model_path), "model_sha256": digest,
        "fingerprint": hashlib.sha256(manifest_bytes).hexdigest() + ":" + digest,
        "label": str(manifest.get("displayName") or package.name.replace("_", " ")),
        "source_object": str(manifest.get("sourceObject") or ""),
        "source_blend": str(manifest.get("sourceBlend") or ""),
    }


def _metadata(root):
    return {key: copy.deepcopy(root[key]) for key in IDENTITY_PROPS if key in root}


def _lookup_state(root, callbacks):
    return {
        "name": str(root.name), "metadata": _metadata(root),
        "asset_id": callbacks["asset_id"](root),
        "previous_ids": list(callbacks["previous_ids"](root)),
        "core": bool(callbacks["is_core"](root)),
        "group": bool(callbacks["is_group"](root)),
        "editable": bool(callbacks["is_editable"](root)),
        "unbound_asset_id": callbacks["unbound_asset_id"](root),
    }


def _conflicts(states, touched):
    conflicts = []
    for index, (root, state) in enumerate(states):
        for other, peer in states[index + 1:]:
            if id(root) not in touched and id(other) not in touched:
                continue
            labels = f"{state['name']} and {peer['name']}"
            stable, peer_stable = state["stable_id"], peer["stable_id"]
            if stable and stable.casefold() == peer_stable.casefold():
                conflicts.append(f"{labels} share a persistent asset identity.")
            if state["asset_id"].casefold() == peer["asset_id"].casefold():
                conflicts.append(f"{labels} would write the same asset folder.")
            for owner, holder in ((state, peer), (peer, state)):
                if holder["asset_id"].casefold() in {value.casefold() for value in owner["previous_ids"]}:
                    conflicts.append(f"{owner['name']} still claims the previous asset name used by {holder['name']}.")
    return list(dict.fromkeys(conflicts))


def build_rebind_plan(target, package, roots, *, asset_id, previous_ids,
                      is_core, is_group, is_editable, unbound_asset_id=None, detach=()):
    """Preview a binding and only explicitly listed independent copies.

    ``is_core`` must check actual Scene pointers across all scenes. ``is_group``
    must include managed roots and members. No metadata is changed by preview;
    a different live identity cannot be detached just because it owns an alias.
    """
    verified = read_export_package(package["package_dir"] if isinstance(package, dict) else package)
    if isinstance(package, dict) and package.get("fingerprint") != verified["fingerprint"]:
        raise ValueError("The Unity package changed; inspect it again before applying.")
    package = verified
    callbacks = dict(asset_id=asset_id, previous_ids=previous_ids, is_core=is_core,
                     is_group=is_group, is_editable=is_editable,
                     unbound_asset_id=unbound_asset_id or asset_id)
    detached = list({id(root): root for root in detach if root is not None}.values())
    roots = list({id(root): root for root in list(roots) + [target] + detached if root is not None}.values())
    if target is None:
        raise ValueError("Choose the Blender object to pair with this asset.")
    observed = [(root, _lookup_state(root, callbacks)) for root in roots]
    by_id = {id(root): state for root, state in observed}
    target_state = by_id[id(target)]
    if not target_state["editable"] or target_state["group"]:
        raise ValueError("Choose an editable independent object; managed groups require their group workflow.")
    if any(root is target for root in detached):
        raise ValueError("The paired object cannot also be detached.")
    changes, desired = [], {}
    for root in detached:
        state = by_id[id(root)]
        if state["core"] or state["group"] or not state["editable"]:
            raise ValueError(f"{state['name']} is Core, managed, or read-only and cannot be detached.")
        if str(root.get(STABLE_PROP, "") or "").strip().casefold() != package["stable_id"].casefold():
            raise ValueError(f"{state['name']} is a different asset owner, not a copy of this package.")
        current = state["unbound_asset_id"]
        if not _valid_id(current):
            raise ValueError(f"{state['name']} has no valid independent export name.")
        values = {STABLE_PROP: f"rr_asset_{uuid.uuid4().hex}", LAST_PROP: current,
                  PREVIOUS_PROP: "[]"}
        changes.append(dict(root=root, kind="independent_copy", label=state["name"],
                            values=values, remove=(ASSET_PROP,) + CORE_PROPS))
        desired[id(root)] = dict(name=state["name"], asset_id=current,
                                stable_id=values[STABLE_PROP], previous_ids=[])
    history = list(package["previous_ids"])
    if str(target.get(STABLE_PROP, "") or "").strip().casefold() == package["stable_id"].casefold():
        history.extend(target_state["previous_ids"])
        history.extend(value for value in (target.get(LAST_PROP), target_state["asset_id"]) if value)
    history = _previous(history, package["asset_id"])
    values = {STABLE_PROP: package["stable_id"], LAST_PROP: package["asset_id"],
              PREVIOUS_PROP: json.dumps(history, ensure_ascii=True), ASSET_PROP: package["asset_id"]}
    if target_state["core"]:
        values["rr_reference_stable_id"] = package["stable_id"]
    changes.insert(0, dict(root=target, kind="paired_asset", label=target_state["name"],
                           values=values, remove=() if target_state["core"] else CORE_PROPS))
    desired[id(target)] = dict(name=target_state["name"], asset_id=package["asset_id"],
                              stable_id=package["stable_id"], previous_ids=history)
    states = [(root, desired.get(id(root), dict(name=state["name"], asset_id=state["asset_id"],
               stable_id=str(root.get(STABLE_PROP, "") or "").strip(), previous_ids=state["previous_ids"])))
              for root, state in observed]
    conflicts = _conflicts(states, set(desired))
    if conflicts:
        raise ValueError("The proposed pairing still conflicts:\n" + "\n".join(conflicts))
    return dict(target=target, package=package, observed_roots=observed,
                changes=changes, callbacks=callbacks)


def apply_rebind_plan(plan, *, validate):
    """Recheck preview, apply custom properties, validate, or restore them all.

    ``validate(root)`` must use the existing all-scene export validator, not a
    filtered list from this preview. Callers may wrap this in a Blender Undo
    operator; applying never exports or writes the selected Unity package.
    """
    package = plan["package"]
    if read_export_package(package["package_dir"])["fingerprint"] != package["fingerprint"]:
        raise ValueError("The Unity package changed; inspect it again before applying.")
    for root, observed in plan["observed_roots"]:
        if _lookup_state(root, plan["callbacks"]) != observed:
            raise ValueError("Blender objects changed since the preview; inspect the pairing again.")
    snapshots = [(change["root"], _metadata(change["root"])) for change in plan["changes"]]
    try:
        for change in plan["changes"]:
            root = change["root"]
            for key in change["remove"]:
                if key in root:
                    del root[key]
            for key, value in change["values"].items():
                root[key] = value
        for root, _snapshot in snapshots:
            if validate(root) is False:
                raise ValueError("The existing export validator rejected this pairing.")
        if read_export_package(package["package_dir"])["fingerprint"] != package["fingerprint"]:
            raise ValueError("The Unity package changed while applying; inspect it again.")
    except Exception:
        for root, snapshot in snapshots:
            for key in IDENTITY_PROPS:
                if key in root and key not in snapshot:
                    del root[key]
            for key, value in snapshot.items():
                root[key] = value
        raise
    return plan
