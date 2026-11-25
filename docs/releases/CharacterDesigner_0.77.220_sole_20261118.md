# Character Designer 0.77.220 · 腳底 IK 輪廓

2026-11-18

舊版的腳底輪廓跟隨求解後的腳骨，轉動圓形箭頭踮腳時，輪廓也會隨鞋跟抬起。現在左右腳的輪廓分別跟隨 `CTRL_foot_IK.L` 和 `CTRL_foot_IK.R`。移動腳部 IK 目標時，輪廓會一起移動。只轉動 `CTRL_foot_roll.L` 或 `CTRL_foot_roll.R` 時，輪廓留在原位，鞋跟以前腳掌為支點抬起。

新建的腳部控制器會直接使用這項設定。已有 0.77.219 踮腳控制器的角色，可在 **Body Setup & Recovery** 選擇左腿或右腿，按 **Anchor Sole Outline**。較早的腳部控制器使用 **Set Up Heel Lift**，一次設定該側的踮腳動作與腳底輪廓。

Blender 5.2 與 X 專案均已安裝 0.77.220，兩處各核對 296 個檔案，差異為零。已在開啟的 X 場景第 26 幀完成雙腳設定。切換前後，腳底輪廓頂點的最大位移低於 0.001 毫米，392 根既有骨骼的靜止矩陣與姿勢輸入均一致。現場變更仍在記憶體中，場景與偏好均未儲存。[現場雙腳驗證](D:/Blender/Projects/Character/X/validation/foot_ik_sole_20261118/artist01.json) · [最終驗收與部署記錄](D:/Blender/Projects/Character/X/validation/foot_ik_sole_20261118/acceptance220.json)
