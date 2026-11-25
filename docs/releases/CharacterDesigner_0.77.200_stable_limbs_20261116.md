# Character Designer 0.77.200 · X 的 Stable 四肢

在 Pose Mode 關閉 Auto Key，選取整身控制器，使用 **Reset Selected Chains** 即可回到 Original Bones 的 A 字姿勢。控制器為原比例時，在同一幀各按一次 Alt R 和 Alt G 即可，兩者順序任意。若控制器已有縮放，另按一次 Alt S。單選手腕仍可用 Alt R 重設朝向。完成後可用原有的手腕、肘膝 pole 和腳控制器繼續擺姿勢。

X 第 12 幀的兩份資料有不同的縮放。先前 RAM 的姿勢已清除縮放，兩個快捷鍵即可完成。最新保存的 X 重新讀取後左手腕控制器的縮放約為 0.961028，此幀整身還原需再按一次 Alt S 或直接用面板按鈕。按鍵在此幀各一次即可。

四條隱藏的兩節 IK 鏈負責求解。每段保留原骨的靜止軸向，再由 ORI 骨傳回朝向。每個私有解算骨架有 16 根非變形中轉骨；角色骨架保持 392 根。記錄舊 Direct 動畫的關鍵幀繼續走對應解算，新建 Stable 關鍵幀可與它們在相鄰幀混合。未打幀的還原可隨既有 Action 換幀離開。已驗證 Blender Undo/Redo 和新舊關鍵幀的保存、重開及插值。

X 的 Cosha 身體在完整修改器堆疊得到 13,134 個頂點。在還原姿勢，IK/FK 混合設為 0、0.25、0.5、0.75、1，所有頂點逐位相同。切換 Edit Mode 的 Armature 視窗顯示，最大差 0.000476968 毫米；Blender 原生 Rest 開關差 0.000477201 毫米。還原後直接和原生 Rest 比，最大距離 0.000363211 毫米。這些量測僅適用於 Cosha 身體。服裝、裙子與頭髮可有各自的動畫或修改器。

同一組輸入下，.199 Direct 測試基準與 Stable 的 A 字姿勢逐點相同。約 72° 彎肘時兩種解算的身體表面最大距離為 0.6902 毫米。保持彎肘並將手腕控制旋轉 90°，Stable 掌面轉了 89.93°。圖像取自成功路徑相同的前一候選，最終版另外修正新骨建立失敗時的清理，並重測還原及 IK/FK。

[檢視姿勢對照](D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/Stable_Direct.png)

官方套件已部署到 Blender 5.2 和 X。現場 PID 29916 已從 .198 載入 .200。更新前後比對了姿勢、選取、Action 與 slot；X 保留第 12 幀及 57 根骨的選取。臨時切換的底部區域已還原為 Action Editor，視窗標題顯示未儲存標記；場景和偏好未寫入磁碟。沒有執行 Git 提交或推送。舊 .198 套件、所有權拒絕和 rollback 測試依據保留。測試和升級用 Codex；模型推理等級未記錄。

## 驗證資料

- [body](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/mesh03.json>)
- [current](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/current03.json>)
- [keys_saved](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/currentKeys03.json>)
- [old_file](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/legacy/old198a.json>)
- [mixed](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/legacy/mixed03.json>)
- [rollback](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/rollback04.json>)
- [gui](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/gui/undo01.json>)
- [artist](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/live/artist_final01.json>)
- [delivery](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/deliveryfinal03.json>)
- [warm198](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/live/adopt198e.json>)
- [warm199](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/live/adopt199e.json>)
- [visual](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/visual02.json>)
- [motion](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/pose_difference02.json>)
- [keyed](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/legacy/keyed01.json>)
- [no_action](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/legacy/static01.json>)
- [preview](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/legacy/preview01.json>)

[驗收記錄](<D:/Blender/Projects/Character/X/validation/stable_limbs_20261116/acceptance200.json>)
