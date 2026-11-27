# Character Designer 0.77.265 · 动画导出入口

在已连到 Unity、尚无可发送工作列表条目的场景里，`Animation → Export Animations` 显示 `Export Current Action as FBX…`。该入口沿用单 Action 导出，并根据当前骨架、动作、预览和编辑状态决定是否可用。已有工作列表的场景继续显示 Unity 发送入口。

在实际 X 界面中发现了空折叠区。修正后在隔离 Blender 5.2 核对了未连接、连接无条目、连接有条目和已有合法编辑场景四种状态。[面板调整记录](<D:/MyRepository/Blender-addons-by-Randy/docs/releases/CharacterDesigner_0.77.264_animation_panel_layout_20261127.md>) · [原生核对](<D:/Blender/Projects/Character/X/validation/animation_panel_layout_20261127/native265.json>)
