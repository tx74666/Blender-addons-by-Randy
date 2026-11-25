# Character Designer 0.77.206

在 Pose Mode 選一隻手腕 IK 控制器，從 Animation 面板按 **Match Wrist to Edit Pose**，再把游標移到 3D 視窗按 **I**。手部會對準 Original Bones 的編輯朝向，手腕位置沿用當前姿勢。

若要整身恢復 A 字，在關閉 Auto Key 時選取全身可見控制器，按 **Reset Body to Edit Pose**，再於 3D 視窗插鍵。Blender 的 Alt G、Alt R、Alt S 各自處理所選骨骼的本地位置、旋轉與縮放。

保存的 X 已有控制器工作動作。第 26 幀的左、右手腕在本地旋轉歸零後，手骨實際方向離編輯姿態分別約 87°、46°。先前全身重置的求解輸入在視圖刷新時被動畫重新取值，Cosha 的最大頂點偏差達 58.85 毫米。

在私有 X 使用原生 3D 視窗按鍵驗證。全身重置對比關閉 Armature 的網格最大偏差為 0.000493 毫米。與插鍵前的姿勢相比，保存重開差 0.000000 毫米，返回第 26 幀差 0.000359 毫米。與既有動作對比的其他取樣幀見驗收。已在開啟的 X 載入並核對姿勢、選取與 Action。

[完整驗收與測量](<D:/Blender/Projects/Character/X/validation/frame26_pose_20261116/acceptance206.json>) · [完整套件投影](<D:/Blender/Projects/Character/X/validation/frame26_pose_20261116/stage206_01/projection.json>) · [前版驗收](<D:/Blender/Projects/Character/X/validation/blender_first_20261116/acceptance205.json>)
