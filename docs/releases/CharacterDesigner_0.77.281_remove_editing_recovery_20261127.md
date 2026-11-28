# Character Designer 0.77.281 · 移除 Editing & Recovery

从 Animation 面板完全移除 **Editing & Recovery**。移除折叠栏状态及其专属的绘制代码。归档动作、恢复记录和脚本命令保留。已接入的 Original Rigging Inputs 仍可从原有独立区域继续编辑或关闭。常规同步和导出保持原流程。

83 项相关测试及隔离的 Blender 5.2 验证通过。更广的界面测试中有三项旧 Physics 断言失败，使用与 0.77.280 字节一致的 Physics 代码复现。插件已安装到 Blender 5.2 和 X，各 326 个文件、0 差异。当前 X 会话未刷新。

[交付回执](<D:/Blender/Projects/Character/X/validation/remove_editing_recovery_20261127/delivery281.json>)
