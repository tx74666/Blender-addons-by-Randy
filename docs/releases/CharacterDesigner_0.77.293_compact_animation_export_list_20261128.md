# Character Designer 0.77.293 · 精简动画导出列表

**Miscellaneous → Unity Export → Animations** 的 **Show Export List** 默认收起，有排队项时在标题显示数量。展开后可用 **Pick from Existing Animations** 多选入队，或用 **Add Starred to Queue** 跨分类一次加入星标动作。每行的 × 只移除对应队列项。

动画列表的勾号继续用于切换星标。新版星标随 Blender 项目保存，可跨会话反复使用。导出选择器也支持临时多选。重复入队保持单条，成功项离开队列，失败项保留用于重试。快速导出和 Unity 审阅入口继续可用。

29 项相关测试、隔离的布局与保存重开验证通过。原生测试在一次性目录生成四个 FBX，验证成功和失败时星标都保留，原 Meta 文件和路径保持一致。首次回归测试的标题断言漏掉队列已有数量，修正夹具后通过。Blender 5.2 和 X 各安装 329 个文件、0 差异。正式 X 会话未刷新。

[交付回执](<D:/Blender/Projects/Character/X/validation/compact_animation_export_list_20261128/delivery293.json>)
