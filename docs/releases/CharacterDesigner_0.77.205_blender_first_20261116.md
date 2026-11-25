# Character Designer 0.77.205

Cosha 的動作原稿保存在 Blender 的 `.blend`。打開 **Animation → Animations**，選本地 Action、調整關鍵幀並存檔。下次從同一個 Action 接著改。

- 已綁定的動作使用 **Send Selected to Unity**（單動作連結用 **Send to Unity**），在 Unity 勾對應槽位並點 **Update**。
- 獨立製作的動作可以直接 **Export Current Action as FBX…**，匯入 Unity 後在所需槽位 **Assign Clip**。Unity 會建立角色的工作副本，後續手動匯出的新版也要指派給該槽位。
- 要以僅存於 Unity 的動作起稿，在 Unity 槽位點 **Edit in Blender**，再從 Blender 的 **Advanced** 連接並讀取動作清單。匯入後保存編輯用的 Action。

原有預覽及播放控制保留。在獨立骨架上，原稿有 3 個打鍵時間點。輸出 FBX 並重新打開原稿後，45 個關鍵點、插值及左右手柄逐項一致。同一個 FBX 重匯 Blender 則有 17 個採樣時間點。磁碟安裝完成。現有 X 視窗最小化時未執行面板更新指令，該工作階段待啟用與核對，右側區域暫停在 Python Console。這次沒有輸出正式角色動作或變更 Unity 槽位。

[驗收與版本證據](<D:/Blender/Projects/Character/X/validation/blender_first_20261116/acceptance205.json>) · [前版驗收](<D:/Blender/Projects/Character/X/validation/animation_panel_20261116/followup/acceptance204.json>)
