using System;
using UnityEngine;

namespace CharacterDesigner.Unity
{
    /// <summary>
    /// Port of Character Designer's forearm_twist_math.py. All points, rest axes,
    /// pivots and deformation matrices must use the same coordinate system.
    /// A deformation matrix is pose * inverse(rest), not a bone pose alone.
    /// These methods allocate no managed objects on their successful hot paths.
    /// </summary>
    public static class ForearmCorrectionMath
    {
        public static bool IsFinite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);

        public static bool IsFinite(Vector3 value) =>
            IsFinite(value.x) && IsFinite(value.y) && IsFinite(value.z);

        public static bool IsFinite(in Matrix4x4 value)
        {
            for (int i = 0; i < 16; ++i)
                if (!IsFinite(value[i])) return false;
            return true;
        }

        /// <summary>
        /// Signed hand-relative-to-forearm axial twist on the principal [-pi, pi]
        /// branch. Common root rotation cancels. Rejects reflection, nonuniform
        /// scale, shear and the undefined perpendicular 180-degree swing.
        /// </summary>
        public static bool TryTwistAngle(in Matrix4x4 lower, in Matrix4x4 hand,
            Vector3 restAxis, out float angleRadians, out string error)
        {
            angleRadians = 0f;
            if (!IsFinite(restAxis) || restAxis.sqrMagnitude < 1e-12f)
            {
                error = "Forearm axis must be finite and nonzero.";
                return false;
            }
            restAxis.Normalize();
            if (!TryRigidRotation(lower, out Quaternion lowerRotation, out error) ||
                !TryRigidRotation(hand, out Quaternion handRotation, out error)) return false;

            Quaternion relative = Quaternion.Inverse(lowerRotation) * handRotation;
            relative.Normalize();
            if (relative.w < 0f)
                relative = new Quaternion(-relative.x, -relative.y, -relative.z, -relative.w);
            double projected = (double)relative.x * restAxis.x +
                (double)relative.y * restAxis.y + (double)relative.z * restAxis.z;
            if ((double)relative.w * relative.w + projected * projected < 1e-12)
            {
                error = "Axial twist is undefined at this 180-degree wrist swing.";
                return false;
            }
            double angle = 2.0 * Math.Atan2(projected, relative.w);
            angleRadians = Math.Abs(angle) < 1e-8 ? 0f : (float)angle;
            error = null;
            return true;
        }

        /// <summary>Validate and extract a positive uniformly scaled rigid rotation.</summary>
        public static bool TryRigidRotation(in Matrix4x4 matrix,
            out Quaternion rotation, out string error)
        {
            rotation = Quaternion.identity;
            if (!IsFinite(matrix))
            {
                error = "Bone deformation contains non-finite values.";
                return false;
            }
            if (Mathf.Abs(matrix.m30) > 1e-6f || Mathf.Abs(matrix.m31) > 1e-6f ||
                Mathf.Abs(matrix.m32) > 1e-6f || Mathf.Abs(matrix.m33 - 1f) > 1e-6f)
            {
                error = "Bone deformation must be affine.";
                return false;
            }
            Vector3 x = new Vector3(matrix.m00, matrix.m10, matrix.m20);
            Vector3 y = new Vector3(matrix.m01, matrix.m11, matrix.m21);
            Vector3 z = new Vector3(matrix.m02, matrix.m12, matrix.m22);
            float lx = x.magnitude, ly = y.magnitude, lz = z.magnitude;
            float scale = (lx + ly + lz) / 3f;
            if (!IsFinite(scale) || scale < 1e-7f || Vector3.Dot(x, Vector3.Cross(y, z)) <= 0f)
            {
                error = "Zero or reflected bone scale is unsupported.";
                return false;
            }
            float tolerance = scale * 1e-4f;
            if (Mathf.Abs(lx - scale) > tolerance || Mathf.Abs(ly - scale) > tolerance ||
                Mathf.Abs(lz - scale) > tolerance)
            {
                error = "Nonuniform bone scale is unsupported.";
                return false;
            }
            float dotTolerance = scale * scale * 1e-4f;
            if (Mathf.Abs(Vector3.Dot(x, y)) > dotTolerance ||
                Mathf.Abs(Vector3.Dot(x, z)) > dotTolerance ||
                Mathf.Abs(Vector3.Dot(y, z)) > dotTolerance)
            {
                error = "Sheared bone deformation is unsupported.";
                return false;
            }
            // Unity and mathutils both use column vectors. LookRotation builds
            // the rotation whose local +Z/+Y match these validated columns.
            rotation = Quaternion.LookRotation(z / lz, y / ly);
            rotation.Normalize();
            error = null;
            return true;
        }

        /// <summary>
        /// Additional post-skin point delta, retaining the hand's original swing
        /// and every unrelated bone contribution. Inputs must already be validated:
        /// normalized nonnegative skin weights, ratio and influence in [0,1],
        /// finite angle, and a finite nonzero rest axis. Recompute from the original
        /// uncorrected input each frame; never add this to a previous correction.
        /// </summary>
        public static Vector3 ExtraDelta(Vector3 point, in Matrix4x4 lower,
            in Matrix4x4 hand, Vector3 restAxis, Vector3 pivot, float ratio,
            float angleRadians, float lowerWeight, float handWeight, float influence = 1f)
        {
            if (influence == 0f || Mathf.Abs(angleRadians) < 1e-8f ||
                (lowerWeight == 0f && handWeight == 0f)) return Vector3.zero;
            restAxis.Normalize();
            Vector3 delta = Vector3.zero;
            if (lowerWeight != 0f && ratio != 0f)
                delta += lowerWeight * lower.MultiplyVector(
                    RotateOffset(point - pivot, restAxis, ratio * angleRadians) - (point - pivot));
            if (handWeight != 0f && ratio != 1f)
                delta += handWeight * hand.MultiplyVector(
                    RotateOffset(point - pivot, restAxis, (ratio - 1f) * angleRadians) - (point - pivot));
            return influence * delta;
        }

        private static Vector3 RotateOffset(Vector3 offset, Vector3 unitAxis, float radians)
        {
            float cosine = Mathf.Cos(radians), sine = Mathf.Sin(radians);
            return offset * cosine + Vector3.Cross(unitAxis, offset) * sine +
                unitAxis * (Vector3.Dot(unitAxis, offset) * (1f - cosine));
        }

        /// <summary>
        /// Invert only the linear 3x3 skinning blend. Translation is deliberately
        /// zero: multiply a post-skin correction delta with MultiplyVector.
        /// Rejects singular/ill-conditioned blends rather than creating spikes.
        /// </summary>
        public static bool TryInverseLinear(in Matrix4x4 blend, out Matrix4x4 inverseLinear,
            out string error, float maxCondition = 1e5f)
        {
            inverseLinear = default;
            if (!IsFinite(blend) || !IsFinite(maxCondition) || maxCondition <= 0f)
            {
                error = "Skinning blend and condition limit must be finite.";
                return false;
            }
            double a = blend.m00, b = blend.m01, c = blend.m02;
            double d = blend.m10, e = blend.m11, f = blend.m12;
            double g = blend.m20, h = blend.m21, i = blend.m22;
            double aa = e * i - f * h, ab = c * h - b * i, ac = b * f - c * e;
            double ad = f * g - d * i, ae = a * i - c * g, af = c * d - a * f;
            double ag = d * h - e * g, ah = b * g - a * h, ai = a * e - b * d;
            double determinant = a * aa + b * ad + c * ag;
            double norm = Math.Sqrt(a*a + b*b + c*c + d*d + e*e + f*f + g*g + h*h + i*i);
            if (norm < 1e-9 || Math.Abs(determinant) < 1e-9 * norm * norm * norm)
            {
                error = "The current linear skinning blend is singular.";
                return false;
            }
            double inverseNorm = Math.Sqrt(aa*aa + ab*ab + ac*ac + ad*ad + ae*ae + af*af +
                ag*ag + ah*ah + ai*ai) / Math.Abs(determinant);
            if (norm * inverseNorm > maxCondition)
            {
                error = "The current linear skinning blend is too close to singular.";
                return false;
            }
            double reciprocal = 1.0 / determinant;
            inverseLinear.m00 = (float)(aa * reciprocal);
            inverseLinear.m01 = (float)(ab * reciprocal);
            inverseLinear.m02 = (float)(ac * reciprocal);
            inverseLinear.m10 = (float)(ad * reciprocal);
            inverseLinear.m11 = (float)(ae * reciprocal);
            inverseLinear.m12 = (float)(af * reciprocal);
            inverseLinear.m20 = (float)(ag * reciprocal);
            inverseLinear.m21 = (float)(ah * reciprocal);
            inverseLinear.m22 = (float)(ai * reciprocal);
            inverseLinear.m33 = 1f;
            if (!IsFinite(inverseLinear))
            {
                inverseLinear = default;
                error = "Inverse skinning produced a non-finite matrix.";
                return false;
            }
            error = null;
            return true;
        }

        /// <summary>
        /// Sample saved (rest position, absolute ratio) knots with the same local
        /// smoothstep segments as Blender's active runtime. This is not the 0.4
        /// default-distribution authoring curve. Knot order is never regenerated.
        /// </summary>
        public static bool TryProfileRatio(float position, Vector2[] knots,
            out float ratio, out string error)
        {
            ratio = 0f;
            if (!IsFinite(position) || knots == null || knots.Length == 0)
            {
                error = "A finite position and saved profile knots are required.";
                return false;
            }
            for (int i = 0; i < knots.Length; ++i)
            {
                if (!IsFinite(knots[i].x) || !IsFinite(knots[i].y) ||
                    knots[i].y < 0f || knots[i].y > 1f ||
                    (i > 0 && knots[i].x - knots[i - 1].x <= 1e-8f))
                {
                    error = "Profile knots must be ordered, distinct and finite, with ratios in [0,1].";
                    return false;
                }
            }
            if (position <= knots[0].x) ratio = knots[0].y;
            else if (position >= knots[knots.Length - 1].x) ratio = knots[knots.Length - 1].y;
            else
            {
                for (int i = 1; i < knots.Length; ++i)
                {
                    if (position > knots[i].x) continue;
                    float t = (position - knots[i - 1].x) / (knots[i].x - knots[i - 1].x);
                    ratio = Mathf.LerpUnclamped(knots[i - 1].y, knots[i].y, Smoothstep(t));
                    break;
                }
            }
            error = null;
            return true;
        }

        /// <summary>Open-interval gate; its two fades stay entirely inside the selected range.</summary>
        public static bool TryRangeInfluence(float position, float first, float last,
            float transition, out float influence, out string error)
        {
            influence = 0f;
            if (!IsFinite(position) || !IsFinite(first) || !IsFinite(last) ||
                !IsFinite(last - first) || first >= last || !IsFinite(transition) ||
                transition < 0f || transition > 0.5f)
            {
                error = "Range must be finite and increasing, with transition in [0,0.5].";
                return false;
            }
            if (position > first && position < last)
            {
                float amount = transition == 0f ? 1f : Mathf.Min(1f,
                    Mathf.Min(((position - first) / (last - first)) / transition,
                        ((last - position) / (last - first)) / transition));
                influence = Smoothstep(amount);
            }
            error = null;
            return true;
        }

        private static float Smoothstep(float t) => t * t * (3f - 2f * t);
    }
}
