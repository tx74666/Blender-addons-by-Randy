// Explicit acceptance harness. Deploy only to an Editor test folder when Unity is available.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using CharacterDesigner.Unity.Editor;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace CharacterDesigner.Validation
{
    public static class CharacterAnimationReturnValidation
    {
        const string TargetPath = "Assets/Prefabs/Characters/Cosha/Cosha.Player.prefab";
        const string WalkPath = "Assets/~External/StarterAssets/ThirdPersonController/Character/Animations/Locomotion--Walk_N.anim.fbx";
        const string Artifacts = @"D:\Blender\Projects\Character\X\task_artifacts\animation_roundtrip";
        const string OutputPrefix = "Assets/CharacterAnimationReturnValidation_";
        static readonly PropertyInfo DirtyId = typeof(Scene).GetProperty("dirtyID", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        static readonly HumanBodyBones[] Joints = { HumanBodyBones.Hips, HumanBodyBones.Head,
            HumanBodyBones.LeftUpperArm, HumanBodyBones.LeftLowerArm, HumanBodyBones.LeftHand,
            HumanBodyBones.RightUpperArm, HumanBodyBones.RightLowerArm, HumanBodyBones.RightHand,
            HumanBodyBones.LeftUpperLeg, HumanBodyBones.LeftLowerLeg, HumanBodyBones.LeftFoot,
            HumanBodyBones.RightUpperLeg, HumanBodyBones.RightLowerLeg, HumanBodyBones.RightFoot };

        [Serializable] public sealed class Job
        {
            public string outputFolder;
            public bool captures = true;
            public ActionInput[] actions;
        }
        [Serializable] public sealed class ActionInput { public string fbx, clipName; public bool loop; }
        [Serializable] sealed class ExportMetadata { public string filename, channels, fbx_sha256, origin; }
        [Serializable] public sealed class FileStamp { public string path, sha256; public bool dirty; }
        [Serializable] public sealed class JointSample { public string bone, path; public Vector3 position; public Quaternion rotation; }
        [Serializable] public sealed class PoseSample { public float normalizedTime, time; public float[] root; public JointSample[] joints; }
        [Serializable] public sealed class ClipEvidence
        {
            public string assetPath, name, image;
            public bool humanMotion, loop;
            public float duration, fps, repeatMatrixError, rootEndpointDistance, leftFootEndpointDistance, rightFootEndpointDistance;
            public float minFootHeight, maxFootHeight;
            public PoseSample[] samples;
            public CharacterAnimationReturn.Result imported;
        }
        [Serializable] public sealed class Report
        {
            public string operation, utc, unityVersion, target, targetDependencyHash, avatar, controller;
            public string packet, packetSha256, clip, clipName, clipGuid, jobFile, jobSha256, outputFolder, error;
            public long clipLocalId;
            public float sourceClipFps, sourceClipDuration, packetSampleRate;
            public bool passed, originalFilesPreserved, scenesPreserved, selectionPreserved;
            public string sceneBefore, sceneAfter;
            public FileStamp[] originals;
            public List<ClipEvidence> clips = new();
        }

        [MenuItem("Tools/Character Designer/Validation/Export Current Cosha Walk")]
        public static void ExportCurrentWalk()
        {
            Run("export", (report, target, directory) =>
            {
                RuntimeAnimatorController controller = target.GetComponent<Animator>().runtimeAnimatorController;
                Require(controller != null, "The current Cosha character has no animation controller.");
                AnimationClip[] matches = controller.animationClips.Where(value => value != null &&
                    string.Equals(AssetDatabase.GetAssetPath(value), WalkPath, StringComparison.Ordinal)).Distinct().ToArray();
                Require(matches.Length == 1, "The current controller must reference exactly one normal Walk_N clip from " + WalkPath + ".");
                AnimationClip clip = matches[0];
                report.clip = AssetDatabase.GetAssetPath(clip);
                report.clipName = clip.name;
                AssetDatabase.TryGetGUIDAndLocalFileIdentifier(clip, out report.clipGuid, out report.clipLocalId);
                report.sourceClipFps = clip.frameRate; report.sourceClipDuration = clip.length;
                report.packet = CharacterAnimationTransfer.Export(target, clip, directory);
                report.packetSha256 = Hash(report.packet);
                var document = JsonUtility.FromJson<CharacterAnimationTransfer.Document>(File.ReadAllText(report.packet));
                Require(document != null && document.frames != null && document.frames.Length > 1 && document.bones != null && document.bones.Length >= 15,
                    "The exported Walk packet is incomplete.");
                report.packetSampleRate = document.sampleRate;
                report.clips.Add(SampleClip(target, clip, directory, "current-walk", true));
            });
        }

        [MenuItem("Tools/Character Designer/Validation/Validate Two Blender Returns")]
        public static void ValidateReturns()
        {
            Run("return", (report, target, directory) =>
            {
                report.jobFile = Path.Combine(Artifacts, "return-validation-job.json");
                Require(File.Exists(report.jobFile) && new FileInfo(report.jobFile).Length <= 65536,
                    "Create the bounded return-validation-job.json before running this check.");
                report.jobSha256 = Hash(report.jobFile);
                Job job = JsonUtility.FromJson<Job>(File.ReadAllText(report.jobFile));
                Require(job != null && job.actions != null && job.actions.Length == 2,
                    "The validation job must contain exactly two actions: edited Walk and a new Blender Action.");
                string output = (job.outputFolder ?? "").Replace('\\', '/').TrimEnd('/');
                Require(output.StartsWith(OutputPrefix, StringComparison.Ordinal) && output.IndexOf('/', 7) < 0 &&
                        output.Length > OutputPrefix.Length && output.Substring(7).IndexOfAny(Path.GetInvalidFileNameChars()) < 0 &&
                        output == output.TrimEnd(' ', '.'),
                    "Choose a new direct child folder named Assets/CharacterAnimationReturnValidation_<run>.");
                Require(!Directory.Exists(output) && !File.Exists(output + ".meta") && !AssetDatabase.IsValidFolder(output),
                    "The validation output folder already exists. Choose a new run name; existing results are retained.");
                foreach (ActionInput action in job.actions)
                {
                    Require(action != null && !string.IsNullOrWhiteSpace(action.fbx) && Path.IsPathRooted(action.fbx) &&
                            File.Exists(action.fbx) && string.Equals(Path.GetExtension(action.fbx), ".fbx", StringComparison.OrdinalIgnoreCase),
                        "Each action needs an existing absolute FBX path.");
                    Require(!string.IsNullOrWhiteSpace(action.clipName) && action.clipName == action.clipName.Trim() &&
                            action.clipName.IndexOfAny(Path.GetInvalidFileNameChars()) < 0 && !action.clipName.EndsWith(".", StringComparison.Ordinal),
                        "Each action needs a valid new clip name.");
                    string metadata = Path.ChangeExtension(action.fbx, ".animation.json");
                    Require(File.Exists(metadata) && new FileInfo(metadata).Length <= 1024 * 1024,
                        "Each acceptance example must include its bounded .animation.json export report.");
                    ExportMetadata export = JsonUtility.FromJson<ExportMetadata>(File.ReadAllText(metadata));
                    Require(export != null && export.channels == "skeletal-only" && export.filename == Path.GetFileName(action.fbx) &&
                            string.Equals(export.fbx_sha256, Hash(action.fbx), StringComparison.OrdinalIgnoreCase),
                        "An acceptance FBX does not match its bones-only export report.");
                    Require(export.origin == (action == job.actions[0] ? "unity-edit" : "blender-original"),
                        "The job order must be edited Unity Walk first, then a new original Blender Action.");
                }
                Require(!string.Equals(Path.GetFullPath(job.actions[0].fbx), Path.GetFullPath(job.actions[1].fbx), StringComparison.OrdinalIgnoreCase),
                    "Choose two different exported FBX files.");
                report.outputFolder = output;
                Require(!string.IsNullOrEmpty(AssetDatabase.CreateFolder("Assets", output.Substring(7))), "Cannot create the new validation folder.");
                foreach (ActionInput action in job.actions)
                {
                    var imported = CharacterAnimationReturn.Import(action.fbx, target, action.clipName, action.loop, output);
                    // Keep the imported asset path in a failure report even if preview sampling fails.
                    var evidence = new ClipEvidence { imported = imported, assetPath = imported.clipPath, name = action.clipName };
                    report.clips.Add(evidence);
                    SampleClip(target, imported.Clip, directory, "return-" + report.clips.Count, job.captures, evidence);
                    Require(evidence.loop == action.loop, "The standalone clip did not retain the requested Loop setting.");
                }
                Require(report.jobSha256 == Hash(report.jobFile), "The job changed while validation was running.");
            });
        }

        static void Run(string operation, Action<Report, GameObject, string> action)
        {
            string parent = operation == "export" ? "current-unity" : "validation-runs";
            string directory = Path.Combine(Artifacts, parent, DateTime.UtcNow.ToString("yyyyMMdd-HHmmss") + "-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            var report = new Report { operation = operation, utc = DateTime.UtcNow.ToString("O"), unityVersion = Application.unityVersion, target = TargetPath };
            Object[] selection = Selection.objects; Object activeSelection = Selection.activeObject;
            bool hadPreference = EditorPrefs.HasKey(CharacterAnimationTransfer.FolderPreference);
            string preference = EditorPrefs.GetString(CharacterAnimationTransfer.FolderPreference, "");
            report.sceneBefore = SceneState();
            try
            {
                RequireIdle();
                var target = AssetDatabase.LoadAssetAtPath<GameObject>(TargetPath);
                Animator animator = target != null ? target.GetComponent<Animator>() : null;
                Require(animator != null && animator.avatar != null && animator.avatar.isHuman && animator.avatar.isValid,
                    "The current Cosha Player needs its valid refreshed Humanoid Avatar.");
                Require(!EditorUtility.IsDirty(target) && !EditorUtility.IsDirty(animator.avatar), "Save pending prefab/Avatar asset edits before this check.");
                report.targetDependencyHash = AssetDatabase.GetAssetDependencyHash(TargetPath).ToString();
                report.avatar = AssetDatabase.GetAssetPath(animator.avatar);
                report.controller = AssetDatabase.GetAssetPath(animator.runtimeAnimatorController);
                report.originals = OriginalFiles(report.avatar, report.controller);
                Directory.CreateDirectory(directory);
                action(report, target, directory);
                Require(report.targetDependencyHash == AssetDatabase.GetAssetDependencyHash(TargetPath).ToString(),
                    "The current character dependencies changed during validation.");
            }
            catch (Exception error) { report.error = error.ToString(); }
            finally
            {
                if (hadPreference) EditorPrefs.SetString(CharacterAnimationTransfer.FolderPreference, preference);
                else EditorPrefs.DeleteKey(CharacterAnimationTransfer.FolderPreference);
                if (!Selection.objects.SequenceEqual(selection)) Selection.objects = selection;
                if (Selection.activeObject != activeSelection) Selection.activeObject = activeSelection;
                EditorUtility.ClearProgressBar();
                report.sceneAfter = SceneState();
                report.scenesPreserved = report.sceneBefore == report.sceneAfter;
                report.selectionPreserved = Selection.objects.SequenceEqual(selection) && Selection.activeObject == activeSelection;
                try
                {
                    report.originalFilesPreserved = report.originals != null && report.originals.All(value =>
                        File.Exists(value.path) && Hash(value.path) == value.sha256 && IsDirty(value.path) == value.dirty);
                }
                catch (Exception error) { report.error += "\nCould not finish original asset verification: " + error; }
                if (!report.scenesPreserved || !report.selectionPreserved || (report.originals != null && !report.originalFilesPreserved))
                    report.error += "\nOriginal scene, selection, or asset preservation check failed.";
                report.passed = string.IsNullOrEmpty(report.error) && report.originalFilesPreserved;
                Directory.CreateDirectory(directory);
                string path = Path.Combine(directory, "report.json");
                File.WriteAllText(path, JsonUtility.ToJson(report, true));
                if (report.passed) Debug.Log("Character animation " + operation + " passed: " + path);
                else Debug.LogError("Character animation " + operation + " failed: " + path + "\n" + report.error);
            }
        }

        static ClipEvidence SampleClip(GameObject target, AnimationClip clip, string directory, string label, bool captures, ClipEvidence evidence = null)
        {
            Require(clip != null && clip.humanMotion && clip.length > 0f, "The acceptance clip must contain Humanoid motion.");
            evidence ??= new ClipEvidence();
            evidence.assetPath = AssetDatabase.GetAssetPath(clip); evidence.name = clip.name;
            evidence.humanMotion = clip.humanMotion; evidence.duration = clip.length; evidence.fps = clip.frameRate;
            evidence.loop = AnimationUtility.GetAnimationClipSettings(clip).loopTime;
            using var preview = new CharacterAnimationTransfer.Preview(target, clip);
            evidence.samples = new[] { 0f, .25f, .5f, .75f, 1f }.Select(normalized =>
            {
                CharacterAnimationTransfer.Frame frame = preview.Sample(normalized * clip.length);
                Require(Finite(frame.root) && frame.poses.All(pose => Finite(pose.matrix)), "A sampled bone or root matrix is invalid.");
                var joints = Joints.Select(id =>
                {
                    Transform bone = preview.Animator.GetBoneTransform(id);
                    Require(bone != null, "Missing acceptance joint: " + id);
                    return new JointSample { bone = id.ToString(), path = CharacterAnimationTransfer.PathOf(preview.Root.transform, bone),
                        position = bone.position, rotation = bone.rotation };
                }).ToArray();
                return new PoseSample { normalizedTime = normalized, time = frame.time, root = frame.root, joints = joints };
            }).ToArray();
            var first = preview.Sample(clip.length * .5f);
            var repeat = preview.Sample(clip.length * .5f);
            evidence.repeatMatrixError = first.root.Zip(repeat.root, (a, b) => Mathf.Abs(a - b)).Max();
            for (int i = 0; i < first.poses.Length; i++)
                evidence.repeatMatrixError = Mathf.Max(evidence.repeatMatrixError,
                    first.poses[i].matrix.Zip(repeat.poses[i].matrix, (a, b) => Mathf.Abs(a - b)).Max());
            Require(evidence.repeatMatrixError <= .0002f, "Preview sampling is not deterministic.");
            PoseSample start = evidence.samples[0], end = evidence.samples[evidence.samples.Length - 1];
            evidence.rootEndpointDistance = Vector3.Distance(Translation(start.root), Translation(end.root));
            evidence.leftFootEndpointDistance = FootDistance(start, end, HumanBodyBones.LeftFoot);
            evidence.rightFootEndpointDistance = FootDistance(start, end, HumanBodyBones.RightFoot);
            float[] heights = evidence.samples.SelectMany(sample => sample.joints)
                .Where(joint => joint.bone == "LeftFoot" || joint.bone == "RightFoot").Select(joint => joint.position.y).ToArray();
            evidence.minFootHeight = heights.Min(); evidence.maxFootHeight = heights.Max();
            if (captures)
            {
                preview.Sample(clip.length * .35f);
                using var renderer = new CharacterAnimationPreviewRenderer(preview);
                evidence.image = Path.Combine(directory, label + ".png");
                renderer.CapturePng(evidence.image);
                Require(File.Exists(evidence.image), "Preview screenshot was not written.");
            }
            return evidence;
        }

        static float FootDistance(PoseSample first, PoseSample last, HumanBodyBones foot) =>
            Vector3.Distance(first.joints.First(joint => joint.bone == foot.ToString()).position,
                last.joints.First(joint => joint.bone == foot.ToString()).position);
        static Vector3 Translation(float[] matrix) => new(matrix[3], matrix[7], matrix[11]);
        static bool Finite(float[] values) => values != null && values.Length == 16 && values.All(value => !float.IsNaN(value) && !float.IsInfinity(value));

        static FileStamp[] OriginalFiles(string avatar, string controller)
        {
            string[] extensions = { ".fbx", ".prefab", ".controller", ".overridecontroller", ".anim" };
            var paths = AssetDatabase.GetDependencies(TargetPath, true)
                .Where(path => extensions.Contains(Path.GetExtension(path).ToLowerInvariant()))
                .Concat(new[] { TargetPath, avatar, controller, "Assets/Prefabs/Characters/Cosha/Cosha.Setup.asset" })
                .Where(path => !string.IsNullOrEmpty(path) && File.Exists(path)).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
            return paths.SelectMany(path => File.Exists(path + ".meta") ? new[] { path, path + ".meta" } : new[] { path })
                .Select(path => new FileStamp { path = path, sha256 = Hash(path), dirty = IsDirty(path) }).ToArray();
        }
        static bool IsDirty(string path)
        {
            if (path.EndsWith(".meta", StringComparison.Ordinal)) return false;
            Object asset = AssetDatabase.LoadMainAssetAtPath(path);
            return asset != null && EditorUtility.IsDirty(asset);
        }
        static string SceneState()
        {
            var values = new List<string> { "active=" + SceneManager.GetActiveScene().handle, "previews=" + EditorSceneManager.previewSceneCount };
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                Scene scene = SceneManager.GetSceneAt(i);
                values.Add(scene.handle + ":" + scene.path + ":" + scene.isLoaded + ":" + scene.isDirty + ":" + DirtyId?.GetValue(scene));
            }
            return string.Join("|", values);
        }
        static void RequireIdle()
        {
            Require(!EditorApplication.isPlayingOrWillChangePlaymode && !EditorApplication.isCompiling && !EditorApplication.isUpdating &&
                    !AssetDatabase.IsAssetImportWorkerProcess(), "Run in the idle main Editor with Play stopped.");
            Type player = AppDomain.CurrentDomain.GetAssemblies().Select(assembly => assembly.GetType("Unity.Multiplayer.PlayMode.CurrentPlayer", false)).FirstOrDefault(type => type != null);
            Require(player == null || player.GetProperty("IsMainEditor", BindingFlags.Public | BindingFlags.Static)?.GetValue(null) is true,
                "Do not run acceptance writes in a Multiplayer Play Mode clone.");
        }
        static string Hash(string path)
        {
            using var file = File.OpenRead(path); using var hash = SHA256.Create();
            return string.Concat(hash.ComputeHash(file).Select(value => value.ToString("x2")));
        }
        static void Require(bool value, string message) { if (!value) throw new InvalidOperationException(message); }
    }
}
