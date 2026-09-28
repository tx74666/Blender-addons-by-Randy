# RR Helper 0.2.19 — Export 面板折叠

2026-09-28。为过长的 Export 面板增加独立收起入口：

- Export Queue：队列与对应输出设置整体收起。
- Group：分组操作整体收起。
- Reference：只收起参考布局设置，不改变 Use Layout 或 Include Mesh。
- Icon：尺寸、取景、灯光、预览与参考图一起收起。

各标题前有箭头。四项状态独立保存在 Scene 属性中，随用户保存 `.blend` 保留；默认展开以兼容已有布局。Sections 原来的显示开关与折叠状态互不覆盖。Reference 使用适合 box 内布局的箭头，不创建 Blender 不支持的嵌套原生 panel。

验证：语法与 diff 检查通过；已有注册／刷新生命周期 63 项检查通过。在当前 Builder6 窗口刷新后，实际点击验证 Queue 收起／展开、Reference 与 Icon 独立收起。队列再次展开时，Reference／Icon 保持收起；三个队列项目与原选取保留。Group 的显示条件仍与原版一致，本次未改选生产场景对象来触发 Group。

`dist/rr_helper-0.2.19.zip` 包含 14 个文件。已同步并逐文件校验 Blender 实际加载的 `D:\Blender5.2\5.2\scripts\addons_core`、用户 addons 目录及 Unity Blender 镜像。实际加载目录更新前备份：`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260928-113219-840711a8`。

当前界面保留常用 Queue 展开，Reference 与 Icon 收起。未烘焙、导出、保存场景或重启 Blender；改动仅在本机，未提交／推送 Git。
