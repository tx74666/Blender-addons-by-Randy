using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using UnityEngine;

namespace CharacterDesigner.Unity
{
    /// <summary>
    /// Applies only Character Designer's additional forearm correction. It owns a
    /// private mesh clone; skin weights, artist blendshapes and imported assets are
    /// retained. Run after Animator and any project-specific late IK scripts.
    /// </summary>
    [DisallowMultipleComponent]
    [ExecuteAlways]
    [DefaultExecutionOrder(20000)]
    [AddComponentMenu("Character Designer/Forearm Correction")]
    public sealed class ForearmCorrection : MonoBehaviour
    {
        public SkinnedMeshRenderer targetRenderer;
        public ForearmCorrectionData data;
        public bool correctionEnabled = true;
        [Tooltip("Rotate original normals and tangents by the local geometric normal change. Artist normals are not globally recalculated.")]
        public bool updateSurfaceDirections = true;

        public string LastError { get; private set; }
        public bool IsInitialized => ownedMesh != null;
        public Mesh OriginalMesh => originalMesh;

        private static readonly Dictionary<SkinnedMeshRenderer, ForearmCorrection> Owners =
            new Dictionary<SkinnedMeshRenderer, ForearmCorrection>();
        private SkinnedMeshRenderer ownedRenderer;
        private ForearmCorrectionData activeData;
        private Mesh originalMesh;
        private Mesh ownedMesh;
        private Bounds originalBounds;
        private Transform[] bones;
        private Matrix4x4[] bindposes;
        private Matrix4x4[] deformations;
        private BoneWeight1[] skinWeights;
        private int[] weightOffsets;
        private byte[] weightCounts;
        private Vector3[] baseVertices;
        private Vector3[] pendingVertices;
        private Vector3[] sourcePoints;
        private Vector3[] sourceDeltas;
        private float[] sideAngles;
        private Vector3[] baseNormals;
        private Vector3[] pendingNormals;
        private Vector4[] baseTangents;
        private Vector4[] pendingTangents;
        private Vector3[] baseAreaNormals;
        private Vector3[] pendingAreaNormals;
        private int[] affectedTriangles;
        private bool[] affectedVertices;
        private bool isCorrected;
        private string lastReportedError;
        private bool hasCachedInputs;
        private Matrix4x4[] cachedDeformations;
        private ForearmCorrectionData.SourceSample[] cachedSamples;
        private SideState[] cachedSides;
        private float[] shapeValues;
        private float[] cachedShapeValues;
        private int cachedInfluenceLimit;
        private bool cachedSurfaceDirections;
        private ShapeMapping[] shapeMappings;
        private StencilMapping[] stencilMappings;
        private string bindingFingerprint;

        private struct SideState
        {
            public int lowerBone, handBone;
            public Vector3 axis, pivot;
            public bool enabled;
        }

        private sealed class ShapeMapping
        {
            public string name;
            public int index;
            public int[] sourceIndices;
            public Vector3[] deltas;
        }

        private sealed class StencilMapping
        {
            public int vertex;
            public int[] sourceIndices;
            public float[] weights;
        }

        private void OnEnable()
        {
            if (Application.IsPlaying(gameObject)) ApplyAndReport();
        }

        private void LateUpdate()
        {
            if (Application.IsPlaying(gameObject)) ApplyAndReport();
        }
        private void OnDisable() => Release();
        private void OnDestroy() => Release();

        private void ApplyAndReport()
        {
            if (!ApplyNow(out string error))
            {
                if (error != lastReportedError)
                {
                    Debug.LogWarning("Character Designer forearm correction: " + error, this);
                    lastReportedError = error;
                }
            }
            else lastReportedError = null;
        }

        /// <summary>
        /// Rebuild cached binding after replacing the renderer or immutable mesh,
        /// stencil, or blendshape mapping. Source ratios/points/weights, side
        /// settings and artist blendshape values can change without reinitializing.
        /// </summary>
        public bool Reinitialize(out string error)
        {
            Release();
            return ApplyNow(out error);
        }

        /// <summary>
        /// Evaluate both arms from the original input, then commit together. This
        /// entry point also permits isolated editor validation without Play Mode.
        /// Failure restores ordinary skinning instead of leaving one arm corrected.
        /// </summary>
        public bool ApplyNow(out string error)
        {
            try { return Evaluate(out error); }
            catch (Exception exception)
            {
                return Fail("Correction input changed or became unavailable: " + exception.Message, out error);
            }
        }

        private bool Evaluate(out string error)
        {
            if (ownedMesh == null && !Initialize(out error)) return Fail(error, out error);
            if (targetRenderer != ownedRenderer || data != activeData)
                return Fail("Renderer or calibration changed; call Reinitialize to bind the new data.", out error);
            if (ownedRenderer.sharedMesh != ownedMesh)
            {
                Release();
                return Fail("Another operation replaced the renderer mesh; correction released its private copy.", out error);
            }
            if (!correctionEnabled)
            {
                ResetCorrection();
                LastError = error = null;
                return true;
            }
            int influenceLimit = CurrentInfluenceLimit(ownedRenderer.quality);
            if (!BindingMappingMatches())
                return Fail("Calibration mesh or mapping changed; call Reinitialize to bind the new data.", out error);
            bool unchanged = hasCachedInputs && influenceLimit == cachedInfluenceLimit &&
                updateSurfaceDirections == cachedSurfaceDirections;

            Matrix4x4 worldToMesh = ownedRenderer.transform.worldToLocalMatrix;
            for (int i = 0; i < bones.Length; ++i)
            {
                if (bones[i] == null) return Fail("A bound bone has been removed.", out error);
                deformations[i] = worldToMesh * bones[i].localToWorldMatrix * bindposes[i];
                unchanged &= SameMatrix(deformations[i], cachedDeformations[i]);
            }
            for (int i = 0; i < activeData.sides.Length; ++i)
            {
                ForearmCorrectionData.Side side = activeData.sides[i];
                if (!ValidSide(side)) return Fail("A forearm side has invalid bones, axis or pivot.", out error);
                SideState previous = cachedSides[i];
                unchanged &= side.lowerBone == previous.lowerBone && side.handBone == previous.handBone &&
                    side.enabled == previous.enabled && side.axis.Equals(previous.axis) && side.pivot.Equals(previous.pivot);
            }
            for (int i = 0; i < sourcePoints.Length; ++i)
            {
                ForearmCorrectionData.SourceSample sample = activeData.sources[i];
                if (!ValidSample(sample)) return Fail("A source calibration sample is invalid.", out error);
                unchanged &= SameSample(sample, cachedSamples[i]);
            }
            for (int i = 0; i < activeData.shapes.Length; ++i)
            {
                shapeValues[i] = ownedRenderer.GetBlendShapeWeight(activeData.shapes[i].blendShapeIndex) * .01f;
                if (!ForearmCorrectionMath.IsFinite(shapeValues[i])) return Fail("An artist blendshape weight is non-finite.", out error);
                unchanged &= shapeValues[i] == cachedShapeValues[i];
            }
            if (unchanged)
            {
                LastError = error = null;
                return true;
            }
            for (int i = 0; i < activeData.sides.Length; ++i)
            {
                ForearmCorrectionData.Side side = activeData.sides[i];
                sideAngles[i] = 0f;
                if (!side.enabled) continue;
                if (!ForearmCorrectionMath.TryTwistAngle(deformations[side.lowerBone],
                    deformations[side.handBone], side.axis, out sideAngles[i], out error))
                    return Fail(error, out error);
                if (Mathf.Abs(sideAngles[i]) > 120.001f * Mathf.Deg2Rad)
                    return Fail("Wrist twist exceeds the calibrated ±120 degree range; ordinary skinning is retained.", out error);
            }
            for (int i = 0; i < sourcePoints.Length; ++i)
            {
                ForearmCorrectionData.SourceSample sample = activeData.sources[i];
                sourcePoints[i] = sample.point;
            }
            for (int i = 0; i < activeData.shapes.Length; ++i)
            {
                ForearmCorrectionData.ShapeInput shape = activeData.shapes[i];
                float value = shapeValues[i];
                for (int j = 0; j < shape.sourceIndices.Length; ++j)
                    sourcePoints[shape.sourceIndices[j]] += shape.deltas[j] * value;
            }
            for (int i = 0; i < sourceDeltas.Length; ++i)
            {
                ForearmCorrectionData.SourceSample sample = activeData.sources[i];
                ForearmCorrectionData.Side side = activeData.sides[sample.side];
                sourceDeltas[i] = side.enabled ? ForearmCorrectionMath.ExtraDelta(sourcePoints[i],
                    deformations[side.lowerBone], deformations[side.handBone], side.axis, side.pivot,
                    sample.ratio, sideAngles[sample.side], sample.lowerWeight, sample.handWeight,
                    sample.influence) : Vector3.zero;
                if (!ForearmCorrectionMath.IsFinite(sourceDeltas[i]))
                    return Fail("The correction produced a non-finite source delta.", out error);
            }
            Array.Copy(baseVertices, pendingVertices, baseVertices.Length);
            bool anyCorrection = false;
            for (int i = 0; i < activeData.stencils.Length; ++i)
            {
                ForearmCorrectionData.Stencil stencil = activeData.stencils[i];
                Vector3 postSkinDelta = Vector3.zero;
                for (int j = 0; j < stencil.sourceIndices.Length; ++j)
                    postSkinDelta += sourceDeltas[stencil.sourceIndices[j]] * stencil.weights[j];
                if (postSkinDelta.sqrMagnitude < 1e-20f) continue;
                int vertex = stencil.vertexIndex;
                Matrix4x4 blend = default;
                int offset = weightOffsets[vertex];
                float total = 0f;
                // Unity stores influences in descending order. Match the same
                // truncation and renormalization used by this renderer's current
                // quality; never alter ordinary skinning elsewhere in the mesh.
                int usedWeights = Math.Min(weightCounts[vertex], influenceLimit);
                for (int j = 0; j < usedWeights; ++j)
                {
                    BoneWeight1 weight = skinWeights[offset + j];
                    Matrix4x4 bone = deformations[weight.boneIndex];
                    float amount = weight.weight;
                    total += amount;
                    blend.m00 += bone.m00 * amount; blend.m01 += bone.m01 * amount; blend.m02 += bone.m02 * amount;
                    blend.m10 += bone.m10 * amount; blend.m11 += bone.m11 * amount; blend.m12 += bone.m12 * amount;
                    blend.m20 += bone.m20 * amount; blend.m21 += bone.m21 * amount; blend.m22 += bone.m22 * amount;
                }
                if (total < 1e-8f) return Fail("An affected exported vertex has no skin weights.", out error);
                float inverseTotal = 1f / total;
                blend.m00 *= inverseTotal; blend.m01 *= inverseTotal; blend.m02 *= inverseTotal;
                blend.m10 *= inverseTotal; blend.m11 *= inverseTotal; blend.m12 *= inverseTotal;
                blend.m20 *= inverseTotal; blend.m21 *= inverseTotal; blend.m22 *= inverseTotal;
                blend.m33 = 1f;
                if (!ForearmCorrectionMath.TryInverseLinear(blend, out Matrix4x4 inverse, out error))
                    return Fail(error, out error);
                Vector3 corrected = baseVertices[vertex] + inverse.MultiplyVector(postSkinDelta);
                if (!ForearmCorrectionMath.IsFinite(corrected)) return Fail("Inverse skinning produced a non-finite vertex.", out error);
                pendingVertices[vertex] = corrected;
                anyCorrection = true;
            }
            if (!anyCorrection) ResetCorrection();
            else
            {
                UpdateSurfaceDirections();
                isCorrected = true;
                ownedMesh.SetVertices(pendingVertices);
                if (pendingNormals != null) ownedMesh.SetNormals(pendingNormals);
                if (pendingTangents != null) ownedMesh.SetTangents(pendingTangents);
                UpdateBounds();
            }
            CacheSuccessfulInputs(influenceLimit);
            LastError = error = null;
            return true;
        }

        private bool Initialize(out string error)
        {
            error = null;
            if (targetRenderer == null || data == null || data.sourceMesh == null)
            { error = "Assign a renderer and imported correction data."; return false; }
            if (Owners.TryGetValue(targetRenderer, out ForearmCorrection other) && other != null && other != this)
            { error = "This renderer is already owned by another forearm correction component."; return false; }
            Mesh mesh = targetRenderer.sharedMesh;
            if (mesh != data.sourceMesh || mesh.vertexCount != data.vertexCount || data.formatVersion != 1)
            { error = "Correction data does not match this imported mesh. Reimport the Character Designer export."; return false; }
            if (!mesh.isReadable)
            { error = "The correction mesh needs Read/Write enabled in its import settings."; return false; }
            if (string.IsNullOrEmpty(data.sourceFingerprint) || ComputeFingerprint(mesh) != data.sourceFingerprint)
            { error = "The mesh geometry, skinning or artist blendshapes changed after calibration. Reimport the Character Designer export."; return false; }
            if (data.sides == null || data.sources == null || data.stencils == null || data.shapes == null ||
                data.sides.Length == 0 || data.sources.Length == 0 || data.stencils.Length == 0)
            { error = "Calibration arrays are missing or empty."; return false; }
            bones = targetRenderer.bones;
            bindposes = mesh.bindposes;
            if (bones.Length == 0 || bones.Length != bindposes.Length)
            { error = "Renderer bones and mesh bindposes do not match."; return false; }
            for (int i = 0; i < bones.Length; ++i)
                if (bones[i] == null || !ForearmCorrectionMath.IsFinite(bindposes[i]))
                { error = "A renderer bone or bindpose is missing or invalid."; return false; }
            for (int i = 0; i < data.sides.Length; ++i)
            {
                ForearmCorrectionData.Side side = data.sides[i];
                if (side == null || side.lowerBone < 0 || side.lowerBone >= bones.Length ||
                    side.handBone < 0 || side.handBone >= bones.Length ||
                    !ForearmCorrectionMath.IsFinite(side.axis) || side.axis.sqrMagnitude < 1e-12f ||
                    !ForearmCorrectionMath.IsFinite(side.pivot))
                { error = "A forearm side has invalid bones, axis or pivot."; return false; }
            }
            for (int i = 0; i < data.sources.Length; ++i)
                if (!ValidSample(data.sources[i]))
                { error = "A source calibration sample is invalid."; return false; }
            affectedVertices = new bool[mesh.vertexCount];
            for (int i = 0; i < data.stencils.Length; ++i)
            {
                ForearmCorrectionData.Stencil stencil = data.stencils[i];
                if (stencil == null || stencil.vertexIndex < 0 || stencil.vertexIndex >= mesh.vertexCount ||
                    affectedVertices[stencil.vertexIndex] || stencil.sourceIndices == null || stencil.weights == null ||
                    stencil.sourceIndices.Length == 0 || stencil.sourceIndices.Length != stencil.weights.Length)
                { error = "An exported vertex stencil is invalid or duplicated."; return false; }
                affectedVertices[stencil.vertexIndex] = true;
                for (int j = 0; j < stencil.sourceIndices.Length; ++j)
                    if (stencil.sourceIndices[j] < 0 || stencil.sourceIndices[j] >= data.sources.Length ||
                        !ForearmCorrectionMath.IsFinite(stencil.weights[j]) || stencil.weights[j] < 0f)
                    { error = "A subdivision stencil contains an invalid source or weight."; return false; }
            }
            for (int i = 0; i < data.shapes.Length; ++i)
            {
                ForearmCorrectionData.ShapeInput shape = data.shapes[i];
                if (shape == null || shape.blendShapeIndex < 0 || shape.blendShapeIndex >= mesh.blendShapeCount ||
                    mesh.GetBlendShapeName(shape.blendShapeIndex) != shape.blendShapeName ||
                    mesh.GetBlendShapeFrameCount(shape.blendShapeIndex) != 1 ||
                    Mathf.Abs(mesh.GetBlendShapeFrameWeight(shape.blendShapeIndex, 0) - 100f) > 1e-4f ||
                    shape.sourceIndices == null || shape.deltas == null || shape.sourceIndices.Length != shape.deltas.Length)
                { error = "An artist blendshape no longer matches the exported calibration."; return false; }
                for (int j = 0; j < shape.sourceIndices.Length; ++j)
                    if (shape.sourceIndices[j] < 0 || shape.sourceIndices[j] >= data.sources.Length ||
                        !ForearmCorrectionMath.IsFinite(shape.deltas[j]))
                    { error = "An artist blendshape source delta is invalid."; return false; }
            }
            using (var weights = mesh.GetAllBoneWeights()) skinWeights = weights.ToArray();
            using (var counts = mesh.GetBonesPerVertex()) weightCounts = counts.ToArray();
            if (weightCounts.Length != mesh.vertexCount)
            { error = "The imported mesh has incomplete skin weights."; return false; }
            weightOffsets = new int[mesh.vertexCount];
            int cursor = 0;
            for (int i = 0; i < mesh.vertexCount; ++i)
            {
                weightOffsets[i] = cursor;
                cursor += weightCounts[i];
                if (affectedVertices[i] && weightCounts[i] == 0)
                { error = "An affected exported vertex has no skin weights."; return false; }
            }
            if (cursor != skinWeights.Length)
            { error = "The imported mesh has inconsistent skin weight storage."; return false; }
            for (int i = 0; i < skinWeights.Length; ++i)
                if (skinWeights[i].boneIndex < 0 || skinWeights[i].boneIndex >= bones.Length ||
                    !ForearmCorrectionMath.IsFinite(skinWeights[i].weight) || skinWeights[i].weight < 0f)
                { error = "The imported mesh has invalid bone weights."; return false; }
            baseVertices = mesh.vertices;
            pendingVertices = new Vector3[baseVertices.Length];
            sourcePoints = new Vector3[data.sources.Length];
            sourceDeltas = new Vector3[data.sources.Length];
            sideAngles = new float[data.sides.Length];
            deformations = new Matrix4x4[bones.Length];
            cachedDeformations = new Matrix4x4[bones.Length];
            cachedSamples = new ForearmCorrectionData.SourceSample[data.sources.Length];
            cachedSides = new SideState[data.sides.Length];
            shapeValues = new float[data.shapes.Length];
            cachedShapeValues = new float[data.shapes.Length];
            CaptureBindingMapping();
            hasCachedInputs = false;
            baseNormals = mesh.normals;
            if (baseNormals.Length != mesh.vertexCount) baseNormals = null;
            pendingNormals = baseNormals == null ? null : new Vector3[baseNormals.Length];
            baseTangents = mesh.tangents;
            if (baseTangents.Length != mesh.vertexCount) baseTangents = null;
            pendingTangents = baseTangents == null ? null : new Vector4[baseTangents.Length];
            CaptureSurfaceDirections(mesh);
            ownedRenderer = targetRenderer;
            activeData = data;
            originalMesh = mesh;
            originalBounds = ownedRenderer.localBounds;
            ownedMesh = Instantiate(mesh);
            ownedMesh.name = mesh.name + " (Forearm Correction Instance)";
            ownedMesh.hideFlags = HideFlags.DontSave;
            ownedMesh.MarkDynamic();
            ownedRenderer.sharedMesh = ownedMesh;
            Owners[ownedRenderer] = this;
            return true;
        }

        private bool ValidSample(ForearmCorrectionData.SourceSample sample) =>
            data != null && data.sides != null && sample.side >= 0 && sample.side < data.sides.Length &&
            ForearmCorrectionMath.IsFinite(sample.point) && InUnit(sample.ratio) && InUnit(sample.influence) &&
            InUnit(sample.lowerWeight) && InUnit(sample.handWeight) && sample.lowerWeight + sample.handWeight <= 1.0001f;

        private bool ValidSide(ForearmCorrectionData.Side side) =>
            side != null && side.lowerBone >= 0 && side.lowerBone < bones.Length &&
            side.handBone >= 0 && side.handBone < bones.Length &&
            ForearmCorrectionMath.IsFinite(side.axis) && side.axis.sqrMagnitude >= 1e-12f &&
            ForearmCorrectionMath.IsFinite(side.pivot);

        private void CaptureBindingMapping()
        {
            bindingFingerprint = data.sourceFingerprint;
            shapeMappings = new ShapeMapping[data.shapes.Length];
            for (int i = 0; i < shapeMappings.Length; ++i)
            {
                ForearmCorrectionData.ShapeInput shape = data.shapes[i];
                shapeMappings[i] = new ShapeMapping { name = shape.blendShapeName, index = shape.blendShapeIndex,
                    sourceIndices = (int[])shape.sourceIndices.Clone(), deltas = (Vector3[])shape.deltas.Clone() };
            }
            stencilMappings = new StencilMapping[data.stencils.Length];
            for (int i = 0; i < stencilMappings.Length; ++i)
            {
                ForearmCorrectionData.Stencil stencil = data.stencils[i];
                stencilMappings[i] = new StencilMapping { vertex = stencil.vertexIndex,
                    sourceIndices = (int[])stencil.sourceIndices.Clone(), weights = (float[])stencil.weights.Clone() };
            }
        }

        // Cheap exact comparisons avoid overlooking in-place edits to public
        // calibration arrays. Mapping edits require rebinding; scalar calibration
        // edits participate in the normal input cache and update on the next frame.
        private bool BindingMappingMatches()
        {
            if (activeData.sourceMesh != originalMesh || activeData.sourceFingerprint != bindingFingerprint ||
                activeData.vertexCount != baseVertices.Length || activeData.formatVersion != 1 ||
                activeData.sources == null || activeData.sources.Length != cachedSamples.Length ||
                activeData.sides == null || activeData.sides.Length != cachedSides.Length ||
                activeData.shapes == null || activeData.shapes.Length != shapeMappings.Length ||
                activeData.stencils == null || activeData.stencils.Length != stencilMappings.Length) return false;
            for (int i = 0; i < shapeMappings.Length; ++i)
            {
                ForearmCorrectionData.ShapeInput current = activeData.shapes[i];
                ShapeMapping captured = shapeMappings[i];
                if (current == null || current.blendShapeName != captured.name || current.blendShapeIndex != captured.index ||
                    current.sourceIndices == null || current.deltas == null ||
                    current.sourceIndices.Length != captured.sourceIndices.Length || current.deltas.Length != captured.deltas.Length) return false;
                for (int j = 0; j < captured.sourceIndices.Length; ++j)
                    if (current.sourceIndices[j] != captured.sourceIndices[j] || !current.deltas[j].Equals(captured.deltas[j])) return false;
            }
            for (int i = 0; i < stencilMappings.Length; ++i)
            {
                ForearmCorrectionData.Stencil current = activeData.stencils[i];
                StencilMapping captured = stencilMappings[i];
                if (current == null || current.vertexIndex != captured.vertex || current.sourceIndices == null || current.weights == null ||
                    current.sourceIndices.Length != captured.sourceIndices.Length || current.weights.Length != captured.weights.Length) return false;
                for (int j = 0; j < captured.sourceIndices.Length; ++j)
                    if (current.sourceIndices[j] != captured.sourceIndices[j] || current.weights[j] != captured.weights[j]) return false;
            }
            return true;
        }

        private void CacheSuccessfulInputs(int influenceLimit)
        {
            Array.Copy(deformations, cachedDeformations, deformations.Length);
            Array.Copy(activeData.sources, cachedSamples, cachedSamples.Length);
            Array.Copy(shapeValues, cachedShapeValues, shapeValues.Length);
            for (int i = 0; i < cachedSides.Length; ++i)
            {
                ForearmCorrectionData.Side side = activeData.sides[i];
                cachedSides[i] = new SideState { lowerBone = side.lowerBone, handBone = side.handBone,
                    axis = side.axis, pivot = side.pivot, enabled = side.enabled };
            }
            cachedInfluenceLimit = influenceLimit;
            cachedSurfaceDirections = updateSurfaceDirections;
            hasCachedInputs = true;
        }

        private static bool SameSample(ForearmCorrectionData.SourceSample a, ForearmCorrectionData.SourceSample b) =>
            a.side == b.side && a.point.Equals(b.point) && a.lowerWeight == b.lowerWeight && a.handWeight == b.handWeight &&
            a.ratio == b.ratio && a.influence == b.influence;

        private static bool SameMatrix(in Matrix4x4 a, in Matrix4x4 b) =>
            a.m00 == b.m00 && a.m01 == b.m01 && a.m02 == b.m02 && a.m03 == b.m03 &&
            a.m10 == b.m10 && a.m11 == b.m11 && a.m12 == b.m12 && a.m13 == b.m13 &&
            a.m20 == b.m20 && a.m21 == b.m21 && a.m22 == b.m22 && a.m23 == b.m23 &&
            a.m30 == b.m30 && a.m31 == b.m31 && a.m32 == b.m32 && a.m33 == b.m33;

        private static bool InUnit(float value) => ForearmCorrectionMath.IsFinite(value) && value >= 0f && value <= 1f;

        private static int CurrentInfluenceLimit(SkinQuality quality)
        {
            if (quality == SkinQuality.Bone1) return 1;
            if (quality == SkinQuality.Bone2) return 2;
            if (quality == SkinQuality.Bone4) return 4;
            switch (QualitySettings.skinWeights)
            {
                case SkinWeights.OneBone: return 1;
                case SkinWeights.TwoBones: return 2;
                case SkinWeights.FourBones: return 4;
                default: return 255;
            }
        }

        /// <summary>
        /// Importer/runtime binding checksum. Computed only when binding, never
        /// per frame. Includes artist geometry so editing an existing mesh asset
        /// in place cannot accidentally reuse stale calibration of the same size.
        /// </summary>
        public static string ComputeFingerprint(Mesh mesh)
        {
            if (mesh == null || !mesh.isReadable) throw new ArgumentException("A readable mesh is required.", nameof(mesh));
            using (SHA256 hash = SHA256.Create())
            using (var stream = new CryptoStream(Stream.Null, hash, CryptoStreamMode.Write))
            using (var writer = new BinaryWriter(stream, System.Text.Encoding.UTF8, true))
            {
                writer.Write(1);
                WriteVectors(writer, mesh.vertices);
                WriteVectors(writer, mesh.normals);
                Vector4[] tangents = mesh.tangents;
                writer.Write(tangents.Length);
                for (int i = 0; i < tangents.Length; ++i)
                { writer.Write(tangents[i].x); writer.Write(tangents[i].y); writer.Write(tangents[i].z); writer.Write(tangents[i].w); }
                writer.Write(mesh.subMeshCount);
                for (int i = 0; i < mesh.subMeshCount; ++i)
                {
                    writer.Write((int)mesh.GetTopology(i));
                    int[] indices = mesh.GetIndices(i);
                    writer.Write(indices.Length);
                    for (int j = 0; j < indices.Length; ++j) writer.Write(indices[j]);
                }
                Matrix4x4[] poses = mesh.bindposes;
                writer.Write(poses.Length);
                for (int i = 0; i < poses.Length; ++i)
                    for (int j = 0; j < 16; ++j) writer.Write(poses[i][j]);
                using (var counts = mesh.GetBonesPerVertex())
                {
                    writer.Write(counts.Length);
                    for (int i = 0; i < counts.Length; ++i) writer.Write(counts[i]);
                }
                using (var weights = mesh.GetAllBoneWeights())
                {
                    writer.Write(weights.Length);
                    for (int i = 0; i < weights.Length; ++i)
                    { writer.Write(weights[i].boneIndex); writer.Write(weights[i].weight); }
                }
                writer.Write(mesh.blendShapeCount);
                var positionDeltas = new Vector3[mesh.vertexCount];
                var normalDeltas = new Vector3[mesh.vertexCount];
                var tangentDeltas = new Vector3[mesh.vertexCount];
                for (int i = 0; i < mesh.blendShapeCount; ++i)
                {
                    writer.Write(mesh.GetBlendShapeName(i));
                    int frames = mesh.GetBlendShapeFrameCount(i);
                    writer.Write(frames);
                    for (int j = 0; j < frames; ++j)
                    {
                        writer.Write(mesh.GetBlendShapeFrameWeight(i, j));
                        mesh.GetBlendShapeFrameVertices(i, j, positionDeltas, normalDeltas, tangentDeltas);
                        WriteVectors(writer, positionDeltas);
                        WriteVectors(writer, normalDeltas);
                        WriteVectors(writer, tangentDeltas);
                    }
                }
                writer.Flush();
                stream.FlushFinalBlock();
                return BitConverter.ToString(hash.Hash).Replace("-", "").ToLowerInvariant();
            }
        }

        private static void WriteVectors(BinaryWriter writer, Vector3[] values)
        {
            writer.Write(values.Length);
            for (int i = 0; i < values.Length; ++i)
            { writer.Write(values[i].x); writer.Write(values[i].y); writer.Write(values[i].z); }
        }

        private bool Fail(string message, out string error)
        {
            ResetCorrection();
            LastError = error = message;
            return false;
        }

        private void CaptureSurfaceDirections(Mesh mesh)
        {
            int[] triangles = mesh.triangles;
            baseAreaNormals = new Vector3[mesh.vertexCount];
            pendingAreaNormals = new Vector3[mesh.vertexCount];
            var affected = new List<int>();
            for (int i = 0; i + 2 < triangles.Length; i += 3)
            {
                int a = triangles[i], b = triangles[i + 1], c = triangles[i + 2];
                Vector3 area = Vector3.Cross(baseVertices[b] - baseVertices[a], baseVertices[c] - baseVertices[a]);
                baseAreaNormals[a] += area;
                baseAreaNormals[b] += area;
                baseAreaNormals[c] += area;
                if (!affectedVertices[a] && !affectedVertices[b] && !affectedVertices[c]) continue;
                affected.Add(a); affected.Add(b); affected.Add(c);
            }
            affectedTriangles = affected.ToArray();
        }

        private void UpdateSurfaceDirections()
        {
            if (pendingNormals != null) Array.Copy(baseNormals, pendingNormals, baseNormals.Length);
            if (pendingTangents != null) Array.Copy(baseTangents, pendingTangents, baseTangents.Length);
            if (!updateSurfaceDirections || (pendingNormals == null && pendingTangents == null)) return;
            Array.Copy(baseAreaNormals, pendingAreaNormals, baseAreaNormals.Length);
            for (int i = 0; i < affectedTriangles.Length; i += 3)
            {
                int a = affectedTriangles[i], b = affectedTriangles[i + 1], c = affectedTriangles[i + 2];
                Vector3 before = Vector3.Cross(baseVertices[b] - baseVertices[a], baseVertices[c] - baseVertices[a]);
                Vector3 after = Vector3.Cross(pendingVertices[b] - pendingVertices[a], pendingVertices[c] - pendingVertices[a]);
                Vector3 difference = after - before;
                pendingAreaNormals[a] += difference;
                pendingAreaNormals[b] += difference;
                pendingAreaNormals[c] += difference;
            }
            for (int i = 0; i < activeData.stencils.Length; ++i)
            {
                int vertex = activeData.stencils[i].vertexIndex;
                if (baseAreaNormals[vertex].sqrMagnitude < 1e-18f || pendingAreaNormals[vertex].sqrMagnitude < 1e-18f) continue;
                Quaternion delta = Quaternion.FromToRotation(baseAreaNormals[vertex], pendingAreaNormals[vertex]);
                if (pendingNormals != null) pendingNormals[vertex] = delta * baseNormals[vertex];
                if (pendingTangents != null)
                {
                    Vector4 tangent = baseTangents[vertex];
                    Vector3 rotated = delta * new Vector3(tangent.x, tangent.y, tangent.z);
                    pendingTangents[vertex] = new Vector4(rotated.x, rotated.y, rotated.z, tangent.w);
                }
            }
        }

        private void UpdateBounds()
        {
            Bounds bounds = originalMesh.bounds;
            for (int i = 0; i < activeData.stencils.Length; ++i)
                bounds.Encapsulate(pendingVertices[activeData.stencils[i].vertexIndex]);
            ownedMesh.bounds = bounds;
            Bounds rendererBounds = originalBounds;
            rendererBounds.Encapsulate(bounds.min);
            rendererBounds.Encapsulate(bounds.max);
            rendererBounds.Expand(bounds.size.magnitude * .05f);
            ownedRenderer.localBounds = rendererBounds;
        }

        private void ResetCorrection()
        {
            hasCachedInputs = false;
            if (ownedMesh == null || !isCorrected || ownedRenderer == null || ownedRenderer.sharedMesh != ownedMesh) return;
            ownedMesh.SetVertices(baseVertices);
            if (baseNormals != null) ownedMesh.SetNormals(baseNormals);
            if (baseTangents != null) ownedMesh.SetTangents(baseTangents);
            ownedMesh.bounds = originalMesh.bounds;
            ownedRenderer.localBounds = originalBounds;
            isCorrected = false;
        }

        private void Release()
        {
            hasCachedInputs = false;
            if (ownedRenderer != null)
            {
                if (ownedMesh != null && ownedRenderer.sharedMesh == ownedMesh)
                {
                    ownedRenderer.sharedMesh = originalMesh;
                    ownedRenderer.localBounds = originalBounds;
                }
                if (Owners.TryGetValue(ownedRenderer, out ForearmCorrection owner) && owner == this) Owners.Remove(ownedRenderer);
            }
            if (ownedMesh != null)
            {
                if (Application.isPlaying) Destroy(ownedMesh);
                else DestroyImmediate(ownedMesh);
            }
            ownedMesh = null;
            ownedRenderer = null;
            originalMesh = null;
            activeData = null;
            isCorrected = false;
        }
    }
}
