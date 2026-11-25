# Character Designer 0.77.201 · 用 Alt G 和 Alt R 還原整身

在 Pose Mode 關閉自動打幀，選好整身控制器，同一幀內連續按 Alt G 和 Alt R，各一次，順序任意。第二鍵會一併清除縮放並執行與 Reset Selected Chains 相同的 Original Bones 還原。單鍵或局部骨骼的操作維持原有語意。

舊版在 X 的第 12 幀重現了等待第三鍵的情況。左腕原有縮放約 0.961，兩鍵後左腕中心仍偏離原位約 11.9 毫米。新版兩種按法以實際的 Blender 視窗事件驗證，第二鍵後 13,134 個身體頂點與按鈕結果逐點相同，再按一次也沒有額外位移。開關 Armature 的最大頂點差為 0.000477 毫米，與先前的 Blender 原生 Rest 基準同一量級。撤銷和重做、加按 Alt S 的順序、切換到下一幀均在副本核對。既有 Stable IK、FK 切換測量見上一版驗收。

Blender 5.2 與 X 的兩處安裝各核對 281 檔。現場核對時，已開啟的 X 正使用 0.77.201，還原模組按需載入。程式核對前後，第 12 幀、姿勢、骨骼選取及 Action 綁定一致。場景與偏好均未儲存。

## 驗證資料

- [body](<D:/Blender/Projects/Character/X/validation/two_shortcuts_20261116/body01.json>)
- [direct_200](<D:/Blender/Projects/Character/X/validation/two_shortcuts_20261116/gui/RG200a.json>)
- [RG](<D:/Blender/Projects/Character/X/validation/two_shortcuts_20261116/gui/RG201a.json>)
- [GR](<D:/Blender/Projects/Character/X/validation/two_shortcuts_20261116/gui/GR201a.json>)
- [cold](<D:/Blender/Projects/Character/X/validation/two_shortcuts_20261116/live/cold01.json>)
- [warm](<D:/Blender/Projects/Character/X/validation/two_shortcuts_20261116/live/warm01.json>)
- [delivery](<D:/Blender/Projects/Character/X/validation/two_shortcuts_20261116/deliveryfinal01.json>)
- [artist](<D:/Blender/Projects/Character/X/validation/two_shortcuts_20261116/live/artist_final01.json>)

[驗收記錄](<D:/Blender/Projects/Character/X/validation/two_shortcuts_20261116/acceptance201.json>)
