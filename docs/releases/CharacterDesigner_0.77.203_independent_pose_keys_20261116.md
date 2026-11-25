# Character Designer 0.77.203 · 獨立還原控制器的位置、旋轉與縮放

在 Pose Mode，Alt G、Alt R、Alt S 直接使用 Blender 的原生位置、旋轉及縮放命令。插件已移除原有的三個按鍵綁定，以及辨認整身連按順序的程式。清除位置與旋轉可保留既有縮放。手腕原有的 Edit Mode 朝向匹配仍可點選 Reset Target Rotation。整身校正至 Original Bones 可點選 Reset Selected Chains。

在 X 第 12 幀副本上以五種情境比對十五次實際按鍵與 Blender 原生命令，涵蓋兩種整身按序、個別手腕、鎖軸與姿勢鏡像、自動打幀。每一步的骨骼通道及 13,134 個身體頂點都一致。整身 Alt G 和 Alt R 後，左手腕縮放保留 0.961028，按下 Alt S 才回到 1。單獨測試整身還原按鈕時，身體網格逐點吻合先前驗收，包括切換到下一幀的結果。

Blender 5.2 與專案安裝皆已逐檔核對。開啟中的 X 已核對新程式及按鍵設定，保留目前姿勢、Action 與使用者的未儲存工作。測試採用獨立副本。上一版動畫面板的驗收與後續事項由上一版記錄接續。

[驗收與實測](<D:/Blender/Projects/Character/X/validation/independent_pose_keys_20261116/acceptance203.json>) · [上一版驗收](<D:/Blender/Projects/Character/X/validation/animation_panel_20261116/acceptance202.json>)
