using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace CharacterDesigner.Unity.Editor
{
    public static class ForearmCorrectionExportValidation
    {
        [Serializable] public sealed class Fixture { public string meshName; public Pose[] cases; }
        [Serializable] public sealed class Pose { public string name; public Bone[] bones; public int[] denseIds; public Vector3[] denseExtra; }
        [Serializable] public sealed class Bone { public string name; public float[] matrix; }
        [Serializable] public sealed class Result
        {
            public bool passed;
            public string prefab;
            public int poseCases, importedVertices, blendShapes, correctedVertices;
            public float maximumDeltaError, outsideDelta, repeatDelta;
            public double medianMilliseconds, worstMilliseconds, movingMedianMilliseconds, movingWorstMilliseconds;
            public long allocatedBytes, movingAllocatedBytes;
            public string sourceFingerprintBefore, sourceFingerprintAfter;
        }
        public static Result Run(string sidecar, string fixturePath)
        {
            var file = ForearmCorrectionImporter.ReadVerified(sidecar);
            var fixture = JsonUtility.FromJson<Fixture>(File.ReadAllText(fixturePath));
            string prefab = ForearmCorrectionImporter.Build(sidecar);
            var scene = EditorSceneManager.NewPreviewScene();
            GameObject instance = null;
            var beforeMesh = new Mesh();
            var afterMesh = new Mesh();
            ForearmCorrectionData temporary = null;
            try
            {
                instance = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(prefab), scene);
                var components = instance.GetComponentsInChildren<ForearmCorrection>(true);
                ForearmCorrection component = null;
                foreach (var candidate in components) if (candidate.targetRenderer.name == fixture.meshName) component = candidate;
                Require(component != null, "Missing correction component.");
                var renderer = component.targetRenderer;
                var source = renderer.sharedMesh;
                var entry = Array.Find(file.meshes, m => m.objectName == fixture.meshName);
                temporary = ForearmCorrectionImporter.Convert(entry, renderer, out Matrix4x4 convert, out int[] ids);
                var result = new Result { prefab = prefab, importedVertices = source.vertexCount,
                    blendShapes = source.blendShapeCount, correctedVertices = component.data.stencils.Length,
                    sourceFingerprintBefore = ForearmCorrection.ComputeFingerprint(source) };
                for (int i = 0; i < source.blendShapeCount; ++i) renderer.SetBlendShapeWeight(i, 0f);
                Transform[] bones = renderer.bones;
                Matrix4x4[] bindposes = source.bindposes;
                var order = new List<int>();
                for (int i = 0; i < bones.Length; ++i) order.Add(i);
                order.Sort((a,b) => Depth(bones[a]).CompareTo(Depth(bones[b])));
                foreach (var pose in fixture.cases)
                {
                    foreach (bool outerRootTurn in new[] { false, true })
                    {
                        instance.transform.SetPositionAndRotation(outerRootTurn ? new Vector3(2, .3f, -1) : Vector3.zero,
                            outerRootTurn ? Quaternion.Euler(13, 71, -8) : Quaternion.identity);
                        var matrices = new Dictionary<string, Matrix4x4>();
                        foreach (var bone in pose.bones) matrices.Add(bone.name, convert * Matrix(bone.matrix) * convert.inverse);
                        foreach (int i in order)
                        {
                            Require(matrices.ContainsKey(bones[i].name), "Golden pose is missing " + bones[i].name);
                            Matrix4x4 desired = renderer.transform.localToWorldMatrix * matrices[bones[i].name] * bindposes[i].inverse;
                            bones[i].SetPositionAndRotation(desired.GetColumn(3), desired.rotation);
                        }
                        foreach (int i in order)
                        {
                            Matrix4x4 actual = renderer.transform.worldToLocalMatrix * bones[i].localToWorldMatrix * bindposes[i];
                            Require(MatrixError(actual, matrices[bones[i].name]) < 2e-4f, "Pose conversion mismatch: " + bones[i].name);
                        }
                        component.correctionEnabled = false;
                        Require(component.ApplyNow(out string error), error);
                        renderer.BakeMesh(beforeMesh, false);
                        component.correctionEnabled = true;
                        Require(component.ApplyNow(out error), error);
                        renderer.BakeMesh(afterMesh, false);
                        Vector3[] before = beforeMesh.vertices, after = afterMesh.vertices;
                        var expected = new Dictionary<int, Vector3>();
                        for (int i = 0; i < pose.denseIds.Length; ++i) expected.Add(pose.denseIds[i], convert.MultiplyVector(pose.denseExtra[i]));
                        for (int i = 0; i < after.Length; ++i)
                        {
                            Vector3 difference = after[i] - before[i];
                            if (expected.TryGetValue(ids[i], out Vector3 target))
                                result.maximumDeltaError = Mathf.Max(result.maximumDeltaError, (difference-target).magnitude);
                            else result.outsideDelta = Mathf.Max(result.outsideDelta, difference.magnitude);
                        }
                        Require(result.maximumDeltaError < 8e-6f, "Actual Unity skin delta differs from Blender: " + result.maximumDeltaError + " in " + pose.name);
                        Require(result.outsideDelta < 1e-6f, "Correction escaped its exported support.");
                        for (int i = 0; i < 5; ++i) Require(component.ApplyNow(out error), error);
                        renderer.BakeMesh(afterMesh, false);
                        Vector3[] repeat = afterMesh.vertices;
                        for (int i = 0; i < after.Length; ++i) result.repeatDelta = Mathf.Max(result.repeatDelta, (repeat[i]-after[i]).magnitude);
                        Require(result.repeatDelta < 1e-7f, "Repeated refresh accumulated correction.");
                        result.poseCases++;
                    }
                }
                for (int i = 0; i < 10; ++i) Require(component.ApplyNow(out _), "Warmup failed.");
                var timings = new double[100];
                long allocated = GC.GetAllocatedBytesForCurrentThread();
                var stopwatch = new Stopwatch();
                for (int i = 0; i < timings.Length; ++i)
                {
                    stopwatch.Restart();
                    Require(component.ApplyNow(out _), "Timing sample failed.");
                    stopwatch.Stop(); timings[i] = stopwatch.Elapsed.TotalMilliseconds;
                }
                result.allocatedBytes = GC.GetAllocatedBytesForCurrentThread() - allocated;
                Array.Sort(timings);
                result.medianMilliseconds = timings[50]; result.worstMilliseconds = timings[99];
                // Measure changing input separately so the idle cache cannot hide update cost.
                Transform movingHand = renderer.bones[component.data.sides[0].handBone];
                Quaternion handRotation = movingHand.localRotation;
                for (int i = 0; i < 10; ++i)
                {
                    movingHand.localRotation = handRotation * Quaternion.AngleAxis(.02f * i, Vector3.up);
                    Require(component.ApplyNow(out _), "Moving warmup failed.");
                }
                allocated = GC.GetAllocatedBytesForCurrentThread();
                for (int i = 0; i < timings.Length; ++i)
                {
                    movingHand.localRotation = handRotation * Quaternion.AngleAxis(.02f * (i + 10), Vector3.up);
                    stopwatch.Restart();
                    Require(component.ApplyNow(out _), "Moving timing sample failed.");
                    stopwatch.Stop(); timings[i] = stopwatch.Elapsed.TotalMilliseconds;
                }
                result.movingAllocatedBytes = GC.GetAllocatedBytesForCurrentThread() - allocated;
                Array.Sort(timings);
                result.movingMedianMilliseconds = timings[50]; result.movingWorstMilliseconds = timings[99];
                movingHand.localRotation = handRotation;
                component.enabled = false;
                Require(renderer.sharedMesh == source, "Disabling did not restore the imported mesh.");
                component.enabled = true;
                Require(component.ApplyNow(out string finalError), finalError);
                component.enabled = false;
                result.sourceFingerprintAfter = ForearmCorrection.ComputeFingerprint(source);
                Require(result.sourceFingerprintAfter == result.sourceFingerprintBefore, "Imported mesh asset was changed.");
                result.passed = true;
                return result;
            }
            finally
            {
                if (instance != null) UnityEngine.Object.DestroyImmediate(instance);
                if (temporary != null) UnityEngine.Object.DestroyImmediate(temporary);
                UnityEngine.Object.DestroyImmediate(beforeMesh); UnityEngine.Object.DestroyImmediate(afterMesh);
                EditorSceneManager.ClosePreviewScene(scene);
            }
        }
        static int Depth(Transform t) { int depth=0; while(t.parent != null) { depth++; t=t.parent; } return depth; }
        static Matrix4x4 Matrix(float[] a) { Require(a.Length==16,"Invalid matrix."); var m = new Matrix4x4(); for(int r=0;r<4;r++) for(int c=0;c<4;c++) m[r,c]=a[r*4+c]; return m; }
        static float MatrixError(Matrix4x4 a, Matrix4x4 b) { float max=0; for(int i=0;i<16;i++) max=Mathf.Max(max,Mathf.Abs(a[i]-b[i])); return max; }
        static void Require(bool value,string message) { if(!value) throw new InvalidDataException(message); }

        [Serializable] public sealed class Request { public string sidecar, fixture, mathFixture, result; }
        [Serializable] sealed class Report
        {
            public bool passed;
            public string error, sceneBefore, sceneAfter;
            public bool dirtyBefore, dirtyAfter;
            public string math, runtime, import;
            public Result character;
        }
        [MenuItem("Tools/Character Designer/Run Validation Request")]
        static void RunRequest()
        {
            var request = JsonUtility.FromJson<Request>(File.ReadAllText("Temp/CharacterDesignerValidation.json"));
            var before = EditorSceneManager.GetActiveScene();
            var result = new Report { sceneBefore=before.path, dirtyBefore=before.isDirty };
            try
            {
                result.math = JsonUtility.ToJson(ForearmCorrectionMathValidation.Run(File.ReadAllText(request.mathFixture)));
                result.runtime = JsonUtility.ToJson(global::ForearmCorrectionRuntimeValidation.Run());
                result.character = Run(request.sidecar, request.fixture);
                result.import = JsonUtility.ToJson(ForearmCorrectionImportValidation.Run(request.sidecar));
                result.passed = true;
            }
            catch(Exception e) { result.error=e.ToString(); UnityEngine.Debug.LogError("Character Designer validation: " + e); }
            finally
            {
                var after = EditorSceneManager.GetActiveScene(); result.sceneAfter=after.path; result.dirtyAfter=after.isDirty;
                File.WriteAllText(request.result,JsonUtility.ToJson(result,true));
                UnityEngine.Debug.Log("Character Designer validation report: " + request.result);
            }
        }
    }
}
