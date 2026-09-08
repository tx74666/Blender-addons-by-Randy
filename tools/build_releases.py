"""Package each add-on without overwriting a different release of the same version."""

import ast
import hashlib
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "random_realm_builder_exporter": "rr_helper",
    "character_designer": "character_designer",
}


def version_of(source):
    tree = ast.parse((source / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "bl_info"
            for target in node.targets
        ):
            info = ast.literal_eval(node.value)
            return ".".join(str(part) for part in info["version"])
    raise ValueError(f"Missing bl_info: {source}")


def build(module, label):
    source = ROOT / "addons" / module
    files = sorted(
        path for path in source.rglob("*")
        if path.is_file()
        and not any(part.startswith(".") or part == "__pycache__"
                    for part in path.relative_to(source).parts)
        and path.suffix not in {".pyc", ".pyo"}
    )
    payload = {f"{module}/{path.relative_to(source).as_posix()}": path.read_bytes() for path in files}
    archive = ROOT / "dist" / f"{label}-{version_of(source)}.zip"
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
    print(f"Verified {archive.name}: {len(payload)} files")


def main():
    (ROOT / "dist").mkdir(exist_ok=True)
    for module, label in PACKAGES.items():
        build(module, label)
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
             for path in sorted((ROOT / "dist").glob("*.zip"))]
    (ROOT / "dist" / "SHA256SUMS.txt").write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
