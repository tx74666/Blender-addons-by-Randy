using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using UnityEditor;
using UnityEditor.AssetImporters;
using UnityEngine;
using Object = UnityEngine.Object;

namespace CharacterDesigner.Unity.Editor
{
    /// <summary>Explicitly imports one bones-only FBX as a new Humanoid clip; never edits a controller.</summary>
    public static class CharacterAnimationReturn
    {
        const string Ownership = "CharacterDesigner.AnimationReturn/1";
        const float PositionTolerance = 0.001f;
        const float RotationTolerance = 0.2f;
        static bool importing;
        internal static Action BeforeClipSaveForTests;

        [Serializable]
        public sealed class Result
        {
            public string schema = Ownership;
            public string sourceFile, sourceSha256, targetPrefab, targetGuid, targetDependencyHash;
            public string avatarPath, avatarGuid, importedFbx, clipPath, clipName, importedUtc;
            public string metadataFile, metadataSha256, sourceAction, origin, sourcePackage, sourcePackageSha256;
            public string channels = "Humanoid bone motion and root motion only; no blend shapes, materials, events, constraints or controller edits.";
            public bool loopTime;
            public float frameRate, duration;
            public int verifiedHumanBones;
            public string[] importWarnings = Array.Empty<string>();
            [NonSerialized] public AnimationClip Clip;
        }

        [Serializable]
        sealed class ExportMetadata
        {
            public string filename, action, origin, source_package, source_package_sha256, fbx_sha256, channels;
            public float duration, fps, fps_base, effective_sample_rate;
            public bool loop;
            public bool has_reference_frame;
            public int reference_frame, playable_first_frame, playable_last_frame;
        }

        /// <summary>The output folder must already exist in Assets. Every call creates unique output paths.</summary>
        public static Result Import(string sourceFbx, GameObject targetPrefab, string clipName, bool loopTime, string outputFolder)
        {
            RequireIdle();
            Require(!importing, "An animation return is already being imported.");
            Require(targetPrefab != null && EditorUtility.IsPersistent(targetPrefab) &&
                    PrefabUtility.IsPartOfPrefabAsset(targetPrefab), "Choose a character prefab asset.");
            Animator[] animators = targetPrefab.GetComponentsInChildren<Animator>(true)
                .Where(animator => animator.avatar != null && animator.avatar.isValid && animator.avatar.isHuman).ToArray();
            Require(animators.Length == 1 && animators[0].transform == targetPrefab.transform,
                "The target needs one valid Humanoid Animator on its root.");
            Avatar avatar = animators[0].avatar;
            Require(EditorUtility.IsPersistent(avatar) && !EditorUtility.IsDirty(avatar) && !EditorUtility.IsDirty(targetPrefab),
                "Save the target prefab and its current Humanoid Avatar before importing an animation.");
            Require(!string.IsNullOrWhiteSpace(sourceFbx), "Choose the Blender animation FBX.");
            sourceFbx = Path.GetFullPath(sourceFbx);
            Require(File.Exists(sourceFbx) && string.Equals(Path.GetExtension(sourceFbx), ".fbx", StringComparison.OrdinalIgnoreCase),
                "Choose an existing FBX containing one exported Action.");
            Require(!string.IsNullOrWhiteSpace(clipName) && clipName == clipName.Trim() &&
                    clipName.IndexOfAny(Path.GetInvalidFileNameChars()) < 0 && !clipName.EndsWith(".", StringComparison.Ordinal) &&
                    clipName != "." && clipName != "..", "Enter a valid new animation name.");
            outputFolder = (outputFolder ?? "").Replace('\\', '/').TrimEnd('/');
            Require((outputFolder == "Assets" || outputFolder.StartsWith("Assets/", StringComparison.Ordinal)) &&
                    AssetDatabase.IsValidFolder(outputFolder), "Choose an existing output folder inside this project's Assets.");

            string fbxPath = AssetDatabase.GenerateUniqueAssetPath(outputFolder + "/" + clipName + ".fbx");
            string clipPath = AssetDatabase.GenerateUniqueAssetPath(outputFolder + "/" + clipName + ".anim");
            Require(!File.Exists(fbxPath) && !File.Exists(clipPath) && !File.Exists(fbxPath + ".meta") && !File.Exists(clipPath + ".meta"),
                "An unimported output already exists. Wait for Unity to finish importing and retry.");
            var result = new Result
            {
                sourceFile = sourceFbx,
                sourceSha256 = FileHash(sourceFbx),
                targetPrefab = AssetDatabase.GetAssetPath(targetPrefab),
                targetGuid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(targetPrefab)),
                targetDependencyHash = AssetDatabase.GetAssetDependencyHash(AssetDatabase.GetAssetPath(targetPrefab)).ToString(),
                avatarPath = AssetDatabase.GetAssetPath(avatar),
                avatarGuid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(avatar)),
                importedFbx = fbxPath,
                clipPath = clipPath,
                clipName = clipName,
                loopTime = loopTime,
                importedUtc = DateTime.UtcNow.ToString("O")
            };
            ExportMetadata metadata = ReadMetadata(sourceFbx, result);
            AnimationClip extracted = null;
            bool createdFbx = false, createdClip = false;
            importing = true;
            try
            {
                Require(!File.Exists(fbxPath + ".meta"), "The new FBX destination was claimed by another import. Retry.");
                using (var source = File.OpenRead(sourceFbx))
                using (var destination = new FileStream(fbxPath, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    // Ownership starts only after exclusive creation succeeds. A competing
                    // file must never be removed by this operation's failure cleanup.
                    createdFbx = true;
                    source.CopyTo(destination);
                }
                AssetDatabase.ImportAsset(fbxPath, ImportAssetOptions.ForceSynchronousImport);
                var importer = AssetImporter.GetAtPath(fbxPath) as ModelImporter;
                Require(importer != null, "Unity could not import the animation FBX.");
                importer.animationType = ModelImporterAnimationType.Generic;
                importer.avatarSetup = ModelImporterAvatarSetup.NoAvatar;
                // Unity can expose the first animated pose as the imported hierarchy.
                // Verify the static reference skeleton before enabling clip import.
                importer.importAnimation = false;
                importer.importBlendShapes = false;
                importer.materialImportMode = ModelImporterMaterialImportMode.None;
                importer.animationCompression = ModelImporterAnimationCompression.Off;
                importer.resampleCurves = true;
                importer.optimizeGameObjects = false;
                importer.preserveHierarchy = true;
                importer.SaveAndReimport();
                GameObject imported = AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath);
                Require(imported != null && imported.GetComponentsInChildren<Renderer>(true).Length == 0 &&
                        imported.GetComponentsInChildren<Camera>(true).Length == 0 && imported.GetComponentsInChildren<Light>(true).Length == 0,
                    "Return export must contain only the character armature and one Action, without meshes, cameras or lights.");
                result.verifiedHumanBones = VerifyRestSkeleton(targetPrefab, imported, avatar);
                importer.importAnimation = true;
                importer.SaveAndReimport();
                ModelImporterClipAnimation[] takes = importer.defaultClipAnimations;
                Require(takes != null && takes.Length == 1 && takes[0].lastFrame > takes[0].firstFrame,
                    "Return export must contain exactly one non-empty Action. Disable All Actions and NLA export.");
                AnimationClip genericClip = SingleClip(fbxPath);
                Require(genericClip != null && genericClip.frameRate > 0f && Finite(genericClip.frameRate),
                    "The exported FBX has no valid animation sample rate.");
                result.frameRate = genericClip.frameRate;
                if (metadata != null)
                {
                    Require(metadata.has_reference_frame || (metadata.reference_frame == 0 &&
                            metadata.playable_first_frame == 0 && metadata.playable_last_frame == 0),
                        "The export metadata declares a playable reference range without its reference-frame flag.");
                    if (metadata.fps > 0f)
                    {
                        float authoredRate = metadata.fps / (metadata.fps_base > 0f ? metadata.fps_base : 1f);
                        Require(Finite(authoredRate) && Mathf.Abs(result.frameRate - authoredRate) < 0.01f,
                            "The FBX frame rate does not match its Blender export metadata.");
                    }
                    if (metadata.has_reference_frame)
                    {
                        Require(metadata.reference_frame == 0 && metadata.playable_first_frame == 1 &&
                                metadata.playable_last_frame > metadata.playable_first_frame &&
                                Finite(metadata.duration) && metadata.duration > 0f &&
                                Mathf.Abs(takes[0].firstFrame - metadata.reference_frame) < 0.001f &&
                                Mathf.Abs(takes[0].lastFrame - metadata.playable_last_frame) < 0.001f,
                            "The FBX reference and playable frame ranges do not match their export metadata.");
                        float playableDuration = (metadata.playable_last_frame - metadata.playable_first_frame) / result.frameRate;
                        Require(Mathf.Abs(playableDuration - metadata.duration) < 0.0002f &&
                                Mathf.Abs(genericClip.length - metadata.duration - 1f / result.frameRate) < 0.0002f,
                            "The FBX must contain exactly one reference frame before the authored motion.");
                    }
                    else if (metadata.duration > 0f)
                        Require(Finite(metadata.duration) && Mathf.Abs(genericClip.length - metadata.duration) <= 1f / result.frameRate + 0.001f,
                            "The FBX duration does not match its Blender export metadata.");
                }
                ValidateSourceChannels(genericClip);

                ModelImporterClipAnimation take = takes[0];
                if (metadata != null && metadata.has_reference_frame)
                {
                    // Keep the FBX's first sample for Humanoid reference construction,
                    // but exclude it from the playable motion and its loop seam.
                    take.firstFrame = metadata.playable_first_frame;
                    take.lastFrame = metadata.playable_last_frame;
                }
                take.name = clipName;
                take.loopTime = loopTime;
                take.loopPose = false;
                take.cycleOffset = 0f;
                take.mirror = false;
                take.hasAdditiveReferencePose = false;
                take.events = Array.Empty<AnimationEvent>();
                // Preserve the authored motion as root motion, without another matrix conversion or a second bake.
                take.lockRootRotation = false;
                take.lockRootHeightY = false;
                take.lockRootPositionXZ = false;
                take.keepOriginalOrientation = true;
                take.keepOriginalPositionY = true;
                take.keepOriginalPositionXZ = true;
                take.heightFromFeet = false;
                take.heightOffset = 0f;
                take.rotationOffset = 0f;
                importer.animationType = ModelImporterAnimationType.Human;
                importer.avatarSetup = ModelImporterAvatarSetup.CopyFromOther;
                importer.sourceAvatar = avatar;
                importer.clipAnimations = new[] { take };
                importer.userData = JsonUtility.ToJson(result);
                importer.SaveAndReimport();

                importer = AssetImporter.GetAtPath(fbxPath) as ModelImporter;
                // An animation-only CopyFromOther import can have no Animator or
                // Avatar sub-asset. Validate its retained source and real clip.
                Require(importer != null && importer.animationType == ModelImporterAnimationType.Human &&
                        importer.avatarSetup == ModelImporterAvatarSetup.CopyFromOther &&
                        importer.sourceAvatar == avatar && avatar.isValid && avatar.isHuman,
                    "The returned FBX did not retain this character's current Humanoid Avatar.");
                ImportLog importLog = AssetImporter.GetImportLog(fbxPath);
                if (importLog != null && importLog.logEntries != null)
                {
                    string[] errors = importLog.logEntries.Where(entry => (entry.flags & ImportLogFlags.Error) != 0)
                        .Select(entry => entry.message).ToArray();
                    Require(errors.Length == 0, "The returned FBX has import errors: " + string.Join("; ", errors));
                    result.importWarnings = importLog.logEntries.Where(entry => (entry.flags & ImportLogFlags.Warning) != 0)
                        .Select(entry => entry.message).ToArray();
                }
                AnimationClip motion = SingleClip(fbxPath);
                Require(motion != null && motion.humanMotion && !motion.legacy && motion.length > 0f && Finite(motion.length),
                    "Unity did not produce a valid Humanoid animation from the returned Action.");
                Require(Mathf.Abs(motion.frameRate - result.frameRate) < 0.001f,
                    "Humanoid import changed the source FBX sample rate.");
                if (metadata != null && metadata.has_reference_frame)
                    Require(Mathf.Abs(motion.length - metadata.duration) < 0.0002f,
                        "Humanoid import did not preserve the authored duration after removing the reference frame.");
                Require(result.targetDependencyHash == AssetDatabase.GetAssetDependencyHash(result.targetPrefab).ToString(),
                    "The target character changed during import. Re-export against its current Avatar.");
                Require(result.sourceSha256 == FileHash(sourceFbx) && result.sourceSha256 == FileHash(fbxPath),
                    "The source FBX changed during import. Export a complete Action and retry.");
                Require(metadata == null || result.metadataSha256 == FileHash(result.metadataFile),
                    "The export metadata changed during import. Retry after exporting finishes.");
                extracted = Object.Instantiate(motion);
                extracted.name = clipName;
                extracted.hideFlags = HideFlags.None;
                extracted.EnsureQuaternionContinuity();
                AnimationUtility.SetAnimationEvents(extracted, Array.Empty<AnimationEvent>());
                BeforeClipSaveForTests?.Invoke();
                Require(!File.Exists(clipPath) && !File.Exists(clipPath + ".meta") &&
                        AssetDatabase.LoadMainAssetAtPath(clipPath) == null,
                    "The new animation destination was claimed during import. Retry to create a unique asset.");
                AssetDatabase.CreateAsset(extracted, clipPath);
                Require(EditorUtility.IsPersistent(extracted) && AssetDatabase.GetAssetPath(extracted) == clipPath,
                    "Unity did not create the standalone animation asset. Any uncertain output has been retained for inspection.");
                createdClip = true;
                AssetDatabase.SaveAssetIfDirty(extracted);
                result.duration = extracted.length;
                result.Clip = extracted;
                extracted = null;
                importer.userData = JsonUtility.ToJson(result);
                importer.SaveAndReimport();
                Require(AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath) != null,
                    "Unity did not retain the new standalone animation asset.");
                return result;
            }
            catch (Exception failure)
            {
                var errors = new List<Exception> { failure };
                if (createdClip) TrashOwned(clipPath, errors);
                if (createdFbx) TrashOwned(fbxPath, errors);
                if (errors.Count > 1) throw new AggregateException("Animation return failed and some newly created output could not be removed.", errors);
                throw;
            }
            finally
            {
                if (extracted != null && !EditorUtility.IsPersistent(extracted)) Object.DestroyImmediate(extracted);
                importing = false;
            }
        }

        static int VerifyRestSkeleton(GameObject target, GameObject imported, Avatar avatar)
        {
            Transform[] targetBones = target.GetComponentsInChildren<Transform>(true);
            Transform[] importedBones = imported.GetComponentsInChildren<Transform>(true);
            Matrix4x4 targetInverse = target.transform.worldToLocalMatrix;
            Matrix4x4 importedInverse = imported.transform.worldToLocalMatrix;
            HumanBone[] human = avatar.humanDescription.human;
            Require(human != null && human.Length > 0, "The current Avatar has no Humanoid bone mapping.");
            int count = 0;
            foreach (HumanBone mapping in human)
            {
                if (string.IsNullOrEmpty(mapping.boneName)) continue;
                Transform[] original = targetBones.Where(bone => bone.name == mapping.boneName).ToArray();
                Transform[] returned = importedBones.Where(bone => bone.name == mapping.boneName).ToArray();
                Require(original.Length == 1 && returned.Length == 1, "Missing or ambiguous returned bone: " + mapping.boneName);
                Require(AnimationUtility.CalculateTransformPath(original[0], target.transform) ==
                        AnimationUtility.CalculateTransformPath(returned[0], imported.transform),
                    "The returned bone hierarchy differs from the target: " + mapping.boneName);
                Matrix4x4 a = targetInverse * original[0].localToWorldMatrix;
                Matrix4x4 b = importedInverse * returned[0].localToWorldMatrix;
                Require(Vector3.Distance(a.GetColumn(3), b.GetColumn(3)) <= PositionTolerance &&
                        Quaternion.Angle(a.rotation, b.rotation) <= RotationTolerance &&
                        Vector3.Distance(a.lossyScale, b.lossyScale) <= 0.001f,
                    "The returned rest pose differs from the current character at " + mapping.boneName + ". Export the matching unmodified skeleton.");
                count++;
            }
            Require(count >= 15, "Too few matching Humanoid bones to validate an animation return.");
            return count;
        }

        static AnimationClip SingleClip(string path)
        {
            AnimationClip[] clips = AssetDatabase.LoadAllAssetsAtPath(path).OfType<AnimationClip>()
                .Where(clip => !clip.name.StartsWith("__preview__", StringComparison.Ordinal)).ToArray();
            Require(clips.Length == 1, "The animation FBX must import exactly one clip.");
            return clips[0];
        }

        static ExportMetadata ReadMetadata(string source, Result result)
        {
            string path = Path.ChangeExtension(source, ".animation.json");
            if (!File.Exists(path)) return null;
            Require(new FileInfo(path).Length <= 1024 * 1024, "The animation export metadata is unexpectedly large.");
            ExportMetadata metadata = JsonUtility.FromJson<ExportMetadata>(File.ReadAllText(path));
            Require(metadata != null && metadata.channels == "skeletal-only" &&
                    string.Equals(metadata.fbx_sha256, result.sourceSha256, StringComparison.OrdinalIgnoreCase),
                "The adjacent animation metadata does not describe this complete bones-only FBX.");
            Require(string.IsNullOrEmpty(metadata.filename) || metadata.filename == Path.GetFileName(source),
                "The adjacent animation metadata names another FBX.");
            result.metadataFile = path;
            result.metadataSha256 = FileHash(path);
            result.sourceAction = metadata.action;
            result.origin = metadata.origin;
            result.sourcePackage = metadata.source_package;
            result.sourcePackageSha256 = metadata.source_package_sha256;
            return metadata;
        }

        static void ValidateSourceChannels(AnimationClip clip)
        {
            Require(AnimationUtility.GetObjectReferenceCurveBindings(clip).Length == 0 && AnimationUtility.GetAnimationEvents(clip).Length == 0,
                "Object-reference curves and animation events are not supported by this bones-only return.");
            foreach (EditorCurveBinding binding in AnimationUtility.GetCurveBindings(clip))
            {
                bool transform = binding.type == typeof(Transform) &&
                    (binding.propertyName.StartsWith("m_LocalPosition.", StringComparison.Ordinal) ||
                     binding.propertyName.StartsWith("m_LocalRotation.", StringComparison.Ordinal) ||
                     binding.propertyName.StartsWith("localEulerAngles", StringComparison.Ordinal) ||
                     binding.propertyName.StartsWith("m_LocalScale.", StringComparison.Ordinal));
                bool root = binding.type == typeof(Animator) && string.IsNullOrEmpty(binding.path) &&
                    (binding.propertyName.StartsWith("RootT.", StringComparison.Ordinal) || binding.propertyName.StartsWith("RootQ.", StringComparison.Ordinal));
                Require(transform || root, "Unsupported returned animation channel: " + binding.path + "/" + binding.propertyName);
            }
        }

        static void TrashOwned(string path, List<Exception> errors)
        {
            try
            {
                if (File.Exists(path) && !AssetDatabase.MoveAssetToTrash(path))
                    throw new IOException("Could not remove newly created output: " + path);
            }
            catch (Exception error) { errors.Add(error); }
        }

        static string FileHash(string path)
        {
            using var file = File.OpenRead(path);
            using var sha = SHA256.Create();
            return string.Concat(sha.ComputeHash(file).Select(value => value.ToString("x2")));
        }

        static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);

        static void RequireIdle()
        {
            Require(!AssetDatabase.IsAssetImportWorkerProcess() && !EditorApplication.isPlayingOrWillChangePlaymode &&
                    !EditorApplication.isCompiling && !EditorApplication.isUpdating,
                "Use the main Unity Editor with Play stopped after importing and compilation finish.");
            // The companion also works in projects without Multiplayer Play Mode installed.
            Type player = AppDomain.CurrentDomain.GetAssemblies()
                .Select(assembly => assembly.GetType("Unity.Multiplayer.PlayMode.CurrentPlayer", false)).FirstOrDefault(type => type != null);
            if (player != null)
            {
                PropertyInfo main = player.GetProperty("IsMainEditor", BindingFlags.Public | BindingFlags.Static);
                Require(main != null && main.GetValue(null) is bool isMain && isMain,
                    "Return animations from the main Unity Editor, not a Multiplayer Play Mode clone.");
            }
        }

        static void Require(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message);
        }
    }
}
