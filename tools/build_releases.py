"""Package each add-on without overwriting a different release of the same version."""

import argparse
import hashlib
from pathlib import Path
import zipfile

try:
    from release_projection import load_source, version_of
except ModuleNotFoundError as exc:
    if exc.name != "release_projection":
        raise
    # Preserve importing this script by file path from repository tests.
    from tools.release_projection import load_source, version_of


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "random_realm_builder_exporter": "rr_helper",
    "character_designer": "character_designer",
}


def build(module, label, projection=None):
    source = load_source(module, projection, root=ROOT)
    payload = {f"{module}/{relative.as_posix()}": source.files[relative]
               for relative in sorted(source.files)}
    archive = ROOT / "dist" / f"{label}-{source.version}.zip"
    if archive.exists():
        with zipfile.ZipFile(archive) as package:
            existing = {entry.filename: package.read(entry) for entry in package.infolist() if not entry.is_dir()}
            if package.testzip() is not None or existing != payload:
                raise ValueError(f"Source differs from {archive.name}; increase bl_info version first.")
    else:
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
            for name, content in payload.items():
                entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o644 << 16
                package.writestr(entry, content)
    projection_note = f"; approved projection SHA256 {source.projection_sha256}" if projection else ""
    print(f"Verified {archive.name}: {len(payload)} files{projection_note}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", action="append", choices=tuple(PACKAGES),
                        help="Build only this module; repeat to select several")
    parser.add_argument("--projection", type=Path,
                        help="Use a complete hash-locked approved projection manifest for one --module")
    args = parser.parse_args(argv)
    selected = tuple(dict.fromkeys(args.module or PACKAGES))
    if args.projection and len(selected) != 1:
        parser.error("--projection requires exactly one --module")
    (ROOT / "dist").mkdir(exist_ok=True)
    for module in selected:
        build(module, PACKAGES[module], args.projection)
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
             for path in sorted((ROOT / "dist").glob("*.zip"))]
    (ROOT / "dist" / "SHA256SUMS.txt").write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
