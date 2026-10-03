"""Guarded deployment of the current multifunction Ring Mask, not its fixture."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ASSET_NAME = "Ring Mask"
FILENAME = "Randy_Ring_Mask.blend"


def deploy(args):
    evidence = json.loads(Path(args.verification).read_text(encoding="utf-8-sig"))
    metadata = evidence.get("asset_metadata", {})
    # Never accept the historical radial asset under the same visible filename.
    outputs = [(s.get("name"), s.get("type")) for s in metadata.get("outputs", [])]
    if metadata.get("version") != "0.2.1" or outputs != [
            ("Mask", "NodeSocketFloat"), ("Ring Data", "NodeSocketBundle")]:
        raise ValueError("Deployment requires current Ring Mask 0.2.1 with Ring Data.")
    path = Path(__file__).with_name("deploy_ring_mask.py")
    spec = importlib.util.spec_from_file_location("_current_ring_mask_guarded_deploy", path)
    guarded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guarded)
    guarded.ASSET_NAME, guarded.FILENAME = ASSET_NAME, FILENAME
    return guarded.deploy(args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("asset", "verification", "library", "backups"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--report")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check and not args.report:
        parser.error("--report is required for deployment")
    print(json.dumps(deploy(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
