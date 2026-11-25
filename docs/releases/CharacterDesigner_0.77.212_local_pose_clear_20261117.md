# Character Designer 0.77.212

在 X 裡選取 Cosha 的本機工作 Action 和姿勢控制器，即可在 3D 視圖按 **Alt+R** 清除旋轉。已連接 Unity 且本地動作清單為空時也可以使用。Auto Key 開啟時會記錄所選控制器本幀的旋轉。[本機 Action 與自動插鍵的修正](<D:/MyRepository/Blender-addons-by-Randy/docs/releases/CharacterDesigner_0.77.211_local_pose_clear_20261117.md>)

本版修正了允許建立新曲線時的失敗回退。曾在 X 副本中刻意中斷清除操作，舊版回退會刪掉兩個既有的空曲線群組。更新後，以 57 個控制器、57 個既有空群組重試，正常操作只變更 208 條旋轉曲線。注入相同錯誤時，旋轉和關鍵幀完整回退。兩次測試都保留了全部 57 個群組的原有身分，5 項獨立的 Blender 曲線回退測試通過。[完整驗收](<D:/Blender/Projects/Character/X/validation/local_alt_r_20261116/acceptance212.json>)

正式 ZIP、Blender 5.2 使用者安裝和 X 專案副本均已核對，各有 283 個檔案。部署時藝術家的 Blender 已關閉。再次開啟時會從磁碟載入已安裝的版本。[磁碟部署](<D:/Blender/Projects/Character/X/validation/local_alt_r_20261116/delivery_release212.json>)
