# Character Designer 0.77.280 · 精简 Editing & Recovery

移除 **Register Action in Worklist** 的面板按钮。已有 Action、工作清单和正常同步沿用原有逻辑。原登记命令及其 ID 保留，仍可由脚本调用，或在开启 Blender 开发者选项后通过 Operator Search 找到。普通 F3 搜索以菜单入口为主。

**Editing & Recovery** 会在有归档动作、可恢复 Main Rig 或有 Original Rigging Inputs 时显示。

78 项相关测试和 Blender 5.2 隔离验证通过。Blender 5.2 与 X 的安装各 326 个文件，均与投影一致。本次交付未刷新正式 X。

[交付回执](<D:/Blender/Projects/Character/X/validation/worklist_registration_search_20261127/delivery280.json>) · [Blender 搜索说明](https://docs.blender.org/manual/en/5.0/interface/operators.html)
