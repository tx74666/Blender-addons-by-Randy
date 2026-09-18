using System;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using Object=UnityEngine.Object;

namespace CharacterDesigner.Unity.Editor
{
    /// <summary>The window and isolated visual checks share this exact camera/material render path.</summary>
    public sealed class CharacterAnimationPreviewRenderer : IDisposable
    {
        readonly CharacterAnimationTransfer.Preview preview;
        readonly Camera camera;
        readonly GameObject[] lights;
        RenderTexture texture;
        float yaw=20, pitch=4, distance=1.8f;
        public void Orbit(Vector2 delta)
        {yaw+=delta.x*.5f;pitch=Mathf.Clamp(pitch+delta.y*.5f,-80,80);}
        public void Zoom(float delta)
        {distance=Mathf.Clamp(distance*Mathf.Exp(delta*.07f),.45f,4f);}
        public CharacterAnimationPreviewRenderer(CharacterAnimationTransfer.Preview preview)
        {
            this.preview=preview;
            var obj=new GameObject("Character Designer Preview Camera") {hideFlags=HideFlags.HideAndDontSave};
            SceneManager.MoveGameObjectToScene(obj,preview.Root.scene);
            camera=obj.AddComponent<Camera>();camera.enabled=false;camera.cameraType=CameraType.Preview;
            camera.clearFlags=CameraClearFlags.SolidColor;camera.backgroundColor=new Color(.15f,.16f,.18f);
            camera.nearClipPlane=.01f;camera.farClipPlane=100;camera.fieldOfView=32;
            camera.scene=preview.Root.scene;
            camera.overrideSceneCullingMask=EditorSceneManager.GetSceneCullingMask(preview.Root.scene);
            lights=new GameObject[2];
            for(int i=0;i<lights.Length;i++)
            {
                var lightObject=new GameObject("Preview Light") {hideFlags=HideFlags.HideAndDontSave};lights[i]=lightObject;
                SceneManager.MoveGameObjectToScene(lightObject,preview.Root.scene);
                var light=lightObject.AddComponent<Light>();light.type=LightType.Directional;light.intensity=i==0?1.3f:.6f;
                lightObject.transform.rotation=Quaternion.Euler(35,i==0?-30:140,0);
            }
        }
        public RenderTexture Render(int width,int height)
        {
            width=Mathf.Clamp(width,64,2048);height=Mathf.Clamp(height,64,2048);
            if(texture==null || texture.width!=width || texture.height!=height)
            {
                ReleaseTexture();texture=new RenderTexture(width,height,24){hideFlags=HideFlags.HideAndDontSave};texture.Create();
            }
            var bounds=preview.Bounds;
            Vector3 direction=Quaternion.Euler(pitch,yaw,0)*Vector3.forward;
            camera.transform.position=bounds.center+direction*Mathf.Max(.5f,bounds.size.magnitude*distance);
            camera.transform.LookAt(bounds.center);camera.targetTexture=texture;
            if(GraphicsSettings.currentRenderPipeline!=null)
            {
                var request=new RenderPipeline.StandardRequest {destination=texture};
                CharacterAnimationTransfer.Require(RenderPipeline.SupportsRenderRequest(camera,request),
                    "The active render pipeline does not support isolated preview rendering.");
                RenderPipeline.SubmitRenderRequest(camera,request);
            }
            else camera.Render();
            return texture;
        }
        public int CapturePng(string path)
        {
            var rendered=Render(640,800);
            var previous=RenderTexture.active;
            var image=new Texture2D(rendered.width,rendered.height,TextureFormat.RGBA32,false);
            try
            {
                RenderTexture.active=rendered;image.ReadPixels(new Rect(0,0,rendered.width,rendered.height),0,0);image.Apply();
                var pixels=image.GetPixels32();var background=pixels[0];int foreground=0;
                foreach(var p in pixels)if(Math.Abs(p.r-background.r)+Math.Abs(p.g-background.g)+Math.Abs(p.b-background.b)>30)foreground++;
                Directory.CreateDirectory(Path.GetDirectoryName(path));File.WriteAllBytes(path,image.EncodeToPNG());return foreground;
            }
            finally{RenderTexture.active=previous;Object.DestroyImmediate(image);}
        }
        void ReleaseTexture(){if(texture!=null){texture.Release();Object.DestroyImmediate(texture);texture=null;}}
        public void Dispose()
        {
            ReleaseTexture();if(camera!=null)Object.DestroyImmediate(camera.gameObject);
            foreach(var light in lights)if(light!=null)Object.DestroyImmediate(light);
        }
    }
}
