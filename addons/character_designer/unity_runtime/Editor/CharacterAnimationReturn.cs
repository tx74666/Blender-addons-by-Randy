using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEditor.AssetImporters;
using UnityEngine;
using Object = UnityEngine.Object;

namespace CharacterDesigner.Unity.Editor
{
    /// <summary>Explicitly imports one bones-only FBX as a new Humanoid clip; never edits a controller.</summary>
    public static class CharacterAnimationReturn
    {
        // Full sampled composition evidence is larger than its small Link manifest.
        public const int MaxMetadataBytes = 32 * 1024 * 1024;
        const string Ownership = "CharacterDesigner.AnimationReturn/1";
        const float PositionTolerance = 0.001f;
        const float RotationTolerance = 0.2f;
        const float IsolatedComponentTolerance = 0.00002f;
        const float IsolatedEulerTolerance = 0.002f;
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
            public string editScope = "All";
            public FingerRestriction fingerRestriction;
            public UpperBodyRestriction upperBodyRestriction;
            public CharacterAnimationComposition composition;
            public CharacterAnimationCompositionExport composition_export;
            public CharacterAnimationRigging.Returned rigging;
            public string targetClipPath, targetClipGuid, targetClipSha256;
            public long targetClipLocalId;
            public bool loopTime;
            public float frameRate, duration;
            public int verifiedHumanBones;
            public string[] importWarnings = Array.Empty<string>();
            public CharacterAnimationDressMetadataReader.Declaration dressDeclaration = new CharacterAnimationDressMetadataReader.Declaration();
            [NonSerialized] public AnimationClip Clip;
            [NonSerialized] public AnimationClip TargetClip;
        }

        [Serializable]
        sealed class ExportMetadata
        {
            public string filename, action, origin, source_package, source_package_sha256, fbx_sha256, channels;
            public int actionSlot = -1;
            public float duration, fps, fps_base, effective_sample_rate;
            public bool loop;
            public bool has_reference_frame;
            public int reference_frame, playable_first_frame, playable_last_frame;
            public CharacterAnimationComposition composition;
            public CharacterAnimationCompositionExport composition_export;
            public CharacterAnimationRigging.Returned rigging;
        }

        /// <summary>The output folder must already exist in Assets. Every call creates unique output paths.</summary>
        public static Result Import(string sourceFbx, GameObject targetPrefab, string clipName, bool loopTime, string outputFolder, string editScope = "All", CharacterAnimationComposition composition = null,
            string sourcePacketPath = null, string expectedWorkItemId = null, string expectedSourcePacketSha256 = null)
        {
            RequireIdle();
            RequireValidEditScope(editScope);
            Require((editScope == CharacterAnimationCompositionRules.Scope) == (composition != null),
                "UpperBodyOverride requires its authenticated composition; it cannot use the finger or ordinary return route.");
            if (composition != null)
            {
                CharacterAnimationCompositionRules.Validate(composition);
                CharacterAnimationWeaponContext.Validate(composition);
            }
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
                editScope = editScope,
                composition = CharacterAnimationCompositionRules.Clone(composition),
                loopTime = loopTime,
                importedUtc = DateTime.UtcNow.ToString("O")
            };
            ExportMetadata metadata = ReadMetadata(sourceFbx, result);
            if (composition != null)
            {
                Require(metadata != null && CharacterAnimationCompositionRules.Fingerprint(metadata.composition) ==
                    CharacterAnimationCompositionRules.Fingerprint(composition), "The return has a different composition declaration.");
                CharacterAnimationCompositionRules.ValidateExport(composition, metadata.composition_export, metadata.action);
                result.composition_export = metadata.composition_export;
            }
            else Require(metadata == null || metadata.composition == null && metadata.composition_export == null,
                "A composition return cannot silently enter an ordinary animation route.");
            CharacterAnimationTransfer.Document sourcePacket = null;
            string sourcePacketSha256 = null;
            if (!string.IsNullOrEmpty(sourcePacketPath))
            {
                sourcePacketPath = Path.GetFullPath(sourcePacketPath);
                Require(metadata != null && Path.IsPathRooted(metadata.source_package) &&
                    string.Equals(sourcePacketPath, Path.GetFullPath(metadata.source_package), StringComparison.OrdinalIgnoreCase),
                    "The return does not name the caller's authenticated source packet.");
                sourcePacket = ReadSourcePacket(sourcePacketPath, expectedSourcePacketSha256, out sourcePacketSha256);
                Require(sourcePacket.schema == CharacterAnimationTransfer.Schema && sourcePacket.targetGuid == result.targetGuid &&
                    string.Equals(metadata.source_package_sha256, sourcePacketSha256, StringComparison.OrdinalIgnoreCase),
                    "The return source packet identity differs from this character and metadata.");
            }
            bool hasRigging = metadata?.rigging != null;
            Require(hasRigging == (sourcePacket?.rigging != null),
                "Rigging controls must return with their authenticated source packet; do not silently discard their Target candidate.");
            if (hasRigging)
            {
                Require(editScope == "All" && composition == null && metadata.source_package_sha256 == sourcePacketSha256 &&
                    loopTime == metadata.loop, "Rigging returns need their own base-motion route and exact source/timing identity.");
                CharacterAnimationRigging.ValidateReturn(metadata.rigging, sourcePacket, sourcePacketSha256,
                    targetPrefab, expectedWorkItemId, metadata.action, metadata.duration, metadata.loop, metadata.actionSlot);
                result.rigging = metadata.rigging;
                result.targetClipPath = AssetDatabase.GenerateUniqueAssetPath(outputFolder + "/" + clipName + ".targets.anim");
                Require(!File.Exists(result.targetClipPath) && !File.Exists(result.targetClipPath + ".meta") &&
                    AssetDatabase.LoadMainAssetAtPath(result.targetClipPath) == null, "The Target candidate destination is already claimed.");
            }
            AnimationClip extracted = null;
            AnimationClip targetCandidate = null;
            bool createdFbx = false, createdClip = false, createdTargetClip = false;
            bool constantPoseSourceVerified = false;
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
                // Keep a fixed Pose's linear Euler source through its Rest boundary.
                // Quaternion resampling can introduce motion between equal playable keys.
                // The complete Generic constant proof still runs before Humanoid conversion.
                importer.resampleCurves = composition == null || composition.timeMode != "HoldPose";
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
                if (hasRigging)
                    Require(AnimationUtility.GetCurveBindings(genericClip).All(binding => binding.type != typeof(Transform) ||
                        !sourcePacket.rigging.bindings.Any(arm => arm.targetPath == binding.path || arm.hintPath == binding.path)),
                        "The base FBX includes Target/Hint channels. Return those local controls separately.");
                if (composition != null)
                {
                    var allowedPaths = CompositionSourcePaths(targetPrefab, avatar, composition);
                    Require(allowedPaths.SetEquals(composition.allowedBonePaths),
                        "The composition's editable paths differ from this Avatar's declared upper-body roles.");
                    float playableStart = metadata.has_reference_frame
                        ? (metadata.playable_first_frame - takes[0].firstFrame) / result.frameRate : 0f;
                    ValidateIsolatedCompositionChannels(genericClip, allowedPaths, playableStart, genericClip.length);
                    if (composition.timeMode == "HoldPose")
                    {
                        RequireConstantCompositionPoseSource(genericClip, playableStart, genericClip.length);
                        constantPoseSourceVerified = true;
                    }
                }

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
                importer.userData = CharacterAnimationCompositionJson.ToJson(result);
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
                if (editScope == CharacterAnimationCompositionRules.Scope)
                {
                    if (composition.timeMode == "HoldPose")
                    {
                        Require(constantPoseSourceVerified, "A HoldPose needs verified constant Generic playable motion before Humanoid normalization.");
                        NormalizeProvenHoldPoseUpperBody(extracted, composition);
                    }
                    result.upperBodyRestriction = RestrictToUpperBody(extracted, composition);
                    result.channels = "Declared upper-body Humanoid muscles only; no root, pelvis, legs, Transform, events or object-reference curves.";
                }
                else if (editScope != "All")
                {
                    // Restrict the new owned clip after native Humanoid retargeting, before
                    // publication. The FBX/Avatar and other characters' original clips stay intact.
                    result.fingerRestriction = RestrictToFingers(extracted, editScope);
                    result.channels = "Selected hand finger Humanoid muscles only; no wrist, arm, body, root motion, events or object-reference curves. Runtime consumer acceptance is separate.";
                }
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
                if (hasRigging)
                {
                    Require(FileHash(sourcePacketPath) == sourcePacketSha256,
                        "The authenticated rigging source packet changed during import.");
                    Require(Math.Abs(result.duration - result.rigging.duration) <= .0002f,
                        "The imported base and Target controls do not have the same authored seconds range.");
                    CharacterAnimationRigging.ValidateInput(sourcePacket.rigging, targetPrefab);
                    targetCandidate = CharacterAnimationRigging.CreateTransientCandidate(result.rigging, sourcePacket.rigging, clipName + " Targets");
                    Require(!File.Exists(result.targetClipPath) && !File.Exists(result.targetClipPath + ".meta") &&
                        AssetDatabase.LoadMainAssetAtPath(result.targetClipPath) == null,
                        "The Target candidate destination was claimed during import.");
                    AssetDatabase.CreateAsset(targetCandidate, result.targetClipPath);
                    Require(EditorUtility.IsPersistent(targetCandidate) && AssetDatabase.GetAssetPath(targetCandidate) == result.targetClipPath,
                        "Unity did not create the standalone Target candidate. Uncertain output was retained for inspection.");
                    createdTargetClip = true;
                    AssetDatabase.SaveAssetIfDirty(targetCandidate);
                    Require(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(targetCandidate, out string targetGuid, out long targetId) &&
                        !string.IsNullOrEmpty(targetGuid) && targetId != 0, "The Target candidate has no persistent identity.");
                    result.targetClipGuid = targetGuid; result.targetClipLocalId = targetId;
                    result.targetClipSha256 = FileHash(result.targetClipPath);
                    result.TargetClip = targetCandidate; targetCandidate = null;
                    result.channels += " Separate Target candidate contains only the actual two arm targets' local position/quaternion and hints' local position.";
                }
                importer.userData = CharacterAnimationCompositionJson.ToJson(result);
                importer.SaveAndReimport();
                Require(AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath) != null,
                    "Unity did not retain the new standalone animation asset.");
                return result;
            }
            catch (Exception failure)
            {
                var errors = new List<Exception> { failure };
                if (createdTargetClip) TrashOwned(result.targetClipPath, errors);
                if (createdClip) TrashOwned(clipPath, errors);
                if (createdFbx) TrashOwned(fbxPath, errors);
                if (errors.Count > 1) throw new AggregateException("Animation return failed and some newly created output could not be removed.", errors);
                throw;
            }
            finally
            {
                if (extracted != null && !EditorUtility.IsPersistent(extracted)) Object.DestroyImmediate(extracted);
                if (targetCandidate != null && !EditorUtility.IsPersistent(targetCandidate)) Object.DestroyImmediate(targetCandidate);
                importing = false;
            }
        }

        [Serializable]
        public sealed class FingerRestriction
        {
            public string editScope;
            public int keptChannels, removedChannels;
            public float originalDuration, restrictedDuration;
            public string[] retainedProperties = Array.Empty<string>();
            public string runtimeAcceptance = "Unassessed; use an explicit hand-pose consumer, never replace its full-body Animator slot.";
        }

        public static void RequireValidEditScope(string editScope)
        {
            Require(editScope == "All" || editScope == "FingersLeft" || editScope == "FingersRight" || editScope == "FingersBoth" ||
                editScope == CharacterAnimationCompositionRules.Scope,
                "Unknown or empty animation edit scope.");
        }

        [Serializable]
        public sealed class UpperBodyRestriction
        {
            public int keptChannels, removedChannels;
            public float originalDuration, restrictedDuration;
            public string[] retainedProperties;
        }

        public static UpperBodyRestriction RestrictToUpperBody(AnimationClip clip, CharacterAnimationComposition composition)
        {
            var allowed = CharacterAnimationCompositionRules.AllowedMuscleProperties(composition);
            Require(clip != null && !EditorUtility.IsPersistent(clip) && clip.humanMotion && !clip.legacy &&
                Finite(clip.length) && clip.length > 0, "Restrict only a new owned Humanoid candidate with positive duration.");
            float duration = clip.length;
            var bindings = AnimationUtility.GetCurveBindings(clip);
            var kept = new List<EditorCurveBinding>();
            foreach (var binding in bindings)
            {
                if (binding.type == typeof(Animator) && string.IsNullOrEmpty(binding.path) && allowed.Contains(binding.propertyName))
                {
                    var curve = AnimationUtility.GetEditorCurve(clip, binding);
                    Require(curve != null && curve.length > 0 && curve.keys.All(key => Finite(key.time) && Finite(key.value)),
                        "The returned upper-body curve is empty or non-finite: " + binding.propertyName);
                    kept.Add(binding);
                }
                else AnimationUtility.SetEditorCurve(clip, binding, null);
            }
            foreach (var binding in AnimationUtility.GetObjectReferenceCurveBindings(clip)) AnimationUtility.SetObjectReferenceCurve(clip, binding, null);
            AnimationUtility.SetAnimationEvents(clip, Array.Empty<AnimationEvent>());
            Require(kept.Count > 0 && clip.humanMotion && Mathf.Abs(clip.length - duration) <= 0.0002f,
                "Upper-body pruning found no declared channels or changed the authored duration.");
            if (composition.timeMode == "HoldPose") RequireConstantUpperBodyPose(clip);
            return new UpperBodyRestriction { keptChannels = kept.Count, removedChannels = bindings.Length - kept.Count,
                originalDuration = duration, restrictedDuration = clip.length,
                retainedProperties = kept.Select(binding => binding.propertyName).OrderBy(value => value, StringComparer.Ordinal).ToArray() };
        }
        public static void RequireConstantUpperBodyPose(AnimationClip clip)
        {
            Require(clip != null && clip.length > 0, "A HoldPose needs a positive-duration clip.");
            foreach (var binding in AnimationUtility.GetCurveBindings(clip))
            {
                var curve = AnimationUtility.GetEditorCurve(clip, binding);
                Require(curve != null && curve.length > 0, "A HoldPose has an empty effective curve.");
                float value = curve.Evaluate(0);
                foreach (var key in curve.keys)
                    Require(Mathf.Abs(key.value - value) < 0.0001f, "HoldPose contains motion; use HoldLoop instead: " + binding.propertyName);
                for (int interval = 0; interval < curve.length - 1; interval++)
                    for (int sample = 1; sample <= 8; sample++)
                        Require(Mathf.Abs(curve.Evaluate(Mathf.Lerp(curve.keys[interval].time, curve.keys[interval + 1].time, sample / 9f)) - value) < 0.0001f,
                            "HoldPose contains interpolated motion; use HoldLoop instead: " + binding.propertyName);
            }
        }

        // Import is the only caller: its local proof flag is set after the complete
        // Generic playable interval passes, and source bytes are rechecked before this call.
        static void NormalizeProvenHoldPoseUpperBody(AnimationClip clip, CharacterAnimationComposition composition)
        {
            var allowed = CharacterAnimationCompositionRules.AllowedMuscleProperties(composition);
            Require(composition.timeMode == "HoldPose" && clip != null && !EditorUtility.IsPersistent(clip) &&
                clip.humanMotion && !clip.legacy && Finite(clip.length) && clip.length > 0f,
                "Normalize only a proven HoldPose on its new owned Humanoid candidate.");
            float duration = clip.length, frameRate = clip.frameRate;
            WrapMode wrapMode = clip.wrapMode;
            int kept = 0;
            foreach (var binding in AnimationUtility.GetCurveBindings(clip))
            {
                if (binding.type != typeof(Animator) || !string.IsNullOrEmpty(binding.path) || !allowed.Contains(binding.propertyName)) continue;
                var curve = AnimationUtility.GetEditorCurve(clip, binding);
                Require(curve != null && curve.length > 0 && curve.keys.All(key => Finite(key.time) && Finite(key.value) &&
                    !float.IsNaN(key.inTangent) && !float.IsNaN(key.outTangent) && Finite(key.inWeight) && Finite(key.outWeight) &&
                    ((key.weightedMode & WeightedMode.In) == 0 || key.inWeight >= 0f && key.inWeight <= 1f) &&
                    ((key.weightedMode & WeightedMode.Out) == 0 || key.outWeight >= 0f && key.outWeight <= 1f)),
                    "The returned upper-body curve is empty, non-finite or has invalid tangent weights: " + binding.propertyName);
                float value = curve.Evaluate(0f);
                Require(Finite(value), "The returned HoldPose has a non-finite first pose: " + binding.propertyName);
                foreach (var key in curve.keys)
                    Require(Mathf.Abs(key.value - value) < 0.0001f,
                        "HoldPose contains motion; Humanoid normalization is not permitted: " + binding.propertyName);
                AnimationUtility.SetEditorCurve(clip, binding, new AnimationCurve(
                    new Keyframe(0f, value, 0f, 0f), new Keyframe(duration, value, 0f, 0f))
                    { preWrapMode = curve.preWrapMode, postWrapMode = curve.postWrapMode });
                kept++;
            }
            Require(kept > 0 && clip.length == duration && clip.frameRate == frameRate && clip.wrapMode == wrapMode,
                "HoldPose normalization found no declared muscles or changed the authored duration, sample rate or wrap mode.");
        }

        /// <summary>
        /// Mutates only a caller-owned unsaved copy of an already retargeted Humanoid clip.
        /// Exact finger muscle channels survive; root/wrist/arm/body/Transform channels do not.
        /// This is an asset channel contract, not evidence that a runtime consumer preserves arms.
        /// </summary>
        public static FingerRestriction RestrictToFingers(AnimationClip clip, string editScope)
        {
            RequireValidEditScope(editScope);
            Require(editScope == "FingersLeft" || editScope == "FingersRight" || editScope == "FingersBoth", "RestrictToFingers requires an explicit finger scope.");
            Require(clip != null && !EditorUtility.IsPersistent(clip) && string.IsNullOrEmpty(AssetDatabase.GetAssetPath(clip)),
                "Restrict only a new owned unsaved clip; persistent source animation assets are never modified.");
            Require(clip.humanMotion && !clip.legacy && Finite(clip.length) && clip.length > 0f,
                "Finger restriction requires a valid native Humanoid clip, preserving Humanoid retargeting.");
            Require(AnimationUtility.GetObjectReferenceCurveBindings(clip).Length == 0 && AnimationUtility.GetAnimationEvents(clip).Length == 0,
                "Finger input must contain no object-reference curves or animation events.");
            bool left = editScope != "FingersRight", right = editScope != "FingersLeft";
            var leftProperties = FingerProperties(true); var rightProperties = FingerProperties(false);
            var bindings = AnimationUtility.GetCurveBindings(clip);
            var keep = new HashSet<EditorCurveBinding>();
            var properties = new List<string>(); int leftCount = 0, rightCount = 0;
            float duration = clip.length;
            foreach (var binding in bindings)
            {
                if (binding.type != typeof(Animator) || !string.IsNullOrEmpty(binding.path)) continue;
                bool isLeft = left && leftProperties.Contains(binding.propertyName);
                bool isRight = right && rightProperties.Contains(binding.propertyName);
                if (!isLeft && !isRight) continue;
                AnimationCurve curve = AnimationUtility.GetEditorCurve(clip, binding);
                Require(curve != null && curve.length > 0, "Selected finger curve is empty: " + binding.propertyName);
                foreach (var key in curve.keys)
                {
                    Require(Finite(key.time) && Finite(key.value), "Selected finger curve has a non-finite key: " + binding.propertyName);
                }
                keep.Add(binding); properties.Add(binding.propertyName);
                if (isLeft) leftCount++; if (isRight) rightCount++;
            }
            Require(keep.Count > 0 && (!left || leftCount > 0) && (!right || rightCount > 0),
                "The returned Humanoid clip has no actual curves for every selected hand; empty partial gestures are rejected.");
            // Constant poses may have one optimized key. Native clip duration must still
            // survive pruning below; never invent hold keys or retime authored curves.
            foreach (var binding in bindings) if (!keep.Contains(binding)) AnimationUtility.SetEditorCurve(clip, binding, null);
            var actual = AnimationUtility.GetCurveBindings(clip);
            Require(actual.Length == keep.Count && actual.All(keep.Contains) && clip.humanMotion &&
                Mathf.Abs(clip.length - duration) <= 0.0002f,
                "Native finger pruning changed the expected binding set, retargeting mode or duration.");
            return new FingerRestriction { editScope = editScope, keptChannels = keep.Count,
                removedChannels = bindings.Length - keep.Count, originalDuration = duration, restrictedDuration = clip.length,
                retainedProperties = properties.OrderBy(p => p, StringComparer.Ordinal).ToArray() };
        }

        static HashSet<string> FingerProperties(bool left)
        {
            var values = new HashSet<string>(StringComparer.Ordinal);
            string side = left ? "Left" : "Right";
            foreach (string finger in new[] { "Thumb", "Index", "Middle", "Ring", "Little" })
            {
                for (int joint = 1; joint <= 3; joint++) values.Add(side + "Hand." + finger + "." + joint + " Stretched");
                values.Add(side + "Hand." + finger + ".Spread");
            }
            // Unity exposes canonical muscle names as well as serialized Hand.* aliases.
            // Derive only muscles of the 15 selected mapped finger bones, never the wrist/arm.
            int first = (int)(left ? HumanBodyBones.LeftThumbProximal : HumanBodyBones.RightThumbProximal);
            string[] muscleNames = HumanTrait.MuscleName;
            for (int bone = first; bone < first + 15; bone++) for (int axis = 0; axis < 3; axis++)
            {
                int muscle = HumanTrait.MuscleFromBone(bone, axis);
                if (muscle >= 0 && muscle < muscleNames.Length) values.Add(muscleNames[muscle]);
            }
            return values;
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
            Require(new FileInfo(path).Length <= MaxMetadataBytes, "The animation export metadata exceeds 32 MiB.");
            byte[] metadataBytes = File.ReadAllBytes(path);
            Require(metadataBytes.Length <= MaxMetadataBytes, "The animation export metadata grew beyond 32 MiB while being read.");
            string metadataJson = new UTF8Encoding(false, true).GetString(metadataBytes);
            if (metadataJson.Length > 0 && metadataJson[0] == '\uFEFF') metadataJson = metadataJson.Substring(1);
            // Check the same bytes before Import can create/copy any animation asset.
            result.dressDeclaration = CharacterAnimationDressMetadataReader.Read(metadataJson);
            ExportMetadata metadata = CharacterAnimationCompositionJson.FromJson<ExportMetadata>(metadataJson);
            if (metadata?.rigging != null) CharacterAnimationRigging.ValidateReturnJson(metadataJson);
            Require(metadata != null && metadata.channels == "skeletal-only" &&
                    string.Equals(metadata.fbx_sha256, result.sourceSha256, StringComparison.OrdinalIgnoreCase),
                "The adjacent animation metadata does not describe this complete bones-only FBX.");
            Require(string.IsNullOrEmpty(metadata.filename) || metadata.filename == Path.GetFileName(source),
                "The adjacent animation metadata names another FBX.");
            result.metadataFile = path;
            using (var sha = SHA256.Create())
                result.metadataSha256 = string.Concat(sha.ComputeHash(metadataBytes).Select(value => value.ToString("x2")));
            result.sourceAction = metadata.action;
            result.origin = metadata.origin;
            result.sourcePackage = metadata.source_package;
            result.sourcePackageSha256 = metadata.source_package_sha256;
            return metadata;
        }

        static CharacterAnimationTransfer.Document ReadSourcePacket(string path, string expectedSha256, out string actualSha256)
        {
            const int maxBytes = 128 * 1024 * 1024;
            Require(!string.IsNullOrEmpty(expectedSha256) && expectedSha256.Length == 64 &&
                expectedSha256.All(c => c >= '0' && c <= '9' || c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F') &&
                File.Exists(path) && new FileInfo(path).Length <= maxBytes,
                "Use the existing Link's pinned complete source packet, not an unverified rigging declaration.");
            byte[] bytes = File.ReadAllBytes(path);
            Require(bytes.Length <= maxBytes, "The source packet grew while being read.");
            using (var sha = SHA256.Create())
                actualSha256 = string.Concat(sha.ComputeHash(bytes).Select(value => value.ToString("x2")));
            Require(string.Equals(actualSha256, expectedSha256, StringComparison.OrdinalIgnoreCase),
                "The rigging source packet differs from the Link's immutable packet hash.");
            string json = new UTF8Encoding(false, true).GetString(bytes);
            if (json.Length > 0 && json[0] == '\uFEFF') json = json.Substring(1);
            var packet = CharacterAnimationCompositionJson.FromJson<CharacterAnimationTransfer.Document>(json);
            Require(packet != null, "The authenticated source packet is empty.");
            return packet;
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

        static HashSet<string> CompositionSourcePaths(GameObject target, Avatar avatar, CharacterAnimationComposition composition)
        {
            var transforms = target.GetComponentsInChildren<Transform>(true);
            var paths = new HashSet<string>(StringComparer.Ordinal);
            foreach (string role in composition.humanRoles)
            {
                int bone = (int)(HumanBodyBones)Enum.Parse(typeof(HumanBodyBones), role);
                var mappings = avatar.humanDescription.human.Where(mapping => mapping.humanName == HumanTrait.BoneName[bone] &&
                    !string.IsNullOrEmpty(mapping.boneName)).ToArray();
                Require(mappings.Length == 1, "The source Avatar has no unique declared upper-body role: " + role);
                var matches = transforms.Where(transform => transform.name == mappings[0].boneName).ToArray();
                Require(matches.Length == 1, "The target has no unique declared upper-body bone: " + role);
                string path = AnimationUtility.CalculateTransformPath(matches[0], target.transform);
                Require(!string.IsNullOrEmpty(path) && paths.Add(path), "The source Avatar has an ambiguous editable upper-body path: " + role);
            }
            return paths;
        }

        /// <summary>
        /// Read-only Generic-source gate for disallowed playable motion, before Humanoid conversion/pruning.
        /// Callers authenticate the allowed paths against the verified Avatar. Rest is outside the supplied range.
        /// Numeric local components tolerate 2e-5 (position metres, quaternion/scale components); Euler tolerates 0.002 degrees.
        /// This rejects dynamic lower/root contamination; it does not prove same-B/reference invariance.
        /// </summary>
        public static void ValidateIsolatedCompositionChannels(AnimationClip clip, IEnumerable<string> allowedBonePaths,
            float playableStart, float playableEnd)
        {
            Require(clip != null && !clip.humanMotion && !clip.legacy && Finite(clip.length) &&
                Finite(playableStart) && Finite(playableEnd) && playableStart >= 0f && playableStart < clip.length && playableEnd > playableStart &&
                playableEnd <= clip.length + 0.0002f && allowedBonePaths != null,
                "The isolated source gate needs a Generic clip, authenticated paths and a positive playable range.");
            ValidateSourceChannels(clip);
            var allowed = new HashSet<string>(allowedBonePaths, StringComparer.Ordinal);
            Require(allowed.Count > 0 && allowed.All(path => !string.IsNullOrWhiteSpace(path)),
                "The isolated source gate has no authenticated upper-body paths.");
            foreach (var binding in AnimationUtility.GetCurveBindings(clip))
            {
                if (binding.type == typeof(Transform) && !string.IsNullOrEmpty(binding.path) && allowed.Contains(binding.path)) continue;
                var curve = AnimationUtility.GetEditorCurve(clip, binding);
                float tolerance = binding.propertyName.StartsWith("localEulerAngles", StringComparison.Ordinal)
                    ? IsolatedEulerTolerance : IsolatedComponentTolerance;
                RequireConstantSourceCurve(curve, playableStart, Mathf.Min(playableEnd, clip.length), tolerance,
                    binding.path + "/" + binding.propertyName);
            }
        }

        /// <summary>
        /// Read-only proof that every supported Generic local/root channel is constant
        /// throughout the playable interval. This does not authorize a caller to bypass
        /// the public Humanoid HoldPose guard or normalize an arbitrary candidate.
        /// </summary>
        public static int RequireConstantCompositionPoseSource(AnimationClip clip, float playableStart, float playableEnd)
        {
            Require(clip != null && !clip.humanMotion && !clip.legacy && Finite(clip.length) &&
                Finite(playableStart) && Finite(playableEnd) && playableStart >= 0f && playableStart < clip.length &&
                playableEnd > playableStart && playableEnd <= clip.length + 0.0002f,
                "The HoldPose source proof needs a Generic clip and a positive playable range.");
            ValidateSourceChannels(clip);
            var bindings = AnimationUtility.GetCurveBindings(clip);
            Require(bindings.Length > 0, "The HoldPose source proof has no local or root channels.");
            foreach (var binding in bindings)
            {
                var curve = AnimationUtility.GetEditorCurve(clip, binding);
                float tolerance = binding.propertyName.StartsWith("localEulerAngles", StringComparison.Ordinal)
                    ? IsolatedEulerTolerance : IsolatedComponentTolerance;
                RequireConstantSourceCurve(curve, playableStart, Mathf.Min(playableEnd, clip.length), tolerance,
                    binding.path + "/" + binding.propertyName);
            }
            return bindings.Length;
        }

        static void RequireConstantSourceCurve(AnimationCurve curve, float start, float end, float tolerance, string channel)
        {
            Require(curve != null && curve.length > 0, "The isolated source has an empty local channel: " + channel);
            var keys = curve.keys;
            Require(keys.All(key => Finite(key.time) && Finite(key.value)), "The isolated source has a non-finite local key: " + channel);
            double baseline = curve.Evaluate(start);
            void Check(double time, double value)
            {
                Require(Finite((float)value) && Math.Abs(value - baseline) <= tolerance,
                    "The isolated B source contains disallowed playable motion: " + channel + " at " + time + " seconds.");
            }
            Check(start, baseline); Check(end, curve.Evaluate(end));
            // A looping curve can expose keys outside the requested interval, including Rest.
            if (start < keys[0].time && (curve.preWrapMode == WrapMode.Loop || curve.preWrapMode == WrapMode.PingPong) ||
                end > keys[keys.Length - 1].time && (curve.postWrapMode == WrapMode.Loop || curve.postWrapMode == WrapMode.PingPong))
            { start = Mathf.Min(start, keys[0].time); end = Mathf.Max(end, keys[keys.Length - 1].time); }
            foreach (var key in keys) if (key.time >= start && key.time <= end) Check(key.time, key.value);
            for (int index = 0; index < keys.Length - 1; index++)
            {
                Keyframe left = keys[index], right = keys[index + 1];
                double duration = right.time - left.time;
                Require(duration > 0, "The isolated source has unordered local keys: " + channel);
                double begin = Math.Max(start, left.time), finish = Math.Min(end, right.time);
                if (finish <= begin) continue; // In particular, exclude the Rest-to-first-playable segment.
                Check(begin, curve.Evaluate((float)begin)); Check(finish, curve.Evaluate((float)finish));
                Check((begin + finish) * .5, curve.Evaluate((float)((begin + finish) * .5)));
                Require(!float.IsNaN(left.outTangent) && !float.IsNaN(right.inTangent), "The isolated source has an invalid local tangent: " + channel);
                if (float.IsInfinity(left.outTangent) || float.IsInfinity(right.inTangent)) continue; // Native stepped segment, already checked.
                double outWeight = (left.weightedMode & WeightedMode.Out) != 0 ? left.outWeight : 1d / 3d;
                double inWeight = (right.weightedMode & WeightedMode.In) != 0 ? right.inWeight : 1d / 3d;
                Require(outWeight >= 0 && outWeight <= 1 && inWeight >= 0 && inWeight <= 1,
                    "The isolated source has invalid local tangent weights: " + channel);
                double y0 = left.value, y1 = y0 + left.outTangent * duration * outWeight;
                double y3 = right.value, y2 = y3 - right.inTangent * duration * inWeight;
                // Cubic Bezier y extrema cover the complete weighted/unweighted interval,
                // rather than assuming constant keys or sampling only authored frames.
                double a = 3 * (-y0 + 3 * y1 - 3 * y2 + y3), b = 2 * (3 * y0 - 6 * y1 + 3 * y2), c = -3 * y0 + 3 * y1;
                void CheckExtremum(double u)
                {
                    if (!(u > 0 && u < 1)) return;
                    double v = 1 - u;
                    double time = left.time + duration * (3 * v * v * u * outWeight + 3 * v * u * u * (1 - inWeight) + u * u * u);
                    if (time < begin || time > finish) return;
                    Check(time, v * v * v * y0 + 3 * v * v * u * y1 + 3 * v * u * u * y2 + u * u * u * y3);
                    Check(time, curve.Evaluate((float)time));
                }
                if (Math.Abs(a) < 1e-12) { if (Math.Abs(b) >= 1e-12) CheckExtremum(-c / b); }
                else
                {
                    double discriminant = b * b - 4 * a * c;
                    if (discriminant >= 0)
                    { double root = Math.Sqrt(discriminant); CheckExtremum((-b - root) / (2 * a)); CheckExtremum((-b + root) / (2 * a)); }
                }
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
