# Character Designer 0.77.213

正式 X 已儲存目前角色的設定。在 **Animation → Animations** 可以看到 Unity 準備好的 29 個動作名稱。25 個角色動作槽已有可編輯的本機 Custom Action。點 **Walk Forward** 就能進入同一個檔案內的 **Animation Worklist · Cosha** 編輯場景，完成後按 **Return to X** 回到角色。另有四項先前準備的請求列在同一份清單。已在另一個 Blender 5.2 行程重開正式 X，開啟 Walk Forward 的 Custom 並返回成功。[正式 X 重開驗證](<D:/Blender/Projects/Character/X/validation/current_motion_session_20261117/private/formal_reopen_live01.json>)

在副本內逐一按 **Import** 測試這四項歷史請求，第一個 **Walk_N** 和 **Great Sword Slash** 均可開啟本機 Custom 動作。歷史版本的 **Idle** 因 Unity 與 Blender 的角色尺寸不符而無法匯入。另一個 **Walk_N** 的 TwoBoneIK 未綁定來源角色原有的手臂骨鏈，匯入也被拒絕。[四項歷史動作的匯入結果](<D:/Blender/Projects/Character/X/validation/current_motion_session_20261117/private/history_buttons_short.json>)

舊檔將 Main Rig 的編輯記錄保留下來，但用來定位當時骨架和動作等資料的參照表已經缺失，因而顯示「A saved Main Rig recovery data block was removed」。現在由目前的 CoshaRig 接手工作清單，**Sync List** 可以刷新動作名稱。現場更新前後原有的 10 個動作均通過比對。正式檔重開後，Hair Custom 在原槽位保有 2,694 條曲線。[現場儲存記錄](<D:/Blender/Projects/Character/X/validation/current_motion_session_20261117/live/post_event_save_confirmation.json>)

正式 X 與修改前的原生備份各自在第 20 幀重開，兩者的姿勢、骨架、約束及 6 個網格一致。與儲存前的現場姿勢逐骨比對時，392 根骨骼中有兩個控制器會在重開後重新求值，分別是 `CTRL_foot_IK.L` 的位置與 `CTRL_toe_bend.L` 的歐拉旋轉。相同差異也出現在修改前的備份。第 20 幀的左腳 IK 位置三軸均無鍵，腳趾歐拉旋轉只有 X 軸有鍵。[該幀的控制器通道](<D:/Blender/Projects/Character/X/validation/current_motion_session_20261117/private/pose_reopen_channels02.json>) · [修改前的原生備份](<D:/Blender/Projects/Character/X/validation/current_motion_session_20261117/live/X_before_from_live_RAM02.blend>) · [逐骨比對](<D:/Blender/Projects/Character/X/validation/current_motion_session_20261117/private/pose_reopen02.json>) · [保存前的姿勢數值](<D:/Blender/Projects/Character/X/validation/current_motion_session_20261117/live/live_pose02.json>)

動作的 Custom 在來源骨架上編輯。以 Walk Forward 為例，218 個來源骨骼中有 121 個與目前的 X 骨架同名。對齊後的最大綁定位置差約 42.7 mm。此版本提供可編輯的來源動畫，轉換成目前 X 的 IK 控制器關鍵幀仍需另做映射。[骨架比對](<D:/Blender/Projects/Character/X/validation/current_motion_session_20261117/private/bind01.json>)

本版沿用 0.77.212 的完整投影，保留 278 個檔案、更新 5 個、新增 1 個。正式 ZIP、Blender 5.2 使用者安裝及 X 專案的 284 個檔案已核對。工作清單相關的 114 項測試均通過。[磁碟部署](<D:/Blender/Projects/Character/X/validation/current_motion_session_20261117/delivery213_03.json>) · [驗收與正式 X 狀態](<D:/Blender/Projects/Character/X/validation/current_motion_session_20261117/acceptance213.json>)

首次現場操作預期版本為 0.77.212，版本檢查中止後已回退。第二次確認現場已載入 0.77.213 及所需的 4 個新類別，沿用現有程式完成角色設定。Blender 原生儲存回傳 FINISHED，該次控制台命令內的 dirty 讀數仍為 true。返回 3D 視圖後，視窗標題的未儲存星號消失，磁碟雜湊與獨立重開記錄一致，全程正式儲存一次。[首次回退](<D:/Blender/Projects/Character/X/validation/current_motion_session_20261117/live/applied.json>) · [開啟中的版本](<D:/Blender/Projects/Character/X/validation/current_motion_session_20261117/live/version_audit_01.json>) · [儲存及返回視圖的記錄](<D:/Blender/Projects/Character/X/validation/current_motion_session_20261117/live/post_event_save_confirmation.json>)
