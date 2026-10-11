using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using UnityEngine;

namespace CharacterDesigner.Unity
{
    [Serializable]
    public sealed class CharacterAnimationRestIdentity
    {
        public string path, humanRole, restSource;
        public int parent;
        public float[] rest;
    }
    // Optional, versioned authoring/application declaration. Source identity remains in the outer packet.
    [Serializable]
    public sealed class CharacterAnimationComposition
    {
        public string schema = CharacterAnimationCompositionRules.Schema;
        public string compositionId, applicationRole = CharacterAnimationCompositionRules.Role;
        public string baseClipGuid, baseClipSha256, referencePacketFile, referencePacketSha256;
        public long baseClipLocalId;
        public string editWorkItemId, editNewActionId;
        public string mask = "UpperBody", blendMode = "Override", timeMode = "HoldPose";
        public float weight = 1, referenceSpeed = 1, editSpeed = 1, referencePhase, editPhase;
        public float fps = 60, sampleStart, sampleEnd = 1;
        public string holdWorkItemId, holdNewActionId;
        public string weaponGuid, weaponModelFile, weaponModelSha256, weaponSocketPath;
        public string weaponPrefabGuid, weaponRigBonePath, sourceRestSha256;
        public string weaponContextFile, weaponContextSha256;
        public float[] weaponLocalMatrix, weaponPrefabLocalMatrix;
        public string[] humanRoles, allowedBonePaths, editableControlIds;
        public GripTarget[] gripTargets;

        [Serializable]
        public sealed class GripTarget
        {
            public string id, rigBonePath, controlId;
            public float[] localMatrix;
        }
    }

    // Export evidence is a declaration, not a substitute for the same-B/different-A acceptance test.
    [Serializable]
    public sealed class CharacterAnimationCompositionExport
    {
        public string schema = "cdesigner.animation-composition-export/1";
        public string compositionId, editWorkItemId, editNewActionId, sourceAction, isolatedEditAction;
        public int sourceActionSlot = -1;
        public bool referenceActionUnchanged, excludedReferenceEvaluation;
        public string referenceActionBeforeSha256, referenceActionAfterSha256, effectiveChannelsSha256;
        public int effectiveChannelCount;
        public string[] effectiveChannels;
        public string sampleSpace = "bone-local";
    }

    public static class CharacterAnimationCompositionRules
    {
        public const string Schema = "cdesigner.animation-composition/1";
        public const string Scope = "UpperBodyOverride";
        public const string Role = "UpperBodyWeapon";

        public static bool IsUpperBody(CharacterAnimationComposition spec) => spec != null && spec.applicationRole == Role;
        public static CharacterAnimationComposition Clone(CharacterAnimationComposition spec) => spec == null ? null :
            JsonUtility.FromJson<CharacterAnimationComposition>(JsonUtility.ToJson(spec));
        public static string ComputeRestSha256(IReadOnlyList<CharacterAnimationRestIdentity> bones)
        {
            Require(bones != null && bones.Count > 0 && bones.All(bone => bone != null && bone.rest != null &&
                bone.rest.Length == 16 && bone.rest.All(Finite)), "The parsed packet has an invalid rest skeleton.");
            using var data = new MemoryStream();
            using (var writer = new BinaryWriter(data, Encoding.UTF8, leaveOpen: true))
            {
                foreach (var bone in bones.OrderBy(bone => bone.path, StringComparer.Ordinal))
                {
                    WriteString(writer, bone.path); WriteString(writer, bone.humanRole); WriteString(writer, bone.restSource);
                    writer.Write(bone.parent);
                    foreach (float value in bone.rest) writer.Write(value);
                }
            }
            data.Position = 0;
            using var sha = SHA256.Create();
            return string.Concat(sha.ComputeHash(data).Select(value => value.ToString("x2")));
        }
        static void WriteString(BinaryWriter writer, string value)
        {
            byte[] bytes = Encoding.UTF8.GetBytes(value ?? ""); writer.Write((uint)bytes.Length); writer.Write(bytes);
        }
        public static string Fingerprint(CharacterAnimationComposition spec)
        {
            if (spec == null) return "";
            Validate(spec, requirePrepared: false);
            using var sha = SHA256.Create();
            return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(JsonUtility.ToJson(spec))).Select(value => value.ToString("x2")));
        }

        public static void Validate(CharacterAnimationComposition spec, bool requirePrepared = true)
        {
            Require(spec != null && spec.schema == Schema && spec.applicationRole == Role,
                "Unsupported composition version or application role. The animation stays pending.");
            Require(Guid.TryParse(spec.compositionId, out Guid compositionId) && compositionId != Guid.Empty &&
                Guid.TryParse(spec.editWorkItemId, out Guid workId) && workId != Guid.Empty &&
                (string.IsNullOrEmpty(spec.editNewActionId) || Guid.TryParse(spec.editNewActionId, out Guid actionId) && actionId != Guid.Empty),
                "The composition needs stable composition and editing-action identities.");
            Require(Hex(spec.baseClipGuid, 32) && spec.baseClipLocalId != 0 && Hex(spec.baseClipSha256, 64) &&
                (!requirePrepared || !string.IsNullOrWhiteSpace(spec.referencePacketFile) && Hex(spec.referencePacketSha256, 64)),
                "The composition needs the actual read-only reference clip and packet versions.");
            Require(spec.mask == "UpperBody" && spec.blendMode == "Override" && spec.weight == 1f,
                "Direct composition editing currently requires the UpperBody mask and Override at full weight.");
            Require(spec.timeMode == "HoldPose" || spec.timeMode == "HoldLoop" || spec.timeMode == "Raise",
                "Choose HoldPose, HoldLoop or Raise timing.");
            Require(Finite(spec.referenceSpeed) && spec.referenceSpeed > 0 && Finite(spec.editSpeed) && spec.editSpeed > 0 &&
                Finite(spec.referencePhase) && Finite(spec.editPhase) && Finite(spec.fps) && spec.fps > 0 &&
                Finite(spec.sampleStart) && Finite(spec.sampleEnd) && spec.sampleEnd > spec.sampleStart,
                "Composition timing must have finite speeds, phases and a positive sampling range.");
            Require(spec.timeMode == "Raise" || string.IsNullOrEmpty(spec.holdWorkItemId) && string.IsNullOrEmpty(spec.holdNewActionId),
                "A Hold action cannot contain a Raise-to-Hold destination.");
            Require((string.IsNullOrEmpty(spec.holdWorkItemId) || Guid.TryParse(spec.holdWorkItemId, out _)) &&
                (string.IsNullOrEmpty(spec.holdNewActionId) || Guid.TryParse(spec.holdNewActionId, out _)),
                "The Raise-to-Hold destination has an invalid action identity.");
            Require((string.IsNullOrEmpty(spec.holdWorkItemId) || Guid.Parse(spec.holdWorkItemId) != Guid.Empty) &&
                (string.IsNullOrEmpty(spec.holdNewActionId) || Guid.Parse(spec.holdNewActionId) != Guid.Empty) &&
                (!requirePrepared || spec.timeMode != "Raise" || !string.IsNullOrEmpty(spec.holdWorkItemId) || !string.IsNullOrEmpty(spec.holdNewActionId)),
                "Raise needs an actual following Hold action before preparing or applying.");
            Require((!requirePrepared && string.IsNullOrEmpty(spec.sourceRestSha256) || Hex(spec.sourceRestSha256, 64)) && spec.humanRoles != null && spec.humanRoles.Length > 0 &&
                spec.humanRoles.Distinct(StringComparer.Ordinal).Count() == spec.humanRoles.Length &&
                spec.humanRoles.All(AllowedRole), "The composition needs a verified rest pose and explicit upper-body roles.");
            Require(spec.allowedBonePaths != null && spec.allowedBonePaths.Length > 0 &&
                spec.allowedBonePaths.All(path => !string.IsNullOrWhiteSpace(path)) &&
                spec.allowedBonePaths.Distinct(StringComparer.Ordinal).Count() == spec.allowedBonePaths.Length,
                "The composition needs its verified editable bone paths.");
            Require(spec.editableControlIds == null || spec.editableControlIds.All(id => !string.IsNullOrWhiteSpace(id)) &&
                spec.editableControlIds.Distinct(StringComparer.Ordinal).Count() == spec.editableControlIds.Length,
                "Composition control identities must be explicit and unique.");
            Require(string.IsNullOrEmpty(spec.weaponModelFile) || Hex(spec.weaponModelSha256, 64),
                "The weapon model reference needs its actual version.");
            Require(string.IsNullOrEmpty(spec.weaponPrefabGuid) || Hex(spec.weaponPrefabGuid, 32), "The weapon prefab identity is invalid.");
            Require(spec.weaponLocalMatrix == null || Matrix(spec.weaponLocalMatrix), "The weapon attachment needs a row-major local matrix.");
            Require((!requirePrepared && spec.weaponPrefabLocalMatrix == null || Matrix(spec.weaponPrefabLocalMatrix)),
                "The weapon preview needs the actual prefab root scale as a row-major local matrix.");
            Require(!requirePrepared || Hex(spec.weaponPrefabGuid, 32) && Hex(spec.weaponGuid, 32) &&
                !string.IsNullOrWhiteSpace(spec.weaponModelFile) && Hex(spec.weaponModelSha256, 64) &&
                !string.IsNullOrWhiteSpace(spec.weaponSocketPath) && !string.IsNullOrWhiteSpace(spec.weaponRigBonePath) && Matrix(spec.weaponLocalMatrix),
                "Prepared composition needs the actual weapon prefab, source model and attachment identity.");
            Require((!requirePrepared && string.IsNullOrEmpty(spec.weaponContextFile) && string.IsNullOrEmpty(spec.weaponContextSha256)) ||
                !string.IsNullOrWhiteSpace(spec.weaponContextFile) && Hex(spec.weaponContextSha256, 64),
                "The composition needs its captured Unity weapon import and renderer context version.");
            var ids = new HashSet<string>(StringComparer.Ordinal);
            foreach (var grip in spec.gripTargets ?? Array.Empty<CharacterAnimationComposition.GripTarget>())
                Require(grip != null && !string.IsNullOrWhiteSpace(grip.id) && ids.Add(grip.id) &&
                    !string.IsNullOrWhiteSpace(grip.rigBonePath) && Matrix(grip.localMatrix) &&
                    (string.IsNullOrEmpty(grip.controlId) || (spec.editableControlIds ?? Array.Empty<string>()).Contains(grip.controlId)),
                    "Grip targets need actual unique identities, local matrices and verified controls.");
        }

        public static void ValidateExport(CharacterAnimationComposition spec, CharacterAnimationCompositionExport proof, string sourceAction)
        {
            Validate(spec);
            Require(proof != null && proof.schema == "cdesigner.animation-composition-export/1" &&
                proof.compositionId == spec.compositionId && proof.editWorkItemId == spec.editWorkItemId &&
                (proof.editNewActionId ?? "") == (spec.editNewActionId ?? "") && proof.sourceAction == sourceAction &&
                !string.IsNullOrWhiteSpace(proof.sourceAction) && proof.sourceActionSlot >= 0, "The returned composition belongs to another edit Action or slot.");
            Require(!string.IsNullOrWhiteSpace(proof.isolatedEditAction) && proof.referenceActionUnchanged && proof.excludedReferenceEvaluation &&
                proof.sampleSpace == "bone-local" && Hex(proof.referenceActionBeforeSha256, 64) &&
                proof.referenceActionBeforeSha256 == proof.referenceActionAfterSha256,
                "The export has no verified isolated B evaluation or preserved reference Action.");
            Require(proof.effectiveChannels != null && proof.effectiveChannelCount > 0 &&
                proof.effectiveChannelCount == proof.effectiveChannels.Length && Hex(proof.effectiveChannelsSha256, 64) &&
                proof.effectiveChannels.All(channel => !string.IsNullOrWhiteSpace(channel)) &&
                proof.effectiveChannels.Distinct(StringComparer.Ordinal).Count() == proof.effectiveChannels.Length,
                "The export needs a nonempty effective-channel declaration and fingerprint.");
            foreach (string channel in proof.effectiveChannels)
            {
                string[] parts = channel.Split('|');
                Require(parts.Length == 3 && spec.allowedBonePaths.Contains(parts[0]) &&
                    (parts[1] == "localPosition" || parts[1] == "localRotation" || parts[1] == "localScale") &&
                    parts[2].Length == 1 && parts[2][0] >= '0' && parts[2][0] <= (parts[1] == "localRotation" ? '3' : '2'),
                    "The effective channel declaration contains an unmapped path, property or component: " + channel);
            }
        }

        public static HashSet<string> AllowedMuscleProperties(CharacterAnimationComposition spec, bool requirePrepared = true)
        {
            Validate(spec, requirePrepared);
            var properties = new HashSet<string>(StringComparer.Ordinal);
            foreach (string role in spec.humanRoles)
            {
                var bone = (HumanBodyBones)Enum.Parse(typeof(HumanBodyBones), role);
                for (int axis = 0; axis < 3; axis++)
                {
                    int muscle = HumanTrait.MuscleFromBone((int)bone, axis);
                    if (muscle >= 0 && muscle < HumanTrait.MuscleName.Length) properties.Add(HumanTrait.MuscleName[muscle]);
                }
                if (role.IndexOf("Proximal", StringComparison.Ordinal) >= 0 || role.IndexOf("Intermediate", StringComparison.Ordinal) >= 0 || role.IndexOf("Distal", StringComparison.Ordinal) >= 0)
                {
                    string side = role.StartsWith("Left", StringComparison.Ordinal) ? "Left" : "Right";
                    string finger = new[] { "Thumb", "Index", "Middle", "Ring", "Little" }.FirstOrDefault(name => role.Contains(name));
                    if (finger != null)
                    {
                        int joint = role.EndsWith("Proximal", StringComparison.Ordinal) ? 1 : role.EndsWith("Intermediate", StringComparison.Ordinal) ? 2 : 3;
                        properties.Add(side + "Hand." + finger + "." + joint + " Stretched");
                        if (joint == 1) properties.Add(side + "Hand." + finger + ".Spread");
                    }
                }
            }
            return properties;
        }

        public static bool AllowedRole(string role)
        {
            if (!Enum.TryParse(role, out HumanBodyBones bone) || Enum.GetName(typeof(HumanBodyBones), bone) != role) return false;
            return bone == HumanBodyBones.Spine || bone == HumanBodyBones.Chest || bone == HumanBodyBones.UpperChest ||
                bone == HumanBodyBones.LeftShoulder || bone == HumanBodyBones.RightShoulder ||
                bone == HumanBodyBones.LeftUpperArm || bone == HumanBodyBones.RightUpperArm ||
                bone == HumanBodyBones.LeftLowerArm || bone == HumanBodyBones.RightLowerArm ||
                bone == HumanBodyBones.LeftHand || bone == HumanBodyBones.RightHand ||
                (int)bone >= (int)HumanBodyBones.LeftThumbProximal && (int)bone <= (int)HumanBodyBones.RightLittleDistal;
        }
        static bool Matrix(float[] matrix) => matrix != null && matrix.Length == 16 && matrix.All(Finite) &&
            Mathf.Abs(matrix[12]) < 0.00001f && Mathf.Abs(matrix[13]) < 0.00001f && Mathf.Abs(matrix[14]) < 0.00001f && Mathf.Abs(matrix[15] - 1) < 0.00001f;
        static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
        static bool Hex(string value, int count) => value != null && value.Length == count && value.All(character =>
            character >= '0' && character <= '9' || character >= 'a' && character <= 'f' || character >= 'A' && character <= 'F');
        static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
    }
}
