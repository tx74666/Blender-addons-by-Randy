using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace CharacterDesigner.Unity.Editor
{
    /// <summary>Explicit validation of owned export assets; never invoked automatically.</summary>
    public static class ForearmCorrectionImportValidation
    {
        [Serializable] public sealed class Report
        {
            public int passed;
            public bool repeatGuidStable, invalidSecondSideRejected, publishRollbackExact;
            public bool emptyMarkerRemovedControls, repeatEmptyWorked, originalsRestored, sceneUnchanged;
            public string prefab;
        }
        sealed class Snapshot
        {
            internal string path, guid;
            internal byte[] asset, meta;
            internal Snapshot(string path)
            {
                this.path = path;
                guid = AssetDatabase.AssetPathToGUID(path);
                asset = File.ReadAllBytes(path);
                meta = File.ReadAllBytes(path + ".meta");
            }
            internal void AssertExact()
            {
                Check(Equal(asset, File.ReadAllBytes(path)) && Equal(meta, File.ReadAllBytes(path + ".meta")),
                    "Generated asset or meta was not restored: " + path);
            }
            internal void Restore()
            {
                File.WriteAllBytes(path, asset);
                File.WriteAllBytes(path + ".meta", meta);
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
            }
        }
        public static Report Run(string sidecar)
        {
            Check(!EditorApplication.isPlayingOrWillChangePlaymode, "Stop Play before import validation.");
            Check(ForearmCorrectionImporter.BeforePrefabSaveForTests == null, "Another import validation owns the fault hook.");
            string sceneBefore = SceneState();
            byte[] originalJson = File.ReadAllBytes(sidecar);
            string originalText = File.ReadAllText(sidecar);
            var original = ForearmCorrectionImporter.ReadVerified(sidecar);
            string folder = Path.GetDirectoryName(sidecar);
            string fbx = Path.Combine(folder, original.fbx);
            string fbxHash = Hash(fbx);
            string temporary = Path.Combine(folder, ".cdesigner-validation-" + Guid.NewGuid().ToString("N") + ".forearm.json");
            var snapshots = new List<Snapshot>();
            var report = new Report();
            bool began = false;
            try
            {
                report.prefab = ForearmCorrectionImporter.Build(sidecar);
                began = true;
                var asset = AssetDatabase.LoadAssetAtPath<GameObject>(report.prefab);
                var components = asset.GetComponentsInChildren<ForearmCorrection>(true);
                Check(components.Length > 0, "Validation requires an existing corrected export.");
                var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (var component in components)
                {
                    string path = AssetDatabase.GetAssetPath(component.data);
                    if (paths.Add(path)) snapshots.Add(new Snapshot(path));
                }
                snapshots.Add(new Snapshot(report.prefab));

                ForearmCorrectionImporter.Build(sidecar);
                foreach (Snapshot snapshot in snapshots)
                    Check(AssetDatabase.AssetPathToGUID(snapshot.path) == snapshot.guid, "Repeated import changed an asset GUID.");
                report.repeatGuidStable = true;
                ++report.passed;

                var invalid = JsonUtility.FromJson<ForearmCorrectionImporter.ExportFile>(originalText);
                bool changed = false;
                foreach (var mesh in invalid.meshes)
                    foreach (var sample in mesh.sources)
                        if (!changed && sample.side == 1) { sample.ratio = 2f; changed = true; }
                Check(changed, "Validation requires a second forearm side.");
                File.WriteAllText(temporary, JsonUtility.ToJson(invalid));
                ExpectFailure(() => ForearmCorrectionImporter.Build(temporary), "invalid, overlapping or unnormalized source sample");
                AssertSnapshots(snapshots);
                report.invalidSecondSideRejected = true;
                ++report.passed;

                var revised = JsonUtility.FromJson<ForearmCorrectionImporter.ExportFile>(originalText);
                var first = revised.meshes[0].sources[0];
                first.ratio = first.ratio < .5f ? .73f : .27f;
                File.WriteAllText(temporary, JsonUtility.ToJson(revised));
                ForearmCorrectionImporter.BeforePrefabSaveForTests = () => { throw new IOException("Injected prefab save failure"); };
                try { ExpectFailure(() => ForearmCorrectionImporter.Build(temporary), "Injected prefab save failure"); }
                finally { ForearmCorrectionImporter.BeforePrefabSaveForTests = null; }
                AssertSnapshots(snapshots);
                report.publishRollbackExact = true;
                ++report.passed;

                revised.meshes = Array.Empty<ForearmCorrectionImporter.MeshExport>();
                File.WriteAllText(temporary, JsonUtility.ToJson(revised));
                ForearmCorrectionImporter.Build(temporary);
                Check(AssetDatabase.LoadAssetAtPath<GameObject>(report.prefab).GetComponentsInChildren<ForearmCorrection>(true).Length == 0,
                    "Empty marker retained a forearm correction component.");
                report.emptyMarkerRemovedControls = true;
                ++report.passed;
                ForearmCorrectionImporter.Build(temporary);
                Check(AssetDatabase.LoadAssetAtPath<GameObject>(report.prefab).GetComponentsInChildren<ForearmCorrection>(true).Length == 0,
                    "Repeated empty marker recreated correction components.");
                report.repeatEmptyWorked = true;
                ++report.passed;
            }
            finally
            {
                ForearmCorrectionImporter.BeforePrefabSaveForTests = null;
                try
                {
                    if (began)
                    {
                        try { ForearmCorrectionImporter.Build(sidecar); }
                        finally
                        {
                            // Re-adding a removed component may assign new local
                            // file IDs. Restore the original bytes even if rebuild
                            // itself fails during cleanup.
                            var failures = new List<Exception>();
                            foreach (Snapshot snapshot in snapshots)
                                try { snapshot.Restore(); }
                                catch (Exception error) { failures.Add(error); }
                            if (failures.Count > 0) throw new AggregateException("Could not restore generated validation assets.", failures);
                            AssertSnapshots(snapshots);
                        }
                    }
                }
                finally
                {
                    if (File.Exists(temporary)) File.Delete(temporary);
                    if (File.Exists(temporary + ".meta")) File.Delete(temporary + ".meta");
                }
            }
            Check(Equal(originalJson, File.ReadAllBytes(sidecar)) && Hash(fbx) == fbxHash,
                "Validation changed the source FBX or sidecar.");
            report.originalsRestored = true;
            ++report.passed;
            Check(SceneState() == sceneBefore, "Validation changed the user's scene setup or dirty state.");
            report.sceneUnchanged = true;
            ++report.passed;
            return report;
        }
        static void AssertSnapshots(List<Snapshot> snapshots) { foreach (Snapshot snapshot in snapshots) snapshot.AssertExact(); }
        static void ExpectFailure(Action action, string reason)
        {
            try { action(); }
            catch (Exception error) { Check(error.ToString().Contains(reason), "Unexpected import failure: " + error); return; }
            throw new InvalidOperationException("Invalid import unexpectedly succeeded: " + reason);
        }
        static string SceneState()
        {
            string state = SceneManager.GetActiveScene().handle + ":" + EditorSceneManager.previewSceneCount;
            for (int i = 0; i < SceneManager.sceneCount; ++i)
            {
                var scene = SceneManager.GetSceneAt(i);
                state += "|" + scene.handle + ":" + scene.path + ":" + scene.isLoaded + ":" + scene.isDirty;
            }
            return state;
        }
        static string Hash(string path)
        {
            using (var input = File.OpenRead(path)) using (var hash = SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(input));
        }
        static bool Equal(byte[] left, byte[] right)
        {
            if (left.Length != right.Length) return false;
            for (int i = 0; i < left.Length; ++i) if (left[i] != right[i]) return false;
            return true;
        }
        static void Check(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
    }
}
