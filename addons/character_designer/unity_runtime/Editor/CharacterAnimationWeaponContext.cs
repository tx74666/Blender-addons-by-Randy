using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace CharacterDesigner.Unity.Editor
{
    // Captures imported Unity geometry, not a guessed FBX unit/axis conversion.
    // No gameplay instance, importer write, mesh readability change or asset publication is needed.
    public static class CharacterAnimationWeaponContext
    {
        public const string Schema = "cdesigner.weapon-context/1";

        [Serializable] public sealed class Document
        {
            public string schema = Schema, units = "metres", coordinate = "unity-lh-y-up", matrixLayout = "row-major";
            public string prefabGuid, prefabAssetPath, prefabFileSha256, prefabMetaSha256, prefabDependencyHash;
            public string modelGuid, modelAssetPath, modelFile, modelSha256, modelMetaSha256, modelDependencyHash;
            public SourceDependency[] sourceDependencies;
            public ExcludedDependency[] excludedDependencies;
            public ImportSettings importer;
            public TransformRecord modelRoot, prefabRoot;
            public float[] spawnedPrefabRootMatrix;
            public RendererRecord[] meshes;
            public BoundsRecord prefabBounds, spawnedBounds;
            public string geometrySpace = "prefab-local; prefab root TRS excluded";
            public string spawnedGeometrySpace = "weapon socket-local; spawned prefab root matrix applied once";
            public string boundsPolicy = "Geometry of enabled mesh renderers with active authored ancestors; no camera LOD selection, shaders or procedural effects.";
            public string[] nonMeshRenderers;
        }
        [Serializable] public sealed class ImportSettings
        {
            public float globalScale, fileScale;
            public bool useFileScale, bakeAxisConversion;
            public string[] verifiedSerializedFields, publicApiOnlyFields;
            public SerializedField[] serializedFields;
        }
        [Serializable] public sealed class SerializedField
        { public string path, type, stringValue; public float floatValue; public bool boolValue; public int intValue; }
        [Serializable] public sealed class SourceDependency
        { public string assetPath, file, guid, fileSha256, metaSha256; }
        [Serializable] public sealed class ExcludedDependency
        { public string assetPath, guid, reason; }
        [Serializable] public sealed class TransformRecord
        {
            public Vector3 position, scale;
            public Quaternion rotation;
            public float[] matrix;
        }
        [Serializable] public sealed class BoundsRecord { public Vector3 min, max; }
        [Serializable] public sealed class RendererRecord
        {
            public string rendererPath, rendererType, meshName, meshGuid, meshAssetPath, geometryKind;
            public string[] modelRendererPaths;
            public long meshLocalId;
            public int vertexCount;
            public bool enabled, activeInPrefab;
            public BoundsRecord meshLocalBounds;
            public float[] rendererLocalToPrefab;
            public Vector3[] vertices, renderedVertices;
            public int[] triangles;
            public SubmeshRecord[] submeshes;
        }
        [Serializable] public sealed class SubmeshRecord
        {
            public int submesh, triangleStart, triangleCount;
            public string sourceTopology;
        }

        public static string Export(GameObject weapon, string outputFolder)
        {
            RequireIdle();
            var document = Capture(weapon);
            Require(!string.IsNullOrWhiteSpace(outputFolder), "Choose an exchange folder for the actual weapon context.");
            string folder = Path.GetFullPath(outputFolder);
            Require(!Within(folder, Path.GetFullPath(Application.dataPath)), "Export weapon context outside Unity Assets.");
            byte[] bytes = new UTF8Encoding(false).GetBytes(CharacterAnimationCompositionJson.ToJson(document));
            string sha = Hash(bytes);
            // Every published context is immutable, including contexts already referenced by another B.
            string destination = Path.Combine(folder, "weapon-contexts", sha, "weapon_context.json");
            VerifySources(document);
            Directory.CreateDirectory(Path.GetDirectoryName(destination));
            if (File.Exists(destination))
            {
                Require(FileHash(destination) == sha, "An existing weapon context changed. Preserve it for inspection and use another exchange folder.");
                return destination;
            }
            string temporary = Path.Combine(Path.GetDirectoryName(destination), Guid.NewGuid().ToString("N") + ".tmp");
            bool ownsTemporary = false;
            try
            {
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    ownsTemporary = true;
                    stream.Write(bytes, 0, bytes.Length);
                }
                VerifySources(document);
                if (File.Exists(destination)) Require(FileHash(destination) == sha, "The weapon context destination was claimed by different content.");
                else { File.Move(temporary, destination); ownsTemporary = false; }
                return destination;
            }
            finally { if (ownsTemporary && File.Exists(temporary)) File.Delete(temporary); }
        }

        public static void Validate(CharacterAnimationComposition spec)
        {
            Require(spec != null && !string.IsNullOrWhiteSpace(spec.weaponContextFile) && Path.IsPathRooted(spec.weaponContextFile) &&
                !string.IsNullOrEmpty(spec.weaponContextSha256) && File.Exists(spec.weaponContextFile) &&
                FileHash(spec.weaponContextFile) == spec.weaponContextSha256, "The captured Unity weapon context changed or is missing.");
            Document document;
            try { document = CharacterAnimationCompositionJson.FromJson<Document>(File.ReadAllText(spec.weaponContextFile)); }
            catch (Exception error) { throw new InvalidOperationException("The weapon context is not a valid captured document.", error); }
            Require(document != null && document.schema == Schema && document.units == "metres" &&
                document.coordinate == "unity-lh-y-up" && document.matrixLayout == "row-major" &&
                document.prefabGuid == spec.weaponPrefabGuid && document.modelGuid == spec.weaponGuid &&
                document.modelSha256 == spec.weaponModelSha256 && SamePath(document.modelFile, spec.weaponModelFile),
                "The weapon context belongs to another prefab, source model, version or coordinate convention.");
            VerifySources(document);
            Require(MatrixEqual(document.spawnedPrefabRootMatrix, spec.weaponPrefabLocalMatrix),
                "The weapon context has a different spawned prefab scale.");
            Require(document.meshes != null && document.meshes.Length > 0 && document.meshes.All(mesh =>
                mesh != null && mesh.vertexCount > 0 && mesh.vertices != null && mesh.vertices.Length == mesh.vertexCount &&
                mesh.meshGuid == document.modelGuid && mesh.meshAssetPath == document.modelAssetPath &&
                mesh.modelRendererPaths != null && mesh.modelRendererPaths.Length > 0 &&
                mesh.renderedVertices != null && mesh.renderedVertices.Length == mesh.vertexCount &&
                mesh.vertices.All(Finite) && mesh.renderedVertices.All(Finite) &&
                mesh.triangles != null && mesh.triangles.Length > 0 && mesh.triangles.Length % 3 == 0 &&
                mesh.triangles.All(index => index >= 0 && index < mesh.vertexCount) && MatrixValid(mesh.rendererLocalToPrefab)),
                "The captured weapon context has incomplete mesh geometry.");
        }

        static Document Capture(GameObject weapon)
        {
            string prefabPath = RequirePrefab(weapon);
            string modelPath = ResolveSourceModel(weapon);
            var importer = AssetImporter.GetAtPath(modelPath) as ModelImporter;
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(modelPath);
            Require(importer != null && model != null && !EditorUtility.IsDirty(importer) && !EditorUtility.IsDirty(model),
                "Save the actual weapon's model import settings before preparing its context.");
            var document = new Document
            {
                prefabGuid = AssetDatabase.AssetPathToGUID(prefabPath), prefabAssetPath = prefabPath,
                prefabFileSha256 = FileHash(prefabPath), prefabMetaSha256 = FileHash(prefabPath + ".meta"), prefabDependencyHash = DependencyHash(prefabPath),
                modelGuid = AssetDatabase.AssetPathToGUID(modelPath), modelAssetPath = modelPath, modelFile = Path.GetFullPath(modelPath),
                modelSha256 = FileHash(modelPath), modelMetaSha256 = FileHash(modelPath + ".meta"), modelDependencyHash = DependencyHash(modelPath),
                importer = ReadSettings(importer), modelRoot = TransformOf(model.transform), prefabRoot = TransformOf(weapon.transform),
                spawnedPrefabRootMatrix = SpawnMatrix(weapon)
            };
            ReadDependencies(prefabPath, modelPath, out document.sourceDependencies, out document.excludedDependencies);
            var rootScale = weapon.transform.localScale;
            Require(MatrixValid(document.spawnedPrefabRootMatrix) && Mathf.Abs(rootScale.x * rootScale.y * rootScale.z) > 1e-12f,
                "The actual weapon prefab has an invalid or singular root scale.");
            var meshes = new List<RendererRecord>();
            var nonMesh = new List<string>();
            Bounds prefabBounds = default, spawnedBounds = default; bool anyBounds = false;
            Matrix4x4 spawn = Matrix4x4.TRS(Vector3.zero, Quaternion.identity, weapon.transform.localScale);
            foreach (var renderer in weapon.GetComponentsInChildren<Renderer>(true).OrderBy(value => AnimationUtility.CalculateTransformPath(value.transform, weapon.transform), StringComparer.Ordinal))
            {
                Mesh mesh = renderer is SkinnedMeshRenderer skin ? skin.sharedMesh :
                    renderer is MeshRenderer ? renderer.GetComponent<MeshFilter>()?.sharedMesh : null;
                string rendererPath = AnimationUtility.CalculateTransformPath(renderer.transform, weapon.transform);
                if (!(renderer is MeshRenderer) && !(renderer is SkinnedMeshRenderer))
                { nonMesh.Add(rendererPath + "|" + renderer.GetType().Name); continue; }
                Require(mesh != null && EditorUtility.IsPersistent(mesh) && !EditorUtility.IsDirty(mesh),
                    "The actual weapon has an unsaved or missing renderer mesh: " + rendererPath);
                Require(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(mesh, out string meshGuid, out long meshId) &&
                    !string.IsNullOrEmpty(meshGuid) && meshId != 0 && File.Exists(AssetDatabase.GetAssetPath(mesh)),
                    "The actual weapon mesh has no persistent source identity: " + rendererPath);
                string meshPath = AssetDatabase.GetAssetPath(mesh);
                string[] modelRendererPaths = ModelRendererPaths(model, mesh);
                Require(meshGuid == document.modelGuid && meshPath == modelPath && modelRendererPaths.Length > 0,
                    "Every actual weapon mesh must belong to the exact source FBX and one of its imported renderers: " + rendererPath);
                Matrix4x4 relative = weapon.transform.worldToLocalMatrix * renderer.transform.localToWorldMatrix;
                Mesh readable = null, baked = null;
                try
                {
                    var source = mesh;
                    if (!mesh.isReadable) { readable = CharacterAnimationReadableMesh.Copy(mesh); source = readable; }
                    Vector3[] vertices = source.vertices;
                    Require(vertices.Length == mesh.vertexCount && vertices.Length > 0 && vertices.All(Finite), "The actual weapon mesh has incomplete or non-finite vertices.");
                    Vector3[] rendered = vertices;
                    string kind = "static-mesh";
                    if (renderer is SkinnedMeshRenderer skinned)
                    {
                        Require(skinned.bones.All(bone => bone != null && bone.IsChildOf(weapon.transform)) &&
                            (skinned.rootBone == null || skinned.rootBone.IsChildOf(weapon.transform)),
                            "A weapon skin depends on a bone outside its actual prefab.");
                        baked = new Mesh { name = "Weapon Context Geometry", hideFlags = HideFlags.HideAndDontSave };
                        skinned.BakeMesh(baked, false); rendered = baked.vertices; kind = "skinned-authored-prefab-pose";
                        Require(rendered.Length == vertices.Length && rendered.All(Finite), "The weapon's authored skin geometry could not be captured.");
                    }
                    var triangles = new List<int>(); var submeshes = new List<SubmeshRecord>();
                    for (int submesh = 0; submesh < source.subMeshCount; submesh++)
                    {
                        var topology = source.GetTopology(submesh); int start = triangles.Count;
                        int[] indices = source.GetIndices(submesh, true); // Apply baseVertex exactly once.
                        Require(indices.All(index => index >= 0 && index < vertices.Length), "A weapon submesh has out-of-range indices.");
                        if (topology == MeshTopology.Triangles)
                        { Require(indices.Length % 3 == 0, "A weapon submesh has incomplete triangles."); triangles.AddRange(indices); }
                        else if (topology == MeshTopology.Quads)
                        {
                            Require(indices.Length % 4 == 0, "A weapon submesh has incomplete quads.");
                            for (int i = 0; i < indices.Length; i += 4)
                                triangles.AddRange(new[] { indices[i], indices[i + 1], indices[i + 2], indices[i], indices[i + 2], indices[i + 3] });
                        }
                        else throw new InvalidOperationException("The weapon context needs triangle or quad surface geometry: " + rendererPath);
                        submeshes.Add(new SubmeshRecord { submesh = submesh, triangleStart = start / 3, triangleCount = (triangles.Count - start) / 3, sourceTopology = topology.ToString() });
                    }
                    Require(triangles.Count > 0, "The actual weapon mesh has no surface triangles.");
                    bool active = ActiveWithin(renderer.transform, weapon.transform);
                    meshes.Add(new RendererRecord { rendererPath = rendererPath, rendererType = renderer.GetType().Name, meshName = mesh.name,
                        meshGuid = meshGuid, meshLocalId = meshId, meshAssetPath = meshPath, vertexCount = mesh.vertexCount,
                        enabled = renderer.enabled, activeInPrefab = active, meshLocalBounds = BoundsOf(mesh.bounds),
                        modelRendererPaths = modelRendererPaths,
                        rendererLocalToPrefab = CharacterAnimationTransfer.Rows(relative), vertices = vertices, renderedVertices = rendered,
                        triangles = triangles.ToArray(), submeshes = submeshes.ToArray(), geometryKind = kind });
                    if (renderer.enabled && active)
                        foreach (int index in triangles.Distinct())
                        {
                            Vector3 point = relative.MultiplyPoint3x4(rendered[index]); Vector3 spawned = spawn.MultiplyPoint3x4(point);
                            Require(Finite(point) && Finite(spawned), "The actual weapon has non-finite rendered geometry.");
                            if (!anyBounds) { prefabBounds = new Bounds(point, Vector3.zero); spawnedBounds = new Bounds(spawned, Vector3.zero); anyBounds = true; }
                            else { prefabBounds.Encapsulate(point); spawnedBounds.Encapsulate(spawned); }
                        }
                }
                finally { if (readable != null) Object.DestroyImmediate(readable); if (baked != null) Object.DestroyImmediate(baked); }
            }
            Require(meshes.Count > 0 && anyBounds, "The actual weapon prefab has no enabled authored surface geometry.");
            Require(meshes.Select(mesh => mesh.rendererPath + "|" + mesh.rendererType).Distinct(StringComparer.Ordinal).Count() == meshes.Count,
                "The actual weapon has ambiguous duplicate renderer paths.");
            document.meshes = meshes.ToArray(); document.nonMeshRenderers = nonMesh.ToArray();
            document.prefabBounds = BoundsOf(prefabBounds); document.spawnedBounds = BoundsOf(spawnedBounds);
            VerifySources(document);
            return document;
        }

        static void VerifySources(Document document)
        {
            Require(document != null && !string.IsNullOrEmpty(document.prefabAssetPath) && !string.IsNullOrEmpty(document.modelAssetPath) &&
                AssetDatabase.AssetPathToGUID(document.prefabAssetPath) == document.prefabGuid &&
                AssetDatabase.AssetPathToGUID(document.modelAssetPath) == document.modelGuid &&
                SamePath(document.modelFile, document.modelAssetPath) && FileHash(document.prefabAssetPath) == document.prefabFileSha256 &&
                FileHash(document.prefabAssetPath + ".meta") == document.prefabMetaSha256 &&
                FileHash(document.modelFile) == document.modelSha256 && FileHash(document.modelFile + ".meta") == document.modelMetaSha256 &&
                DependencyHash(document.prefabAssetPath) == document.prefabDependencyHash &&
                DependencyHash(document.modelAssetPath) == document.modelDependencyHash,
                "The actual weapon prefab, model or import dependencies changed. Capture its current context again.");
            Require(document.sourceDependencies != null && document.sourceDependencies.Length > 0 && document.excludedDependencies != null,
                "The weapon context has no complete source dependency inventory.");
            ReadDependencies(document.prefabAssetPath, document.modelAssetPath, out var sourceDependencies, out var excludedDependencies);
            Require(CharacterAnimationCompositionJson.ToJson(sourceDependencies) == CharacterAnimationCompositionJson.ToJson(document.sourceDependencies) &&
                CharacterAnimationCompositionJson.ToJson(excludedDependencies) == CharacterAnimationCompositionJson.ToJson(document.excludedDependencies),
                "The weapon source dependency set, files or metadata changed, or its captured inventory is incomplete.");
            var weapon = AssetDatabase.LoadAssetAtPath<GameObject>(document.prefabAssetPath);
            RequirePrefab(weapon);
            Require(ResolveSourceModel(weapon) == document.modelAssetPath && MatrixEqual(SpawnMatrix(weapon), document.spawnedPrefabRootMatrix),
                "The captured weapon model or spawned prefab root scale no longer matches.");
            var importer = AssetImporter.GetAtPath(document.modelAssetPath) as ModelImporter;
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(document.modelAssetPath);
            Require(importer != null && model != null && !EditorUtility.IsDirty(importer) && !EditorUtility.IsDirty(model) && document.importer != null &&
                CharacterAnimationCompositionJson.ToJson(ReadSettings(importer)) == CharacterAnimationCompositionJson.ToJson(document.importer) &&
                document.modelRoot != null && MatrixEqual(TransformOf(model.transform).matrix, document.modelRoot.matrix) &&
                document.prefabRoot != null && MatrixEqual(TransformOf(weapon.transform).matrix, document.prefabRoot.matrix),
                "The captured Unity weapon units, axis handling or model/prefab root transform changed.");
            if (document.meshes != null)
            {
                var actual = weapon.GetComponentsInChildren<Renderer>(true).Where(renderer => renderer is MeshRenderer || renderer is SkinnedMeshRenderer).ToArray();
                Require(actual.Length == document.meshes.Length, "The weapon renderer set changed after its context was captured.");
                foreach (var entry in document.meshes)
                {
                    Require(entry != null, "The weapon context has a missing renderer declaration.");
                    var matches = actual.Where(renderer => AnimationUtility.CalculateTransformPath(renderer.transform, weapon.transform) == entry.rendererPath &&
                        renderer.GetType().Name == entry.rendererType).ToArray();
                    Require(matches.Length == 1, "The weapon context has no unique actual renderer: " + entry.rendererPath);
                    var renderer = matches[0];
                    var mesh = renderer is SkinnedMeshRenderer skin ? skin.sharedMesh : renderer.GetComponent<MeshFilter>()?.sharedMesh;
                    Require(mesh != null && !EditorUtility.IsDirty(mesh), "The weapon context's actual mesh changed or is missing.");
                    Require(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(mesh, out string guid, out long localId) &&
                        guid == entry.meshGuid && localId == entry.meshLocalId && AssetDatabase.GetAssetPath(mesh) == entry.meshAssetPath &&
                        entry.meshGuid == document.modelGuid && entry.meshAssetPath == document.modelAssetPath &&
                        entry.modelRendererPaths != null && entry.modelRendererPaths.Length > 0 &&
                        ModelRendererPaths(model, mesh).SequenceEqual(entry.modelRendererPaths, StringComparer.Ordinal) &&
                        mesh.name == entry.meshName && mesh.vertexCount == entry.vertexCount && renderer.enabled == entry.enabled &&
                        ActiveWithin(renderer.transform, weapon.transform) == entry.activeInPrefab &&
                        MatrixEqual(CharacterAnimationTransfer.Rows(weapon.transform.worldToLocalMatrix * renderer.transform.localToWorldMatrix), entry.rendererLocalToPrefab),
                        "The weapon renderer geometry identity or local transform changed: " + entry.rendererPath);
                }
            }
        }
        static void ReadDependencies(string prefabPath, string modelPath, out SourceDependency[] sources, out ExcludedDependency[] excluded)
        {
            var files = new List<SourceDependency>(); var omitted = new List<ExcludedDependency>();
            // Recursive includes both roots and every indirect nested-prefab, mesh,
            // material and importer dependency. Never substitute a hand-picked subset.
            string[] paths = AssetDatabase.GetDependencies(new[] { prefabPath, modelPath }, true)
                .Distinct(StringComparer.Ordinal).OrderBy(path => path, StringComparer.Ordinal).ToArray();
            Require(paths.Contains(prefabPath, StringComparer.Ordinal) && paths.Contains(modelPath, StringComparer.Ordinal),
                "Unity did not report both actual weapon source roots in its recursive dependency set.");
            foreach (string path in paths)
            {
                Require(!string.IsNullOrWhiteSpace(path), "Unity reported an empty weapon source dependency.");
                string guid = AssetDatabase.AssetPathToGUID(path);
                string file = DependencyFile(path);
                if (file != null)
                {
                    Require(!string.IsNullOrEmpty(guid), "A weapon source dependency has no persistent GUID: " + path);
                    files.Add(new SourceDependency { assetPath = path, file = file, guid = guid,
                        fileSha256 = FileHash(file), metaSha256 = FileHash(file + ".meta") });
                    continue;
                }
                Require(!path.StartsWith("Assets/", StringComparison.Ordinal), "A weapon source dependency file is missing: " + path);
                string reason = path.StartsWith("Packages/", StringComparison.Ordinal) ? "package-no-physical-file" :
                    path == "Resources/unity_builtin_extra" || path == "Library/unity default resources" || path == "Resources/unity default resources"
                    ? "unity-builtin-no-physical-file" : null;
                Require(reason != null, "A weapon source dependency cannot be verified as a file or declared Unity builtin: " + path);
                omitted.Add(new ExcludedDependency { assetPath = path, guid = guid, reason = reason });
            }
            sources = files.ToArray(); excluded = omitted.ToArray();
        }
        static string DependencyFile(string assetPath)
        {
            if (File.Exists(assetPath)) return Path.GetFullPath(assetPath);
            if (!assetPath.StartsWith("Packages/", StringComparison.Ordinal)) return null;
            var package = UnityEditor.PackageManager.PackageInfo.FindForAssetPath(assetPath);
            if (package == null || string.IsNullOrEmpty(package.assetPath) || string.IsNullOrEmpty(package.resolvedPath) ||
                !assetPath.StartsWith(package.assetPath.TrimEnd('/') + "/", StringComparison.Ordinal)) return null;
            string root = Path.GetFullPath(package.resolvedPath);
            string file = Path.GetFullPath(Path.Combine(root, assetPath.Substring(package.assetPath.TrimEnd('/').Length + 1)));
            Require(Within(file, root), "A weapon package dependency escaped its resolved package folder.");
            return File.Exists(file) ? file : null;
        }
        static ImportSettings ReadSettings(ModelImporter importer)
        {
            using var serialized = new SerializedObject(importer);
            serialized.Update();
            var verified = new List<string>(); var apiOnly = new List<string>();
            VerifyFloat(serialized, importer.globalScale, "globalScale", verified, apiOnly, "m_GlobalScale", "globalScale");
            // File-unit labels/factors are separate inspector fields. Capture their actual
            // values without assuming that an internal display field equals public fileScale.
            apiOnly.Add("fileScale");
            VerifyBool(serialized, importer.useFileScale, "useFileScale", verified, apiOnly, "m_UseFileScale", "useFileScale");
            VerifyBool(serialized, importer.bakeAxisConversion, "bakeAxisConversion", verified, apiOnly, "bakeAxisConversion", "m_BakeAxisConversion");
            var raw = new List<SerializedField>();
            foreach (string path in new[] { "m_GlobalScale", "globalScale", "m_UseFileScale", "useFileScale", "m_FileScale", "m_FileScaleFactor", "m_FileScaleUnit", "fileScale", "bakeAxisConversion", "m_BakeAxisConversion" })
            {
                var property = serialized.FindProperty(path);
                if (property == null) continue;
                var field = new SerializedField { path = property.propertyPath, type = property.propertyType.ToString() };
                if (property.propertyType == SerializedPropertyType.Float) { field.floatValue = property.floatValue; Require(Finite(field.floatValue), "The actual weapon has a non-finite importer field."); }
                else if (property.propertyType == SerializedPropertyType.Boolean) field.boolValue = property.boolValue;
                else if (property.propertyType == SerializedPropertyType.String) field.stringValue = property.stringValue;
                else if (property.propertyType == SerializedPropertyType.Integer || property.propertyType == SerializedPropertyType.Enum) field.intValue = property.intValue;
                else throw new InvalidOperationException("The actual weapon importer field has an unsupported representation: " + path);
                raw.Add(field);
            }
            Require(Finite(importer.globalScale) && importer.globalScale > 0 && Finite(importer.fileScale) && importer.fileScale > 0,
                "The actual weapon importer has invalid unit scale settings.");
            return new ImportSettings { globalScale = importer.globalScale, fileScale = importer.fileScale,
                useFileScale = importer.useFileScale, bakeAxisConversion = importer.bakeAxisConversion,
                verifiedSerializedFields = verified.ToArray(), publicApiOnlyFields = apiOnly.ToArray(), serializedFields = raw.ToArray() };
        }
        static void VerifyFloat(SerializedObject source, float actual, string name, List<string> verified, List<string> apiOnly, params string[] paths)
        {
            var property = paths.Select(source.FindProperty).FirstOrDefault(value => value != null);
            if (property == null) { apiOnly.Add(name); return; }
            Require(property.propertyType == SerializedPropertyType.Float && property.floatValue == actual, "The weapon import field differs from its public API: " + name);
            verified.Add(property.propertyPath);
        }
        static void VerifyBool(SerializedObject source, bool actual, string name, List<string> verified, List<string> apiOnly, params string[] paths)
        {
            var property = paths.Select(source.FindProperty).FirstOrDefault(value => value != null);
            if (property == null) { apiOnly.Add(name); return; }
            Require(property.propertyType == SerializedPropertyType.Boolean && property.boolValue == actual, "The weapon import field differs from its public API: " + name);
            verified.Add(property.propertyPath);
        }
        static string RequirePrefab(GameObject weapon)
        {
            string path = weapon != null ? AssetDatabase.GetAssetPath(weapon) : "";
            Require(weapon != null && EditorUtility.IsPersistent(weapon) && !EditorUtility.IsDirty(weapon) &&
                PrefabUtility.IsPartOfPrefabAsset(weapon) && string.Equals(Path.GetExtension(path), ".prefab", StringComparison.OrdinalIgnoreCase) &&
                AssetDatabase.LoadAssetAtPath<GameObject>(path) == weapon && File.Exists(path),
                "Choose the saved actual weapon prefab root, not a scene instance or placeholder model.");
            Require(weapon.GetComponentsInChildren<Component>(true).All(component => component != null && !EditorUtility.IsDirty(component)),
                "Save the actual weapon prefab's authored components before capturing its context.");
            return path;
        }
        // Dependencies also include projectiles, sounds and effects. The authoring model is owned by the actual body renderers.
        public static string ResolveSourceModel(GameObject weapon)
        {
            RequirePrefab(weapon);
            var renderers = weapon.GetComponentsInChildren<Renderer>(true)
                .Where(renderer => renderer is MeshRenderer || renderer is SkinnedMeshRenderer).ToArray();
            Require(renderers.Length > 0, "The actual weapon prefab has no persistent body renderer geometry.");
            var meshes = renderers.Select(renderer => renderer is SkinnedMeshRenderer skin ? skin.sharedMesh :
                renderer.GetComponent<MeshFilter>()?.sharedMesh).ToArray();
            Require(meshes.All(mesh => mesh != null && EditorUtility.IsPersistent(mesh) && !EditorUtility.IsDirty(mesh)),
                "Save all actual weapon body renderer meshes before resolving their source model.");
            foreach (var mesh in meshes)
            {
                string path = AssetDatabase.GetAssetPath(mesh);
                Require(string.Equals(Path.GetExtension(path), ".fbx", StringComparison.OrdinalIgnoreCase) && File.Exists(path) &&
                    AssetDatabase.TryGetGUIDAndLocalFileIdentifier(mesh, out string guid, out long localId) && localId != 0 &&
                    !string.IsNullOrEmpty(guid) && guid == AssetDatabase.AssetPathToGUID(path),
                    "Every actual weapon body mesh needs its exact persistent FBX source identity.");
            }
            string[] models = meshes.Select(AssetDatabase.GetAssetPath).Distinct(StringComparer.Ordinal).ToArray();
            Require(models.Length == 1, "The actual weapon body meshes belong to more than one source FBX; their geometry is ambiguous.");
            string modelPath = models[0];
            var importer = AssetImporter.GetAtPath(modelPath) as ModelImporter;
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(modelPath);
            Require(importer != null && model != null && !EditorUtility.IsDirty(importer) && !EditorUtility.IsDirty(model) &&
                meshes.All(mesh => ModelRendererPaths(model, mesh).Length > 0),
                "Every actual weapon body mesh must be the exact sharedMesh of an imported source-model renderer.");
            return modelPath;
        }
        static TransformRecord TransformOf(Transform transform) => new TransformRecord { position = transform.localPosition,
            rotation = transform.localRotation, scale = transform.localScale,
            matrix = CharacterAnimationTransfer.Rows(Matrix4x4.TRS(transform.localPosition, transform.localRotation, transform.localScale)) };
        static string[] ModelRendererPaths(GameObject model, Mesh mesh) => model.GetComponentsInChildren<Renderer>(true).Where(renderer =>
            (renderer is SkinnedMeshRenderer skin ? skin.sharedMesh : renderer is MeshRenderer ? renderer.GetComponent<MeshFilter>()?.sharedMesh : null) == mesh)
            .Select(renderer => AnimationUtility.CalculateTransformPath(renderer.transform, model.transform)).Distinct(StringComparer.Ordinal).OrderBy(path => path, StringComparer.Ordinal).ToArray();
        static float[] SpawnMatrix(GameObject weapon) => CharacterAnimationTransfer.Rows(Matrix4x4.TRS(Vector3.zero, Quaternion.identity, weapon.transform.localScale));
        static BoundsRecord BoundsOf(Bounds bounds) => new BoundsRecord { min = bounds.min, max = bounds.max };
        static bool ActiveWithin(Transform transform, Transform root)
        { for (var current = transform; current != null; current = current.parent) { if (!current.gameObject.activeSelf) return false; if (current == root) return true; } return false; }
        static bool MatrixEqual(float[] left, float[] right) => left != null && right != null && left.Length == 16 && right.Length == 16 &&
            left.All(Finite) && right.All(Finite) && left.Zip(right, (a, b) => Mathf.Abs(a - b) <= .00002f).All(value => value);
        static bool MatrixValid(float[] matrix) => matrix != null && matrix.Length == 16 && matrix.All(Finite);
        static bool Finite(Vector3 value) => Finite(value.x) && Finite(value.y) && Finite(value.z);
        static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
        static bool SamePath(string left, string right) => !string.IsNullOrWhiteSpace(left) && !string.IsNullOrWhiteSpace(right) &&
            Path.GetFullPath(left).Equals(Path.GetFullPath(right), StringComparison.OrdinalIgnoreCase);
        static bool Within(string candidate, string root) => candidate.Equals(root, StringComparison.OrdinalIgnoreCase) ||
            (candidate.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar).StartsWith(
                root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
        static string DependencyHash(string path) => AssetDatabase.GetAssetDependencyHash(path).ToString();
        static string FileHash(string path) { Require(File.Exists(path), "A weapon context source file is missing: " + path); using var stream = File.OpenRead(path); using var sha = SHA256.Create(); return Hex(sha.ComputeHash(stream)); }
        static string Hash(byte[] bytes) { using var sha = SHA256.Create(); return Hex(sha.ComputeHash(bytes)); }
        static string Hex(byte[] bytes) => string.Concat(bytes.Select(value => value.ToString("x2")));
        static void RequireIdle() => Require(!EditorApplication.isPlayingOrWillChangePlaymode && !EditorApplication.isCompiling && !EditorApplication.isUpdating,
            "Capture weapon context from an idle Editor outside Play Mode.");
        static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
    }
}
