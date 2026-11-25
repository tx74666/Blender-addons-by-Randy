# 源码与 Blender 安装路径

唯一维护源码位于本仓库：

- `addons/character_designer`
- `addons/random_realm_builder_exporter`（显示名称 RR Helper）

Blender 的 `scripts/addons` 与 X 项目的验证目录目前仍是实体副本，不是目录联接。
修改始终从仓库开始，完成后单向部署到 Blender。GitHub Desktop 因此能直接看到每次源码修改。

## 当前 Character Designer 发布流程

先读仓库和 X 的 `AGENTS.md`，核对最新完整批准投影及其 SHA256。
开发目录存在尚未验收的并行改动时，必须从完整批准版本叠加本次明确验收的文件，
不能直接把整个 canonical 工作树打包或部署。准备步骤和验证范围见
[Focused release workflow](release_workflow.md)。

多项工作共用源码、其中一些尚未验收时，可冻结「已批准底版＋本次已验收改动」
并生成完整 SHA256 清单。运行时代码仍在 canonical 仓库开发；冻结目录只是发布输入，
不能作为新的开发副本。清单需记录底版来源、批准文件的前后哈希及排除的待验收改动。

在仓库修改源码并完成相关验证后，使用 `tools/prepare_release_projection.py`
预览完整候选；核对显式增量后才用 `--apply` 创建新目录。准备工具不会自动批准、
打包、部署或刷新 Blender。正式打包、部署和检查都显式指定同一份已验收清单：

```powershell
python tools/build_releases.py --module character_designer --projection 'D:\path\release_projection.json'
python tools/deploy_local.py --module character_designer --blender-version 5.2 --project-addons 'D:\Blender\Projects\Character\X\addons' --projection 'D:\path\release_projection.json'
python tools/deploy_local.py --module character_designer --blender-version 5.2 --project-addons 'D:\Blender\Projects\Character\X\addons' --projection 'D:\path\release_projection.json' --check
```

工具校验完整文件集合、哈希和版本后使用同一份字节快照。`RELEASE_PROJECTION_MATCH`
表示安装与该批准清单一致，不表示与包含其他待验收改动的整个工作树一致。
当前 Character Designer 不使用省略 `--projection` 的 canonical 发布方式。

部署只更新不同文件，覆盖前保留旧文件备份，并保留现有的版本、文件集与回滚检查。
只想检查时运行 `--check`，不会写文件。实际 Blender 版本须先核对；另一台机器可用
`--addons-dir` 指定目标。ZIP 是固定版本产物，源码变动不会自动更新旧 ZIP。

磁盘部署与运行中 Blender 的代码刷新是两件事。运行中须重新确认进程、已加载代码、
未保存内容和活动会话，再使用匹配版本且已验证的窄更新流程。不要重播旧安装器，
也不要把普通部署当成保存场景、重启或完整重载的授权。

本地部署不等于上传。Commit / Push 由用户在 GitHub Desktop 操作；代理须有明确
授权才可执行 Git 写入。RR Helper 更新应明确选择 `--module random_realm_builder_exporter`。

仓库与 X 项目的 `AGENTS.md` 已记录上述开发入口，避免以后又从安装副本开始修改。
