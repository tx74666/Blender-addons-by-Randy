# Character Designer 0.77.211

在 X 的 Pose Mode 選取角色控制器，滑鼠放在 3D 視圖，按 **Alt+R** 即可清除所選控制器的旋轉。Cosha 的本機工作 Action 可以在已連接 Unity、尚未匯入動作清單時繼續使用。開啟 Auto Key 時，旋轉鍵寫入當前工作 Action 的對應槽位。

0.77.210 修正了空清單下對本機 Action 的身分檢查。0.77.211 處理了多選控制器並開啟 Auto Key 時的第二個問題。Blender 的標量屬性及單分量陣列都可能在 F-Curve 使用索引 0。插件現在按原槽位、通道和影格清除重設過程中臨時產生的鍵，保留原有位置、縮放和自訂屬性曲線。連結的 Source 與封存的動作仍受保護。[0.77.210 部署紀錄](<D:/Blender/Projects/Character/X/validation/local_alt_r_20261116/delivery_release210.json>)

在 X 副本的第 25 幀，以 Cosha 的本機工作 Action 和 57 個選取控制器重現了原提示。更新後，Auto Key 關閉時實際按 Alt+R 可清除旋轉，動作曲線沒有改寫。開啟 Auto Key 的相同操作變更了 208 條旋轉曲線，9 個其他 Action 的鍵均通過比對。這兩次都核對了所選控制器的位置與縮放。另通過 15 項權限測試和 4 項 Blender 原生曲線測試。[驗收](<D:/Blender/Projects/Character/X/validation/local_alt_r_20261116/acceptance211.json>) · [允許新增旋轉曲線時的群組測試](<D:/Blender/Projects/Character/X/validation/local_alt_r_20261116/native/emptyMissingAllowNewAuto03.json>)

X 的原生快捷鍵測試包含同名的既有空群組，成功的操作保留了這些群組。另一次測試允許 Auto Key 建立新曲線，在處理過程刻意引發錯誤。Blender 在回滾、移除群組最後一條曲線時刪掉了兩個原有空群組。[底層測試](<D:/Blender/Projects/Character/X/validation/local_alt_r_20261116/channel_rollback211.stderr>) · [X 副本的故障測試](<D:/Blender/Projects/Character/X/validation/local_alt_r_20261116/native/emptyMissingAllowNewInjectAuto04.json>)

完整投影含 283 個檔案。正式 ZIP、Blender 5.2 使用者安裝與 X 專案副本均已逐檔核對。現場啟用使用獨立紀錄。[磁碟部署](<D:/Blender/Projects/Character/X/validation/local_alt_r_20261116/delivery_release211.json>) · [Cosha 動作編輯檔及前版記錄](<D:/MyRepository/Blender-addons-by-Randy/docs/releases/CharacterDesigner_0.77.209_prepared_actions_20261116.md>)
