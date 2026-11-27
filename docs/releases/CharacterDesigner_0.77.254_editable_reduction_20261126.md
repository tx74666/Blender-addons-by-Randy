# Character Designer 0.77.254 · 可编辑的 Reduced 工作副本

打开减键预览后，可以直接在 Reduced 里调整曲线、贝塞尔手柄、关键帧、分组和修改器，也可以增删当前 Action 槽的通道。点 Original 可对照原动作，再点 Reduced 可继续编辑。Apply 把当前槽的结果写回原 Action，Discard 回到原动作。重新调整减键容差会从 Original 重新生成工作副本。

用已编辑过的 .253 预览文件在独立 Blender 5.2 中执行原生 Refresh Add-on，刷新前后原动作、副本、曲线、选取和姿势等 15 项一致。切换两边后 Apply，原 Action 保留自身身份并得到编辑后的曲线。Walk_N 的 Cycles 曲线另有 100 个亚帧的比对。旧预览对当时没有源曲线的输入缺少历史取值，无法追溯该值。

Blender 5.2 用户安装和 X/addons 各 318 个文件通过校验。正式 X 窗口由 Animation 接续。[完整验收](<D:/Blender/Projects/Character/X/validation/editable_reduction_20261126/acceptance254.json>) · [隔离刷新](<D:/Blender/Projects/Character/X/validation/editable_reduction_delivery_20261126/refresh01.json>) · [磁盘交付](<D:/Blender/Projects/Character/X/validation/editable_reduction_delivery_20261126/delivery254.json>)
