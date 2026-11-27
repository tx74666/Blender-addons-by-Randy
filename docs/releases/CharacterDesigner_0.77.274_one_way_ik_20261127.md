# Character Designer 0.77.274 · FK 转 IK

`Convert to IK` 根据当前 FK 工作动作创建并校验 IK 曲线。校验通过后，同一个工作列表条目改用 IK Action。角色若正在编辑这条动作，会在当前帧切换到 IK。原 FK Action 归档保留用于恢复，旧档的备选 Action 引用依然可读。

移除 `Open FK Version` 和 `Open Full IK Version`。已使用 IK 的条目直接沿用现有 Action。普通动作选择器隐藏仅作为旧备选存在的动作。

通过 160 项相关测试和隔离 Blender 5.2 的原生绑定、未打键姿势保护及故障回滚检查。扩展测试报告的 22 个夹具错误均在 0.77.273 上复现。Blender 5.2 和 X 磁盘版各核对 326 个文件，零差异。本次未重新求解正式 X 中的动作。

[交付和验证回执](<D:/Blender/Projects/Character/X/validation/fk_to_ik_working_motion_20261127/delivery274.json>)
