// Run() creates and destroys a PreviewScene; it does not save or replace user scenes.
using System;
using System.Collections.Generic;
using CharacterDesigner.Unity;
using Unity.Collections;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class ForearmCorrectionRuntimeValidation
{
    [Serializable]
    public sealed class Report
    {
        public bool passed;
        public int caseCount;
        public string[] checks;
    }

    [MenuItem("Tools/Character Designer/Validate Runtime Correction")]
    public static void RunMenu() => Debug.Log(JsonUtility.ToJson(Run(), true));

    public static Report Run()
    {
        var passed = new List<string>();
        Scene scene = EditorSceneManager.NewPreviewScene();
        GameObject root = null;
        Mesh mesh = null;
        Mesh baked = null;
        ForearmCorrectionData calibration = null;
        try
        {
            root = new GameObject("Character Designer Runtime Validation");
            SceneManager.MoveGameObjectToScene(root, scene);
            var renderer = root.AddComponent<SkinnedMeshRenderer>();
            var boneObjects = new GameObject[4];
            var bones = new Transform[4];
            for (int side = 0; side < 2; ++side)
            {
                int offset = side * 2;
                boneObjects[offset] = new GameObject(side == 0 ? "Lower.L" : "Lower.R");
                boneObjects[offset].transform.SetParent(root.transform, false);
                boneObjects[offset].transform.localPosition = new Vector3(side == 0 ? -1f : 1f, 0f, 0f);
                boneObjects[offset + 1] = new GameObject(side == 0 ? "Hand.L" : "Hand.R");
                boneObjects[offset + 1].transform.SetParent(boneObjects[offset].transform, false);
                boneObjects[offset + 1].transform.localPosition = Vector3.up;
                bones[offset] = boneObjects[offset].transform;
                bones[offset + 1] = boneObjects[offset + 1].transform;
            }
            mesh = new Mesh { name = "Correction Validation Mesh" };
            var vertices = new Vector3[8];
            for (int side = 0; side < 2; ++side)
            {
                float x = side == 0 ? -1f : 1f;
                vertices[side * 4] = new Vector3(x - .12f, .3f, .06f);
                vertices[side * 4 + 1] = new Vector3(x + .12f, .3f, .06f);
                vertices[side * 4 + 2] = new Vector3(x + .12f, .8f, .06f);
                vertices[side * 4 + 3] = new Vector3(x - .12f, .8f, .06f);
            }
            mesh.vertices = vertices;
            mesh.triangles = new[] { 0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7 };
            mesh.RecalculateNormals();
            var bindposes = new Matrix4x4[4];
            for (int i = 0; i < bones.Length; ++i) bindposes[i] = bones[i].worldToLocalMatrix * root.transform.localToWorldMatrix;
            mesh.bindposes = bindposes;
            var counts = new NativeArray<byte>(8, Allocator.Temp);
            var weights = new NativeArray<BoneWeight1>(20, Allocator.Temp);
            try
            {
                int cursor = 0;
                for (int i = 0; i < 8; ++i)
                {
                    bool fourInfluences = i == 0 || i == 3;
                    counts[i] = fourInfluences ? (byte)4 : (byte)2;
                    weights[cursor++] = new BoneWeight1 { boneIndex = i < 4 ? 0 : 2, weight = fourInfluences ? .4f : .7f };
                    weights[cursor++] = new BoneWeight1 { boneIndex = i < 4 ? 1 : 3, weight = .3f };
                    if (!fourInfluences) continue;
                    weights[cursor++] = new BoneWeight1 { boneIndex = 2, weight = .2f };
                    weights[cursor++] = new BoneWeight1 { boneIndex = 3, weight = .1f };
                }
                mesh.SetBoneWeights(counts, weights);
            }
            finally { counts.Dispose(); weights.Dispose(); }
            var artistDeltas = new Vector3[8];
            artistDeltas[0] = new Vector3(.03f, -.02f, .025f);
            mesh.AddBlendShapeFrame("Artist", 100f, artistDeltas, new Vector3[8], new Vector3[8]);
            renderer.sharedMesh = mesh;
            renderer.bones = bones;
            renderer.rootBone = root.transform;
            renderer.quality = SkinQuality.Bone2;
            renderer.SetBlendShapeWeight(0, 37f);
            Bounds originalBounds = renderer.localBounds;
            string originalHash = ForearmCorrection.ComputeFingerprint(mesh);
            calibration = ScriptableObject.CreateInstance<ForearmCorrectionData>();
            calibration.sourceMesh = mesh;
            calibration.sourceFingerprint = originalHash;
            calibration.vertexCount = 8;
            calibration.sides = new[]
            {
                new ForearmCorrectionData.Side { name = "L", lowerBone = 0, handBone = 1, axis = Vector3.up, pivot = new Vector3(-1, 1, 0) },
                new ForearmCorrectionData.Side { name = "R", lowerBone = 2, handBone = 3, axis = Vector3.up, pivot = new Vector3(1, 1, 0) }
            };
            calibration.sources = new ForearmCorrectionData.SourceSample[8];
            calibration.stencils = new ForearmCorrectionData.Stencil[8];
            for (int i = 0; i < 8; ++i)
            {
                calibration.sources[i] = new ForearmCorrectionData.SourceSample
                {
                    point = vertices[i], lowerWeight = .7f, handWeight = .3f,
                    ratio = .55f, influence = i == 3 ? 0f : 1f, side = i < 4 ? 0 : 1
                };
                calibration.stencils[i] = new ForearmCorrectionData.Stencil
                { vertexIndex = i, sourceIndices = new[] { i }, weights = new[] { 1f } };
            }
            calibration.shapes = new[] { new ForearmCorrectionData.ShapeInput
            { blendShapeName = "Artist", blendShapeIndex = 0, sourceIndices = new[] { 0 }, deltas = new[] { artistDeltas[0] } } };
            var correction = root.AddComponent<ForearmCorrection>();
            correction.targetRenderer = renderer;
            correction.data = calibration;
            baked = new Mesh();

            bones[1].localRotation = Quaternion.AngleAxis(45f, Vector3.up);
            bones[3].localRotation = Quaternion.AngleAxis(-60f, Vector3.up);
            Require(correction.ApplyNow(out string error), error);
            Require(renderer.sharedMesh != mesh && renderer.quality == SkinQuality.Bone2, "Expected a private mesh clone without changing renderer quality.");
            passed.Add("private mesh with original renderer skin quality preserved");
            Vector3[] first = renderer.sharedMesh.vertices;
            for (int i = 0; i < 25; ++i) Require(correction.ApplyNow(out error), error);
            RequireSame(first, renderer.sharedMesh.vertices, 1e-8f, "Repeated evaluation accumulated correction.");
            passed.Add("25 repeated evaluations do not accumulate");

            renderer.BakeMesh(baked);
            Vector3[] corrected = baked.vertices;
            correction.correctionEnabled = false;
            Require(correction.ApplyNow(out error), error);
            renderer.BakeMesh(baked);
            Vector3[] ordinary = baked.vertices;
            correction.correctionEnabled = true;
            Require(correction.ApplyNow(out error), error);
            for (int i = 0; i < 8; ++i)
            {
                var sample = calibration.sources[i];
                var side = calibration.sides[sample.side];
                Matrix4x4 lower = root.transform.worldToLocalMatrix * bones[side.lowerBone].localToWorldMatrix * bindposes[side.lowerBone];
                Matrix4x4 hand = root.transform.worldToLocalMatrix * bones[side.handBone].localToWorldMatrix * bindposes[side.handBone];
                Require(ForearmCorrectionMath.TryTwistAngle(lower, hand, side.axis, out float angle, out error), error);
                Vector3 point = sample.point + artistDeltas[i] * .37f;
                Vector3 expected = ForearmCorrectionMath.ExtraDelta(point, lower, hand, side.axis, side.pivot,
                    sample.ratio, angle, sample.lowerWeight, sample.handWeight, sample.influence);
                Require((corrected[i] - ordinary[i] - expected).magnitude < 2e-5f, "Skinned mesh extra delta differs from the source correction at " + i);
            }
            Require((corrected[3] - ordinary[3]).sqrMagnitude < 1e-12f, "Zero-influence boundary moved.");
            passed.Add("actual BakeMesh delta, artist input and zero-influence boundary");

            foreach (SkinQuality quality in new[] { SkinQuality.Bone1, SkinQuality.Bone4, SkinQuality.Auto, SkinQuality.Bone2 })
            {
                renderer.quality = quality;
                correction.correctionEnabled = false;
                Require(correction.ApplyNow(out error), error);
                renderer.BakeMesh(baked);
                Vector3[] qualityOrdinary = baked.vertices;
                correction.correctionEnabled = true;
                Require(correction.ApplyNow(out error), error);
                renderer.BakeMesh(baked);
                Vector3[] qualityCorrected = baked.vertices;
                for (int i = 0; i < 8; ++i)
                    Require((qualityCorrected[i] - qualityOrdinary[i] - (corrected[i] - ordinary[i])).magnitude < 3e-5f,
                        "The correction failed to match changed renderer influence limits at " + quality + ", vertex " + i);
                Require(renderer.quality == quality, "The runtime changed externally selected skin quality.");
            }
            passed.Add("Bone1/Bone2/Bone4/Auto inverse skinning with four stored influences and unchanged outside baseline");

            renderer.SetBlendShapeWeight(0, 82f);
            Require(correction.ApplyNow(out error), error);
            Require((renderer.sharedMesh.vertices[0] - first[0]).sqrMagnitude > 1e-10f,
                "An artist blendshape change reused stale correction inputs.");
            renderer.SetBlendShapeWeight(0, 37f);
            Require(correction.ApplyNow(out error), error);
            RequireSame(first, renderer.sharedMesh.vertices, 2e-6f, "Restoring an artist input did not restore the correction.");

            ForearmCorrectionData.SourceSample savedSample = calibration.sources[0];
            calibration.sources[0].ratio = .72f;
            Require(correction.ApplyNow(out error), error);
            Require((renderer.sharedMesh.vertices[0] - first[0]).sqrMagnitude > 1e-9f,
                "A per-source calibration edit was skipped by the input cache.");
            calibration.sources[0] = savedSample;
            calibration.sides[0].enabled = false;
            Require(correction.ApplyNow(out error), error);
            for (int i = 0; i < 4; ++i)
                Require((renderer.sharedMesh.vertices[i] - vertices[i]).sqrMagnitude < 1e-12f,
                    "Disabling one side left a cached correction on that side.");
            calibration.sides[0].enabled = true;
            Require(correction.ApplyNow(out error), error);
            RequireSame(first, renderer.sharedMesh.vertices, 2e-6f, "Re-enabling a side did not restore correction.");

            Vector3[] correctedNormals = renderer.sharedMesh.normals;
            correction.updateSurfaceDirections = false;
            Require(correction.ApplyNow(out error), error);
            RequireSame(mesh.normals, renderer.sharedMesh.normals, 1e-8f, "The surface direction toggle reused stale normals.");
            correction.updateSurfaceDirections = true;
            Require(correction.ApplyNow(out error), error);
            RequireSame(correctedNormals, renderer.sharedMesh.normals, 2e-6f, "The surface direction toggle failed to recover.");
            calibration.stencils[0].weights[0] = .5f;
            Require(!correction.ApplyNow(out error), "An in-place mapping change was silently ignored.");
            RequireSame(vertices, renderer.sharedMesh.vertices, 1e-8f, "Invalid mapping left cached correction active.");
            calibration.stencils[0].weights[0] = 1f;
            Require(correction.ApplyNow(out error), error);
            RequireSame(first, renderer.sharedMesh.vertices, 2e-6f, "Mapping failure recovery reused a failed cache.");
            passed.Add("cache invalidation for artist shapes, source ratios, side enables, normals and edited mappings");

            root.transform.rotation = Quaternion.Euler(21f, 73f, -14f);
            Require(correction.ApplyNow(out error), error);
            RequireSame(first, renderer.sharedMesh.vertices, 2e-5f, "Common root rotation changed local correction.");
            passed.Add("common Root rotation invariance");
            bones[0].localRotation = Quaternion.Euler(19f, 0f, 33f);
            bones[2].localRotation = Quaternion.Euler(-27f, 0f, -15f);
            bones[1].localRotation = Quaternion.AngleAxis(25f, Vector3.right) * Quaternion.AngleAxis(-75f, Vector3.up);
            Require(correction.ApplyNow(out error), error);
            passed.Add("bent and elevated arms with signed wrist swing/twist");

            bones[3].localRotation = Quaternion.AngleAxis(140f, Vector3.up);
            Require(!correction.ApplyNow(out error), "Expected ±120 degree guard.");
            RequireSame(vertices, renderer.sharedMesh.vertices, 1e-8f, "A failed side left either arm corrected.");
            bones[3].localRotation = Quaternion.AngleAxis(-60f, Vector3.up);
            Require(correction.ApplyNow(out error), error);
            passed.Add("one-side failure restores both sides and recovers");

            correction.enabled = false;
            Require(renderer.sharedMesh == mesh && renderer.quality == SkinQuality.Bone2, "Disable did not restore original mesh and quality.");
            Require(renderer.localBounds == originalBounds, "Disable did not restore original bounds.");
            Require(renderer.GetBlendShapeWeight(0) == 37f && ForearmCorrection.ComputeFingerprint(mesh) == originalHash,
                "The source mesh, artist shape or skin weights were modified.");
            passed.Add("disable restores original mesh, bounds and quality; artist assets unchanged");
            correction.enabled = true;
            Require(correction.ApplyNow(out error), error);
            Require(correction.Reinitialize(out error), error);
            Require(renderer.sharedMesh != mesh, "Re-enable did not create a new owned clone.");
            passed.Add("re-enable and explicit reinitialize");
            correction.enabled = false;
            calibration.sourceFingerprint = "stale";
            Require(!correction.ApplyNow(out error) && renderer.sharedMesh == mesh, "Stale mesh fingerprint was accepted.");
            passed.Add("stale data rejected before renderer mutation");
            return new Report { passed = true, caseCount = passed.Count, checks = passed.ToArray() };
        }
        finally
        {
            if (root != null) UnityEngine.Object.DestroyImmediate(root);
            if (mesh != null) UnityEngine.Object.DestroyImmediate(mesh);
            if (baked != null) UnityEngine.Object.DestroyImmediate(baked);
            if (calibration != null) UnityEngine.Object.DestroyImmediate(calibration);
            EditorSceneManager.ClosePreviewScene(scene);
        }
    }

    private static void Require(bool condition, string message)
    { if (!condition) throw new InvalidOperationException(message); }

    private static void RequireSame(Vector3[] expected, Vector3[] actual, float tolerance, string message)
    {
        Require(expected.Length == actual.Length, message);
        for (int i = 0; i < expected.Length; ++i)
            Require((expected[i] - actual[i]).magnitude <= tolerance, message + " Vertex " + i);
    }
}
