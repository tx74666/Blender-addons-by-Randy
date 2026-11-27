# Character Designer 0.77.257 · 视口 FK/IK 切换打键

在 Pose 模式选中肢体控制器，切换 FK/IK 后，在 3D 视口按 **I** 即可记录当前帧的模式和姿势。混选时，Blender 也会按当前打键设置记录其他选中骨骼的修改。验证覆盖同时选中骨盆时保留尚未打键的位移，以及撤销和重做。

在真实 Walk_N 的隔离副本中，删除左脚底目标全部 10 条变换曲线后重新打键，回放第 9、10、14、15 帧时，左腿依次为 FK、IK、IK、FK。[功能验收](<D:/Blender/Projects/Character/X/validation/viewport_limb_keys_20261127/acceptance257.json>)

Blender 5.2 使用原生 Refresh Add-on 连续刷新两次，Pose 的 I 键保持 PRESS 和 CLICK 各一条。停用后两条清除，重新启用后分别恢复。[隔离热刷新](<D:/Blender/Projects/Character/X/validation/fk_ik_keying_delivery_20261127/gui_stage257_05/status.json>)

正式 ZIP、Blender 5.2 用户插件和 X/addons 均已逐文件核对，每处 320 个文件。正式 X 窗口由 Animation 接续。[交付回执](<D:/Blender/Projects/Character/X/validation/fk_ik_keying_delivery_20261127/delivery257.json>)
