"""Publish an already verified Mix Shaders build into repository metadata.

Ordinary Python only: no Blender, rendering or installed-library changes.
Requires a passed report bound to the exact build and unchanged dependencies.
Writes the new asset, preview, saved evidence and manifest hashes together.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
NAME = "Mix Shaders"
CATALOG_ID = "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
ROLE_SOURCES = {role: "tools/randy_node_assets/{}_mix_shaders.py".format(role)
                for role in ("build", "verify", "deploy")}
DEPENDENCIES = {"addons/random_realm_builder_exporter/rr_shader_mixer.py",
                "tools/randy_node_assets/verify_ring_mask.py",
                "tools/randy_node_assets/deploy_ring_mask.py",
                "tools/randy_node_assets/deploy_assets.py"}


def digest(contents, *, text=False):
    return hashlib.sha256(contents.replace(b"\r\n", b"\n") if text else contents).hexdigest()


def atomic_write(path, contents, *, expected=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".mix-shaders-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        current = path.read_bytes() if path.exists() else None
        if current != expected:
            raise RuntimeError("Destination changed before finalization: {}".format(path))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish_new_or_identical(path, contents):
    if path.exists():
        if path.read_bytes() != contents:
            raise FileExistsError("Refusing to replace an existing different publication: {}".format(path))
    else:
        atomic_write(path, contents)


def finalize(asset_path, verification_path):
    source = asset_path.read_bytes()
    report = json.loads(verification_path.read_text(encoding="utf-8-sig"))
    assert report.get("passed") is True and report.get("state") == "complete"
    assert report.get("tests") and all(test.get("passed") is True for test in report["tests"])
    assert report.get("asset_sha256") == digest(source)
    assert report["asset_metadata"]["name"] == NAME
    assert report["asset_metadata"]["catalog_id"] == CATALOG_ID
    assert set(report["source_dependencies_sha256"]) == DEPENDENCIES
    for path, expected in report["source_dependencies_sha256"].items():
        assert digest((ROOT / path).read_bytes(), text=True) == expected, "Dependency changed: " + path
    ring = ROOT / "node_library/assets/Randy_Ring_Mask.blend"
    assert digest(ring.read_bytes()) == report["ring_mask_sha256"], "Ring Mask changed after verification"
    preview_path = Path(report["render_preview"]).resolve()
    preview = preview_path.read_bytes()
    assert preview.startswith(b"\x89PNG\r\n\x1a\n")
    manifest_path = ROOT / "node_library/manifest.json"
    original = manifest_path.read_bytes()
    manifest = json.loads(original.decode("utf-8-sig"))
    bundle = {"id": "mix_shaders", "path": "node_library/assets/Randy_Mix_Shaders.blend",
              "sha256": digest(source), "source": ROLE_SOURCES,
              "source_sha256": {path: digest((ROOT/path).read_bytes(), text=True) for path in ROLE_SOURCES.values()},
              "source_dependencies_sha256": report["source_dependencies_sha256"],
              "verification": "node_library/validation/mix_shaders.json"}
    asset = {"id": "mix_shaders", "name": NAME, "display_name": NAME, "version": "0.1.0",
             "node_tree_type": "ShaderNodeTree", "bundle_id": "mix_shaders",
             "catalog_path": "Textures", "catalog_id": CATALOG_ID,
             "description": "Expandable Mask / Shader pairs over a Base Shader; earlier slots cover later slots.",
             "interface_note": "_Connected 1 is a hidden per-instance RR Helper connection-state input.",
             "runtime_note": "Keep RR Helper enabled for slot expansion and automatic empty-Shader detection. Saved graphs render natively.",
             "inputs": [{"name": "Base Shader", "type": "NodeSocketShader"},
                        {"name": "Mask 1", "type": "NodeSocketFloat", "default": 0.0},
                        {"name": "Shader 1", "type": "NodeSocketShader"},
                        {"name": "_Connected 1", "type": "NodeSocketFloat", "default": 0.0, "hidden": True}],
             "outputs": [{"name": "Shader", "type": "NodeSocketShader"}]}
    for key, entry in (("bundles", bundle), ("assets", asset)):
        matches = [index for index, existing in enumerate(manifest[key]) if existing["id"] == "mix_shaders"]
        assert len(matches) <= 1
        if matches:
            manifest[key][matches[0]] = entry
        else:
            manifest[key].append(entry)
    manifest["library_version"] = "0.1.2"
    publish_new_or_identical(ROOT / bundle["path"], source)
    publish_new_or_identical(ROOT / "node_library/previews/mix_shaders.png", preview)
    report["published_asset"] = bundle["path"]
    report["published_preview"] = "node_library/previews/mix_shaders.png"
    evidence = json.dumps(report, indent=2).encode("utf-8") + b"\n"
    publish_new_or_identical(ROOT / bundle["verification"], evidence)
    atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n", expected=original)
    return {"passed": True, "library_version": "0.1.2", "asset": bundle["path"],
            "asset_sha256": bundle["sha256"], "verification": bundle["verification"],
            "tests": len(report["tests"]), "shader_samples": report["shader_sample_count"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(finalize(args.asset.resolve(), args.verification.resolve()), indent=2))


if __name__ == "__main__":
    main()
