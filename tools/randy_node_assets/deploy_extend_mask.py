"""Deploy a verified Extend Mask while preserving the existing personal library.

The shared catalog/deployment guard remains unchanged. Backups and receipts
must stay outside the library, and --check is read-only.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ASSET_NAME = "Extend Mask"
FILENAME = "Randy_Extend_Mask.blend"


def deploy(args):
    path = Path(__file__).with_name("deploy_ring_mask.py")
    spec = importlib.util.spec_from_file_location("_extend_mask_guarded_deploy", path)
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
