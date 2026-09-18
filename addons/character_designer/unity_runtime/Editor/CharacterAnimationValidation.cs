using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace CharacterDesigner.Unity.Editor
{
    public static class CharacterAnimationValidation
    {
        [Serializable] public sealed class Result
        {
            public bool passed, avatarValid, avatarHuman, clipHuman, sceneUnchanged, sourceUnchanged;
            public string error, output, target, clip, sceneBefore, sceneAfter;
            public int bones, samples, repeats;
            public int cancellationChecks, publishFailureChecks;
            public string skinWeights;
            public string[] rendererQualities;
            public string[] previewImages;
            public int[] foregroundPixels;
            public float duration, maximumRepeatBoneError, maximumRepeatMeshError, motionDistance;
        }
        [MenuItem("Tools/Character Designer/Validation/Unity Animation (Isolated)")]
        public static void RunMenu()
        {
            var result=new Result();
            var scenes=SceneState();var selection=Selection.objects.ToArray();var active=SceneManager.GetActiveScene();
            result.sceneBefore=SceneManager.GetActiveScene().path;
            try
            {
                var target=AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Prefabs/Characters/Cosha/Cosha.Player.prefab");
                CharacterAnimationTransfer.Require(target!=null,"Cosha.Player.prefab was not found.");
                var clip=CharacterAnimationTransfer.Clips(target).FirstOrDefault(c=>c.name=="Walk");
                CharacterAnimationTransfer.Require(clip!=null,"Cosha's existing Walk clip was not found.");
                var animator=target.GetComponentInChildren<Animator>(true);
                result.skinWeights=QualitySettings.skinWeights.ToString();
                result.rendererQualities=target.GetComponentsInChildren<SkinnedMeshRenderer>(true)
                    .Select(r=>r.name+": "+r.quality).ToArray();
                result.target=AssetDatabase.GetAssetPath(target);result.clip=clip.name;
                result.avatarValid=animator!=null && animator.avatar!=null && animator.avatar.isValid;
                result.avatarHuman=animator!=null && animator.avatar!=null && animator.avatar.isHuman;result.clipHuman=clip.humanMotion;
                string sourceBefore=AssetDatabase.GetAssetDependencyHash(result.target).ToString();
                using(var preview=new CharacterAnimationTransfer.Preview(target,clip))
                {
                    result.duration=clip.length;result.bones=preview.Bones.Length;
                    var first=preview.Sample(0,true);var middle=preview.Sample(clip.length*.5f,true);
                    for(int i=0;i<first.poses.Length;i++)
                    {
                        var a=CharacterAnimationTransfer.Matrix(first.poses[i].matrix).GetColumn(3);
                        var b=CharacterAnimationTransfer.Matrix(middle.poses[i].matrix).GetColumn(3);
                        result.motionDistance=Mathf.Max(result.motionDistance,Vector3.Distance(a,b));
                    }
                    CharacterAnimationTransfer.Require(result.motionDistance>.01f,"The selected animation did not move the actual target bones.");
                    foreach(float fraction in new[]{.73f,.13f,1f,0f,.5f,.5f})
                    {
                        preview.Sample(clip.length*fraction,true);
                        var repeated=preview.Sample(clip.length*.5f,true);
                        for(int i=0;i<middle.poses.Length;i++) result.maximumRepeatBoneError=Mathf.Max(result.maximumRepeatBoneError,
                            CharacterAnimationTransfer.MatrixError(CharacterAnimationTransfer.Matrix(middle.poses[i].matrix),CharacterAnimationTransfer.Matrix(repeated.poses[i].matrix)));
                        for(int m=0;m<middle.meshes.Length;m++)for(int v=0;v<middle.meshes[m].positions.Length;v++)
                            result.maximumRepeatMeshError=Mathf.Max(result.maximumRepeatMeshError,Vector3.Distance(middle.meshes[m].positions[v],repeated.meshes[m].positions[v]));
                        result.repeats++;
                    }
                    CharacterAnimationTransfer.Require(result.maximumRepeatBoneError<2e-5f && result.maximumRepeatMeshError<2e-5f,"Repeated seeking changed the same-time pose or skin.");
                    result.previewImages=new string[3];result.foregroundPixels=new int[3];
                    using(var renderer=new CharacterAnimationPreviewRenderer(preview))
                    {
                        for(int i=0;i<3;i++)
                        {
                            preview.Sample(clip.length*i*.5f);
                            string image=Path.GetFullPath("Temp/CDAnimation/Cosha_Walk_"+i+".png");
                            result.previewImages[i]=image;result.foregroundPixels[i]=renderer.CapturePng(image);
                            CharacterAnimationTransfer.Require(result.foregroundPixels[i]>1000,"The isolated preview image contains no visible character.");
                        }
                    }
                }
                ValidateFailedExport(target, clip, result);
                result.output=CharacterAnimationTransfer.Export(target,clip,CharacterAnimationTransfer.DefaultFolder,true);
                var exported=JsonUtility.FromJson<CharacterAnimationTransfer.Document>(File.ReadAllText(result.output));
                result.samples=exported.frames.Length;
                CharacterAnimationTransfer.Require(exported.frames[0].time==0 && Mathf.Abs(exported.frames[^1].time-clip.length)<1e-6f,"Exported time range is incorrect.");
                result.sourceUnchanged=sourceBefore==AssetDatabase.GetAssetDependencyHash(result.target).ToString();
                CharacterAnimationTransfer.Require(result.sourceUnchanged,"The source character dependencies changed.");
                result.passed=true;
            }
            catch(Exception e){result.error=e.ToString();result.passed=false;}
            finally
            {
                EditorUtility.ClearProgressBar();
                result.sceneAfter=SceneManager.GetActiveScene().path;
                result.sceneUnchanged=scenes==SceneState() && active==SceneManager.GetActiveScene() && selection.SequenceEqual(Selection.objects);
                if(!result.sceneUnchanged){result.passed=false;result.error+="\nScene/dirty state/selection changed during isolated preview.";}
                Directory.CreateDirectory("Temp");File.WriteAllText("Temp/CharacterDesignerAnimationValidation.json",JsonUtility.ToJson(result,true));
                if(result.passed)Debug.Log("Character Designer animation validation passed: "+result.output);
                else Debug.LogError("Character Designer animation validation failed: "+result.error);
            }
        }
        static void ValidateFailedExport(GameObject target, AnimationClip clip, Result result)
        {
            string root=Path.GetFullPath(Path.Combine("Temp","CDAnimation","FailureValidation",Guid.NewGuid().ToString("N")));
            string preference=EditorPrefs.GetString(CharacterAnimationTransfer.FolderPreference,"");
            string name=target.name.Replace(".Player","").Replace(".Runtime","")+"_"+clip.name+".cdanim.json";
            Directory.CreateDirectory(root);
            string package=Path.Combine(root,name), latest=Path.Combine(root,CharacterAnimationTransfer.LatestFile);
            File.WriteAllText(package,"previous package");File.WriteAllText(latest,"previous manifest");
            try
            {
                CharacterAnimationTransfer.CancelSamplingForTests=i=>i==3;
                bool cancelled=false;
                try{CharacterAnimationTransfer.Export(target,clip,root,false);}
                catch(OperationCanceledException){cancelled=true;}
                CharacterAnimationTransfer.Require(cancelled && File.ReadAllText(package)=="previous package" && File.ReadAllText(latest)=="previous manifest",
                    "Cancelling sampling changed the previous handoff files.");
                CharacterAnimationTransfer.Require(Directory.GetFiles(root,"*.tmp").Length==0,"Cancellation left a temporary publication file.");
                result.cancellationChecks++;
            }
            finally{CharacterAnimationTransfer.CancelSamplingForTests=null;}
            foreach(bool hadPackage in new[]{true,false})
            {
                string folder=Path.Combine(root,hadPackage?"replace-existing":"new-package");Directory.CreateDirectory(folder);
                package=Path.Combine(folder,name);latest=Path.Combine(folder,CharacterAnimationTransfer.LatestFile);
                if(hadPackage)File.WriteAllText(package,"previous package");
                File.WriteAllText(latest,"previous manifest");
                bool failed=false;
                using(var locked=new FileStream(latest,FileMode.Open,FileAccess.Read,FileShare.Read))
                {
                    try{CharacterAnimationTransfer.Export(target,clip,folder,false);}
                    catch(IOException){failed=true;}
                }
                CharacterAnimationTransfer.Require(failed,"Expected locked-manifest publication failure was not reported.");
                CharacterAnimationTransfer.Require(hadPackage?File.ReadAllText(package)=="previous package":!File.Exists(package),
                    "Failed publication did not restore the original package state.");
                CharacterAnimationTransfer.Require(File.ReadAllText(latest)=="previous manifest" && Directory.GetFiles(folder,"*.tmp").Length==0,
                    "Failed publication changed the manifest or left temporary output.");
                result.publishFailureChecks++;
            }
            CharacterAnimationTransfer.Require(preference==EditorPrefs.GetString(CharacterAnimationTransfer.FolderPreference,""),
                "Cancelled or failed export changed the configured handoff folder.");
        }

        static string SceneState()
        {
            var values=new List<string>();for(int i=0;i<SceneManager.sceneCount;i++){var s=SceneManager.GetSceneAt(i);values.Add(s.handle+"|"+s.path+"|"+s.isDirty+"|"+s.isLoaded);}
            return string.Join("\n",values);
        }
    }
}
