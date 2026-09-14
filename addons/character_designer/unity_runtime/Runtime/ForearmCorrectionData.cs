using System;
using UnityEngine;

namespace CharacterDesigner.Unity
{
    /// <summary>Imported calibration and sparse subdivision response. All coordinates are in sourceMesh local space.</summary>
    [CreateAssetMenu(menuName = "Character Designer/Forearm Correction Data")]
    public sealed class ForearmCorrectionData : ScriptableObject
    {
        public int formatVersion = 1;
        public Mesh sourceMesh;
        public string sourceFingerprint;
        public int vertexCount;
        public Side[] sides = Array.Empty<Side>();
        public SourceSample[] sources = Array.Empty<SourceSample>();
        public ShapeInput[] shapes = Array.Empty<ShapeInput>();
        public Stencil[] stencils = Array.Empty<Stencil>();

        [Serializable]
        public sealed class Side
        {
            public string name;
            public int lowerBone;
            public int handBone;
            public Vector3 axis;
            public Vector3 pivot;
            public bool enabled = true;
        }

        [Serializable]
        public struct SourceSample
        {
            public Vector3 point;
            public float lowerWeight;
            public float handWeight;
            public float ratio;
            public float influence;
            public int side;
        }

        [Serializable]
        public sealed class ShapeInput
        {
            public string blendShapeName;
            public int blendShapeIndex;
            public int[] sourceIndices = Array.Empty<int>();
            public Vector3[] deltas = Array.Empty<Vector3>();
        }

        [Serializable]
        public sealed class Stencil
        {
            public int vertexIndex;
            public int[] sourceIndices = Array.Empty<int>();
            public float[] weights = Array.Empty<float>();
        }
    }
}
