using System;
using UnityEngine;

namespace CharacterDesigner.Unity.Editor
{
    /// <summary>
    /// Scene-independent golden validation. The fixture is produced by Blender's
    /// actual Python/mathutils implementation, not by this C# port. Call Run(json)
    /// from an editor validation command; it neither loads nor saves any scene.
    /// </summary>
    public static class ForearmCorrectionMathValidation
    {
        [Serializable]
        public sealed class Result
        {
            public int twistCases, deltaCases, inverseCases, profileCases, rangeCases;
            public float maxAngleError, maxDeltaError, maxInverseRelativeError;
            public string sourceMathSha256;
        }

        // Populated by JsonUtility from Blender's independent golden data.
#pragma warning disable CS0649
        [Serializable] private sealed class Fixture
        {
            public int schema;
            public string matrixLayout, sourceMathSha256;
            public float tolerance;
            public TwistCase[] twistCases;
            public DeltaCase[] deltaCases;
            public InverseCase[] inverseCases;
            public ProfileCase[] profileCases;
            public RangeCase[] rangeCases;
        }
        [Serializable] private sealed class TwistCase
        {
            public string name;
            public float[] lower, hand, axis;
            public bool ok;
            public float angle;
        }
        [Serializable] private sealed class DeltaCase
        {
            public string name;
            public float[] lower, hand, axis, point, pivot, expected;
            public float ratio, angle, lowerWeight, handWeight, influence;
        }
        [Serializable] private sealed class InverseCase
        {
            public string name;
            public float[] matrix, expected;
            public float maxCondition;
            public bool ok;
        }
        [Serializable] private sealed class ProfileCase
        {
            public string name;
            public Vector2[] knots;
            public float position, expected;
        }
        [Serializable] private sealed class RangeCase
        {
            public string name;
            public float position, first, last, transition, expected;
        }

#pragma warning restore CS0649
        public static Result Run(string fixtureJson)
        {
            Fixture fixture = JsonUtility.FromJson<Fixture>(fixtureJson);
            Require(fixture != null && fixture.schema == 1 && fixture.matrixLayout == "row-major",
                "Unsupported Blender math fixture schema.");
            Require(fixture.tolerance > 0f && fixture.tolerance <= 1e-4f, "Invalid fixture tolerance.");
            float tolerance = fixture.tolerance;
            Result result = new Result { sourceMathSha256 = fixture.sourceMathSha256 };

            foreach (TwistCase item in fixture.twistCases)
            {
                bool success = ForearmCorrectionMath.TryTwistAngle(Matrix(item.lower), Matrix(item.hand),
                    Vector(item.axis), out float actual, out string error);
                Require(success == item.ok, item.name + ": twist result mismatch: " + error);
                if (success)
                {
                    float difference = Mathf.Abs(actual - item.angle);
                    result.maxAngleError = Mathf.Max(result.maxAngleError, difference);
                    Require(difference <= tolerance, item.name + ": twist differs from Blender by " + difference);
                }
                ++result.twistCases;
            }

            foreach (DeltaCase item in fixture.deltaCases)
            {
                Vector3 actual = ForearmCorrectionMath.ExtraDelta(Vector(item.point), Matrix(item.lower),
                    Matrix(item.hand), Vector(item.axis), Vector(item.pivot), item.ratio, item.angle,
                    item.lowerWeight, item.handWeight, item.influence);
                Vector3 expected = Vector(item.expected);
                float difference = (actual - expected).magnitude;
                result.maxDeltaError = Mathf.Max(result.maxDeltaError, difference);
                Require(ForearmCorrectionMath.IsFinite(actual) && difference <= tolerance,
                    item.name + ": extra post-skin delta differs from Blender by " + difference);
                ++result.deltaCases;
            }

            foreach (InverseCase item in fixture.inverseCases)
            {
                Matrix4x4 source = Matrix(item.matrix);
                bool success = ForearmCorrectionMath.TryInverseLinear(source, out Matrix4x4 actual,
                    out string error, item.maxCondition);
                Require(success == item.ok, item.name + ": inverse result mismatch: " + error);
                if (success)
                {
                    Matrix4x4 expected = Matrix(item.expected);
                    for (int r = 0; r < 3; ++r)
                        for (int c = 0; c < 3; ++c)
                        {
                            float difference = Mathf.Abs(actual[r,c] - expected[r,c]) /
                                Mathf.Max(1f, Mathf.Abs(expected[r,c]));
                            result.maxInverseRelativeError = Mathf.Max(result.maxInverseRelativeError, difference);
                            Require(difference <= tolerance,
                                item.name + ": inverse differs from Blender by " + difference);
                        }
                    Vector3 probe = new Vector3(0.13f, -0.27f, 0.41f);
                    Require((actual.MultiplyVector(source.MultiplyVector(probe)) - probe).magnitude <= tolerance,
                        item.name + ": inverse round trip failed.");
                }
                ++result.inverseCases;
            }

            foreach (ProfileCase item in fixture.profileCases)
            {
                Require(ForearmCorrectionMath.TryProfileRatio(item.position, item.knots,
                    out float actual, out string error), item.name + ": invalid profile: " + error);
                Require(Mathf.Abs(actual - item.expected) <= tolerance, item.name + ": saved profile mismatch.");
                ++result.profileCases;
            }
            foreach (RangeCase item in fixture.rangeCases)
            {
                Require(ForearmCorrectionMath.TryRangeInfluence(item.position, item.first, item.last,
                    item.transition, out float actual, out string error), item.name + ": invalid range: " + error);
                Require(Mathf.Abs(actual - item.expected) <= tolerance, item.name + ": range gate mismatch.");
                ++result.rangeCases;
            }

            // Non-finite values are not legal JSON and are therefore tested here.
            Matrix4x4 invalid = Matrix4x4.identity;
            invalid.m00 = float.NaN;
            Require(!ForearmCorrectionMath.TryRigidRotation(invalid, out _, out _), "NaN rotation was accepted.");
            Require(!ForearmCorrectionMath.TryInverseLinear(invalid, out _, out _), "NaN inverse was accepted.");
            Require(!ForearmCorrectionMath.TryTwistAngle(Matrix4x4.identity, Matrix4x4.identity,
                new Vector3(0f, float.PositiveInfinity, 0f), out _, out _), "Infinite axis was accepted.");
            Require(!ForearmCorrectionMath.TryProfileRatio(0.5f,
                new[] { new Vector2(0f, 0f), new Vector2(0f, 1f) }, out _, out _),
                "Duplicate profile positions were accepted.");
            Require(!ForearmCorrectionMath.TryRangeInfluence(0.5f, 0.8f, 0.2f, 0.1f, out _, out _),
                "Reversed boundaries were accepted.");
            return result;
        }

        private static Matrix4x4 Matrix(float[] data)
        {
            Require(data != null && data.Length == 16, "Fixture matrix must have 16 row-major entries.");
            Matrix4x4 matrix = default;
            for (int r = 0; r < 4; ++r)
                for (int c = 0; c < 4; ++c) matrix[r,c] = data[4 * r + c];
            return matrix;
        }

        private static Vector3 Vector(float[] data)
        {
            Require(data != null && data.Length == 3, "Fixture vector must have three entries.");
            return new Vector3(data[0], data[1], data[2]);
        }

        private static void Require(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message);
        }
    }
}
