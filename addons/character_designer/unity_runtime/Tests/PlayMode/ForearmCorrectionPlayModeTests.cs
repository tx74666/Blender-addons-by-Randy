#if UNITY_EDITOR
using System;
using System.Collections;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;
using UnityEngine.TestTools;

namespace CharacterDesigner.Unity.Tests
{
    public sealed class ForearmCorrectionPlayModeTests
    {
        [UnityTest]
        public IEnumerator AnimatorThenLateUpdateCorrectsActualMeshAndDisableRestores()
        {
            var root = new GameObject("Character Designer PlayMode Validation");
            root.SetActive(false);
            Mesh mesh = null;
            AnimationClip clip = null;
            ForearmCorrectionData data = null;
            PlayableGraph graph = default;
            try
            {
                var lower = new GameObject("Lower").transform;
                lower.SetParent(root.transform, false);
                var hand = new GameObject("Hand").transform;
                hand.SetParent(lower, false);
                hand.localPosition = Vector3.up;
                var renderer = root.AddComponent<SkinnedMeshRenderer>();
                var animator = root.AddComponent<Animator>();
                animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
                mesh = new Mesh { name = "PlayMode Correction Mesh" };
                Vector3[] points = { new Vector3(-.15f, .4f, .05f), new Vector3(.15f, .4f, .05f), new Vector3(.1f, .8f, .05f) };
                mesh.vertices = points;
                mesh.triangles = new[] { 0, 1, 2 };
                mesh.RecalculateNormals();
                Matrix4x4[] bindposes = { lower.worldToLocalMatrix * root.transform.localToWorldMatrix,
                    hand.worldToLocalMatrix * root.transform.localToWorldMatrix };
                mesh.bindposes = bindposes;
                var skin = new BoneWeight[3];
                for (int i = 0; i < skin.Length; ++i)
                    skin[i] = new BoneWeight { boneIndex0 = 0, weight0 = .7f, boneIndex1 = 1, weight1 = .3f };
                mesh.boneWeights = skin;
                renderer.sharedMesh = mesh;
                renderer.bones = new[] { lower, hand };
                renderer.rootBone = root.transform;
                renderer.quality = SkinQuality.Bone2;
                renderer.updateWhenOffscreen = true;
                data = ScriptableObject.CreateInstance<ForearmCorrectionData>();
                data.sourceMesh = mesh;
                data.sourceFingerprint = ForearmCorrection.ComputeFingerprint(mesh);
                data.vertexCount = points.Length;
                data.sides = new[] { new ForearmCorrectionData.Side { name = "L", lowerBone = 0,
                    handBone = 1, axis = Vector3.up, pivot = Vector3.up } };
                data.sources = new ForearmCorrectionData.SourceSample[3];
                data.stencils = new ForearmCorrectionData.Stencil[3];
                for (int i = 0; i < 3; ++i)
                {
                    data.sources[i] = new ForearmCorrectionData.SourceSample { point = points[i], lowerWeight = .7f,
                        handWeight = .3f, ratio = .65f, influence = 1f, side = 0 };
                    data.stencils[i] = new ForearmCorrectionData.Stencil { vertexIndex = i,
                        sourceIndices = new[] { i }, weights = new[] { 1f } };
                }
                var correction = root.AddComponent<ForearmCorrection>();
                correction.targetRenderer = renderer;
                correction.data = data;
                var observer = root.AddComponent<ForearmCorrectionLateObserver>();
                observer.correction = correction;
                observer.rendererToCheck = renderer;
                observer.lower = lower;
                observer.hand = hand;
                observer.bindposes = bindposes;
                observer.points = points;
                observer.baked = new Mesh();

                clip = new AnimationClip { name = "Character Designer Wrist Rotation Test" };
                AnimationUtility.SetEditorCurve(clip,
                    EditorCurveBinding.FloatCurve("Lower/Hand", typeof(Transform), "localEulerAnglesRaw.y"),
                    AnimationCurve.Linear(0f, 10f, 1f, 80f));
                graph = PlayableGraph.Create("Character Designer Test Animation");
                graph.SetTimeUpdateMode(DirectorUpdateMode.GameTime);
                AnimationClipPlayable playable = AnimationClipPlayable.Create(graph, clip);
                AnimationPlayableOutput output = AnimationPlayableOutput.Create(graph, "Wrist", animator);
                output.SetSourcePlayable(playable);
                root.SetActive(true);
                graph.Play();
                for (int i = 0; i < 12; ++i) yield return null;
                Assert.IsNull(correction.LastError);
                Assert.Greater(observer.sampledFrames, 3, "No frames reached the observer after correction LateUpdate.");
                Assert.Greater(observer.largestAngle, 1f, "AnimationClipPlayable did not animate the hand.");
                Assert.Less(observer.maxPositionError, 3e-5f, "The actual skinned mesh lagged or differed from this frame's animated bones.");
                Assert.Greater(observer.maxCorrectionDelta, 1e-4f, "The test never observed meaningful correction.");
                Assert.AreEqual(data.sourceFingerprint, ForearmCorrection.ComputeFingerprint(mesh), "The imported asset was changed.");

                observer.enabled = false;
                correction.enabled = false;
                Assert.AreSame(mesh, renderer.sharedMesh);
                Assert.AreEqual(SkinQuality.Bone2, renderer.quality);
                yield return null;
                Assert.AreSame(mesh, renderer.sharedMesh, "A disabled correction retook mesh ownership.");
            }
            finally
            {
                if (graph.IsValid()) graph.Destroy();
                UnityEngine.Object.DestroyImmediate(root);
                if (mesh != null) UnityEngine.Object.DestroyImmediate(mesh);
                if (clip != null) UnityEngine.Object.DestroyImmediate(clip);
                if (data != null) UnityEngine.Object.DestroyImmediate(data);
            }
        }
    }

    [DefaultExecutionOrder(21000)]
    public sealed class ForearmCorrectionLateObserver : MonoBehaviour
    {
        public ForearmCorrection correction;
        public SkinnedMeshRenderer rendererToCheck;
        public Transform lower, hand;
        public Matrix4x4[] bindposes;
        public Vector3[] points;
        public Mesh baked;
        public int sampledFrames;
        public float largestAngle, maxPositionError, maxCorrectionDelta;

        private void LateUpdate()
        {
            if (correction.LastError != null) throw new InvalidOperationException(correction.LastError);
            Matrix4x4 meshInverse = rendererToCheck.transform.worldToLocalMatrix;
            Matrix4x4 lowerMatrix = meshInverse * lower.localToWorldMatrix * bindposes[0];
            Matrix4x4 handMatrix = meshInverse * hand.localToWorldMatrix * bindposes[1];
            if (!ForearmCorrectionMath.TryTwistAngle(lowerMatrix, handMatrix, Vector3.up, out float angle, out string error))
                throw new InvalidOperationException(error);
            rendererToCheck.BakeMesh(baked);
            Vector3[] actual = baked.vertices;
            for (int i = 0; i < points.Length; ++i)
            {
                Vector3 ordinary = .7f * lowerMatrix.MultiplyPoint3x4(points[i]) + .3f * handMatrix.MultiplyPoint3x4(points[i]);
                Vector3 delta = ForearmCorrectionMath.ExtraDelta(points[i], lowerMatrix, handMatrix, Vector3.up,
                    Vector3.up, .65f, angle, .7f, .3f);
                maxPositionError = Mathf.Max(maxPositionError, (actual[i] - ordinary - delta).magnitude);
                maxCorrectionDelta = Mathf.Max(maxCorrectionDelta, delta.magnitude);
            }
            largestAngle = Mathf.Max(largestAngle, Mathf.Abs(angle * Mathf.Rad2Deg));
            ++sampledFrames;
        }

        private void OnDestroy()
        {
            if (baked != null) DestroyImmediate(baked);
        }
    }
}
#endif
