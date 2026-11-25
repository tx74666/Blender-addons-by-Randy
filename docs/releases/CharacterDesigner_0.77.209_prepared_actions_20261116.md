# Character Designer 0.77.209

Cosha 的 25 個固定動作已匯入獨立編輯檔。開啟檔案後，在 **Animation** 清單選取動作，編輯本地 Action 並保存。要更新遊戲中的對應槽位時，對所選動作執行傳回 Unity。移動這份編輯檔時，請一併保留同層的 `Dependencies` 資料夾。傳回 Unity 仍需可讀取對應的外部專案與工作區。[Cosha Actions.blend](<D:/Blender/Projects/Character/Animation/Cosha Actions/Cosha Actions.blend>)

已連上工作區的空清單會顯示 Unity 已準備的動作數、固定槽位數與本機動作數。這次在新版重新連接後，可看到已準備 29 個動作，其中 25 個是角色固定槽位。舊版已緩存的清單可顯示已準備總數，重新連接才有各列的槽位資料。在無骨架的新檔案可選 **Import Prepared Motions** 匯入全部已準備動作。本次交付的獨立檔僅收錄 25 個固定槽位。[面板驗證](<D:/Blender/Projects/Character/X/validation/edit_selected_20261116/native209.json>)

已保存的 X 中，Main Rig 保留的恢復紀錄缺少對應 Blender 資料塊。該場合會顯示獨立檔編輯入口的說明，檔中既有工作及恢復紀錄維持原狀。先前在新檔匯入 `Idle` 時，500,000 個鍵的控制器轉換上限也會套到沿用原生 FK 動作的路徑。新版讓原生動作保留全部時間點和曲線，控制器轉換仍有輸入鍵數上限。177 項相關測試通過。[原始失敗](<D:/Blender/Projects/Character/X/validation/edit_selected_20261116/library_a03.jsonl>) · [驗收與涵蓋範圍](<D:/Blender/Projects/Character/X/validation/edit_selected_20261116/acceptance209.json>)

正式安裝的 0.77.209 已在獨立 Blender 5.2 重開交付檔，核對 25 個本機 Action 與對應槽位。`Idle`、`Walk Forward` 和 `Sword Slash` 切幀有實際骨骼移動，傳回按鈕可用。[重開測試](<D:/Blender/Projects/Character/X/validation/edit_selected_20261116/library_reopen_c209a.json>)

完整投影從 0.77.208 延伸，283 個檔案有 278 個逐位相同。ZIP、Blender 5.2 使用者安裝與 X 專案插件均已核對。這次僅更新磁碟插件，開啟中的 X 仍使用原先載入的程式。[雙目標部署](<D:/Blender/Projects/Character/X/validation/edit_selected_20261116/delivery_final209.json>) · [前版說明](<D:/MyRepository/Blender-addons-by-Randy/docs/releases/CharacterDesigner_0.77.208_limb_reset_20261116.md>)
