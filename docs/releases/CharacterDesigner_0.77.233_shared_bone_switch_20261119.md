# Character Designer 0.77.233 · Weight 和 Rig 共用骨骼切换按钮

两个页面的 Bone Display 共用一排纯文字按钮 **Original Bones** 和 **Controls**。高亮显示当前操作状态。Weight 可进入当前网格的权重绘制，Rig 可直接编辑原始骨骼姿势。刷权重或编辑原骨期间切换标签页，两页都能通过 Controls 返回当前会话。窗口提示和现有命令仍可说明当前操作。

第 26 帧的 X 副本通过了从 Weight 刷权重、切到 Rig 返回 Controls、转动手腕后回到原网格与权重组的验证。姿势、动画和已写入的权重均保留。70 项相关测试通过。[第 26 帧测试](<D:/Blender/Projects/Character/X/validation/shared_bone_switch_20261119/x02.json>) · [单元测试](<D:/Blender/Projects/Character/X/validation/shared_bone_switch_20261119/unit_final.log>)

基于已发布的 0.77.232，仅变更版本和按钮绘制，297 个文件逐字节一致。官方 ZIP 及 Blender 5.2、X 项目两份安装逐文件一致。正式 Blender 窗口打开于后台，当前进程的插件版本未经核对；下次刷新插件或打开 Blender 可载入磁盘版本。[安装核对](<D:/Blender/Projects/Character/X/validation/shared_bone_switch_20261119/delivery.json>)
