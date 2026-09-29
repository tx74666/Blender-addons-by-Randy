# RR Helper 0.2.24 — Model / Icon 导出与模型契约保护

Model 勾选时，队列中符合 Core 筛选的对象会重新生成模型并更新同一包；Icon 勾选时生成图标。只更新图标时取消 Model。移除 Skip 控件、相关提示和导出结果中的 Skip 计数。保留隐藏的旧 `skip_existing_exports` 属性以读取旧场景，但运行流程不再查询它，旧值 true 不影响重新导出。

两条 Icon-only 路径（共享导出和独立 Render / Update Icon）保留已有 FBX 的 `sourceBlend`、`sourceObject`、`surfaceText`，原字段缺失时继续缺失，不从当前 Font 推测旧模型内容。原模型、源快照、bounds、uvExport 和材质数据继续复用；相对于 Core 的布局仍可更新。Model 导出则同时更新模型及这些描述。

Variant 发布复用 Standard 的快照路径迁移和存续资源 `.meta` 恢复逻辑，避免 `sourceBlend` 指向已清理的 staging 目录，以及纹理重生成造成 GUID 变化。未修改 membership、stableId、Core 筛选、Standard/Modular 路由或 Unity 导入协议。完整保留 0.2.22/0.2.23 的图标与顶部 Core 状态交互。

## 验证

- `python -B tests/test_standard_export_transaction.py`：29/29 通过。真实执行从源码提取的导出、manifest 和 Variant 发布函数，使用临时文件和 Blender API 替身；覆盖旧 Skip true/缺失、Model 与 Icon 组合、两种导出模式、两条 Icon-only 路径、缺失字段、快照迁移、GUID 保留及发布失败回滚。
- 更新既有 Blender 导出契约测试的旧 Skip 假设；源码与测试通过 AST 检查，diff 无空白错误。
- 因共享重型作业窗口由 Character 的 Unity Play 验证占用，本轮没有启动 Blender/Unity，没有执行真实 FBX/图标渲染或现场刷新。以上纯 Python 通过不等于完成真实 Blender 集成验证。

## 部署与后续边界

版本为 0.2.24；发布包 `dist/rr_helper-0.2.24.zip`。部署目标为 Blender 5.2 系统 addons_core、用户 addons 和 RandomRealm2 的 Tools/AssetPipeline/Blender/addons。当前运行中的 Blender 仍需在串行窗口执行 Refresh Add-on 后使用新代码。

2026-09-29 已完成上述三处部署，分别用相同命令追加 `--check` 验证，均为 14 个文件一致、`different_files=0`。发布包 14 文件验证通过。16 个源码/测试文件 AST 检查通过，Core 的 `rr_reference_layout.py` 与 0.2.23 发布包逐字节一致。

本次只更新插件代码和测试，没有修补 live 两个 Font 的绑定，也没有导出生产 Door。生产 manifest 已错配的问题不能通过保留旧契约自动恢复；交由 Building 先按其外观/位置/hash 守卫完成两 Font 补绑，再安排生产 Model 导出及 Unity 修复验证。

更改为本地源码、安装副本和发布包；未提交或推送 Git。

## Unity 测试镜像补齐

已把本版对应的旧 Skip 断言改动定向合并到 Unity 的 `Tools/AssetPipeline/Blender/tests/run_exporter_contracts.py`，保留全部 72 项测试和项目独有内容；7 个相关方法与 canonical 的 AST 一致，其余 93 个方法原样保留。镜像 `test_standard_export_transaction.py` 已补齐新增 13 项覆盖，与 canonical 逐字节一致，并在镜像位置运行 29/29 通过。两个目标文件 AST 与 diff 检查通过，`Sync-RRHelperAddon.ps1 -Check` 通过；版本仍为 0.2.24。

`Invoke-ExporterContracts.ps1` 会启动后台 Blender，依 Character 尚占用共享重型作业窗口的协调要求，本轮未执行；上述 72 项为保留的测试数量，不是本轮 Blender 运行通过数量。
