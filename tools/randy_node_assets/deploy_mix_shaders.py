"""Deploy verified Mix Shaders beside Ring Mask in an existing node library.

Use ordinary Python. Reuses Ring Mask's verified catalog merge, backups, atomic
replacement, conflict detection and rollback. --check is entirely read-only.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True


ASSET_NAME = "Mix Shaders"
FILENAME = "Randy_Mix_Shaders.blend"


def deploy(args):
    # Parameterize the existing safety implementation for this second shader
    # asset in an isolated module. Ring Mask's module globals stay unchanged.
    implementation = Path(__file__).with_name("deploy_ring_mask.py")
    spec = importlib.util.spec_from_file_location("_mix_shaders_guarded_deploy", implementation)
    guarded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guarded)
    guarded.ASSET_NAME = ASSET_NAME
    guarded.FILENAME = FILENAME
    result = guarded.deploy(args)
    result["deployment_script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["guarded_deployment_sha256"] = hashlib.sha256(Path(guarded.__file__).read_bytes()).hexdigest()
    if not args.check:
        # The guarded transaction already saved the complete deployment report.
        # Do not rewrite that report after its concurrency-protected commit.
        result["report_note"] = "Saved report identifies the reused guarded Ring Mask deployment implementation."
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--verification", required=True)
    parser.add_argument("--library", required=True)
    parser.add_argument("--backups", required=True)
    parser.add_argument("--report")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check and not args.report:
        parser.error("--report is required for deployment")
    print(json.dumps(deploy(args), indent=2))


if __name__ == "__main__":
    main()
