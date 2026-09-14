using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace CharacterDesigner.Unity.Editor
{
    /// <summary>Builds an owned runtime prefab beside a verified Blender export. Never changes a user scene.</summary>
    public sealed class ForearmCorrectionImporter : AssetPostprocessor
    {
        [Serializable] public sealed class ExportFile
        {
            public string schema, fbx, fbxSha256;
            public MeshExport[] meshes;
        }
        [Serializable] public sealed class MeshExport
        {
            public string objectName, sourceTopology;
            public int vertexCount, sourceVertexCount, idUvChannel;
            public Vector3[] positions;
            public SideExport[] sides;
            public SampleExport[] sources;
            public ShapeExport[] shapes;
            public StencilExport[] stencils;
        }
        [Serializable] public sealed class SideExport
        {
            public string name, lowerBone, handBone;
            public Vector3 axis, pivot;
            public bool enabled;
        }
        [Serializable] public sealed class SampleExport
        {
            public int vertex, side;
            public Vector3 point;
            public float lowerWeight, handWeight, ratio, influence;
        }
        [Serializable] public sealed class ShapeExport { public string name; public Vector3[] deltas; }
        [Serializable] public sealed class StencilExport { public int vertex; public int[] sources; public float[] weights; }
        static readonly HashSet<string> Pending = new HashSet<string>();
        const string OwnershipMarker = "CharacterDesigner.ForearmRuntime/1";
        static bool scheduled, building;
        // Fault injection for isolated editor validation; never assigned by the importer UI.
        internal static Action BeforePrefabSaveForTests;

        static string Sidecar(string path) => Path.ChangeExtension(path, null) + ".forearm.json";
        void OnPreprocessModel()
        {
            if (!File.Exists(Sidecar(assetPath))) return;
            // An old/malformed adjacent file must not change an unrelated FBX's
            // import settings. Drain reports an incomplete export after import.
            try { if (ReadVerified(Sidecar(assetPath)).meshes.Length == 0) return; }
            catch (Exception) { return; }
            var importer = (ModelImporter)assetImporter;
            importer.isReadable = true;
            importer.importBlendShapes = true;
            importer.meshCompression = ModelImporterMeshCompression.Off;
            importer.skinWeights = ModelImporterSkinWeights.Custom;
            importer.maxBonesPerVertex = 32;
            importer.minBoneWeight = 0f;
        }
        static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
        {
            if (building) return;
            foreach (string path in imported)
            {
                if (path.EndsWith(".forearm.json", StringComparison.OrdinalIgnoreCase)) Pending.Add(path);
                else if (path.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase) && File.Exists(Sidecar(path))) Pending.Add(Sidecar(path));
            }
            if (Pending.Count == 0 || scheduled) return;
            scheduled = true;
            EditorApplication.delayCall += Drain;
        }
        static void Drain()
        {
            scheduled = false;
            if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating)
            {
                scheduled = true;
                EditorApplication.delayCall += Drain;
                return;
            }
            var paths = new List<string>(Pending);
            Pending.Clear();
            foreach (string path in paths)
            {
                try { Build(path); }
                catch (Exception error) { Debug.LogWarning("Character Designer runtime import: " + error.Message); }
            }
        }
        [MenuItem("Assets/Character Designer/Rebuild Runtime Prefab")]
        static void RebuildSelected()
        {
            string path = AssetDatabase.GetAssetPath(Selection.activeObject);
            Build(path.EndsWith(".forearm.json", StringComparison.OrdinalIgnoreCase) ? path : Sidecar(path));
        }
        [MenuItem("Assets/Character Designer/Rebuild Runtime Prefab", true)]
        static bool CanRebuildSelected()
        {
            if (building || EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling)
                return false;
            string path = AssetDatabase.GetAssetPath(Selection.activeObject);
            if (string.IsNullOrEmpty(path)) return false;
            if (path.EndsWith(".forearm.json", StringComparison.OrdinalIgnoreCase)) return File.Exists(path);
            return path.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase) && File.Exists(Sidecar(path));
        }
        public static ExportFile ReadVerified(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || !path.EndsWith(".forearm.json", StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Select a Character Designer forearm export.");
            ExportFile file;
            try { file = JsonUtility.FromJson<ExportFile>(File.ReadAllText(path)); }
            catch (ArgumentException error) { throw new InvalidDataException("The forearm calibration JSON is invalid.", error); }
            if (file == null || file.schema != "cdesigner.forearm/1" || file.meshes == null)
                throw new InvalidDataException("Unsupported forearm export.");
            if (string.IsNullOrWhiteSpace(file.fbx) || Path.GetFileName(file.fbx) != file.fbx ||
                file.fbx.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
                !file.fbx.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Invalid forearm FBX filename.");
            if (string.IsNullOrEmpty(file.fbxSha256) || file.fbxSha256.Length != 64)
                throw new InvalidDataException("The forearm export has no valid FBX checksum.");
            foreach (char character in file.fbxSha256)
                if (!Uri.IsHexDigit(character)) throw new InvalidDataException("Invalid FBX checksum.");
            var names = new HashSet<string>(StringComparer.Ordinal);
            foreach (MeshExport mesh in file.meshes)
            {
                ValidateEntry(mesh);
                if (!names.Add(mesh.objectName)) throw new InvalidDataException("Duplicate bound mesh: " + mesh.objectName);
            }
            string fbx = Path.Combine(Path.GetDirectoryName(path), file.fbx);
            using (var stream = File.OpenRead(fbx))
            using (var hash = SHA256.Create())
                if (!string.Equals(BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", ""),
                    file.fbxSha256, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("FBX and forearm calibration do not match yet. Re-export or rebuild after both files finish updating.");
            return file;
        }
        public static string Build(string path)
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode) throw new InvalidOperationException("Stop Play before rebuilding the runtime prefab.");
            if (building) throw new InvalidOperationException("A forearm runtime prefab is already being built.");
            path = ProjectAssetPath(path);
            ExportFile export = ReadVerified(path);
            string folder = Path.GetDirectoryName(path).Replace('\\', '/');
            string fbx = folder + "/" + export.fbx;
            string stem = Path.GetFileNameWithoutExtension(export.fbx);
            string prefabPath = folder + "/" + stem + ".Runtime.prefab";
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(fbx);
            if (model == null) throw new InvalidDataException("Import the exported FBX before creating its runtime prefab.");
            var existing = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
            if (File.Exists(prefabPath) && existing == null)
                throw new InvalidDataException("A different or unimported asset already uses " + prefabPath);
            if (existing != null)
            {
                var components = existing.GetComponentsInChildren<ForearmCorrection>(true);
                var previousImporter = AssetImporter.GetAtPath(prefabPath);
                string marker = previousImporter == null ? null : previousImporter.userData;
                bool marked = marker == OwnershipMarker;
                if (EditorUtility.IsDirty(existing) || (!marked && (components.Length == 0 || !string.IsNullOrEmpty(marker))))
                    throw new InvalidDataException("The runtime prefab is foreign or has unsaved edits: " + prefabPath);
                foreach (var component in components)
                    if (component.data == null || component.data.sourceMesh == null ||
                        AssetDatabase.GetAssetPath(component.data.sourceMesh) != fbx)
                        throw new InvalidDataException("A different prefab already uses " + prefabPath);
            }
            var preview = EditorSceneManager.NewPreviewScene();
            GameObject instance = null;
            var prepared = new List<ForearmCorrectionData>();
            var destinations = new List<string>();
            var renderers = new List<SkinnedMeshRenderer>();
            var backups = new List<AssetBackup>();
            var destinationSet = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            building = true;
            try
            {
                instance = (GameObject)PrefabUtility.InstantiatePrefab(model, preview);
                instance.name = stem;
                var candidates = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true);
                foreach (MeshExport entry in export.meshes)
                {
                    SkinnedMeshRenderer renderer = null;
                    foreach (var candidate in candidates)
                        if (candidate.name == entry.objectName)
                        {
                            if (renderer != null) throw new InvalidDataException("Ambiguous mesh name: " + entry.objectName);
                            renderer = candidate;
                        }
                    if (renderer == null) throw new InvalidDataException("Missing bound mesh: " + entry.objectName);
                    var data = Convert(entry, renderer, out _, out _);
                    prepared.Add(data);
                    renderers.Add(renderer);
                    string target = folder + "/" + stem + "." + SafeName(entry.objectName) + ".Forearm.asset";
                    if (!destinationSet.Add(target))
                        throw new InvalidDataException("Mesh names produce the same generated asset path: " + target);
                    var previousData = AssetDatabase.LoadAssetAtPath<ForearmCorrectionData>(target);
                    if (File.Exists(target) && (previousData == null || previousData.sourceMesh == null ||
                        AssetDatabase.GetAssetPath(previousData.sourceMesh) != fbx))
                        throw new InvalidDataException("A different asset already uses " + target);
                    if (previousData != null && EditorUtility.IsDirty(previousData))
                        throw new InvalidDataException("Save or revert the edited correction asset before rebuilding: " + target);
                    destinations.Add(target);
                    // Validate both sides and the actual mesh before touching an existing generated asset.
                    var component = renderer.gameObject.AddComponent<ForearmCorrection>();
                    component.targetRenderer = renderer;
                    component.data = data;
                    if (!component.ApplyNow(out string error)) throw new InvalidDataException(error);
                    component.enabled = false;
                    component.enabled = true;
                }
                // All mesh correspondence, sides and runtime inputs passed before
                // taking snapshots or changing any persistent asset. Preserve meta
                // files as well as data so rollback keeps references and GUIDs.
                foreach (string destination in destinations) backups.Add(new AssetBackup(destination));
                backups.Add(new AssetBackup(prefabPath));
                AssetDatabase.DisallowAutoRefresh();
                try
                {
                    for (int i = 0; i < prepared.Count; ++i)
                    {
                        var old = AssetDatabase.LoadAssetAtPath<ForearmCorrectionData>(destinations[i]);
                        if (old == null) { AssetDatabase.CreateAsset(prepared[i], destinations[i]); old = prepared[i]; }
                        else { EditorUtility.CopySerialized(prepared[i], old); EditorUtility.SetDirty(old); }
                        renderers[i].GetComponent<ForearmCorrection>().data = old;
                        AssetDatabase.SaveAssetIfDirty(old);
                    }
                    BeforePrefabSaveForTests?.Invoke();
                    PrefabUtility.SaveAsPrefabAsset(instance, prefabPath, out bool success);
                    if (!success) throw new IOException("Could not save the generated runtime prefab.");
                    // Ownership survives an intentional empty calibration export:
                    // its generated prefab then contains no correction component.
                    var prefabImporter = AssetImporter.GetAtPath(prefabPath);
                    if (prefabImporter == null) throw new IOException("Could not mark the generated runtime prefab.");
                    prefabImporter.userData = OwnershipMarker;
                    prefabImporter.SaveAndReimport();
                }
                catch (Exception publishError)
                {
                    var failures = new List<Exception> { publishError };
                    foreach (AssetBackup backup in backups)
                        try { backup.Restore(); }
                        catch (Exception restoreError) { failures.Add(restoreError); }
                    if (failures.Count > 1)
                        throw new AggregateException("Runtime import failed and some generated assets could not be restored.", failures);
                    throw;
                }
                finally { AssetDatabase.AllowAutoRefresh(); }
                return prefabPath;
            }
            finally
            {
                if (instance != null) UnityEngine.Object.DestroyImmediate(instance);
                foreach (var data in prepared) if (data != null && !AssetDatabase.Contains(data)) UnityEngine.Object.DestroyImmediate(data);
                foreach (AssetBackup backup in backups) backup.Dispose();
                EditorSceneManager.ClosePreviewScene(preview);
                building = false;
            }
        }
        static string SafeName(string name)
        {
            foreach (char c in Path.GetInvalidFileNameChars()) name = name.Replace(c, '_');
            name = name.TrimEnd(' ', '.');
            if (string.IsNullOrEmpty(name)) throw new InvalidDataException("The mesh name cannot form an asset filename.");
            return name;
        }

        static string ProjectAssetPath(string path)
        {
            if (string.IsNullOrWhiteSpace(path)) throw new InvalidDataException("Select a forearm export under Assets.");
            string full = Path.GetFullPath(path);
            string assets = Path.GetFullPath(Application.dataPath).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            if (!full.StartsWith(assets + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Generated runtime assets must remain inside this Unity project's Assets folder.");
            return "Assets/" + full.Substring(assets.Length + 1).Replace('\\', '/');
        }

        sealed class AssetBackup : IDisposable
        {
            readonly string path;
            readonly byte[] contents, metadata;
            readonly ForearmCorrectionData previous;
            public AssetBackup(string path)
            {
                this.path = path;
                contents = File.Exists(path) ? File.ReadAllBytes(path) : null;
                metadata = File.Exists(path + ".meta") ? File.ReadAllBytes(path + ".meta") : null;
                if (contents == null && metadata != null)
                    throw new InvalidDataException("An orphaned meta file already uses " + path);
                var data = AssetDatabase.LoadAssetAtPath<ForearmCorrectionData>(path);
                if (data != null) previous = UnityEngine.Object.Instantiate(data);
            }
            public void Restore()
            {
                if (contents == null)
                {
                    AssetDatabase.DeleteAsset(path);
                    if (File.Exists(path)) File.Delete(path);
                    if (File.Exists(path + ".meta")) File.Delete(path + ".meta");
                    return;
                }
                var current = AssetDatabase.LoadAssetAtPath<ForearmCorrectionData>(path);
                if (previous != null && current != null) EditorUtility.CopySerialized(previous, current);
                File.WriteAllBytes(path, contents);
                if (metadata != null) File.WriteAllBytes(path + ".meta", metadata);
                else if (File.Exists(path + ".meta")) File.Delete(path + ".meta");
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
            }
            public void Dispose()
            {
                if (previous != null) UnityEngine.Object.DestroyImmediate(previous);
            }
        }

        public static ForearmCorrectionData Convert(MeshExport entry, SkinnedMeshRenderer renderer, out Matrix4x4 conversion, out int[] vertexIds)
        {
            ValidateEntry(entry);
            if (renderer == null) throw new InvalidDataException("The bound mesh renderer is missing.");
            Mesh mesh = renderer.sharedMesh;
            if (mesh == null || !mesh.isReadable) throw new InvalidDataException("Runtime correction needs a readable imported mesh. Reimport its FBX with the companion installed.");
            Vector3[] points = mesh.vertices;
            vertexIds = ReadVertexIds(entry, mesh);
            conversion = FitCoordinates(entry.positions, points, vertexIds);
            var data = ScriptableObject.CreateInstance<ForearmCorrectionData>();
            try
            {
                data.name = entry.objectName + " Forearm";
                data.sourceMesh = mesh;
                data.vertexCount = mesh.vertexCount;
                data.sides = new ForearmCorrectionData.Side[entry.sides.Length];
                Transform[] bones = renderer.bones;
                for (int i = 0; i < data.sides.Length; ++i)
                {
                    SideExport side = entry.sides[i];
                    data.sides[i] = new ForearmCorrectionData.Side { name = side.name, lowerBone = BoneIndex(bones, side.lowerBone),
                        handBone = BoneIndex(bones, side.handBone), axis = conversion.MultiplyVector(side.axis).normalized,
                        pivot = conversion.MultiplyPoint3x4(side.pivot), enabled = side.enabled };
                }
                data.sources = new ForearmCorrectionData.SourceSample[entry.sources.Length];
                for (int i = 0; i < data.sources.Length; ++i)
                {
                    SampleExport s = entry.sources[i];
                    data.sources[i] = new ForearmCorrectionData.SourceSample { point = conversion.MultiplyPoint3x4(s.point),
                        side = s.side, lowerWeight = s.lowerWeight, handWeight = s.handWeight, ratio = s.ratio, influence = s.influence };
                }
                data.shapes = new ForearmCorrectionData.ShapeInput[entry.shapes.Length];
                for (int i = 0; i < data.shapes.Length; ++i)
                {
                    ShapeExport shape = entry.shapes[i];
                    if (shape.deltas.Length != data.sources.Length) throw new InvalidDataException("Artist blendshape source correspondence changed.");
                    int shapeIndex = mesh.GetBlendShapeIndex(shape.name);
                    if (shapeIndex < 0) throw new InvalidDataException("Missing artist blendshape: " + shape.name);
                    var indices = new int[shape.deltas.Length];
                    var deltas = new Vector3[indices.Length];
                    for (int j = 0; j < indices.Length; ++j) { indices[j] = j; deltas[j] = conversion.MultiplyVector(shape.deltas[j]); }
                    data.shapes[i] = new ForearmCorrectionData.ShapeInput { blendShapeName = shape.name, blendShapeIndex = shapeIndex,
                        sourceIndices = indices, deltas = deltas };
                }
                var rows = new Dictionary<int, StencilExport>();
                foreach (StencilExport row in entry.stencils) rows.Add(row.vertex, row);
                var stencils = new List<ForearmCorrectionData.Stencil>();
                for (int i = 0; i < vertexIds.Length; ++i)
                    if (rows.TryGetValue(vertexIds[i], out StencilExport row))
                        stencils.Add(new ForearmCorrectionData.Stencil { vertexIndex = i, sourceIndices = row.sources, weights = row.weights });
                data.stencils = stencils.ToArray();
                data.sourceFingerprint = ForearmCorrection.ComputeFingerprint(mesh);
                return data;
            }
            catch { UnityEngine.Object.DestroyImmediate(data); throw; }
        }
        static void ValidateEntry(MeshExport entry)
        {
            if (entry == null || string.IsNullOrWhiteSpace(entry.objectName))
                throw new InvalidDataException("A forearm mesh entry has no object name.");
            if (entry.vertexCount < 4 || entry.sourceVertexCount <= 0 || entry.positions == null ||
                entry.positions.Length != entry.vertexCount || entry.idUvChannel < 0 || entry.idUvChannel > 7)
                throw new InvalidDataException(entry.objectName + ": invalid vertex correspondence or UV channel.");
            foreach (Vector3 point in entry.positions)
                if (!ForearmCorrectionMath.IsFinite(point)) throw new InvalidDataException("A calibration position is non-finite.");
            if (entry.sides == null || entry.sides.Length == 0 || entry.sources == null || entry.sources.Length == 0 ||
                entry.shapes == null || entry.stencils == null || entry.stencils.Length == 0)
                throw new InvalidDataException(entry.objectName + ": missing correction arrays.");
            var names = new HashSet<string>(StringComparer.Ordinal);
            foreach (SideExport side in entry.sides)
            {
                if (side == null || string.IsNullOrWhiteSpace(side.name) || !names.Add(side.name) ||
                    string.IsNullOrWhiteSpace(side.lowerBone) || string.IsNullOrWhiteSpace(side.handBone) ||
                    side.lowerBone == side.handBone || !ForearmCorrectionMath.IsFinite(side.axis) ||
                    side.axis.sqrMagnitude < 1e-12f || !ForearmCorrectionMath.IsFinite(side.pivot))
                    throw new InvalidDataException(entry.objectName + ": invalid or duplicated forearm side.");
            }
            var captured = new HashSet<int>();
            foreach (SampleExport sample in entry.sources)
            {
                if (sample == null || sample.vertex < 0 || sample.vertex >= entry.sourceVertexCount ||
                    !captured.Add(sample.vertex) || sample.side < 0 || sample.side >= entry.sides.Length ||
                    !ForearmCorrectionMath.IsFinite(sample.point) || !UnitInterval(sample.ratio) ||
                    !UnitInterval(sample.influence) || !UnitInterval(sample.lowerWeight) ||
                    !UnitInterval(sample.handWeight) || sample.lowerWeight + sample.handWeight > 1.0001f)
                    throw new InvalidDataException(entry.objectName + ": invalid, overlapping or unnormalized source sample.");
            }
            names.Clear();
            foreach (ShapeExport shape in entry.shapes)
            {
                if (shape == null || string.IsNullOrWhiteSpace(shape.name) || !names.Add(shape.name) ||
                    shape.deltas == null || shape.deltas.Length != entry.sources.Length)
                    throw new InvalidDataException(entry.objectName + ": invalid artist blendshape correspondence.");
                foreach (Vector3 delta in shape.deltas)
                    if (!ForearmCorrectionMath.IsFinite(delta)) throw new InvalidDataException("An artist blendshape delta is non-finite.");
            }
            var destinations = new HashSet<int>();
            foreach (StencilExport row in entry.stencils)
            {
                if (row == null || row.vertex < 0 || row.vertex >= entry.vertexCount || !destinations.Add(row.vertex) ||
                    row.sources == null || row.weights == null || row.sources.Length == 0 ||
                    row.sources.Length != row.weights.Length)
                    throw new InvalidDataException(entry.objectName + ": invalid or duplicated subdivision stencil.");
                captured.Clear();
                double sum = 0.0;
                for (int i = 0; i < row.sources.Length; ++i)
                {
                    if (row.sources[i] < 0 || row.sources[i] >= entry.sources.Length || !captured.Add(row.sources[i]) ||
                        !ForearmCorrectionMath.IsFinite(row.weights[i]) || row.weights[i] < 0f)
                        throw new InvalidDataException(entry.objectName + ": invalid subdivision source or weight.");
                    sum += row.weights[i];
                }
                // A row contains only captured sources, so its sum can be below
                // one. Linear Catmull-Clark response must not amplify a constant.
                if (sum <= 0.0 || sum > 1.001)
                    throw new InvalidDataException(entry.objectName + ": invalid subdivision response sum.");
            }
        }
        static bool UnitInterval(float value) => ForearmCorrectionMath.IsFinite(value) && value >= 0f && value <= 1f;

        static int BoneIndex(Transform[] bones, string name)
        {
            int found = -1;
            for (int i = 0; i < bones.Length; ++i)
                if (bones[i] != null && bones[i].name == name)
                {
                    if (found != -1) throw new InvalidDataException("Ambiguous skin bone: " + name);
                    found = i;
                }
            if (found < 0) throw new InvalidDataException("Missing skin bone: " + name);
            return found;
        }
        public static int[] ReadVertexIds(MeshExport entry, Mesh mesh)
        {
            if (entry == null || mesh == null || !mesh.isReadable || entry.vertexCount <= 0 ||
                entry.idUvChannel < 0 || entry.idUvChannel > 7)
                throw new InvalidDataException("Invalid mesh or forearm vertex-ID channel.");
            var uv = new List<Vector2>();
            mesh.GetUVs(entry.idUvChannel, uv);
            if (uv.Count != mesh.vertexCount) throw new InvalidDataException("The forearm vertex-ID UV channel is missing. Re-export the FBX.");
            var ids = new int[uv.Count];
            var seen = new bool[entry.vertexCount];
            for (int i = 0; i < ids.Length; ++i)
            {
                if (!ForearmCorrectionMath.IsFinite(uv[i].x) || !ForearmCorrectionMath.IsFinite(uv[i].y) ||
                    Mathf.Abs(uv[i].y - .375f) > .002f)
                    throw new InvalidDataException("The forearm vertex-ID UV channel was overwritten.");
                int id = Mathf.RoundToInt(uv[i].x) - 1;
                if (id < 0 || id >= entry.vertexCount || Mathf.Abs(uv[i].x - id - 1) > .002f)
                    throw new InvalidDataException("Forearm vertex IDs were changed by mesh compression or UV processing.");
                ids[i] = id; seen[id] = true;
            }
            for (int i = 0; i < seen.Length; ++i) if (!seen[i]) throw new InvalidDataException("An exported forearm vertex is missing from the imported mesh.");
            return ids;
        }
        public static Matrix4x4 FitCoordinates(Vector3[] source, Vector3[] destination, int[] ids)
        {
            if (source == null || source.Length < 4 || destination == null || ids == null ||
                destination.Length != ids.Length || ids.Length < 4)
                throw new InvalidDataException("Not enough correspondence points.");
            foreach (Vector3 point in source)
                if (!ForearmCorrectionMath.IsFinite(point)) throw new InvalidDataException("Non-finite exported mesh position.");
            for (int i = 0; i < ids.Length; ++i)
                if (ids[i] < 0 || ids[i] >= source.Length || !ForearmCorrectionMath.IsFinite(destination[i]))
                    throw new InvalidDataException("Invalid imported coordinate correspondence.");
            int a = 0, b = 0, c = 0, d = 0;
            float best = 0f;
            Vector3 origin = source[ids[a]];
            for (int i = 1; i < ids.Length; ++i) { float score = (source[ids[i]] - origin).sqrMagnitude; if (score > best) { best = score; b = i; } }
            Vector3 x = source[ids[b]] - origin;
            best = 0f;
            for (int i = 1; i < ids.Length; ++i) { float score = Vector3.Cross(x, source[ids[i]] - origin).sqrMagnitude; if (score > best) { best = score; c = i; } }
            Vector3 normal = Vector3.Cross(x, source[ids[c]] - origin);
            best = 0f;
            for (int i = 1; i < ids.Length; ++i) { float score = Mathf.Abs(Vector3.Dot(normal, source[ids[i]] - origin)); if (score > best) { best = score; d = i; } }
            if (best <= 0f) throw new InvalidDataException("Cannot resolve FBX coordinates from a flat mesh.");
            Matrix4x4 before = Frame(origin, source[ids[b]], source[ids[c]], source[ids[d]]);
            Matrix4x4 after = Frame(destination[a], destination[b], destination[c], destination[d]);
            if (!ForearmCorrectionMath.TryInverseLinear(before, out Matrix4x4 inverse, out string inverseError))
                throw new InvalidDataException("Unstable FBX coordinate correspondence: " + inverseError);
            Vector3 inverseTranslation = -inverse.MultiplyVector(origin);
            inverse.m03 = inverseTranslation.x;
            inverse.m13 = inverseTranslation.y;
            inverse.m23 = inverseTranslation.z;
            Matrix4x4 result = after * inverse;
            if (!ForearmCorrectionMath.IsFinite(result)) throw new InvalidDataException("FBX coordinate conversion is non-finite.");
            float scale = result.MultiplyVector(Vector3.right).magnitude;
            for (int i = 0; i < ids.Length; ++i)
                if ((result.MultiplyPoint3x4(source[ids[i]]) - destination[i]).magnitude > Mathf.Max(1e-6f, scale * 2e-5f))
                    throw new InvalidDataException("Imported mesh vertices no longer match the exported calibration.");
            Vector3 rx = result.GetColumn(0), ry = result.GetColumn(1), rz = result.GetColumn(2);
            if (!ForearmCorrectionMath.IsFinite(scale) || scale < 1e-8f || Mathf.Abs(ry.magnitude-scale) > scale*1e-4f || Mathf.Abs(rz.magnitude-scale) > scale*1e-4f ||
                Mathf.Abs(Vector3.Dot(rx,ry)) > scale*scale*1e-4f || Mathf.Abs(Vector3.Dot(rx,rz)) > scale*scale*1e-4f || Mathf.Abs(Vector3.Dot(ry,rz)) > scale*scale*1e-4f)
                throw new InvalidDataException("Nonuniform FBX coordinate conversion is unsupported.");
            return result;
        }
        static Matrix4x4 Frame(Vector3 a, Vector3 b, Vector3 c, Vector3 d)
        {
            var result = Matrix4x4.identity;
            result.SetColumn(0, new Vector4(b.x-a.x, b.y-a.y, b.z-a.z, 0));
            result.SetColumn(1, new Vector4(c.x-a.x, c.y-a.y, c.z-a.z, 0));
            result.SetColumn(2, new Vector4(d.x-a.x, d.y-a.y, d.z-a.z, 0));
            result.SetColumn(3, new Vector4(a.x,a.y,a.z,1));
            return result;
        }
    }
}
