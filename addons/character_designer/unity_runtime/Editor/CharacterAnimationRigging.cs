using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace CharacterDesigner.Unity.Editor
{
    /// <summary>Explicit controls for the character's two existing arm IK chains. No Rig is evaluated here.</summary>
    public static class CharacterAnimationRigging
    {
        public const string Schema = "cdesigner.two-bone-rigging/1";

        // The project consumer reads its actual TwoBoneIKConstraint components and supplies these plain fields.
        [Serializable] public sealed class Binding
        {
            public string rigPath, constraintPath, rootPath, midPath, tipPath;
            public string targetPath, targetParentPath, hintPath, hintParentPath;
            public float rigWeight, constraintWeight, targetPositionWeight, targetRotationWeight, hintWeight;
            public bool maintainTargetPositionOffset, maintainTargetRotationOffset;
        }
        [Serializable] public sealed class Control
        {
            public float[] targetPosition, targetRotation, hintPosition;
            // Initial packet-root space, as used by the skeletal packet. Preview mapping only.
            public float[] targetParentRoot, hintParentRoot;
        }
        [Serializable] public sealed class Frame { public float time; public Control[] controls; }
        [Serializable] public sealed class WeaponPreview
        {
            public string weaponContextFile, weaponContextSha256, weaponGuid, weaponPrefabGuid;
            public string weaponModelFile, weaponModelSha256, weaponSocketPath, weaponRigBonePath;
            public float[] weaponLocalMatrix, weaponPrefabLocalMatrix;
        }
        [Serializable] public sealed class Input
        {
            public string schema = Schema, targetGuid, targetDependencyHash, bindingsSha256, newActionId;
            public string targetLayoutSha256, baseControllerGuid, baseControllerDependencyHash, avatarDependencyHash;
            public string sourceMode, sourceClipPath, sourceClipGuid, sourceClipSha256;
            public long sourceClipLocalId;
            public string sourceTimeMode = "normalized-base-phase";
            public float duration, sampleRate;
            public bool loopTime;
            public bool previewEnabled;
            public Binding[] bindings;
            public Frame[] frames;
            [Newtonsoft.Json.JsonProperty(NullValueHandling = Newtonsoft.Json.NullValueHandling.Ignore)]
            public WeaponPreview weaponPreview;
        }
        [Serializable] public sealed class Returned
        {
            public string schema, sourcePacketSha256, bindingsSha256, targetGuid;
            public string workItemId, newActionId, sourceAction, targetInputAction;
            public int sourceActionSlot = -1;
            public int targetInputActionSlot = -1;
            public float duration, sampleRate;
            public bool loopTime;
            public Frame[] frames;
        }
        public sealed class Source
        {
            public Binding[] Bindings;
            public AnimationClip TargetClip;
            public string NewActionId;
            public bool PreviewEnabled;
            public WeaponPreview Weapon;
        }

        public sealed class Capture
        {
            readonly GameObject target;
            readonly AnimationClip sourceClip;
            readonly string targetPath, clipHash;
            readonly Binding[] bindings;
            readonly Dictionary<string, AnimationCurve> curves = new(StringComparer.Ordinal);
            GameObject capturedRoot;
            Transform[] targets, hints;
            public Input Declaration { get; }

            public Capture(GameObject target, Source source, float duration, float sampleRate, bool loopTime)
            {
                Require(target != null && EditorUtility.IsPersistent(target) && !EditorUtility.IsDirty(target),
                    "Rigging controls need a saved character prefab.");
                Require(source != null, "Supply the character's actual two arm IK bindings.");
                Require(source.PreviewEnabled && Guid.TryParseExact(source.NewActionId, "N", out _),
                    "Prepare rigging only after an explicit selection, with its own Target draft UUID.");
                this.target = target; sourceClip = source.TargetClip;
                targetPath = AssetDatabase.GetAssetPath(target);
                Require(PrefabUtility.IsPartOfPrefabAsset(target) && !string.IsNullOrEmpty(targetPath),
                    "Rigging controls need an actual prefab asset.");
                bindings = CharacterAnimationCompositionJson.FromJson<Binding[]>(
                    CharacterAnimationCompositionJson.ToJson(source.Bindings));
                ValidateBindings(target, bindings);
                Require(Finite(duration) && duration > 0 && Finite(sampleRate) && sampleRate > 0 &&
                    sampleRate <= 1000 && (double)duration * sampleRate < 200000,
                    "Rigging controls need a finite positive seconds range and sample rate.");
                string mode = "prefab-default", path = "", guid = ""; long localId = 0;
                if (sourceClip != null)
                {
                    Require(EditorUtility.IsPersistent(sourceClip) && !EditorUtility.IsDirty(sourceClip) && !sourceClip.legacy && !sourceClip.humanMotion &&
                        Finite(sourceClip.length) && sourceClip.length > 0 && AnimationUtility.GetAnimationEvents(sourceClip).Length == 0 &&
                        AnimationUtility.GetObjectReferenceCurveBindings(sourceClip).Length == 0,
                        "Use a saved Target clip without events or object-reference curves.");
                    path = AssetDatabase.GetAssetPath(sourceClip);
                    Require(path.StartsWith("Assets/", StringComparison.Ordinal), "Use the character's owned Target clip inside Assets.");
                    Require(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(sourceClip, out guid, out localId) &&
                        !string.IsNullOrEmpty(guid) && localId != 0, "The Target clip has no persistent identity.");
                    clipHash = FileHash(path); mode = "clip";
                    foreach (var binding in AnimationUtility.GetCurveBindings(sourceClip))
                    {
                        bool targetPath = bindings.Any(b => b.targetPath == binding.path);
                        bool hintPath = bindings.Any(b => b.hintPath == binding.path);
                        // Legacy AimingPointControl also authors Hint rotations. They do not drive
                        // TwoBoneIK; pin/preserve that source clip but transfer only effective Hint position.
                        Require(binding.type == typeof(Transform) && (targetPath || hintPath) && SupportedProperty(binding.propertyName),
                            "The Target clip contains a channel outside the actual arm controls: " + binding.path + "/" + binding.propertyName);
                        var curve = AnimationUtility.GetEditorCurve(sourceClip, binding);
                        Require(curve != null && curve.keys.All(k => Finite(k.time) && Finite(k.value)), "The Target clip has invalid keys.");
                        curves.Add(binding.path + "\n" + binding.propertyName, curve);
                    }
                    foreach (var b in bindings)
                        Require(!(new[] { "x", "y", "z", "w" }.Any(axis => Has(b.targetPath, "m_LocalRotation." + axis)) &&
                            new[] { "x", "y", "z" }.Any(axis => Has(b.targetPath, "localEulerAnglesRaw." + axis))),
                            "Use either quaternion or Euler rotation for each Target, not both.");
                }
                Declaration = new Input { targetGuid = AssetDatabase.AssetPathToGUID(targetPath),
                    targetDependencyHash = AssetDatabase.GetAssetDependencyHash(targetPath).ToString(),
                    targetLayoutSha256 = TargetLayoutHash(target), baseControllerGuid = AssetGuid(BaseController(target)),
                    baseControllerDependencyHash = AssetDependency(BaseController(target)),
                    avatarDependencyHash = AssetDependency(target.GetComponent<Animator>().avatar),
                    bindings = bindings, bindingsSha256 = BindingFingerprint(bindings),
                    newActionId = source.NewActionId, previewEnabled = source.PreviewEnabled,
                    sourceMode = mode, sourceClipPath = path, sourceClipGuid = guid, sourceClipLocalId = localId,
                    sourceClipSha256 = clipHash ?? "", duration = duration, sampleRate = sampleRate, loopTime = loopTime,
                    weaponPreview = source.Weapon == null ? null : CharacterAnimationCompositionJson.FromJson<WeaponPreview>(
                        CharacterAnimationCompositionJson.ToJson(source.Weapon)) };
                if (Declaration.weaponPreview != null) ValidateWeaponPreview(Declaration.weaponPreview, target);
            }

            public Frame Sample(GameObject previewRoot, Matrix4x4 initialRootInverse, float time)
            {
                Require(previewRoot != null && !EditorUtility.IsPersistent(previewRoot) && Finite(time) &&
                    time >= 0 && time <= Declaration.duration + .00001f, "Capture controls only on the owned base-only preview.");
                if (capturedRoot == null)
                {
                    capturedRoot = previewRoot;
                    targets = bindings.Select(b => Resolve(previewRoot.transform, b.targetPath)).ToArray();
                    hints = bindings.Select(b => Resolve(previewRoot.transform, b.hintPath)).ToArray();
                }
                Require(capturedRoot == previewRoot, "A rigging capture cannot switch its owned preview hierarchy.");
                float sourceTime = sourceClip == null ? 0 : Mathf.Clamp01(time / Declaration.duration) * sourceClip.length;
                // The whitelist was validated before sampling. Unity evaluates quaternion/Euler
                // interpolation on the disposable full-hierarchy clone; the source asset is untouched.
                // Bone poses have already been captured from the base stream, and this clip has no bone channels.
                if (sourceClip != null) sourceClip.SampleAnimation(previewRoot, sourceTime);
                var controls = new List<Control>();
                for (int arm = 0; arm < bindings.Length; arm++)
                {
                    Transform targetTransform = targets[arm];
                    Transform hintTransform = hints[arm];
                    Vector3 position = targetTransform.localPosition;
                    Vector3 hint = hintTransform.localPosition;
                    Quaternion rotation = targetTransform.localRotation;
                    var control = new Control { targetPosition = XYZ(position), targetRotation = XYZW(rotation), hintPosition = XYZ(hint),
                        targetParentRoot = Rows(initialRootInverse * targetTransform.parent.localToWorldMatrix),
                        hintParentRoot = Rows(initialRootInverse * hintTransform.parent.localToWorldMatrix) };
                    ValidateControl(control, previewMatrices: true); controls.Add(control);
                }
                return new Frame { time = time, controls = controls.ToArray() };
            }

            public Input Finish(Frame[] frames)
            {
                ValidateCurrent(); ValidateFrames(frames, Declaration.duration, Declaration.sampleRate, true);
                Declaration.frames = frames; return Declaration;
            }
            public void ValidateCurrent()
            {
                Require(!EditorUtility.IsDirty(target) && Declaration.targetGuid == AssetDatabase.AssetPathToGUID(targetPath) &&
                    Declaration.targetDependencyHash == AssetDatabase.GetAssetDependencyHash(targetPath).ToString(),
                    "The character's rigging source changed during capture.");
                ValidateBindings(target, bindings);
                if (sourceClip != null)
                    Require(!EditorUtility.IsDirty(sourceClip) && FileHash(Declaration.sourceClipPath) == clipHash &&
                        AssetDatabase.TryGetGUIDAndLocalFileIdentifier(sourceClip, out string guid, out long localId) &&
                        guid == Declaration.sourceClipGuid && localId == Declaration.sourceClipLocalId,
                        "The Target clip changed during capture.");
            }
            bool Has(string path, string property) => curves.ContainsKey(path + "\n" + property);
        }

        public static void ValidateInput(Input input, GameObject target)
        {
            Require(target != null && input != null && input.schema == Schema && input.previewEnabled &&
                Guid.TryParseExact(input.newActionId, "N", out _) && input.sourceTimeMode == "normalized-base-phase" &&
                input.targetGuid == AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(target)),
                "The rigging packet is not the current target character.");
            bool unchanged = input.targetDependencyHash == AssetDatabase.GetAssetDependencyHash(AssetDatabase.GetAssetPath(target)).ToString();
            // Applying either edited stream changes the root's override-controller reference.
            // Permit that exact publication while keeping the prefab layout, Avatar and base controller pinned.
            Require(unchanged || !string.IsNullOrEmpty(input.targetLayoutSha256) &&
                input.targetLayoutSha256 == TargetLayoutHash(target) && input.baseControllerGuid == AssetGuid(BaseController(target)) &&
                input.baseControllerDependencyHash == AssetDependency(BaseController(target)) &&
                input.avatarDependencyHash == AssetDependency(target.GetComponent<Animator>().avatar),
                "The character layout, Avatar or base controller changed after Prepare.");
            ValidateBindings(target, input.bindings);
            if (input.weaponPreview != null) ValidateWeaponPreview(input.weaponPreview, target);
            Require(input.bindingsSha256 == BindingFingerprint(input.bindings), "The rigging binding fingerprint changed.");
            if (input.sourceMode == "prefab-default")
                Require(string.IsNullOrEmpty(input.sourceClipPath) && string.IsNullOrEmpty(input.sourceClipGuid) &&
                    input.sourceClipLocalId == 0 && string.IsNullOrEmpty(input.sourceClipSha256), "A prefab-default control source cannot claim a clip.");
            else
            {
                Require(input.sourceMode == "clip" && !string.IsNullOrEmpty(input.sourceClipPath) &&
                    FileHash(input.sourceClipPath) == input.sourceClipSha256, "The source Target clip is absent or changed.");
                var matches = AssetDatabase.LoadAllAssetsAtPath(input.sourceClipPath).OfType<AnimationClip>().Where(clip =>
                    AssetDatabase.TryGetGUIDAndLocalFileIdentifier(clip, out string guid, out long id) &&
                    guid == input.sourceClipGuid && id == input.sourceClipLocalId).ToArray();
                Require(matches.Length == 1 && !EditorUtility.IsDirty(matches[0]), "The source Target clip identity is absent or ambiguous.");
            }
            ValidateFrames(input.frames, input.duration, input.sampleRate, true);
        }

        public static void ValidateWeaponPreview(WeaponPreview weapon, GameObject target)
        {
            Require(weapon != null && target != null && !string.IsNullOrEmpty(weapon.weaponSocketPath) &&
                !string.IsNullOrEmpty(weapon.weaponRigBonePath), "The weapon preview needs the character's saved attachment.");
            Transform socket = Resolve(target.transform, weapon.weaponSocketPath);
            Require(socket.parent == Resolve(target.transform, weapon.weaponRigBonePath) &&
                CharacterAnimationTransfer.MatrixError(Matrix4x4.TRS(socket.localPosition, socket.localRotation, socket.localScale),
                    CharacterAnimationTransfer.Matrix(weapon.weaponLocalMatrix)) <= .00001f,
                "The weapon preview attachment changed.");
            // Reuse the captured Unity geometry validator; this adapter is not a clip-composition request.
            CharacterAnimationWeaponContext.Validate(new CharacterAnimationComposition {
                weaponContextFile = weapon.weaponContextFile, weaponContextSha256 = weapon.weaponContextSha256,
                weaponGuid = weapon.weaponGuid, weaponPrefabGuid = weapon.weaponPrefabGuid,
                weaponModelFile = weapon.weaponModelFile, weaponModelSha256 = weapon.weaponModelSha256,
                weaponPrefabLocalMatrix = weapon.weaponPrefabLocalMatrix
            });
        }

        static RuntimeAnimatorController BaseController(GameObject target)
        {
            var controller = target.GetComponent<Animator>()?.runtimeAnimatorController;
            var seen = new HashSet<RuntimeAnimatorController>();
            while (controller is AnimatorOverrideController value)
            {
                Require(seen.Add(controller), "The character has a cyclic override-controller chain.");
                controller = value.runtimeAnimatorController;
            }
            Require(controller != null && EditorUtility.IsPersistent(controller) && !EditorUtility.IsDirty(controller),
                "The character needs its saved base controller.");
            return controller;
        }

        static string AssetGuid(UnityEngine.Object asset) => AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(asset));
        static string AssetDependency(UnityEngine.Object asset)
        {
            Require(asset != null && EditorUtility.IsPersistent(asset) && !EditorUtility.IsDirty(asset), "A saved animation dependency is missing or dirty.");
            return AssetDatabase.GetAssetDependencyHash(AssetDatabase.GetAssetPath(asset)).ToString();
        }

        static string TargetLayoutHash(GameObject target)
        {
            string path = AssetDatabase.GetAssetPath(target);
            string guid = ""; long id = 0;
            Require(path.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase) && File.Exists(path) &&
                AssetDatabase.TryGetGUIDAndLocalFileIdentifier(target.GetComponent<Animator>(), out guid, out id) &&
                guid == AssetDatabase.AssetPathToGUID(path), "Use a saved direct root Animator prefab.");
            string text = new UTF8Encoding(false, true).GetString(File.ReadAllBytes(path));
            Match header = Regex.Match(text, @"(?m)^--- !u!95 &" + id.ToString(CultureInfo.InvariantCulture) + @"\r?$");
            Require(header.Success, "The saved root Animator document is missing.");
            int next = text.IndexOf("\n--- ", header.Index + header.Length, StringComparison.Ordinal);
            int end = next < 0 ? text.Length : next;
            string document = text.Substring(header.Index, end - header.Index);
            MatchCollection fields = Regex.Matches(document, @"(?m)^  m_Controller: \{fileID: -?[0-9]+, guid: [0-9a-fA-F]{32}, type: 2\}[ \t]*\r?$");
            Require(fields.Count == 1, "The saved root controller reference is ambiguous.");
            int start = header.Index + fields[0].Index;
            string stable = text.Substring(0, start) + "  m_Controller: <current override>" + text.Substring(start + fields[0].Length);
            using var sha = SHA256.Create(); return Hex(sha.ComputeHash(Encoding.UTF8.GetBytes(stable)));
        }

        /// <summary>Validate the wire types before the normal fields-only JSON reader can coerce them.</summary>
        public static void ValidateReturnJson(string metadataJson)
        {
            JObject metadata = JObject.Parse(metadataJson);
            JToken rigging = metadata["rigging"];
            if (rigging == null || rigging.Type == JTokenType.Null) return;
            Require(rigging.Type == JTokenType.Object, "Rigging return metadata must be an object.");
            foreach (string field in new[] { "schema", "sourcePacketSha256", "bindingsSha256", "targetGuid", "workItemId", "newActionId", "sourceAction", "targetInputAction" })
                Require(rigging[field]?.Type == JTokenType.String, "Rigging return field must be a string: " + field);
            Require(metadata["actionSlot"]?.Type == JTokenType.Integer && rigging["sourceActionSlot"]?.Type == JTokenType.Integer &&
                rigging["targetInputActionSlot"]?.Type == JTokenType.Integer && rigging["loopTime"]?.Type == JTokenType.Boolean,
                "Rigging Action slot and loop mode need exact integer/boolean tokens.");
            foreach (string field in new[] { "duration", "sampleRate" }) NumericToken(rigging[field], field);
            Require(rigging["frames"]?.Type == JTokenType.Array, "Rigging return frames must be an array.");
            foreach (JToken frame in rigging["frames"])
            {
                Require(frame.Type == JTokenType.Object && frame["controls"]?.Type == JTokenType.Array, "Rigging return frame is malformed.");
                NumericToken(frame["time"], "time");
                foreach (JToken control in frame["controls"])
                {
                    Require(control.Type == JTokenType.Object && control["side"] == null, "Rigging controls use the fixed Right, Left binding order.");
                    foreach (string field in new[] { "targetPosition", "targetRotation", "hintPosition" })
                    {
                        Require(control[field]?.Type == JTokenType.Array, "Rigging return channel must be a numeric array: " + field);
                        foreach (JToken value in control[field]) NumericToken(value, field);
                    }
                    foreach (string field in new[] { "targetParentRoot", "targetParentWorld", "hintParentRoot", "hintParentWorld" })
                        Require(control[field] == null || control[field].Type == JTokenType.Null,
                            "Preview parent matrices cannot be returned as saved control channels.");
                }
            }
        }

        public static void ValidateReturn(Returned returned, CharacterAnimationTransfer.Document packet, string packetSha256,
            GameObject target, string workItemId, string action, float authoredDuration, bool authoredLoop, int actionSlot = -1)
        {
            Require(packet != null && packet.rigging != null && packet.editScope == "All" && packet.composition == null,
                "Arm control returns require their full base-motion source packet.");
            ValidateInput(packet.rigging, target);
            Require(packet.targetGuid == packet.rigging.targetGuid && packet.sampleRate == packet.rigging.sampleRate &&
                packet.duration == packet.rigging.duration && packet.loopTime == packet.rigging.loopTime && packet.frames != null &&
                packet.frames.Length == packet.rigging.frames.Length && packet.frames.Select(frame => frame.time)
                    .SequenceEqual(packet.rigging.frames.Select(frame => frame.time)),
                "The Target input and base packet do not share the same source identity and seconds grid.");
            Require(returned != null && returned.schema == Schema && Hex64(packetSha256) && returned.sourcePacketSha256 == packetSha256 &&
                returned.bindingsSha256 == packet.rigging.bindingsSha256 && returned.targetGuid == packet.targetGuid &&
                !string.IsNullOrEmpty(workItemId) && returned.workItemId == workItemId && returned.newActionId == packet.rigging.newActionId &&
                !string.IsNullOrEmpty(action) && returned.sourceAction == action && actionSlot >= 0 && returned.sourceActionSlot >= 0 &&
                returned.sourceActionSlot == actionSlot && !string.IsNullOrWhiteSpace(returned.targetInputAction) &&
                returned.targetInputAction != action && returned.targetInputActionSlot >= 0,
                "The arm control return belongs to another source packet, work item, Action or Action slot.");
            Require(returned.sampleRate == packet.rigging.sampleRate && returned.loopTime == authoredLoop && Finite(authoredDuration) && authoredDuration > 0 &&
                Math.Abs(returned.duration - authoredDuration) <= .0002f, "Arm control timing differs from the authored base return.");
            ValidateFrames(returned.frames, returned.duration, returned.sampleRate, false);
        }

        /// <summary>Creates an owned transient candidate only. The caller publishes it in the existing return transaction.</summary>
        public static AnimationClip CreateTransientCandidate(Returned returned, Input input, string name)
        {
            Require(returned != null && input != null && input.bindingsSha256 == BindingFingerprint(input.bindings), "Validate arm controls before building their candidate.");
            ValidateFrames(returned.frames, returned.duration, returned.sampleRate, false);
            var clip = new AnimationClip { name = name, frameRate = returned.sampleRate, legacy = false };
            try
            {
                for (int arm = 0; arm < input.bindings.Length; arm++)
                {
                    var b = input.bindings[arm]; int index = arm;
                    var controls = returned.frames.Select(frame => frame.controls[index]).ToArray();
                    Set(clip, b.targetPath, "m_LocalPosition", 3, returned.frames, controls.Select(c => c.targetPosition).ToArray());
                    Set(clip, b.targetPath, "m_LocalRotation", 4, returned.frames, controls.Select(c => c.targetRotation).ToArray());
                    Set(clip, b.hintPath, "m_LocalPosition", 3, returned.frames, controls.Select(c => c.hintPosition).ToArray());
                }
                clip.EnsureQuaternionContinuity();
                var settings = AnimationUtility.GetAnimationClipSettings(clip); settings.loopTime = returned.loopTime;
                AnimationUtility.SetAnimationClipSettings(clip, settings);
                Require(clip.length > 0 && Math.Abs(clip.length - returned.duration) <= .00001f, "The Target candidate lost its positive seconds range.");
                return clip;
            }
            catch { UnityEngine.Object.DestroyImmediate(clip); throw; }
        }

        public static string BindingFingerprint(Binding[] bindings)
        {
            Require(bindings != null && bindings.Length == 2, "Exactly two arm bindings are required.");
            using var stream = new MemoryStream(); using var writer = new BinaryWriter(stream, new UTF8Encoding(false), true);
            foreach (var b in bindings)
            {
                Require(b != null, "The arm binding is missing.");
                foreach (string s in new[] { b.rigPath, b.constraintPath, b.rootPath, b.midPath, b.tipPath,
                    b.targetPath, b.targetParentPath, b.hintPath, b.hintParentPath })
                { byte[] bytes = Encoding.UTF8.GetBytes(s ?? ""); writer.Write(bytes.Length); writer.Write(bytes); }
                foreach (float value in new[] { b.rigWeight, b.constraintWeight, b.targetPositionWeight, b.targetRotationWeight, b.hintWeight }) writer.Write(value);
                writer.Write(b.maintainTargetPositionOffset); writer.Write(b.maintainTargetRotationOffset);
            }
            writer.Flush(); using var sha = SHA256.Create(); return Hex(sha.ComputeHash(stream.ToArray()));
        }
        static void ValidateBindings(GameObject target, Binding[] bindings)
        {
            Require(target != null && bindings != null && bindings.Length == 2 && bindings.All(b => b != null),
                "Supply exactly one Left and one Right arm binding.");
            Animator[] animators = target.GetComponentsInChildren<Animator>(true);
            Require(animators.Length == 1 && animators[0].transform == target.transform && animators[0].avatar != null && animators[0].avatar.isHuman,
                "Arm controls require one root Humanoid Animator.");
            var paths = new HashSet<string>(StringComparer.Ordinal);
            for (int arm = 0; arm < bindings.Length; arm++)
            {
                var b = bindings[arm];
                foreach (string path in new[] { b.rigPath, b.constraintPath, b.rootPath, b.midPath, b.tipPath, b.targetPath, b.hintPath })
                    Resolve(target.transform, path);
                Transform targetTransform = Resolve(target.transform, b.targetPath), hint = Resolve(target.transform, b.hintPath);
                Require(targetTransform.parent != null && hint.parent != null && paths.Add(b.targetPath) && paths.Add(b.hintPath) &&
                    PathOf(target.transform, targetTransform.parent) == b.targetParentPath && PathOf(target.transform, hint.parent) == b.hintParentPath,
                    "Arm controls must have distinct actual targets/hints and exact parent paths.");
                bool left = arm == 1;
                Require(AvatarBone(target, animators[0], left ? HumanBodyBones.LeftUpperArm : HumanBodyBones.RightUpperArm) == Resolve(target.transform, b.rootPath) &&
                    AvatarBone(target, animators[0], left ? HumanBodyBones.LeftLowerArm : HumanBodyBones.RightLowerArm) == Resolve(target.transform, b.midPath) &&
                    AvatarBone(target, animators[0], left ? HumanBodyBones.LeftHand : HumanBodyBones.RightHand) == Resolve(target.transform, b.tipPath),
                    "The IK chain is not the target Avatar's actual arm mapping.");
                Require(Resolve(target.transform, b.midPath).IsChildOf(Resolve(target.transform, b.rootPath)) &&
                    Resolve(target.transform, b.tipPath).IsChildOf(Resolve(target.transform, b.midPath)), "The actual arm chain hierarchy is invalid.");
                foreach (float weight in new[] { b.rigWeight, b.constraintWeight, b.targetPositionWeight, b.targetRotationWeight, b.hintWeight })
                    Require(Finite(weight) && weight >= 0 && weight <= 1, "Capture the actual finite IK/Rig weights.");
            }
        }
        static Transform AvatarBone(GameObject target, Animator animator, HumanBodyBones bone)
        {
            string humanName = HumanTrait.BoneName[(int)bone];
            var entries = animator.avatar.humanDescription.human.Where(value => value.humanName == humanName).ToArray();
            Require(entries.Length == 1, "The Avatar mapping is missing or ambiguous: " + bone + ".");
            var matches = target.GetComponentsInChildren<Transform>(true).Where(value => value.name == entries[0].boneName).ToArray();
            Require(matches.Length == 1, "The mapped arm bone is missing or ambiguous: " + bone + ".");
            return matches[0];
        }

        static void ValidateFrames(Frame[] frames, float duration, float sampleRate, bool previewMatrices)
        {
            Require(Finite(duration) && duration > 0 && Finite(sampleRate) && sampleRate > 0 && sampleRate <= 1000 &&
                (double)duration * sampleRate < 200000 && frames != null && frames.Length >= 2 && frames.Length <= 200001,
                "Arm controls need a bounded finite positive playable interval.");
            var times = new List<float>();
            for (int i = 0; i / sampleRate < duration - .000001f; i++) times.Add(i / sampleRate);
            times.Add(duration);
            Require(frames.Length == times.Count, "Arm controls must contain the complete authored seconds grid.");
            for (int i = 0; i < frames.Length; i++)
            {
                var frame = frames[i];
                Require(frame != null && Finite(frame.time) && Math.Abs(frame.time - times[i]) <= .000001f &&
                    (i == 0 || frame.time > frames[i - 1].time) && frame.controls != null && frame.controls.Length == 2 &&
                    frame.controls.All(control => control != null),
                    "Each rigging sample must contain Right/Left controls on the actual seconds grid.");
                foreach (var control in frame.controls) ValidateControl(control, previewMatrices);
            }
        }
        static void ValidateControl(Control control, bool previewMatrices)
        {
            Array(control.targetPosition, 3); Array(control.targetRotation, 4); Array(control.hintPosition, 3);
            double norm = control.targetRotation.Sum(value => (double)value * value);
            Require(Math.Abs(norm - 1) <= .002, "The Target rotation must be a finite unit quaternion.");
            foreach (var matrix in new[] { control.targetParentRoot, control.hintParentRoot })
                if (previewMatrices) Array(matrix, 16); else Require(matrix == null, "Return only local controls, without preview parent matrices.");
        }
        static void Set(AnimationClip clip, string path, string property, int count, Frame[] frames, float[][] values)
        {
            string[] axes = { "x", "y", "z", "w" };
            for (int component = 0; component < count; component++)
            {
                int index = component;
                var curve = new AnimationCurve(frames.Select((frame, i) => new Keyframe(frame.time, values[i][index])).ToArray());
                for (int i = 0; i < curve.length; i++)
                { AnimationUtility.SetKeyLeftTangentMode(curve, i, AnimationUtility.TangentMode.Linear); AnimationUtility.SetKeyRightTangentMode(curve, i, AnimationUtility.TangentMode.Linear); }
                AnimationUtility.SetEditorCurve(clip, EditorCurveBinding.FloatCurve(path, typeof(Transform), property + "." + axes[component]), curve);
            }
        }
        static bool SupportedProperty(string property) => new[] { "m_LocalPosition", "m_LocalRotation", "localEulerAnglesRaw" }
            .Any(prefix => property == prefix + ".x" || property == prefix + ".y" || property == prefix + ".z" || prefix == "m_LocalRotation" && property == prefix + ".w");
        static Transform Resolve(Transform root, string path)
        {
            Require(path != null && !path.StartsWith("/", StringComparison.Ordinal) && !path.EndsWith("/", StringComparison.Ordinal) &&
                !path.Split('/').Any(part => part == "." || part == ".."), "Use an exact Animator-relative transform path.");
            Transform result = string.IsNullOrEmpty(path) ? root : root.Find(path);
            Require(result != null && PathOf(root, result) == path, "The actual rigging transform is missing: " + path);
            var duplicates = root.GetComponentsInChildren<Transform>(true).Count(t => PathOf(root, t) == path);
            Require(duplicates == 1, "The rigging transform path is ambiguous: " + path); return result;
        }
        static string PathOf(Transform root, Transform transform) => AnimationUtility.CalculateTransformPath(transform, root);
        static void Array(float[] values, int size) => Require(values != null && values.Length == size && values.All(Finite), "A rigging channel has invalid components.");
        static void NumericToken(JToken value, string name) => Require(value != null &&
            (value.Type == JTokenType.Integer || value.Type == JTokenType.Float), "Rigging channel requires a numeric token: " + name);
        static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
        static bool Hex64(string value) => value != null && value.Length == 64 && value.All(c => c >= '0' && c <= '9' || c >= 'a' && c <= 'f');
        static string FileHash(string path)
        {
            string absolute = Path.IsPathRooted(path) ? Path.GetFullPath(path) :
                Path.GetFullPath(Path.Combine(Directory.GetParent(Application.dataPath).FullName, path));
            using var file = File.OpenRead(absolute); using var sha = SHA256.Create(); return Hex(sha.ComputeHash(file));
        }
        static string Hex(byte[] bytes) => string.Concat(bytes.Select(value => value.ToString("x2", CultureInfo.InvariantCulture)));
        static float[] XYZ(Vector3 value) => new[] { value.x, value.y, value.z };
        static float[] XYZW(Quaternion value) => new[] { value.x, value.y, value.z, value.w };
        static float[] Rows(Matrix4x4 value) => Enumerable.Range(0, 16).Select(i => value[i / 4, i % 4]).ToArray();
        static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
    }
}
