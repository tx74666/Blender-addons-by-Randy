"""Deploy verified native Ring Group or Arc Mask without replacing other assets."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True
ASSETS = {"ring_group": ("Ring Group", "Randy_Ring_Group.blend"),
          "arc_mask": ("Arc Mask", "Randy_Arc_Mask.blend")}

def deploy(args):
    name, filename = ASSETS[args.kind]
    path = Path(__file__).with_name("deploy_ring_mask.py")
    spec = importlib.util.spec_from_file_location("_ring_nodes_guarded_deploy", path)
    guarded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guarded)
    guarded.ASSET_NAME, guarded.FILENAME = name, filename
    return guarded.deploy(args)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=tuple(ASSETS), required=True)
    for name in ("asset", "verification", "library", "backups"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--report")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check and not args.report:
        parser.error("--report is required for deployment")
    print(json.dumps(deploy(args), indent=2))

if __name__ == "__main__":
    main()
