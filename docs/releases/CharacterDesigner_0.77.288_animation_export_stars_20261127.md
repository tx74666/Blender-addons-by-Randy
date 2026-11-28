# Character Designer 0.77.288 · 动画列表加星排队

Animation 的就绪行用无边框勾号表示。点勾号变成星标，再点一次取消。列表下方的 **Add Starred to Queue** 一次加入所有分类中的星标动作，包括已准备的 Neutral。成功加入后，星标回到勾号，队列中的同一动作保持单条。选择与排队保留当前 Action、槽、帧和姿势。

已有 Unity Export 的动画库和队列共享同一会话的导出选择。待导出动作可继续在 **Miscellaneous → Unity Export → Animations** 设置 FBX 并执行导出。

70 项相关测试和 Blender 5.2 隔离验证通过。原生验证覆盖跨 Basic 和 Group1 选择、取消、批量入队和重复入队。早期测试在手动绑定测试 Action 槽时触发 Blender 原生崩溃，改由 Blender 的正式插键入口创建测试槽后通过。Blender 5.2 和 X 各安装 328 个文件，0 差异。当前 X 会话未刷新。

[交付回执](<D:/Blender/Projects/Character/X/validation/animation_export_stars_20261127/delivery288.json>)
