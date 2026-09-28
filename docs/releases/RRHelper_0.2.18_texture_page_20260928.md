# RR Helper 0.2.18 — Texture 页面整理

2026-09-28，根据用户提供的两个面板截图调整。

- 顶部 Bake 改为 Texture，使用贴图图标。
- Texture 集中现有 PBR Framework、Create Targets、Prepare Bake、Save Images 和 Texture Packages／Apply Latest Packages。
- Export 移除 Texture Packages；Sections 只保留 Queue、Group、Icon，放在同一行，保留各自独立开关。
- 保留旧 BAKE／TEXTURES enum 的顺序及已保存属性。旧 TEXTURES 页面与旧 section 操作会进入 Texture；绘制面板不会改写旧文件的设置。

本次以已部署的 0.2.17 为基线，仅修改版本、页面名称、导航和布局；PBR、贴图包应用、Surface Text、Standard 导出的业务实现不变。

验证：Python 语法与 diff 检查通过；现有 registration lifecycle 的 63 项检查通过。当前 Builder6 窗口已刷新，实际确认 Export 三项同排、Texture 同时显示 PBR 工具与贴图包。没有执行烘焙、应用贴图包、资产导出或保存 Builder6；原来的 Shader Editor 已还原。

发布包：`dist/rr_helper-0.2.18.zip`，14 个文件。以下三个部署副本都经过 `tools/deploy_local.py --check`，与主仓库一致：

- 当前 Builder6 进程实际加载：`D:\Blender5.2\5.2\scripts\addons_core\random_realm_builder_exporter`。
- 用户安装目录：`C:\Users\Randy\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\random_realm_builder_exporter`。
- Unity 镜像：`D:\Unity Projects\RandomRealm2\Tools\AssetPipeline\Blender\addons\random_realm_builder_exporter`。

实际加载路径由 Blender 内只读查询 `module.__file__` 确认；不能仅因用户安装目录同步成功就假定当前进程已更新。当前程序目录副本的更新前备份位于 `C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260928-112749-97c95d55`。

本次只在本机更新，未提交或推送 Git。
