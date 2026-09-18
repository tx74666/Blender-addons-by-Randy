using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
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
            public long clipLocalId;
            public float duration, sampleRate = 60;
            public bool loopTime, humanoid, applyFootIK = true;
            public Bone[] bones;
            public Frame[] frames;
            public MeshDefinition[] meshes;
        }
        [Serializable] public sealed class Bone
        {
            public string name, path, restSource;
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
        {
            Require(!string.IsNullOrWhiteSpace(folder), "Choose the Blender handoff folder.");
            Document document;
            using (var preview = new Preview(target, clip)) document = preview.Capture(meshEvidence);
            string targetLabel = target.name.Replace(".Player", "").Replace(".Runtime", "");
            string path = Path.Combine(Path.GetFullPath(folder), SafeName(targetLabel + "_" + clip.name) + ".cdanim.json");
            // Finish and validate all sampling before publishing either file.
            string json = JsonUtility.ToJson(document, false);
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
            EditorPrefs.SetString(FolderPreference, Path.GetFullPath(folder));
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
            readonly GameObject source;
            readonly AnimationClip sourceClip;
            readonly Scene scene;
            readonly Dictionary<Transform, Transform> clones = new();
            readonly List<RestTransform> rest = new();
            readonly List<SkinnedMeshRenderer> renderers = new();
            readonly List<ForearmCorrection> corrections = new();
            readonly List<Mesh> baked = new();
            readonly List<Transform> bones = new();
            readonly Dictionary<Transform, Matrix4x4> bindRest = new();
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

            public Preview(GameObject target, AnimationClip clip)
            {
                Require(!EditorApplication.isPlayingOrWillChangePlaymode, "Stop Play Mode before previewing an animation.");
                Require(target != null && EditorUtility.IsPersistent(target), "Choose a character prefab asset.");
                Require(clip != null && EditorUtility.IsPersistent(clip), "Choose an existing animation clip asset.");
                source = target; sourceClip = clip;
                var sourceAnimator = target.GetComponentsInChildren<Animator>(true)
                    .FirstOrDefault(a => a.avatar != null && (!clip.humanMotion || a.avatar.isHuman));
                Require(sourceAnimator != null, "The target needs an Animator with an existing Avatar. Select its configured player prefab.");
                Require(sourceAnimator.avatar.isValid && (!clip.humanMotion || sourceAnimator.avatar.isHuman),
                    "The selected clip requires a valid matching Humanoid Avatar.");
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
                    CloneCorrections();
                    BuildBoneDefinitions();
                    meshDefinitions = BuildMeshDefinitions();
                    Sample(0);
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
                    Require(original.sharedMesh != null && original.sharedMesh.isReadable, original.name + ": the mesh must be readable for deformation preview.");
                    var smr = clones[original.transform].gameObject.AddComponent<SkinnedMeshRenderer>();
                    smr.sharedMesh = original.sharedMesh;
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
                Bones = bones.Select(t=>new Bone { name=t.name, path=PathOf(Root.transform,t), parent=bones.IndexOf(t.parent),
                    rest=Rows(bindRest.TryGetValue(t,out var bind)?bind:initialRootInverse*t.localToWorldMatrix),
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
                    if(correction.enabled) Require(correction.ApplyNow(out string error),error);
                Time=Mathf.Min(time,Clip.length);
                var frame=new Frame {time=Time,root=Rows(initialRootInverse*Root.transform.localToWorldMatrix),
                    poses=bones.Select(t=>new Pose {matrix=Rows(initialRootInverse*t.localToWorldMatrix)}).ToArray(),
                    meshes=meshEvidence?SampleMeshes():Array.Empty<MeshPose>()};
                bool first=true;
                foreach(var renderer in renderers) {if(first) {Bounds=renderer.bounds;first=false;} else {var b=Bounds;b.Encapsulate(renderer.bounds);Bounds=b;}}
                return frame;
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
                var times=new List<float>();
                for(int i=0;i/60f<Clip.length-.000001f;i++)times.Add(i/60f);
                times.Add(Clip.length);
                int count=times.Count;
                var frames=new Frame[count];
                for(int i=0;i<count;i++)
                {
                    if((CancelSamplingForTests?.Invoke(i) ?? false) || EditorUtility.DisplayCancelableProgressBar("Character Designer","Sampling "+Clip.name,(float)i/count))
                        throw new OperationCanceledException("Animation export cancelled; previous handoff files were kept.");
                    bool evidence=meshEvidence && (i==0 || i==count-1 || i==Mathf.RoundToInt((count-1)*.25f) || i==Mathf.RoundToInt((count-1)*.5f) || i==Mathf.RoundToInt((count-1)*.75f));
                    frames[i]=Sample(times[i],evidence);
                }
                EditorUtility.ClearProgressBar();
                AssetDatabase.TryGetGUIDAndLocalFileIdentifier(sourceClip,out string clipGuid,out long clipId);
                return new Document { targetName=source.name,targetAssetPath=AssetDatabase.GetAssetPath(source),
                    targetGuid=AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(source)),clipName=sourceClip.name,
                    clipAssetPath=AssetDatabase.GetAssetPath(sourceClip),clipGuid=clipGuid,clipLocalId=clipId,
                    exportedUtc=DateTime.UtcNow.ToString("O"),duration=Clip.length,loopTime=AnimationUtility.GetAnimationClipSettings(Clip).loopTime,
                    humanoid=Clip.humanMotion,bones=Bones,frames=frames,meshes=meshDefinitions };
            }

            public void Dispose()
            {
                if(disposed) return; disposed=true;
                EditorUtility.ClearProgressBar();
                if(graph.IsValid()) graph.Destroy();
                if(Root!=null) Object.DestroyImmediate(Root);
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
