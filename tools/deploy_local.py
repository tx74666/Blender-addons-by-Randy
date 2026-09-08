"""Deploy canonical repository sources and verify every shipped file.

No directory moves, deletion, or junction creation. Changed destination files
are backed up before atomic replacement. --check performs no writes.
"""

import argparse
import ast
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from build_releases import PACKAGES, ROOT, version_of


def shipped_files(folder):
    return {
        path.relative_to(folder): path
        for path in folder.rglob("*")
        if path.is_file()
        and not any(part.startswith(".") or part == "__pycache__"
                    for part in path.relative_to(folder).parts)
        and path.suffix not in {".pyc", ".pyo"}
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender-version", default="5.2")
    parser.add_argument("--addons-dir", type=Path, help="Override Blender's destination add-ons folder")
    parser.add_argument("--project-addons", type=Path, help="Also update an X project Character Designer copy")
    parser.add_argument("--module", action="append", choices=tuple(PACKAGES),
                        help="Deploy/check only this module; repeat to select several")
    parser.add_argument("--check", action="store_true", help="Report differences without writing")
    args = parser.parse_args()
    if args.addons_dir is None:
        if not os.environ.get("APPDATA"):
            parser.error("Pass --addons-dir on systems without Windows APPDATA")
        addons = Path(os.environ["APPDATA"]) / "Blender Foundation" / "Blender" / args.blender_version / "scripts" / "addons"
    else:
        addons = args.addons_dir
    selected_modules = tuple(dict.fromkeys(args.module or PACKAGES))
    jobs = [(module, addons / module) for module in selected_modules]
    if args.project_addons and "character_designer" in selected_modules:
        jobs.append(("character_designer", args.project_addons / "character_designer"))

    plans = []
    for module, target in jobs:
        source = ROOT / "addons" / module
        if target.resolve() == source.resolve():
            raise ValueError(f"Destination is the canonical source itself: {target}")
        if (target / "__init__.py").is_file():
            installed_version = tuple(map(int, version_of(target).split(".")))
            source_version = tuple(map(int, version_of(source).split(".")))
            if installed_version > source_version:
                raise ValueError(
                    f"Installed {module} {version_of(target)} is newer than the canonical "
                    f"{version_of(source)}; select the intended --module or reconcile its source first."
                )
        files = shipped_files(source)
        for relative, path in files.items():
            if relative.suffix == ".py":
                ast.parse(path.read_bytes(), filename=str(path))
            destination = target / relative
            if not destination.resolve().is_relative_to(target.resolve()):
                raise ValueError(f"Destination file resolves outside its add-on: {destination}")
        extras = {p.relative_to(target) for p in target.rglob("*.py")
                  if "__pycache__" not in p.parts} - set(files)
        if extras:
            raise ValueError(f"Extra Python modules in {target}; review them before deploying: {sorted(map(str, extras))}")
        changed = [relative for relative, path in files.items()
                   if not (target / relative).is_file() or path.read_bytes() != (target / relative).read_bytes()]
        plans.append((module, source, target, files, changed))

    backup_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CodexBackups" / "addon-deploy"
    backup = backup_root / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    changes = []
    for index, (module, source, target, files, changed) in enumerate(plans):
        if not args.check:
            for relative in changed:
                destination = target / relative
                saved = backup / str(index) / module / relative
                if destination.exists():
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, saved)
                changes.append({"destination": str(destination), "previous": str(saved) if saved.exists() else None})
                backup.mkdir(parents=True, exist_ok=True)
                (backup / "changes.json").write_text(json.dumps(changes, indent=2), encoding="utf-8")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".deploy-", suffix=".tmp", delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(files[relative].read_bytes())
                os.replace(temporary, destination)
            assert all(path.read_bytes() == (target / relative).read_bytes() for relative, path in files.items())
        print(json.dumps({"module": module, "version": version_of(source), "source": str(source),
                          "destination": str(target), "files": len(files),
                          "different_files" if args.check else "updated_files": len(changed)}, ensure_ascii=False))
    if changes:
        print(f"Previous files and deployment record: {backup}")
    if args.check and any(plan[4] for plan in plans):
        raise SystemExit(1)
    print("SOURCE_DEPLOYMENT_MATCH" if not args.check or not any(plan[4] for plan in plans) else "MISMATCH")


if __name__ == "__main__":
    main()
