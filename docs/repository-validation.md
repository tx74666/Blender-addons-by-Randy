# 仓库整理验证 · 2026-09-08

运行环境：Blender 5.2.0 LTS，独立 `--background --factory-startup --disable-autoexec`
进程。没有操作实时 Blender 工作场景。

| 本次执行的检查 | 结果 |
| --- | --- |
| RR Helper 与 Character Designer 按两种顺序同时注册、卸载、再次注册 | 通过 |
| Forearm 的真实 addon_utils 启用/禁用、初始化和运行时清理 | 通过 |
| Skirt UI 创建、重复执行、回滚、取消烘焙和移除确认 | 通过 |
| Skirt physics：3 个闭合凸 Collider、15 帧缓存、独立动画副本、无插件重开 | 通过 |
| Hair Mirror：左右独立、中央单链、变换跟随、事务回滚、无插件重开编辑 | 通过 |

五项检查的进程退出码均为 0。本机详细日志位于
`.codex-backups/migration-validation-20260908/`，复查命令见 [测试说明](../tests/README.md)。

## 安装包与源码

- Character Designer 0.40.2：25 个运行/说明文件，其中 23 个 Python 文件。
- RR Helper 0.2.5：8 个 Python 文件。
- 迁入时逐字节比对原始源码、当前安装与发布 ZIP；保留原安装包。
- 使用新打包脚本在独立输出目录从零重建两个 ZIP，内部文件与已验证的原安装包完全一致。
- 迁入源码、测试和打包工具的 Python 语法检查通过，Git diff 空白检查通过。
- 20 个已有自建夹具测试随源码迁入，本次执行其中四项精选回归及一个新增共同加载检查。

当前安装包校验：

| 文件 | SHA256 |
| --- | --- |
| character_designer-0.40.2.zip | e65b5a7af1996bf4cbcc0f640eb22d765aa5fdd417735de87046d25cd9704c94 |
| rr_helper-0.2.5.zip | 6b6b1728c5c8f3aca1a302fc22360fc17b905bd537531cbef35e13ccedfd305d |
