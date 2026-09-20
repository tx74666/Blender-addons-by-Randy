using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace CharacterDesigner.Unity.Editor
{
    // Integration fixtures only: production collection code has no character names.
    public static class CharacterAnimationLibraryValidation
    {
        [Serializable] sealed class Result
        {
            public bool passed, sceneUnchanged, sourceUnchanged, duplicateStable, cancelSafe;
            public string error, manifest, source, walk, walkPath, scene, folder;
            public string[] clips, images;
            public int roles;
            public float repeatError, motion;
        }
        [MenuItem("Tools/Character Designer/Validation/Source Walk (Isolated)")]
        public static void Walk()=>Run(false);
        [MenuItem("Tools/Character Designer/Validation/Action Collection (Isolated)")]
        public static void Collection()=>Run(true);
        static string SceneState()=>string.Join("\n",Enumerable.Range(0,SceneManager.sceneCount).Select(i=>
        {var s=SceneManager.GetSceneAt(i);return s.handle+"|"+s.path+"|"+s.isDirty+"|"+s.isLoaded;}));
        static AnimationClip Clip(string guid,long localId)=>AssetDatabase.LoadAllAssetsAtPath(AssetDatabase.GUIDToAssetPath(guid))
            .OfType<AnimationClip>().Single(c=>AssetDatabase.TryGetGUIDAndLocalFileIdentifier(c,out string g,out long id) && id==localId);
        static void Run(bool batch)
        {
            var result=new Result();string before=SceneState(), pref=EditorPrefs.GetString(CharacterAnimationTransfer.FolderPreference,"");
            var active=SceneManager.GetActiveScene();var selection=Selection.objects.ToArray();
            string root=Path.GetFullPath(Path.Combine("Temp","CDAnimationLibrary",batch?"batch":"walk"));Directory.CreateDirectory(root);
            result.folder=root;result.scene=active.path;
            try
            {
                string path="Assets/Prefabs/Characters/Soldier/Soldier 4.0.prefab";
                var source=AssetDatabase.LoadAssetAtPath<GameObject>(path);
                CharacterAnimationTransfer.Require(source!=null,"Soldier fixture is missing.");
                string hash=AssetDatabase.GetAssetDependencyHash(path).ToString();result.source=path;
                var all=CharacterAnimationTransfer.Collect(source);
                // The actual 0/6/10.5 speed BlendTree referenced by Soldier's Controller.
                var walk=Clip("8269a9f8cf495034c817722ac21f309f",1657602633327794031);
                CharacterAnimationTransfer.Require(all.Contains(walk),"Walk is not referenced by the source character's actual Controller.");
                result.walk=walk.name;result.walkPath=AssetDatabase.GetAssetPath(walk);
                using(var preview=new CharacterAnimationTransfer.Preview(source,walk))
                {
                    result.roles=preview.Bones.Count(b=>!string.IsNullOrEmpty(b.humanRole));
                    var first=preview.Sample(0);var mid=preview.Sample(walk.length*.5f);
                    preview.Sample(walk.length);var repeat=preview.Sample(walk.length*.5f);
                    for(int i=0;i<first.poses.Length;i++)
                    {
                        result.repeatError=Mathf.Max(result.repeatError,CharacterAnimationTransfer.MatrixError(CharacterAnimationTransfer.Matrix(mid.poses[i].matrix),CharacterAnimationTransfer.Matrix(repeat.poses[i].matrix)));
                        result.motion=Mathf.Max(result.motion,CharacterAnimationTransfer.MatrixError(CharacterAnimationTransfer.Matrix(first.poses[i].matrix),CharacterAnimationTransfer.Matrix(mid.poses[i].matrix)));
                    }
                    CharacterAnimationTransfer.Require(result.repeatError<2e-5f && result.motion>.01f && result.roles>=15,"Walk repeat/motion/Avatar checks failed.");
                    if(!batch)
                    {
                        result.images=new string[2];
                        using(var renderer=new CharacterAnimationPreviewRenderer(preview))
                            for(int i=0;i<2;i++){preview.Sample(walk.length*i*.5f);result.images[i]=Path.Combine(root,"source_"+i+".png");renderer.CapturePng(result.images[i]);}
                    }
                }
                var clips=batch?new[]{walk,Clip("12e52e465ed793a4d801955e9f964a82",-3100369314251171874),Clip("16114d403eabb53438de032c6f0d1deb",6564411413370888346)}:new[]{walk};
                CharacterAnimationTransfer.Require(clips.All(all.Contains),"A batch fixture is not associated with the source Controller.");
                result.clips=clips.Select(c=>c.name).ToArray();
                result.manifest=CharacterAnimationTransfer.ExportCollection(source,clips,root,"Source Validation");
                if(batch)
                {
                    string manifest=File.ReadAllText(result.manifest);int count=Directory.GetFiles(root,"*.cdanim.json").Length;
                    CharacterAnimationTransfer.ExportCollection(source,clips,root,"Source Validation");
                    result.duplicateStable=manifest==File.ReadAllText(result.manifest) && count==Directory.GetFiles(root,"*.cdanim.json").Length;
                    CharacterAnimationTransfer.CancelSamplingForTests=i=>i==2;
                    bool cancelled=false;try{CharacterAnimationTransfer.ExportCollection(source,clips,root,"Source Validation");}catch(OperationCanceledException){cancelled=true;}
                    result.cancelSafe=cancelled && manifest==File.ReadAllText(result.manifest) && count==Directory.GetFiles(root,"*.cdanim.json").Length;
                    CharacterAnimationTransfer.Require(result.cancelSafe && result.duplicateStable,"Duplicate/cancellation changed existing collection output.");
                }
                result.sourceUnchanged=hash==AssetDatabase.GetAssetDependencyHash(path).ToString();
                CharacterAnimationTransfer.Require(result.sourceUnchanged,"Source asset changed.");result.passed=true;
            }
            catch(Exception e){result.error=e.ToString();}
            finally
            {
                CharacterAnimationTransfer.CancelSamplingForTests=null;EditorUtility.ClearProgressBar();
                EditorPrefs.SetString(CharacterAnimationTransfer.FolderPreference,pref);
                result.sceneUnchanged=before==SceneState() && active==SceneManager.GetActiveScene() && selection.SequenceEqual(Selection.objects);
                result.passed&=result.sceneUnchanged && string.IsNullOrEmpty(result.error);
                File.WriteAllText(Path.Combine(root,"result.json"),JsonUtility.ToJson(result,true));
                if(result.passed)Debug.Log("Character Designer source validation passed: "+result.manifest);
                else Debug.LogError("Character Designer source validation failed: "+result.error);
            }
        }
    }
}
