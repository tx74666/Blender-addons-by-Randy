# Character Designer 0.77.221 · 批量準備角色控制器動作

在角色的 **Animation → Animations** 按一次 **Prepare Missing**，即可依序把清單中缺少的動作映射到目前角色。每條動作各自生成可編輯的控制器 Action。準備期間可看到動作名、目前步驟、取樣進度與輪次。完成後點動作名稱就能在主角場景切換，**Advanced → Open Source Motion** 可開啟匯入的原始動作。

日常準備預設以世界座標檢查全身骨骼，位置容差為 3 毫米，旋轉容差為 1°。需要原版精度時，可在開始前勾選 **Advanced → Precise Preparation**。批量工作記住開始時的選擇，重按 **Prepare Missing** 會跳過已有的動作。[批量與場景切換驗收](<D:/Blender/Projects/Character/X/validation/controller_prepare_cost_20261118/qa/queue03/report.json>)

腿部求解會先嘗試沿用第一個姿勢的膝蓋參考角。整條腿未能貼合時會恢復輸入，再執行完整搜索。需要補幀時會保留已求出的樣本，只求解新增的時間點，並重新處理旋轉的連續性。Windows 短暫占用進度檔時，後台轉換可以繼續。[膝蓋求解對照](<D:/Blender/Projects/Character/X/validation/controller_prepare_cost_20261118/wall_phase01/comparison.json>) · [進度檔重試驗收](<D:/Blender/Projects/Character/X/validation/controller_prepare_cost_20261118/qa/status_retry01/result.json>)

在原先取消的 `Wall Jump` 輸入副本，以 Blender 5.2 單執行緒完成全片轉換和校驗，共 22 分 58 秒。其中 IK 匹配約 2 分 2 秒，控制器回放校驗約 16 分 20 秒。122 個姿勢實際求解一次，後續輪次共重用了 297 次。寫出並壓縮關鍵幀後，489 個原始動作對照點的全身最大位置誤差是 2.44 毫米，最大旋轉誤差是 0.64°。左右腳最大位置誤差分別為 1.64、2.14 毫米。該片段沒有符合量測條件的持續著地區間。29 條動作的總準備時間尚未量測。[整段結果與獨立讀回](<D:/Blender/Projects/Character/X/validation/controller_prepare_cost_20261118/full_wall_jump_acceptance.json>)

Blender 5.2 與 X 已安裝同一份發行版本，兩處各核對 296 個檔案。現場檢查時 0.77.221 已生效。X 留在第 26 幀，保有 59 個已有的 Action 和未儲存的編輯。現場批量尚未啟動。[版本交付與現場核對](<D:/Blender/Projects/Character/X/validation/controller_prepare_cost_20261118/acceptance221.json>)
