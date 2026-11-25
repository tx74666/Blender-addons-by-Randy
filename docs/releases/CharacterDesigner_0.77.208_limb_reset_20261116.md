# Character Designer 0.77.208

在姿態模式全選可見控制器，Alt G 清位置，Alt R 以 Original Bones 為基準清旋轉，Alt S 清縮放。局部手腕、腳踝、腳趾與支援的 IK 混選會依當前姿勢對準各自的編輯軸。普通骨架沿用 Blender 的原生處理。

第 25 幀原先清完控制器以後，左腳變形骨仍比編輯姿態多轉約 42.5°。直接清除可見控制器也會遺漏原生骨上的動畫輸入。新版全身旋轉復位在保留位置與縮放的情況下校準四肢。它在同幀刷新時保留剛清除的位置，插鍵後以當前 Action 實際讀回的值計算骨架校準。

在目前保存的 X 副本實按 Alt G、Alt R，第 25 幀鞋子的最大頂點差從 151.22 毫米降到 0.00030 毫米，左肘從 26.71 毫米降到 0.00025 毫米。身體最大差約 0.00084 毫米。嘴部 1,386 頂點的最大差約 0.00036 毫米，四個嘴部形態鍵在這兩幀均為零。幾何量測採用同幀同角度的 Armature 開關，原始截圖、區域定義與浮點差異可查閱[驗收記錄](<D:/Blender/Projects/Character/X/validation/foot_alt_gr_20261116/qa/acceptance208_04.json>)。[第 25 幀三列對照](<D:/Blender/Projects/Character/X/validation/foot_alt_gr_20261116/qa/X_frame25_arms_lips_feet.png>)。

第 25、26 幀已測兩種按鍵次序、普通 I、自動插鍵、撤銷重做、切幀、重開與保留非中性縮放。現場僅更新程式。開啟中的 X 已由 0.77.207 載入 0.77.208 姿勢修復，第 25 幀、57 根骨骼、原姿勢與 Action 已核對。磁碟為含相同姿勢程式的 0.77.209。[現場回執](<D:/Blender/Projects/Character/X/validation/foot_alt_gr_20261116/live/artist_apply02.json>)。
