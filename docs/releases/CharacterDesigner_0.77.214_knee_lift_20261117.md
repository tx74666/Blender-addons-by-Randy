# Character Designer 0.77.214 · 抬腳時的膝蓋方向

在 X 的第 20 幀，角色左腳沿全域 Z 抬高 20 公分時，舊版的腳踝確實垂直上升，但膝蓋向內偏了 72.22 毫米。舊版從接近伸直的靜止腿推算彎曲平面，腿部原有的小幅側偏會在屈膝時放大。新版在腿已經彎曲時校準 IK 的極向角，讓膝蓋朝向它的膝部極向控制器（knee pole）。

在已有的 X 骨架上，進入 Pose Mode，選取整組身體控制器，按一次 **Alt+R** 回到身體預設旋轉。選取一隻腳的 IK 控制器，例如 `CTRL_foot_IK.L`，在全域座標下按 **G**、**Z** 向上移動。需要讓膝蓋朝側面時，仍可移動同側的 knee pole。新建腿部 IK 會使用角色的預設前方，建置時也可明確指定彎曲方向。

## 第 20 幀正視測量

同一個 X 副本、同一個動作分別測左右腿。表中數字是從全身復位姿勢抬腳後，膝關節往身體中線移動的距離。

| 腳沿全域 Z 抬高 | 左膝，修正前 | 左膝，0.77.214 | 右膝，修正前 | 右膝，0.77.214 |
| --- | ---: | ---: | ---: | ---: |
| 10 公分 | 50.39 mm | 0.24 mm | 56.53 mm | 0.24 mm |
| 20 公分 | 72.22 mm | 0.75 mm | 80.98 mm | 0.75 mm |
| 30 公分 | 87.33 mm | 1.56 mm | 97.83 mm | 1.57 mm |

左腳抬高 20 公分時，腳踝的 X 位移為 0，左膝向前彎曲 248.09 毫米。正視圖中的小幅側移仍可量到。以含動畫清單的新版正式 X 存檔另開副本重測，左膝結果相同。[Blender 正視圖](<D:/Blender/Projects/Character/X/validation/knee_lift_20261117/visual/X_frame20_knee_lift.png>) · [正式 X 副本測量](<D:/Blender/Projects/Character/X/validation/knee_lift_20261117/repro/current_scene214.json>)

## 動畫與交付

已儲存的舊姿勢沿用原有膝蓋校準。第 1、12、19、20、21、25、26、31 幀的雙腿測量與升級前逐項相同。重新復位後抬腳並插鍵，當幀測得的關節位置跳動為 0。移開影格再返回、以及儲存副本再開啟，最大差均為 0.00012 毫米。另驗證了新舊校準之間的插值、改動 knee pole 時的轉向及第二條腿升級失敗時的回復。[插鍵與重開](<D:/Blender/Projects/Character/X/validation/knee_lift_20261117/repro/release214keys.json>) · [升級與回復](<D:/Blender/Projects/Character/X/validation/knee_lift_20261117/solver/upgrade214.out>)

正式套件以 0.77.213 的完整投影為基礎，更新五個檔案。ZIP、Blender 5.2 外掛目錄和 X 專案外掛目錄均核對了 284 個檔案。[發行驗收](<D:/Blender/Projects/Character/X/validation/knee_lift_20261117/acceptance214.json>) · [發行套件](<D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.214.zip>) · [0.77.213 記錄](<D:/MyRepository/Blender-addons-by-Randy/docs/releases/CharacterDesigner_0.77.213_current_character_motions_20261117.md>)

現場核對時，開著的 X 已在執行 0.77.214。CoshaRig 處於第 20 幀 Pose Mode，選取的是左腳掌滾動控制器 `CTRL_foot_roll.L`。核對前後的姿勢、動作與選取資料相同，程式寫入、姿勢操作與場景存檔次數均為 0。其後將暫用的 Console 切回 Properties，視窗標題顯示未儲存星號。再次核對時，磁碟上的 X 檔案雜湊、姿勢、影格與選取均一致。[現場回執](<D:/Blender/Projects/Character/X/validation/knee_lift_20261117/live/artist_knee01.json>)
