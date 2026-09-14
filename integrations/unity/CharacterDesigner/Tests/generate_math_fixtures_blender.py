"""Generate Unity golden fixtures from the real Blender/mathutils implementation.

Run with Blender --background --factory-startup --python THIS_FILE -- --output PATH.
This script creates no scene objects and never loads or saves a character scene.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector


ROOT = Path(__file__).resolve().parents[4]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MATH_PATH = ROOT / "addons/character_designer/forearm_twist_math.py"
fm = load_module("forearm_fixture_math", MATH_PATH)
fp = load_module("forearm_fixture_profile", ROOT / "addons/character_designer/forearm_twist_profile.py")


def values(matrix):
    return [float(item) for row in matrix for item in row]


def vector(value):
    return [float(item) for item in value]


def transform(rotation, translation=(0, 0, 0), scale=1.0):
    return Matrix.Translation(translation) @ rotation.to_matrix().to_4x4() @ Matrix.Scale(scale, 4)


def main(output):
    fixture = {
        "schema": 1,
        "matrixLayout": "row-major",
        "blenderVersion": bpy.app.version_string,
        "sourceMathSha256": hashlib.sha256(MATH_PATH.read_bytes()).hexdigest(),
        "tolerance": 0.000004,
        "twistCases": [], "deltaCases": [], "inverseCases": [],
        "profileCases": [], "rangeCases": [],
    }
    identity = Matrix.Identity(4)

    def twist_case(name, lower, hand, axis):
        case = {"name": name, "lower": values(lower), "hand": values(hand), "axis": vector(axis)}
        try:
            case.update(ok=True, angle=fm.twist_angle(lower, hand, axis))
        except fm.TwistMathError as exc:
            case.update(ok=False, error=str(exc))
        fixture["twistCases"].append(case)
        return case

    for side, axis in (("left", Vector((0.7, 0.3, -0.2)).normalized()),
                       ("right", Vector((-0.7, 0.3, -0.2)).normalized())):
        swing_axis = axis.cross(Vector((0, 0, 1))).normalized()
        point = Vector((0.17 if side == "left" else -0.17, 0.21, -0.13))
        pivot = Vector((0.11 if side == "left" else -0.11, 0.18, -0.1))
        for pose in ("rest", "root_turned", "raised_bent", "swing", "uniform_scale"):
            qlower = Quaternion(Vector((0.3, -0.8, 0.5)).normalized(), math.radians(57)) \
                if pose in ("raised_bent", "swing") else Quaternion()
            root = transform(Quaternion(Vector((0.1, 0.4, 0.9)).normalized(), math.radians(113)),
                             (2.3, -1.7, 0.8), 1.7 if pose == "uniform_scale" else 1.0) \
                if pose in ("root_turned", "uniform_scale") else identity
            for degrees in (-120, -90, -45, -15, 0, 15, 45, 90, 120):
                swing = Quaternion(swing_axis, math.radians(41)) if pose == "swing" else Quaternion()
                qhand = qlower @ swing @ Quaternion(axis, math.radians(degrees))
                lower = root @ transform(qlower, (0.04, -0.06, 0.08))
                hand = root @ transform(qhand, (0.07, -0.02, 0.1))
                name = f"{side}_{pose}_{degrees:+d}deg"
                case = twist_case(name, lower, hand, axis)
                transforms = {"lower": lower, "hand": hand, "other": identity}
                for ratio, wl, wh, influence in ((0.0, 0.6, 0.3, 1.0), (0.27, 0.75, 0.25, 1.0),
                                               (0.5, 0.5, 0.5, 0.4), (0.83, 0.05, 0.85, 1.0),
                                               (1.0, 0.0, 1.0, 1.0), (0.61, 0.7, 0.2, 0.0)):
                    weights = {"lower": wl, "hand": wh, "other": 1.0 - wl - wh}
                    actual = fm.blended_matrix(transforms, weights) @ point
                    expected = influence * (fm.desired_vertex(point, transforms, weights, "lower", "hand",
                                                             axis, pivot, ratio, angle=case["angle"]) - actual)
                    fixture["deltaCases"].append({
                        "name": f"{name}_ratio_{ratio}_influence_{influence}",
                        "lower": values(lower), "hand": values(hand), "axis": vector(axis),
                        "point": vector(point), "pivot": vector(pivot), "ratio": ratio,
                        "angle": case["angle"], "lowerWeight": wl, "handWeight": wh,
                        "influence": influence, "expected": vector(expected),
                    })

    axis = Vector((0, 1, 0))
    twist_case("zero_axis_rejected", identity, identity, (0, 0, 0))
    twist_case("reflected_scale_rejected", identity, Matrix.Diagonal((-1, 1, 1, 1)), axis)
    twist_case("zero_scale_rejected", identity, Matrix.Diagonal((0, 0, 0, 1)), axis)
    twist_case("nonuniform_scale_rejected", identity, Matrix.Diagonal((1, 1.1, 1, 1)), axis)
    shear = identity.copy()
    shear[0][1] = 0.02
    twist_case("shear_rejected", identity, shear, axis)
    projective = identity.copy()
    projective[3][0] = 0.001
    twist_case("nonaffine_rejected", identity, projective, axis)
    twist_case("perpendicular_180_swing_rejected", identity,
               Quaternion((1, 0, 0), math.pi).to_matrix().to_4x4(), axis)

    def inverse_case(name, matrix, limit=1e5):
        case = {"name": name, "matrix": values(matrix), "maxCondition": limit}
        linear = matrix.to_3x3()
        norm = math.sqrt(sum(item * item for row in linear for item in row))
        if norm < 1e-9 or abs(linear.determinant()) < 1e-9 * norm ** 3:
            case.update(ok=False, error="singular")
        else:
            inverse = linear.inverted()
            inverse_norm = math.sqrt(sum(item * item for row in inverse for item in row))
            if norm * inverse_norm > limit:
                case.update(ok=False, error="ill-conditioned")
            else:
                case.update(ok=True, expected=values(inverse.to_4x4()))
        fixture["inverseCases"].append(case)

    inverse_case("identity", identity)
    for degrees in (15, 45, 90, 120):
        hand = transform(Quaternion(axis, math.radians(degrees)), (2, 4, -6), 1.8)
        blend = fm.blended_matrix({"a": identity, "b": hand}, {"a": 0.6, "b": 0.4})
        inverse_case(f"lbs_{degrees}", blend)
    inverse_case("singular", Matrix.Diagonal((0, 1, 0, 1)))
    inverse_case("zero", Matrix.Diagonal((0, 0, 0, 1)))
    inverse_case("near_singular", Matrix.Diagonal((0.000001, 1, 1, 1)))
    inverse_case("uniform_tiny_accepted", Matrix.Diagonal((0.0001, 0.0001, 0.0001, 1)))

    knots = [(0.08, 0.13), (0.18, 0.19), (0.51, 0.57), (0.78, 0.79), (0.91, 0.94)]
    rings = [{"position": at, "ratio": ratio} for at, ratio in knots]
    for position in (-0.2, 0.08, 0.11, 0.18, 0.37, 0.51, 0.63, 0.78, 0.88, 0.91, 1.2):
        fixture["profileCases"].append({"name": f"saved_profile_{position}", "position": position,
            "knots": [{"x": at, "y": ratio} for at, ratio in knots],
            "expected": fm.profile_ratio(position, knots)})
    for transition in (0, 0.1, 0.5):
        for position in (0.1, 0.18, 0.181, 0.20, 0.24, 0.51, 0.72, 0.779, 0.78, 0.9):
            fixture["rangeCases"].append({"name": f"range_{transition}_{position}", "position": position,
                "first": rings[1]["position"], "last": rings[3]["position"], "transition": transition,
                "expected": fp.range_influence(position, rings, 1, 3, transition)})

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(fixture, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "counts": {key: len(value) for key, value in fixture.items()
                      if key.endswith("Cases")}}, sort_keys=True))


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    main(parser.parse_args(arguments).output)
