using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace CharacterDesigner.Unity.Editor
{
    public sealed class CharacterAnimationWindow : EditorWindow
    {
        [SerializeField] GameObject target;
        [SerializeField] AnimationClip clip;
        [SerializeField] string folder;
        [SerializeField] RuntimeAnimatorController controller;
        [SerializeField] UnityEngine.Object resourceScope;
        [SerializeField] string collectionName="Motion Collection";
        [Serializable] sealed class Entry { public AnimationClip clip; public bool selected=true; }
        [SerializeField] List<Entry> entries=new();
        bool sending;
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
            target=(GameObject)EditorGUILayout.ObjectField("Motion Source",target,typeof(GameObject),false);
            controller=(RuntimeAnimatorController)EditorGUILayout.ObjectField("Controller (optional)",controller,typeof(RuntimeAnimatorController),false);
            resourceScope=EditorGUILayout.ObjectField("Resources (optional)",resourceScope,typeof(UnityEngine.Object),false);
            if(EditorGUI.EndChangeCheck()) {ClosePreview();time=0;entries.Clear();clip=null;}
            collectionName=EditorGUILayout.TextField("Collection",collectionName);
            using(new EditorGUI.DisabledScope(target==null || sending || EditorApplication.isPlayingOrWillChangePlaymode))
                if(GUILayout.Button("Collect Actions"))Run(()=>
                { entries=CharacterAnimationTransfer.Collect(target,controller,resourceScope).Select(c=>new Entry{clip=c}).ToList();status=entries.Count+" source actions. Select which to send."; });
            using(new EditorGUILayout.HorizontalScope())
            {
                if(GUILayout.Button("All"))foreach(var entry in entries)entry.selected=true;
                if(GUILayout.Button("None"))foreach(var entry in entries)entry.selected=false;
            }
            foreach(var entry in entries)
            {
                if(entry.clip==null)continue;
                using(new EditorGUILayout.HorizontalScope())
                {
                    entry.selected=EditorGUILayout.Toggle(entry.selected,GUILayout.Width(20));
                    EditorGUILayout.ObjectField(entry.clip,typeof(AnimationClip),false);
                    if(GUILayout.Button("Preview",GUILayout.Width(70)))Run(()=>
                    {ClosePreview();clip=entry.clip;preview=new CharacterAnimationTransfer.Preview(target,clip);time=0;});
                }
            }
            EditorGUI.BeginChangeCheck();
            clip=(AnimationClip)EditorGUILayout.ObjectField("Preview Action",clip,typeof(AnimationClip),false);
            if(EditorGUI.EndChangeCheck()){ClosePreview();time=0;}
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
            using(new EditorGUI.DisabledScope(target==null || !entries.Any(e=>e.selected && e.clip!=null) || sending || EditorApplication.isPlayingOrWillChangePlaymode))
                if(GUILayout.Button(sending?"Sending…":"Send Selected to Blender"))
                {
                    ClosePreview();sending=true;status="Preparing selected actions… Cancel in the progress dialog to stop.";
                    var source=target;var selected=entries.Where(e=>e.selected).Select(e=>e.clip).ToArray();
                    string outputFolder=folder, outputName=collectionName;
                    EditorApplication.delayCall+=()=>
                    {
                        if(this==null)return;
                        try {Run(()=>{string path=CharacterAnimationTransfer.ExportCollection(source,selected,outputFolder,outputName);
                            status="Sent "+selected.Length+" actions. Blender → Animation → Read Exchange, then Import Selected. Import does not start playback.";});}
                        finally {sending=false;Repaint();}
                    };
                }
            EditorGUILayout.HelpBox(status,MessageType.Info);
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
