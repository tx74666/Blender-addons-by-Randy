using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace CharacterDesigner.Unity.Editor
{
    public sealed class CharacterAnimationWindow : EditorWindow
    {
        [SerializeField] GameObject target;
        [SerializeField] AnimationClip clip;
        [SerializeField] string folder;
        [SerializeField] string returnFbx;
        [SerializeField] string returnClipName;
        [SerializeField] string returnFolder="Assets";
        [SerializeField] bool returnLoop;
        [Serializable] sealed class ReturnMetadata { public bool loop; }
        CharacterAnimationTransfer.Preview preview;
        CharacterAnimationPreviewRenderer renderer;
        bool playing;
        double tick;
        float time;
        Vector2 scroll;
        string status="Choose a configured character and an existing animation clip.";

        [MenuItem("Tools/Character Designer/Animation")]
        public static void Open()
        {
            var window=GetWindow<CharacterAnimationWindow>("Character Animation");
            window.minSize=new Vector2(420,520); window.Show();
        }

        public static void OpenWithTarget(GameObject character, AnimationClip animation=null)
        {
            var window=GetWindow<CharacterAnimationWindow>("Character Animation");
            window.ClosePreview(); window.target=character; window.clip=animation;
            window.minSize=new Vector2(420,520); window.Show(); window.Focus();
        }

        void OnEnable()
        {
            folder=EditorPrefs.GetString(CharacterAnimationTransfer.FolderPreference,CharacterAnimationTransfer.DefaultFolder);
            if(target==null)
            {
                target=Selection.activeObject as GameObject;
                if(target!=null && !EditorUtility.IsPersistent(target))target=null;
                if(target==null)target=AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Prefabs/Characters/Cosha/Cosha.Player.prefab");
                if(target!=null && clip==null)clip=CharacterAnimationTransfer.Clips(target).FirstOrDefault(c=>c.name=="Walk");
            }
            EditorApplication.update+=UpdatePlayback;
            EditorApplication.playModeStateChanged+=PlayState;
        }
        void OnDisable()
        {
            EditorApplication.update-=UpdatePlayback;
            EditorApplication.playModeStateChanged-=PlayState;
            ClosePreview();
        }
        void PlayState(PlayModeStateChange state) {if(state==PlayModeStateChange.ExitingEditMode)ClosePreview();}

        void OnGUI()
        {
            using var scrolling = new EditorGUILayout.ScrollViewScope(scroll);
            scroll = scrolling.scrollPosition;
            EditorGUILayout.LabelField("Unity → Blender",EditorStyles.boldLabel);
            EditorGUI.BeginChangeCheck();
            target=(GameObject)EditorGUILayout.ObjectField("Character",target,typeof(GameObject),false);
            clip=(AnimationClip)EditorGUILayout.ObjectField("Animation",clip,typeof(AnimationClip),false);
            if(EditorGUI.EndChangeCheck()) {ClosePreview();time=0;}
            if(target!=null)
            {
                var clips=CharacterAnimationTransfer.Clips(target);
                if(clips.Length>0)
                {
                    int selected=Array.IndexOf(clips,clip);
                    EditorGUI.BeginChangeCheck();
                    int next=EditorGUILayout.Popup("Character Clips",selected,Array.ConvertAll(clips,c=>c.name));
                    if(EditorGUI.EndChangeCheck() && next>=0) {ClosePreview();clip=clips[next];time=0;}
                }
            }
            using(new EditorGUI.DisabledScope(target==null || clip==null || EditorApplication.isPlayingOrWillChangePlaymode))
                if(GUILayout.Button(preview==null?"Preview on Character":"Restart Preview")) Run(()=>{ClosePreview();preview=new CharacterAnimationTransfer.Preview(target,clip);time=0;status="Preview only. Original character and Animator are unchanged.";});
            if(preview!=null)
            {
                DrawPreview();
                using(new EditorGUILayout.HorizontalScope())
                {
                    if(GUILayout.Button(playing?"Pause":"Play",GUILayout.Width(70))) {playing=!playing;tick=EditorApplication.timeSinceStartup;}
                    if(GUILayout.Button("Restore / Close Preview",GUILayout.Width(180))) ClosePreview();
                }
                if(preview!=null)
                {
                    EditorGUI.BeginChangeCheck();
                    float next=EditorGUILayout.Slider("Time (seconds)",time,0,clip.length);
                    if(EditorGUI.EndChangeCheck()) {playing=false;Run(()=>{time=next;preview.Sample(time);});}
                }
            }
            EditorGUILayout.Space();
            using(new EditorGUILayout.HorizontalScope())
            {
                folder=EditorGUILayout.TextField("Blender Folder",folder);
                if(GUILayout.Button("…",GUILayout.Width(28)))
                {string selected=EditorUtility.OpenFolderPanel("Blender animation handoff folder",folder,"");if(!string.IsNullOrEmpty(selected))folder=selected;}
            }
            using(new EditorGUI.DisabledScope(target==null || clip==null || EditorApplication.isPlayingOrWillChangePlaymode))
                if(GUILayout.Button("Send Test Animation to Blender")) Run(()=>
                {
                    playing=false;
                    string path=CharacterAnimationTransfer.Export(target,clip,folder);
                    status="Sent "+Path.GetFileName(path)+". In Blender: Character Designer → Animation → Import Latest from Unity.";
                });
            DrawReturnImport();
            EditorGUILayout.HelpBox(status,MessageType.Info);
        }

        void DrawReturnImport()
        {
            EditorGUILayout.Space();
            EditorGUILayout.LabelField("Blender → Unity",EditorStyles.boldLabel);
            using(new EditorGUILayout.HorizontalScope())
            {
                using(new EditorGUI.DisabledScope(true))
                    EditorGUILayout.TextField("Blender Action FBX",returnFbx??"");
                if(GUILayout.Button("Browse",GUILayout.Width(65))) Run(()=>
                {
                    string initial=string.IsNullOrEmpty(returnFbx)?folder:Path.GetDirectoryName(returnFbx);
                    string selected=EditorUtility.OpenFilePanel("Choose exported Blender Action",initial??"","fbx");
                    if(string.IsNullOrEmpty(selected))return;
                    bool loop=false;
                    string metadataPath=Path.ChangeExtension(selected,".animation.json");
                    if(File.Exists(metadataPath))
                    {
                        if(new FileInfo(metadataPath).Length>1024*1024)
                            throw new InvalidOperationException("The animation export metadata is unexpectedly large.");
                        var metadata=JsonUtility.FromJson<ReturnMetadata>(File.ReadAllText(metadataPath));
                        if(metadata==null)throw new InvalidOperationException("The animation export metadata is invalid.");
                        loop=metadata.loop;
                    }
                    returnFbx=selected;returnClipName=Path.GetFileNameWithoutExtension(selected);returnLoop=loop;
                    status="Review the new animation name and Loop setting, then import for the selected character.";
                });
            }
            returnClipName=EditorGUILayout.TextField("New Animation Name",returnClipName??"");
            returnLoop=EditorGUILayout.Toggle("Loop",returnLoop);
            using(new EditorGUILayout.HorizontalScope())
            {
                returnFolder=EditorGUILayout.TextField("Save in Folder",returnFolder??"Assets");
                if(GUILayout.Button("…",GUILayout.Width(28))) Run(()=>
                {
                    string initial=AssetDatabase.IsValidFolder(returnFolder)?Path.GetFullPath(returnFolder):Application.dataPath;
                    string selected=EditorUtility.OpenFolderPanel("Choose an existing folder inside Assets",initial,"");
                    if(string.IsNullOrEmpty(selected))return;
                    string relative=FileUtil.GetProjectRelativePath(selected.Replace('\\','/'));
                    if((relative!="Assets" && !relative.StartsWith("Assets/",StringComparison.Ordinal)) || !AssetDatabase.IsValidFolder(relative))
                        throw new InvalidOperationException("Choose an existing folder inside this project's Assets.");
                    returnFolder=relative;
                });
            }
            using(new EditorGUI.DisabledScope(target==null || string.IsNullOrWhiteSpace(returnFbx) ||
                string.IsNullOrWhiteSpace(returnClipName) || EditorApplication.isPlayingOrWillChangePlaymode ||
                EditorApplication.isCompiling || EditorApplication.isUpdating))
                if(GUILayout.Button("Import Blender Action")) Run(()=>
                {
                    ClosePreview();
                    var result=CharacterAnimationReturn.Import(returnFbx,target,returnClipName,returnLoop,returnFolder);
                    clip=result.Clip;time=0;
                    status="Created "+result.clipPath+". It is selected above; use Preview on Character to inspect it. The character controller is unchanged.";
                });
        }

        void DrawPreview()
        {
            var rect=GUILayoutUtility.GetRect(64,Mathf.Clamp(position.height-310,180,650),GUILayout.ExpandWidth(true));
            var input=Event.current;
            if(renderer!=null && rect.Contains(input.mousePosition))
            {
                if(input.type==EventType.MouseDrag && input.button==0)
                {renderer.Orbit(input.delta);input.Use();Repaint();}
                else if(input.type==EventType.ScrollWheel)
                {renderer.Zoom(input.delta.y);input.Use();Repaint();}
            }
            if(Event.current.type==EventType.Repaint)
            {
                try
                {
                    renderer??=new CharacterAnimationPreviewRenderer(preview);
                    GUI.DrawTexture(rect,renderer.Render(Mathf.RoundToInt(rect.width),Mathf.RoundToInt(rect.height)),ScaleMode.ScaleToFit,false);
                }
                catch(Exception e){playing=false;status=e.Message;}
            }
        }
        void UpdatePlayback()
        {
            if(!playing || preview==null || clip==null)return;
            double now=EditorApplication.timeSinceStartup;
            float delta=(float)(now-tick);
            if(delta<1f/60f)return;
            tick=now;
            Run(()=>{time=clip.length>0?Mathf.Repeat(time+delta,clip.length):0;preview.Sample(time);});Repaint();
        }
        void Run(Action action)
        {
            try{action();}
            catch(Exception e){playing=false;status=e.Message;Debug.LogWarning("Character Designer animation: "+e.Message);}
            finally{EditorUtility.ClearProgressBar();Repaint();}
        }
        void ClosePreview()
        {
            playing=false;renderer?.Dispose();renderer=null;
            preview?.Dispose();preview=null;
            status="Preview closed. Original character and Animator are unchanged.";
        }
    }
}
