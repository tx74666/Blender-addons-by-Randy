# Character Designer 0.77.215 · 固定常用動作

在 Action Editor 選單列點 **Motions**，或打開 **Character Designer → Animation → Animations**。在 **All** 點動作右邊的圖釘，再選 **Pinned**，就能只看這次要改的動作。再點一次圖釘可以取消固定，按 **All** 回到完整清單。兩個入口共用同一組選擇。

可以把主角色正在使用的本機 Action（例如 Hair Custom）和工作清單中的 Walk Forward 等動作一起固定。點已固定的本機 Action 會打開它原先的 Action 槽。主場景和對應的動作編輯場景共用工作區的已固定清單，正常儲存 `.blend` 後可在下次開啟時繼續用。

Blender 原有的 Action 資料選擇器仍可查閱整個檔案中的資料。圖釘只管理日常編輯清單。

在正式 X 的隔離副本中，以 Hair Custom、Walk Forward 與 Sword Slash 驗證了兩處入口、篩選、動作切換、儲存重開及取消固定。133 個工作清單 UI 測試通過。發行檔以 0.77.214 為基線，沿用 281 個檔案，修改 3 個，新增 3 個。Blender 5.2 和 X 的兩處安裝各核對 287 個檔案。[副本測試](<D:/Blender/Projects/Character/X/validation/pinned_motions_20261117/qa/stage02/outcome.json>) · [發行驗收與現場狀態](<D:/Blender/Projects/Character/X/validation/pinned_motions_20261117/acceptance215.json>)
