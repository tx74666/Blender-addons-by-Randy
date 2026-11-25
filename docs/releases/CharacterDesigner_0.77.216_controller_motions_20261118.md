# Character Designer 0.77.216：匯入動作轉為角色控制器

Motions／角色 Animation 的 **Convert to Character Controls** 預設勾選。首次開啟支援的匯入動作時，建立獨立的角色控制器 Action；取消勾選則開啟原始骨骼動畫。原始 Action、slot 與工作清單來源保持保留。再次開啟會沿用已建立、可繼續編輯的控制器 Action；來源更新不會自動覆蓋既有修改。

需要額外映射的動作會顯示 **Mapping needed**，保留原因並回到原始骨骼編輯。背景轉換可取消；完成前會重新核對來源、角色、Rest、控制器依賴及時間設定，輸入已改變時不發佈過期結果。若操作情境已切換，完成結果保留為 READY，等待明確開啟。

本次 Walk Forward 驗收覆蓋 **56 根骨骼**：身體 22、手指 30、眼睛 2，以及 breast.L／R；有對應控制器的部分轉為控制器通道，其餘支援的原生骨骼保留動畫通道。採同名及祖先關係驗證，依角色目前 Rest 校準旋轉與 Hips 位移。連接關節保持目標骨長與連接，未連接關節保留可表達的來源平移；被投影掉的連接關節位移另有量測記錄。這是按目前 Cosha 骨長重定向，不會改寫 Rest、骨名、階層、網格或權重。

另有 **161 根來源骨骼**未寫入輸出：65 根共享次級骨骼及 96 根來源獨有頭髮骨。此 Walk 的原生採樣證明它們相對動畫父骨保持中性，僅有數值雜訊；若發現有意義的獨立頭髮／裙子動作或姿態，會拒絕此映射，保留完整來源。此版本未宣稱支援任意骨架或完整次級動作轉移。

驗證範圍分開記錄：

- [完整 Walk 獨立來源驗證](D:/Blender/Projects/Character/X/validation/controller_mapping_20261117/probe04/oracle19/receipt.json)：72 個嚴格來源時間、合計 356 個驗證時間，包含控制器自適應時間及區間採樣；沿用既有誤差標準，產物為獨立驗證衍生版本。
- [正式 worker 流程](D:/Blender/Projects/Character/X/validation/controller_mapping_20261117/qa/append_worker01/acceptance.json)：使用保留 2170 個來源通道、裁至第 1–3 幀的私人副本，實測快照、子程序、映射、驗證、匯入及清理。完整 Walk 的數學驗證另見上一項，兩者不合稱一次完整 worker 跑次。
- [完整產物的開啟、修改、快取及儲存重開](D:/Blender/Projects/Character/X/validation/controller_mapping_20261117/qa/edit_reuse01/outcome.json)：重新驗證既有 Walk 產物後，測實際操作與私人副本重開，未重跑求解器。
- [提交前回滾](D:/Blender/Projects/Character/X/validation/controller_mapping_20261117/qa/activation_failure07/result.json)與[提交後刷新失敗／重試](D:/Blender/Projects/Character/X/validation/controller_mapping_20261117/qa/terminal_recovery01/result.json)：回滾保留完整控制圖及姿態；提交後失敗保留 READY Action 並提示重開，不偽造舊圖復原。
- [隔離 GUI 的真實背景工作與 Cancel](D:/Blender/Projects/Character/X/validation/controller_mapping_20261117/qa/gui_a03/acceptance.json)：確認事件迴圈持續運作、取消後子程序退出、計時器／處理器清理及原始資料不變；此取消測試沒有發佈完整轉換結果。

首次準備仍有等待：GUI 本次前景準備為 **31.757 秒**，之後才進入可互動的背景階段。較早完整轉換與驗證跑次總耗時約 **2174 秒**；兩項是不同範圍的實測值，不是固定耗時或 SLA。已開啟的 Motions 彈出面板可能保留靜態進度；按 Escape 後重開可見最新狀態。

原 run17 的正式檔案雜湊變動、oracle18 分類拒絕，以及匯入、回滾與 GUI 測試的早期失敗收據均保留，後續通過不覆寫舊結果。[證據範圍索引](D:/Blender/Projects/Character/X/validation/controller_mapping_20261117/qa/release_scope_review.json)。

已使用正式工具建立封包並部署至 Blender 5.2 與 X；兩處各 291 個檔案、0 差異，安裝版的啟動／註冊／預設值／卸載清理也通過。正式 ZIP SHA256：`0de77327f1ac080c960fddcc150c981d1a945a9e59e92ebeaa9c38b461dc4565`。來源基準為 [stage09 完整投影](D:/Blender/Projects/Character/X/validation/controller_mapping_20261117/stage216_09/projection.json)。本輪未儲存正式 X 或藝術家偏好、未熱更新既有藝術家工作階段，亦未進行 Git 提交／推送；儲存重開驗證只寫私人副本。


[最終驗收](D:/Blender/Projects/Character/X/validation/controller_mapping_20261117/acceptance216.json) · [正式部署回執](D:/Blender/Projects/Character/X/validation/controller_mapping_20261117/delivery216_01/delivery.json) · [安裝版啟動檢查](D:/Blender/Projects/Character/X/validation/controller_mapping_20261117/qa/installed216_01.json)

完整投影 SHA256：`56fa29c6924710a205aaebfecb8867f94f211d9098a77b1246deeb37c63d77cc`；延伸完整215，共291個檔案（8個既有檔變更、4個新增、279個逐位元相同）。交付時沒有開啟的藝術家 Blender 視窗，故本次沒有現場熱更新；下次正常開啟 Blender 5.2 使用已部署版本。
