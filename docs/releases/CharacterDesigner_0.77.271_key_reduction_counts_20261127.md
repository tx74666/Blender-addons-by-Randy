# Character Designer 0.77.271 · 减键计数

**Preview Key Reduction** 的 **Reduced curves** 只统计关键帧数量实际下降的 F-Curve。百分比按这些曲线减少的键数除以它们原有的键数计算。**Same key count** 显示键数量相同的曲线上的键数，**Selected total** 显示本次预览选中变换通道的总数。手动增加键时，面板同时显示 **Added keys**。原版和减键版使用同一个 Action 槽位、控制器范围和 F-Curve 轴进行对比。

使用保存的 X 场景和 `Walk_N · Feet IK`，按 10,000 mm 和 100° 容差实测。实际减键的 72 条曲线为 **5,412 → 321，减少 94.1%**。另有 4,860 个键所在曲线的键数没变，所以所选总数是 10,272 → 5,181。该动作开启循环，曲线需保留循环接缝和其他关键边界。取消预览后核对原动作曲线、槽位和帧一致。

预览计数读取两边现有的曲线长度，兼容已有预览记录及对 Reduced 副本的手动增删键。减键的容差和保护规则沿用原实现。

[实际动作核对](<D:/Blender/Projects/Character/X/validation/key_reduction_counts_20261127/x271.json>) · [计数和面板回归](<D:/Blender/Projects/Character/X/validation/key_reduction_counts_20261127/native271.json>) · [交付回执](<D:/Blender/Projects/Character/X/validation/key_reduction_counts_20261127/delivery271.json>)
