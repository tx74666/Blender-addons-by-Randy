using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace CharacterDesigner.Unity.Editor
{
    /// <summary>Explicit, evaluated target-character motion. Matrices use row-major storage.</summary>
    public static class CharacterAnimationTransfer
    {
        public const string Schema = "cdesigner.animation/1";
        public const string LatestFile = "character_designer_animation_latest.json";
        public const string FolderPreference = "CharacterDesigner.Animation.ExportFolder";
        public const string DefaultFolder = @"D:\Blender\Projects\Character\Animation\UnityExports";
        internal static Func<int, bool> CancelSamplingForTests;

        [Serializable] public sealed class Document
        {
            public string schema = Schema, units = "metres", coordinate = "unity-lh-y-up", matrixLayout = "row-major";
            public string targetName, targetAssetPath, targetGuid, clipName, clipAssetPath, clipGuid, exportedUtc;
            public string projectId, sourceGuid, sourceName;
            public long clipLocalId;
            public float duration, sampleRate = 60;
            public bool loopTime, humanoid, applyFootIK = true;
            // The complete rig/context stays in the packet for retargeting. The return
            // consumer must prune this scope; this declaration alone is not isolation.
            public string editScope = "All";
            public CharacterAnimationComposition composition;
            [Newtonsoft.Json.JsonProperty(NullValueHandling = Newtonsoft.Json.NullValueHandling.Ignore)]
            public CharacterAnimationRigging.Input rigging;
            public Bone[] bones;
            public Frame[] frames;
            public MeshDefinition[] meshes;
        }
        [Serializable] public sealed class Bone
        {
            public string name, path, restSource, humanRole;
            public bool weighted;
            public int parent;
            public float[] rest;
        }
        [Serializable] public sealed class Pose { public float[] matrix; }
        [Serializable] public sealed class Frame
        {
            public float time;
            public float[] root;
            public Pose[] poses;
            public MeshPose[] meshes;
            [NonSerialized] public CharacterAnimationRigging.Frame riggingSample;
        }
        [Serializable] public sealed class MeshDefinition
        {
            public string name, path;
            public int vertexCount;
            public int[] sourceIds;
            public Vector3[] restPositions;
        }
        [Serializable] public sealed class MeshPose
        {
            public string name;
            public Vector3[] positions, uncorrectedPositions, correctionDelta;
        }
        [Serializable] public sealed class Manifest
        {
            public string schema = "cdesigner.animation-latest/1";
            public string file, targetName, clipName, exportedUtc;
        }
        [Serializable] public sealed class CollectionManifest
        {
            public string schema="cdesigner.animation-collection/1", id, name, sourceGuid, sourceName;
            public CollectionEntry[] clips;
        }
        [Serializable] public sealed class CollectionEntry { public string file, sha256, name; }
        public static string RestFingerprint(Document document)
        {
            Require(document != null && document.bones != null && document.bones.Length > 0, "The skeletal packet has no rest skeleton.");
            return CharacterAnimationCompositionRules.ComputeRestSha256(document.bones.Select(bone => new CharacterAnimationRestIdentity
                { path = bone.path, humanRole = bone.humanRole, restSource = bone.restSource, parent = bone.parent, rest = bone.rest }).ToArray());
        }

        public static AnimationClip[] Collect(GameObject source, RuntimeAnimatorController controller=null, Object scope=null)
        {
            var clips=new List<AnimationClip>(Clips(source));
            if(controller!=null)clips.AddRange(controller.animationClips);
            if(scope!=null)
            {
                string path=AssetDatabase.GetAssetPath(scope);
                Require(!string.IsNullOrEmpty(path),"Choose an animation resource or an asset folder.");
                var paths=AssetDatabase.IsValidFolder(path)
                    ? AssetDatabase.FindAssets("t:AnimationClip",new[]{path}).Select(AssetDatabase.GUIDToAssetPath).Distinct()
                    : new[]{path};
                foreach(string p in paths)clips.AddRange(AssetDatabase.LoadAllAssetsAtPath(p).OfType<AnimationClip>());
            }
            return clips.Where(c=>c!=null && EditorUtility.IsPersistent(c) && !c.name.StartsWith("__preview__",StringComparison.Ordinal))
                .Distinct().OrderBy(c=>c.name,StringComparer.OrdinalIgnoreCase).ToArray();
        }

        static string Hash(string value)
        { using var sha=SHA256.Create();return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(value)).Select(b=>b.ToString("x2"))); }

        public static string ExportCollection(GameObject source, IEnumerable<AnimationClip> selected, string folder, string name)
        {
            Require(source!=null && EditorUtility.IsPersistent(source),"Choose a source character prefab with its Avatar.");
            var clips=selected.Where(c=>c!=null).Distinct().ToArray();
            Require(clips.Length>0,"Select one or more source actions.");
            Require(!string.IsNullOrWhiteSpace(folder),"Choose an exchange folder outside Unity Assets.");
            folder=Path.GetFullPath(folder);
            Require(!(folder+Path.DirectorySeparatorChar).StartsWith(Path.GetFullPath(Application.dataPath)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase),
                "Use an exchange folder outside Unity Assets; source resources are never overwritten.");
            string sourceGuid=AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(source));
            name=string.IsNullOrWhiteSpace(name)?source.name+" Motions":name.Trim();
            string id=Hash(PlayerSettings.productGUID+":"+sourceGuid+":"+name);
            var entries=new List<CollectionEntry>();var created=new List<string>();
            Directory.CreateDirectory(folder);
            try
            {
                foreach(var clip in clips)
                {
                    Document document;
                    using(var preview=new Preview(source,clip))document=preview.Capture(false);
                    // Content-addressed packets remain stable on repeated export.
                    document.exportedUtc="";
                    string json=CharacterAnimationCompositionJson.ToJson(document,false), hash=Hash(json);
                    string filename=hash+".cdanim.json", path=Path.Combine(folder,filename);
                    if(!File.Exists(path)){AtomicWrite(path,json);created.Add(path);}
                    else Require(Hash(File.ReadAllText(path))==hash,"An existing exchange packet was changed; choose another output folder.");
                    entries.Add(new CollectionEntry{file=filename,sha256=hash,name=clip.name});
                }
                string manifestPath=Path.Combine(folder,id+".cdcollection.json");
                AtomicWrite(manifestPath,JsonUtility.ToJson(new CollectionManifest{id=id,name=name,sourceGuid=sourceGuid,sourceName=source.name,clips=entries.ToArray()},true));
                EditorPrefs.SetString(FolderPreference,folder);
                return manifestPath;
            }
            catch
            {
                // Only exact files created by this unpublished transaction are removed.
                foreach(string path in created)if(File.Exists(path))File.Delete(path);
                throw;
            }
            finally { EditorUtility.ClearProgressBar(); }
        }

        public static AnimationClip[] Clips(GameObject target)
        {
            if (target == null) return Array.Empty<AnimationClip>();
            var clips = new List<AnimationClip>(AnimationUtility.GetAnimationClips(target));
            foreach (var animator in target.GetComponentsInChildren<Animator>(true))
                if (animator.runtimeAnimatorController != null) clips.AddRange(animator.runtimeAnimatorController.animationClips);
            return clips.Where(c => c != null && !c.name.StartsWith("__preview__", StringComparison.Ordinal))
                .Distinct().OrderBy(c => c.name, StringComparer.OrdinalIgnoreCase).ToArray();
        }

        public static string Export(GameObject target, AnimationClip clip, string folder, bool meshEvidence = true)
            => ExportCore(target, clip, folder, meshEvidence, skeletalOnly: false);

        // Motion editing needs the evaluated skeleton even when a mesh correction cannot
        // represent that pose. This explicit route never claims deformation evidence.
        public static string ExportSkeletal(GameObject target, AnimationClip clip, string folder, string editScope = "All", CharacterAnimationComposition composition = null,
            CharacterAnimationRigging.Source riggingSource = null)
            => ExportCore(target, clip, folder, meshEvidence: false, skeletalOnly: true, editScope: editScope, composition: composition, riggingSource: riggingSource);

        // Caller-driven main-thread work. Each Step returns before the next renderable frame;
        // initialization, JSON and atomic publication remain explicitly separate native stages.
        public static IncrementalExport BeginSkeletalExport(GameObject target, AnimationClip clip, string folder, string editScope = "All", CharacterAnimationComposition composition = null,
            CharacterAnimationRigging.Source riggingSource = null)
            => new IncrementalExport(target, clip, folder, editScope, composition, riggingSource);

        public sealed class IncrementalExport : IDisposable
        {
            enum Phase { Initialize, Sampling, Serialize, Publish, Complete }
            readonly GameObject target;
            readonly AnimationClip clip;
            readonly string folder, editScope;
            readonly CharacterAnimationComposition composition;
            readonly CharacterAnimationRigging.Source riggingSource;
            Preview preview;
            Preview.CaptureOperation capture;
            Document document;
            string json;
            Phase phase;
            bool disposed;
            public bool Finished => phase == Phase.Complete;
            public string PacketPath { get; private set; }
            public int SampledFrames { get; private set; }
            public int TotalFrames { get; private set; }
            public double LastStepMilliseconds { get; private set; }
            public string Stage => disposed && !Finished ? "Cancelled" : phase switch {
                Phase.Initialize => "Creating private preview", Phase.Sampling => "Sampling frames",
                Phase.Serialize => "Serializing animation packet", Phase.Publish => "Validating and publishing packet", _ => "Packet ready" };

            internal IncrementalExport(GameObject target, AnimationClip clip, string folder, string editScope, CharacterAnimationComposition composition = null,
                CharacterAnimationRigging.Source riggingSource = null)
            {
                Require(!string.IsNullOrWhiteSpace(folder), "Choose the Blender handoff folder.");
                CharacterAnimationReturn.RequireValidEditScope(editScope);
                ValidateCompositionScope(editScope, composition);
                Require(riggingSource == null || editScope == "All" && composition == null,
                    "Prepare actual arm controls with their own base motion, not an upper-body deform overlay.");
                this.target=target; this.clip=clip; this.folder=Path.GetFullPath(folder); this.editScope=editScope;
                this.composition = CharacterAnimationCompositionRules.Clone(composition);
                this.riggingSource = riggingSource;
            }

            public void Step(double milliseconds = 10, Action validateBeforePublish = null)
            {
                Require(!disposed, "The incremental animation export is closed.");
                if (Finished) return;
                var clock=System.Diagnostics.Stopwatch.StartNew();
                try
                {
                    Require(!EditorApplication.isPlayingOrWillChangePlaymode && !EditorApplication.isCompiling &&
                        !EditorApplication.isUpdating && !AssetDatabase.IsAssetImportWorkerProcess(),
                        "Continue incremental animation export only in the idle Editor.");
                    switch (phase)
                    {
                        case Phase.Initialize:
                            preview=Preview.CreateSkeletal(target,clip,riggingSource);
                            capture=preview.BeginCapture(false); TotalFrames=capture.Total;
                            phase=Phase.Sampling; break;
                        case Phase.Sampling:
                            capture.Step(milliseconds); SampledFrames=capture.Completed;
                            if(capture.Finished)
                            {
                                document=capture.ToDocument(); document.editScope=editScope;
                                document.composition = CharacterAnimationCompositionRules.Clone(composition);
                                preview.Dispose(); preview=null; capture=null; phase=Phase.Serialize;
                            }
                            break;
                        case Phase.Serialize:
                            json=CharacterAnimationCompositionJson.ToJson(document,false); phase=Phase.Publish; break;
                        case Phase.Publish:
                            validateBeforePublish?.Invoke();
                            PacketPath=PublishDocument(target,clip,document,folder,json,rememberFolder:false);
                            phase=Phase.Complete; document=null; json=null; break;
                    }
                }
                catch { Dispose(); throw; }
                finally { LastStepMilliseconds=clock.Elapsed.TotalMilliseconds; }
            }

            public void Dispose()
            {
                if(disposed)return; disposed=true;
                try { preview?.Dispose(); }
                finally { preview=null; capture=null; document=null; json=null; }
            }
        }

        static string ExportCore(GameObject target, AnimationClip clip, string folder, bool meshEvidence, bool skeletalOnly, string editScope = "All", CharacterAnimationComposition composition = null,
            CharacterAnimationRigging.Source riggingSource = null)
        {
            Require(!string.IsNullOrWhiteSpace(folder), "Choose the Blender handoff folder.");
            CharacterAnimationReturn.RequireValidEditScope(editScope);
            ValidateCompositionScope(editScope, composition);
            Require(riggingSource == null || skeletalOnly && editScope == "All" && composition == null,
                "Prepare actual arm controls with their own base motion, not an upper-body deform overlay.");
            Document document;
            using (var preview = skeletalOnly ? Preview.CreateSkeletal(target, clip, riggingSource) : new Preview(target, clip))
                document = preview.Capture(meshEvidence);
            document.editScope = editScope;
            document.composition = CharacterAnimationCompositionRules.Clone(composition);
            return PublishDocument(target,clip,document,folder,CharacterAnimationCompositionJson.ToJson(document,false),rememberFolder:true);
        }

        static void ValidateCompositionScope(string scope, CharacterAnimationComposition composition)
        {
            Require((scope == CharacterAnimationCompositionRules.Scope) == (composition != null),
                "UpperBodyOverride needs an explicit composition; ordinary and finger packets cannot carry one.");
            if (composition != null) CharacterAnimationCompositionRules.Validate(composition);
        }

        static string PublishDocument(GameObject target, AnimationClip clip, Document document, string folder, string json, bool rememberFolder)
        {
            if (document.rigging != null) CharacterAnimationRigging.ValidateInput(document.rigging, target);
            string targetLabel = target.name.Replace(".Player", "").Replace(".Runtime", "");
            string path = Path.Combine(Path.GetFullPath(folder), SafeName(targetLabel + "_" + clip.name) + ".cdanim.json");
            // Finish and validate all sampling before publishing either file.
            var manifest = new Manifest { file = path, targetName = document.targetName,
                clipName = document.clipName, exportedUtc = document.exportedUtc };
            Directory.CreateDirectory(folder);
            string manifestPath=Path.Combine(folder,LatestFile);
            string previous=File.Exists(path)?File.ReadAllText(path):null;
            try
            {
                AtomicWrite(path, json);
                AtomicWrite(manifestPath, JsonUtility.ToJson(manifest, true));
            }
            catch
            {
                if(previous!=null)AtomicWrite(path,previous);
                else if(File.Exists(path))File.Delete(path);
                throw;
            }
            if(rememberFolder)EditorPrefs.SetString(FolderPreference, Path.GetFullPath(folder));
            return path;
        }

        static void AtomicWrite(string path, string text)
        {
            string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                File.WriteAllText(temporary, text, new System.Text.UTF8Encoding(false));
                if (File.Exists(path)) File.Replace(temporary, path, null);
                else File.Move(temporary, path);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }

        public sealed class Preview : IDisposable
        {
            public GameObject Root { get; private set; }
            public Animator Animator { get; private set; }
            public AnimationClip Clip { get; private set; }
            public Bone[] Bones { get; private set; }
            public float Time { get; private set; }
            public Bounds Bounds { get; private set; }
            public string CorrectionWarning { get; private set; }
            internal int PoseVersion { get; private set; }
            readonly GameObject source;
            readonly AnimationClip sourceClip;
            readonly bool allowForearmFallback;
            readonly bool skeletalOnly;
            readonly CharacterAnimationRigging.Capture riggingCapture;
            readonly Scene scene;
            readonly Dictionary<Transform, Transform> clones = new();
            readonly List<RestTransform> rest = new();
            readonly List<SkinnedMeshRenderer> renderers = new();
            readonly List<ForearmCorrection> corrections = new();
            readonly List<Mesh> baked = new();
            readonly Dictionary<Mesh, Mesh> readableMeshes = new();
            readonly List<Transform> bones = new();
            readonly Dictionary<Transform, Matrix4x4> bindRest = new();
            readonly HashSet<Transform> weightedBones = new();
            readonly Dictionary<SkinnedMeshRenderer, float[]> shapeRest = new();
            Matrix4x4 initialRootInverse;
            PlayableGraph graph;
            MeshDefinition[] meshDefinitions;
            bool disposed;

            struct RestTransform
            {
                public Transform transform;
                public Vector3 position, scale;
                public Quaternion rotation;
                public void Restore() { transform.localPosition = position; transform.localRotation = rotation; transform.localScale = scale; }
            }

            public Preview(GameObject target, AnimationClip clip) : this(target, clip, false) { }

            public Preview(GameObject target, AnimationClip clip, bool allowForearmFallback)
                : this(target, clip, allowForearmFallback, skeletalOnly: false) { }

            internal static Preview CreateSkeletal(GameObject target, AnimationClip clip, CharacterAnimationRigging.Source riggingSource = null)
                => new Preview(target, clip, allowForearmFallback: false, skeletalOnly: true, riggingSource: riggingSource);

            private Preview(GameObject target, AnimationClip clip, bool allowForearmFallback, bool skeletalOnly,
                CharacterAnimationRigging.Source riggingSource = null)
            {
                Require(!EditorApplication.isPlayingOrWillChangePlaymode, "Stop Play Mode before previewing an animation.");
                Require(target != null && EditorUtility.IsPersistent(target), "Choose a character prefab asset.");
                Require(clip != null && EditorUtility.IsPersistent(clip), "Choose an existing animation clip asset.");
                source = target; sourceClip = clip;
                this.allowForearmFallback = allowForearmFallback;
                this.skeletalOnly = skeletalOnly;
                var sourceAnimator = target.GetComponentsInChildren<Animator>(true)
                    .FirstOrDefault(a => a.avatar != null && (!clip.humanMotion || a.avatar.isHuman));
                Require(sourceAnimator != null, "The target needs an Animator with an existing Avatar. Select its configured player prefab.");
                Require(sourceAnimator.avatar.isValid && (!clip.humanMotion || sourceAnimator.avatar.isHuman),
                    "The selected clip requires a valid matching Humanoid Avatar.");
                if (riggingSource != null)
                {
                    Require(skeletalOnly, "Rigging input samples use only the base's own skeletal stream.");
                    riggingCapture = new CharacterAnimationRigging.Capture(target, riggingSource, clip.length, 60,
                        AnimationUtility.GetAnimationClipSettings(clip).loopTime);
                    Require(AnimationUtility.GetCurveBindings(clip).All(binding => binding.type != typeof(Transform) ||
                        !riggingCapture.Declaration.bindings.Any(arm => arm.targetPath == binding.path || arm.hintPath == binding.path)),
                        "The base clip already animates arm controls. Select a separate base motion and Target clip.");
                }
                scene = EditorSceneManager.NewPreviewScene();
                try
                {
                    // Build only safe visual/animation components. Instantiating the player prefab
                    // would run arbitrary ExecuteAlways/Awake code before it could be disabled.
                    Root = CloneTransforms(target.transform, null).gameObject;
                    Root.name = target.name;
                    initialRootInverse = Root.transform.worldToLocalMatrix;
                    CloneRenderers();
                    CaptureBindRest();
                    Animator = clones[sourceAnimator.transform].gameObject.AddComponent<Animator>();
                    Animator.avatar = sourceAnimator.avatar;
                    Animator.runtimeAnimatorController = null;
                    Animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
                    Animator.applyRootMotion = true;
                    Animator.fireEvents = false;
                    Clip = Object.Instantiate(clip);
                    Clip.name = clip.name;
                    Clip.hideFlags = HideFlags.HideAndDontSave;
                    AnimationUtility.SetAnimationEvents(Clip, Array.Empty<AnimationEvent>());
                    foreach (var t in Root.GetComponentsInChildren<Transform>(true))
                        rest.Add(new RestTransform { transform=t, position=t.localPosition, rotation=t.localRotation, scale=t.localScale });
                    if (!skeletalOnly) CloneCorrections();
                    BuildBoneDefinitions();
                    SamplePose(0);
                }
                catch { Dispose(); throw; }
            }

            Transform CloneTransforms(Transform original, Transform parent)
            {
                var obj = new GameObject(original.name) { hideFlags = HideFlags.HideAndDontSave };
                SceneManager.MoveGameObjectToScene(obj, scene);
                var t = obj.transform;
                t.SetParent(parent, false);
                t.localPosition = original.localPosition; t.localRotation = original.localRotation; t.localScale = original.localScale;
                clones.Add(original, t);
                foreach (Transform child in original) CloneTransforms(child, t);
                return t;
            }

            void CloneRenderers()
            {
                foreach (var original in source.GetComponentsInChildren<SkinnedMeshRenderer>(true))
                {
                    Require(original.sharedMesh != null, original.name + ": the mesh is missing.");
                    Mesh mesh = original.sharedMesh;
                    if (!mesh.isReadable)
                    {
                        if (!readableMeshes.TryGetValue(mesh, out var readable))
                        { readable = CharacterAnimationReadableMesh.Copy(mesh); readableMeshes.Add(mesh, readable); }
                        mesh = readable;
                    }
                    var smr = clones[original.transform].gameObject.AddComponent<SkinnedMeshRenderer>();
                    smr.sharedMesh = mesh;
                    smr.sharedMaterials = original.sharedMaterials;
                    smr.bones = original.bones.Select(b => b != null && clones.ContainsKey(b) ? clones[b] : null).ToArray();
                    Require(smr.bones.All(b => b != null), original.name + ": a skin bone is outside the selected prefab.");
                    smr.rootBone = original.rootBone != null && clones.ContainsKey(original.rootBone) ? clones[original.rootBone] : null;
                    smr.quality = original.quality; smr.localBounds = original.localBounds; smr.updateWhenOffscreen = true;
                    smr.enabled = original.enabled;
                    float[] weights = new float[smr.sharedMesh.blendShapeCount];
                    for (int i=0;i<weights.Length;i++) { weights[i]=original.GetBlendShapeWeight(i); smr.SetBlendShapeWeight(i,weights[i]); }
                    shapeRest.Add(smr, weights);
                    renderers.Add(smr); baked.Add(new Mesh { name=original.name + " Preview Bake", hideFlags=HideFlags.HideAndDontSave });
                }
                Require(renderers.Count != 0, "The target contains no skinned mesh.");
            }

            void CaptureBindRest()
            {
                foreach (var smr in renderers)
                {
                    var bindposes = smr.sharedMesh.bindposes;
                    // Borrowed read-only view; include influences beyond the old four-weight API.
                    foreach(var weight in smr.sharedMesh.GetAllBoneWeights())
                        if(weight.weight>0)weightedBones.Add(smr.bones[weight.boneIndex]);
                    Require(bindposes.Length == smr.bones.Length, smr.name + ": skin bindpose count is inconsistent.");
                    Matrix4x4 rendererRest = initialRootInverse * smr.transform.localToWorldMatrix;
                    for (int i=0;i<bindposes.Length;i++)
                    {
                        Matrix4x4 matrix = rendererRest * bindposes[i].inverse;
                        ValidateMatrix(matrix, smr.bones[i].name);
                        if (bindRest.TryGetValue(smr.bones[i],out var prior))
                            Require(MatrixError(prior,matrix) < .0002f, smr.bones[i].name + ": bound meshes disagree on the rest pose.");
                        else bindRest.Add(smr.bones[i],matrix);
                    }
                }
            }

            void CloneCorrections()
            {
                foreach (var original in source.GetComponentsInChildren<ForearmCorrection>(true))
                {
                    Require(original.targetRenderer != null && clones.ContainsKey(original.targetRenderer.transform), "A forearm correction renderer is outside the prefab.");
                    var correction = clones[original.transform].gameObject.AddComponent<ForearmCorrection>();
                    correction.targetRenderer = clones[original.targetRenderer.transform].GetComponent<SkinnedMeshRenderer>();
                    correction.data = original.data;
                    correction.correctionEnabled = original.correctionEnabled;
                    correction.updateSurfaceDirections = original.updateSurfaceDirections;
                    correction.enabled = original.enabled;
                    corrections.Add(correction);
                }
            }

            void BuildBoneDefinitions()
            {
                var set = new HashSet<Transform>();
                foreach (var smr in renderers) foreach(var bone in smr.bones)
                    for (var t=bone;t!=null && t!=Root.transform;t=t.parent) set.Add(t);
                bones.AddRange(set.OrderBy(t=>Depth(t)).ThenBy(t=>PathOf(Root.transform,t),StringComparer.Ordinal));
                Require(bones.Select(b=>PathOf(Root.transform,b)).Distinct().Count()==bones.Count, "Duplicate bone paths cannot be transferred safely.");
                var humanRoles=new Dictionary<Transform,string>();
                if(Animator.isHuman)
                    for(int i=0;i<(int)HumanBodyBones.LastBone;i++)
                    {var t=Animator.GetBoneTransform((HumanBodyBones)i);if(t!=null)humanRoles[t]=((HumanBodyBones)i).ToString();}
                Bones = bones.Select(t=>new Bone { name=t.name, path=PathOf(Root.transform,t), parent=bones.IndexOf(t.parent),
                    rest=Rows(bindRest.TryGetValue(t,out var bind)?bind:initialRootInverse*t.localToWorldMatrix),
                    humanRole=humanRoles.TryGetValue(t,out var role)?role:"",weighted=weightedBones.Contains(t),
                    restSource=bindRest.ContainsKey(t)?"bindpose":"hierarchy" }).ToArray();
            }

            MeshDefinition[] BuildMeshDefinitions()
            {
                return renderers.Select(smr=>new MeshDefinition {name=smr.name,path=PathOf(Root.transform,smr.transform),
                    vertexCount=smr.sharedMesh.vertexCount, sourceIds=SourceIds(smr.sharedMesh),
                    restPositions=smr.sharedMesh.vertices.Select(v=>(initialRootInverse*smr.transform.localToWorldMatrix).MultiplyPoint3x4(v)).ToArray() }).ToArray();
            }

            static int[] SourceIds(Mesh mesh)
            {
                // Character Designer's export-only ID UV survives FBX reordering and seam splitting.
                for(int channel=0;channel<8;channel++)
                {
                    var uv=new List<Vector2>(); mesh.GetUVs(channel,uv);
                    if(uv.Count!=mesh.vertexCount || uv.Any(p=>Mathf.Abs(p.y-.375f)>1e-5f || p.x<1 || Mathf.Abs(p.x-Mathf.Round(p.x))>1e-4f)) continue;
                    return uv.Select(p=>Mathf.RoundToInt(p.x)-1).ToArray();
                }
                return Array.Empty<int>();
            }

            public Frame Sample(float time, bool meshEvidence=false)
            {
                Require(!skeletalOnly || !meshEvidence, "Skeletal-only sampling cannot produce mesh evidence.");
                // Transfer/evidence sampling remains strict, including on an interactive instance.
                EvaluatePose(time, false);
                var frame=new Frame {time=Time,root=Rows(initialRootInverse*Root.transform.localToWorldMatrix),
                    poses=bones.Select(t=>new Pose {matrix=Rows(initialRootInverse*t.localToWorldMatrix)}).ToArray(),
                    meshes=meshEvidence?SampleMeshes():Array.Empty<MeshPose>()};
                // The clone has no Rig components. Capture parent-space context after the base sample,
                // then evaluate only the Target clip's declared local curves; never solve/bake the composed Rig.
                if (riggingCapture != null) frame.riggingSample = riggingCapture.Sample(Root, initialRootInverse, Time);
                UpdateBounds();
                return frame;
            }

            // Interactive viewers need the pose, without allocating an export frame for every tick.
            public void SamplePose(float time)
            {
                EvaluatePose(time, allowForearmFallback);
                UpdateBounds();
            }

            void EvaluatePose(float time, bool allowFallback)
            {
                CorrectionWarning = null;
                Require(!disposed && Root!=null, "The animation preview is closed.");
                Require(IsFinite(time) && time>=0 && time<=Clip.length+.00001f, "Preview time is outside the clip.");
                if(graph.IsValid()) graph.Destroy();
                foreach(var pose in rest) pose.Restore();
                foreach(var pair in shapeRest) for(int i=0;i<pair.Value.Length;i++) pair.Key.SetBlendShapeWeight(i,pair.Value[i]);
                Animator.Rebind();
                // Rebind may restore the Avatar T-pose. Preserve the source hierarchy baseline;
                // actual bound rest matrices above always come from bindposes, never this T-pose.
                foreach(var pose in rest) pose.Restore();
                graph=PlayableGraph.Create("Character Designer Animation Preview");
                graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);
                var playable=AnimationClipPlayable.Create(graph,Clip);
                playable.SetApplyFootIK(true); playable.SetApplyPlayableIK(false);
                playable.SetTime(0);
                var output=AnimationPlayableOutput.Create(graph,"Character",Animator); output.SetSourcePlayable(playable);
                graph.Play(); graph.Evaluate(0);
                if(time>0) graph.Evaluate(time);
                foreach(var correction in corrections)
                {
                    if(!correction.enabled || correction.ApplyNow(out string error)) continue;
                    // ApplyNow has already restored ordinary skinning on failure.
                    if(!allowFallback) Require(false,error);
                    CorrectionWarning ??= error;
                }
                Time=Mathf.Min(time,Clip.length);
            }

            void UpdateBounds()
            {
                bool first=true;
                foreach(var renderer in renderers) {if(first) {Bounds=renderer.bounds;first=false;} else {var b=Bounds;b.Encapsulate(renderer.bounds);Bounds=b;}}
                unchecked { PoseVersion++; }
            }

            MeshPose[] SampleMeshes()
            {
                var result=new MeshPose[renderers.Count];
                for(int i=0;i<renderers.Count;i++)
                {
                    var smr=renderers[i]; smr.BakeMesh(baked[i],false);
                    var matrix=initialRootInverse*smr.transform.localToWorldMatrix;
                    result[i]=new MeshPose {name=smr.name,positions=baked[i].vertices.Select(matrix.MultiplyPoint3x4).ToArray()};
                }
                bool[] enabled=corrections.Select(c=>c.correctionEnabled).ToArray();
                try
                {
                    foreach(var correction in corrections)
                    {
                        correction.correctionEnabled=false;
                        if(correction.enabled)Require(correction.ApplyNow(out string error),error);
                    }
                    for(int i=0;i<renderers.Count;i++)
                    {
                        var smr=renderers[i];smr.BakeMesh(baked[i],false);
                        var matrix=initialRootInverse*smr.transform.localToWorldMatrix;
                        result[i].uncorrectedPositions=baked[i].vertices.Select(matrix.MultiplyPoint3x4).ToArray();
                        result[i].correctionDelta=new Vector3[result[i].positions.Length];
                        for(int v=0;v<result[i].positions.Length;v++)
                            result[i].correctionDelta[v]=result[i].positions[v]-result[i].uncorrectedPositions[v];
                    }
                }
                finally
                {
                    for(int i=0;i<corrections.Count;i++)
                    {
                        corrections[i].correctionEnabled=enabled[i];
                        if(corrections[i].enabled)Require(corrections[i].ApplyNow(out string error),error);
                    }
                }
                return result;
            }

            public Document Capture(bool meshEvidence=true)
            {
                var capture=BeginCapture(meshEvidence);
                double nextProgress=0;
                try
                {
                    while(!capture.Finished)
                    {
                        double now=EditorApplication.timeSinceStartup;
                        if(now>=nextProgress)
                        {
                            nextProgress=now+.1;
                            if(EditorUtility.DisplayCancelableProgressBar("Character Designer","Sampling "+Clip.name,(float)capture.Completed/capture.Total))
                                throw new OperationCanceledException("Animation export cancelled; previous handoff files were kept.");
                        }
                        capture.Step(10);
                    }
                    if(EditorUtility.DisplayCancelableProgressBar("Character Designer","Sampling "+Clip.name,1))
                        throw new OperationCanceledException("Animation export cancelled; previous handoff files were kept.");
                    return capture.ToDocument();
                }
                finally { EditorUtility.ClearProgressBar(); }
            }

            internal CaptureOperation BeginCapture(bool meshEvidence) => new CaptureOperation(this,meshEvidence);

            internal sealed class CaptureOperation
            {
                readonly Preview preview;
                readonly bool meshEvidence;
                readonly List<float> times=new();
                readonly Frame[] frames;
                internal int Completed { get; private set; }
                internal int Total => frames.Length;
                internal bool Finished => Completed==Total;
                internal CaptureOperation(Preview preview,bool meshEvidence)
                {
                    Require(!preview.skeletalOnly || !meshEvidence, "Skeletal-only sampling cannot produce mesh evidence.");
                    this.preview=preview; this.meshEvidence=meshEvidence;
                    if(meshEvidence && preview.meshDefinitions==null)preview.meshDefinitions=preview.BuildMeshDefinitions();
                    for(int i=0;i/60f<preview.Clip.length-.000001f;i++)times.Add(i/60f);
                    times.Add(preview.Clip.length); frames=new Frame[times.Count];
                }
                internal void Step(double milliseconds)
                {
                    Require(milliseconds>0 && !double.IsNaN(milliseconds) && !double.IsInfinity(milliseconds), "Choose a finite positive sampling budget.");
                    double budget=Math.Min(milliseconds,10);
                    var clock=System.Diagnostics.Stopwatch.StartNew();
                    while(!Finished)
                    {
                        int i=Completed;
                        if(CancelSamplingForTests?.Invoke(i) ?? false)
                            throw new OperationCanceledException("Animation export cancelled; previous handoff files were kept.");
                        bool evidence=meshEvidence && (i==0 || i==Total-1 || i==Mathf.RoundToInt((Total-1)*.25f) || i==Mathf.RoundToInt((Total-1)*.5f) || i==Mathf.RoundToInt((Total-1)*.75f));
                        frames[i]=preview.Sample(times[i],evidence); Completed++;
                        // A native sample is indivisible; do not start another after the budget.
                        if(clock.Elapsed.TotalMilliseconds>=budget)break;
                    }
                }
                internal Document ToDocument()
                {
                    Require(Finished,"Finish every authored frame before building the packet.");
                    return preview.DocumentFor(frames,meshEvidence);
                }
            }

            Document DocumentFor(Frame[] frames,bool meshEvidence)
            {
                AssetDatabase.TryGetGUIDAndLocalFileIdentifier(sourceClip,out string clipGuid,out long clipId);
                return new Document { targetName=source.name,targetAssetPath=AssetDatabase.GetAssetPath(source),
                    projectId=PlayerSettings.productGUID.ToString(),sourceGuid=AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(source)),sourceName=source.name,
                    targetGuid=AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(source)),clipName=sourceClip.name,
                    clipAssetPath=AssetDatabase.GetAssetPath(sourceClip),clipGuid=clipGuid,clipLocalId=clipId,
                    exportedUtc=DateTime.UtcNow.ToString("O"),duration=Clip.length,loopTime=AnimationUtility.GetAnimationClipSettings(Clip).loopTime,
                    humanoid=Clip.humanMotion,bones=Bones,frames=frames,meshes=meshEvidence?meshDefinitions:Array.Empty<MeshDefinition>(),
                    rigging=riggingCapture?.Finish(frames.Select(frame=>frame.riggingSample).ToArray()) };
            }

            public void Dispose()
            {
                if(disposed) return; disposed=true;
                EditorUtility.ClearProgressBar();
                if(graph.IsValid()) graph.Destroy();
                if(Root!=null) Object.DestroyImmediate(Root);
                foreach(var mesh in readableMeshes.Values) if(mesh!=null) Object.DestroyImmediate(mesh);
                readableMeshes.Clear();
                foreach(var mesh in baked) if(mesh!=null) Object.DestroyImmediate(mesh);
                if(Clip!=null) Object.DestroyImmediate(Clip);
                if(scene.IsValid()) EditorSceneManager.ClosePreviewScene(scene);
                Root=null; Animator=null; Clip=null;
            }
        }

        public static string PathOf(Transform root,Transform item)
        { var parts=new List<string>();for(var t=item;t!=null && t!=root;t=t.parent)parts.Add(t.name);parts.Reverse();return string.Join("/",parts); }
        static int Depth(Transform t) {int n=0;while(t.parent!=null){n++;t=t.parent;}return n;}
        static string SafeName(string name) {foreach(char c in Path.GetInvalidFileNameChars())name=name.Replace(c,'_');return name;}
        public static float[] Rows(Matrix4x4 matrix) {var values=new float[16];for(int r=0;r<4;r++)for(int c=0;c<4;c++)values[r*4+c]=matrix[r,c];return values;}
        public static Matrix4x4 Matrix(float[] values) {Require(values!=null && values.Length==16,"Invalid matrix.");var m=new Matrix4x4();for(int r=0;r<4;r++)for(int c=0;c<4;c++)m[r,c]=values[r*4+c];return m;}
        public static float MatrixError(Matrix4x4 a,Matrix4x4 b) {float e=0;for(int i=0;i<16;i++)e=Mathf.Max(e,Mathf.Abs(a[i]-b[i]));return e;}
        static bool IsFinite(float x)=>!float.IsNaN(x)&&!float.IsInfinity(x);
        static void ValidateMatrix(Matrix4x4 matrix,string label) {for(int i=0;i<16;i++)Require(IsFinite(matrix[i]),label+": non-finite bind matrix.");Require(Mathf.Abs(matrix.determinant)>1e-9f,label+": singular bind matrix.");}
        internal static void Require(bool value,string message) {if(!value)throw new InvalidOperationException(message);}
    }
}
