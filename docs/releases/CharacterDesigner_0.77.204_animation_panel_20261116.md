# Character Designer 0.77.204

在 Unity 的 **Cosha → 2 Animation** 對目標槽位點 **Edit in Blender**。打開 Blender 的 **Animation → Animations**，第一次點 **Connect Unity Action List** 並選擇 Unity 準備的清單。點 **Sync List**，再點一行動作即可編輯其 Custom。**Send Selected to Unity** 傳回這一條，在 Unity 的對應槽位點 **Update** 套用。舊版準備的普通動作可沿用收集與套用流程。

若 Unity 連結已歸檔，而骨架仍保留以前的工作清單記錄，面板會標出 **Archived link**，直接顯示 Blender 的 Action 和槽位選擇器。展開 Action 選擇器即可核對目前編輯的資料塊。要編輯 Unity 清單的當前副本，連上動作表後點 **Sync List**。普通單動作連結仍可用 **Open Linked Action** 回到正確動作與槽位。

姿勢與批量工具收在 **Advanced**。開啟檔案預覽時，播放和結束預覽仍顯示在主面板。

本次包含 203 的獨立姿勢快捷鍵。202 的完整驗證涵蓋 23 個面板狀態，本次在 Blender 5.2 額外重現歸檔連結、保留工作表所有權與不同當前動作，驗證 6 個狀態、35 項檢查。X 的現場見證核對了動作、槽位和姿勢。測試未把動畫送入 Unity。

[動作表介面](<D:/Blender/Projects/Character/X/validation/animation_panel_20261116/gui03/cosha_action_list.png>) · [歸檔連結介面](<D:/Blender/Projects/Character/X/validation/animation_panel_20261116/followup/gui204/archived_walk_link.png>) · [驗收](<D:/Blender/Projects/Character/X/validation/animation_panel_20261116/followup/acceptance204.json>) · [202 驗收與已知測試限制](<D:/Blender/Projects/Character/X/validation/animation_panel_20261116/acceptance202.json>)
