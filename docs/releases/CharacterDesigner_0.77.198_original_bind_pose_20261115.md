# Character Designer 0.77.198 · 還原 Original Bones 的身體姿勢

在 Pose Mode 關閉 Auto Key，選取身體控制器，包含根控制器及四肢的 IK 目標和 Pole。在同一影格依任意順序按完 **Alt G、Alt R、Alt S**。使用 **Reset Selected Chains** 也可一次完成。單獨選手腕時，Alt R 會把手掌轉回該側 Edit Mode 的方向，保留手的位置。

四肢的 IK 約束在控制器數值歸零後仍會解算骨骼。這版用歸零時實際算出的姿勢和各根 Original Bone 的綁定矩陣求出差值，再把同一修正用到控制器移動後的姿勢。

## X 副本的實測

同一影格切換 Armature 視窗顯示時，Cosha 身體細分網格的 13,134 個頂點最大差為 **0.00047719 毫米**。Blender 原生 Rest 的同項最大差為 **0.00047720 毫米**。兩個數值都保留了 Blender 的浮點誤差。[Edit Mode 網格測量](<D:/Blender/Projects/Character/X/validation/natural_pose_20261115/geometry/formal06.json>)

各物件的細分或完整修改器堆疊在同一副本單獨測量。表中數字是該物件切換 Armature 後的最大頂點位移。[六個物件的原始數字](<D:/Blender/Projects/Character/X/validation/natural_pose_20261115/geometry/six06exact.json>)

| 物件 | 頂點 | 歸零後（毫米） | 原生 Rest（毫米） |
|---|---:|---:|---:|
| Cosha | 13,134 | 0.00047719 | 0.00047720 |
| Clothes | 2,457 | 0.00023872 | 0.00026833 |
| Shoes | 25,362 | 0.00015646 | 0.00009336 |
| Stocking | 1,603 | 0.00047829 | 0.00047687 |
| Hair | 5,463 | 14.01365610 | 0.00033225 |
| Dress | 3,040 | 66.21047778 | 0.00030490 |

Hair 和 Dress 保留各自的姿勢與控制系統。身體歸零的精度以 Cosha 的測量為準。

在隔離的 Blender 介面按下 Alt G、Alt S、Alt R，Undo 恢復第三鍵之前的姿勢，Redo 再次恢復綁定。 [快捷鍵與復原](<D:/Blender/Projects/Character/X/validation/natural_pose_20261115/geometry/guiKeys06.json>)

單手及雙手回轉的測試保留腕心、前臂與 Pole 的位置。 [手腕](<D:/Blender/Projects/Character/X/validation/natural_pose_20261115/geometry/wrists06.json>)

一般骨架與未安裝雙側腳控的選取沿用原有操作。 [其他骨架與鎖定](<D:/Blender/Projects/Character/X/validation/natural_pose_20261115/geometry/scope06.json>)

重播舊動畫時，仍可單獨重設手腕。 [舊動畫切回](<D:/Blender/Projects/Character/X/validation/natural_pose_20261115/geometry/detach06.json>)

手工插入或覆寫關鍵幀後可重播該姿勢，相鄰自然姿勢由原動畫曲線插值。 [插幀與插值](<D:/Blender/Projects/Character/X/validation/natural_pose_20261115/reach_root/key06.json>)

未套用 Action 的角色移動控制器後，存檔重開及切幀完整保留。 [沒有動作的姿勢](<D:/Blender/Projects/Character/X/validation/natural_pose_20261115/reach_root/static01.json>)

另外平移、旋轉並縮放角色後，14 根四肢求解骨的骨架空間矩陣逐值相同。該位置的 Cosha 細分網格比較為 0.00070930 毫米，原生 Rest 為 0.00042273 毫米。把角色放回原處後重現原測量。 [變換角色物件](<D:/Blender/Projects/Character/X/validation/natural_pose_20261115/geometry/placement06.json>)

## 交付

已開啟的 Blender（PID 32008）已載入本版程式。現場姿勢與 Action 綁定的前後記錄一致，場景與偏好設定均未保存。[現場回執](<D:/Blender/Projects/Character/X/validation/natural_pose_20261115/live/artist_final01.json>)

官方 build、deploy 與 check 已核對 Blender 5.2 及 X 專案的插件副本。[磁碟交付回執](<D:/Blender/Projects/Character/X/validation/natural_pose_20261115/delivery198final.json>)

[完整驗收與 SHA-256 索引](<D:/Blender/Projects/Character/X/validation/natural_pose_20261115/acceptance198_01.json>)
