# Character Designer 0.77.290 · Scale 清理

**Clean Animation Data** 对当前管理动作槽逐轴清理中性 Scale。插件管理的非变形 Body 控制器可清理距 `1.0` 不超过 `0.000003` 且整条曲线都在该范围内的烘焙尾差。其他部位的近中性 Scale 只隐藏显示。保留有实际缩放变化、驱动、曲线修改器以及需要维持动作时间范围的通道。新准备的动作沿用同一清理逻辑。

隔离 Blender 5.2 中读取磁盘上的 **In Air**，清除了右脚 312 个键和三个躯干控制器的 1,737 个键。左脚和右脚最终均无 Scale 键。整个动作移除 4,612 个冗余键，并隐藏 100 条无需显示的 Scale 曲线。对 422 根骨骼取 129 个整帧与子帧样本，姿态矩阵最大元素差为 `4.53e-6`。旧算法之外的广域尾差删除试验影响裙摆求解，该候选未发布。

98 项相关测试及两项原生检查通过。Blender 5.2 和 X 的磁盘安装各核对 329 个文件，零差异。正式 X 未重载或保存。[原生验收](<D:/Blender/Projects/Character/X/validation/scale_cleanup_20261127/native290_acceptance.json>) · [交付回执](<D:/Blender/Projects/Character/X/validation/scale_cleanup_20261127/delivery290.json>)
