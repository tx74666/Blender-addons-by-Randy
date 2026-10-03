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


def finalize(asset_path, verification_path, backups=None):
    from finalize_native_ring_nodes import finalize as publish_native
    backup_root = backups or ROOT / "node_library" / "history"
    return publish_native("mix_shaders", asset_path, verification_path, backup_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--backups", type=Path, default=ROOT / "node_library" / "history")
    args = parser.parse_args()
    print(json.dumps(finalize(args.asset.resolve(), args.verification.resolve(), args.backups.resolve()), indent=2))


if __name__ == "__main__":
    main()
