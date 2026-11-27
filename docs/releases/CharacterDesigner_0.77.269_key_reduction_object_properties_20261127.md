# Character Designer 0.77.269 · 减键预览

修复骨架对象带数字自定义属性时，**Preview Key Reduction** 报出 `Object.path_from_id() does not support path creation for this type` 的问题。预览记录当前值时，骨架对象的属性直接从对象根路径读取，骨骼属性按原有姿态骨骼路径读取。

在 Blender 5.2 用旧版 0.77.268 重现了相同报错，并验证其回滚清除了临时预览。新版通过开始预览、切换 Original / Reduced、调整并取消的测试。使用已保存 X 的 `CoshaRig` 和 `Walk_N · Feet IK` 验证时，双脚 IK 控制器的预览关键帧从 1,458 减到 780。取消后恢复了原 Action、槽位和帧，核对原曲线指纹一致。

[真实动作验证](<D:/Blender/Projects/Character/X/validation/key_reduction_object_properties_20261127/x269.json>) · [回归测试](<D:/Blender/Projects/Character/X/validation/key_reduction_object_properties_20261127/native269.json>) · [交付回执](<D:/Blender/Projects/Character/X/validation/key_reduction_object_properties_20261127/delivery269.json>)
