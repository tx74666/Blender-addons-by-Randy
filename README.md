# Blender add-ons by Randy

RR Helper 和 Character Designer 的源码、安装包及插件测试集中维护在这里。

| 插件 | 当前版本 | 源码 | Blender 安装包 |
| --- | --- | --- | --- |
| RR Helper | 0.2.5 | [random_realm_builder_exporter](addons/random_realm_builder_exporter) | [rr_helper-0.2.5.zip](dist/rr_helper-0.2.5.zip) |
| Character Designer | 0.40.2 | [character_designer](addons/character_designer) | [character_designer-0.40.2.zip](dist/character_designer-0.40.2.zip) |

两个插件独立安装，也可以同时启用。RR Helper 的内部模块名保留为
`random_realm_builder_exporter`，便于更新原来的安装。

## 安装与使用

在 Blender 的 **Edit → Preferences → Add-ons → Install from Disk** 中，
分别选择上表的 ZIP 并启用插件。安装时选择插件 ZIP，不要选择整个仓库的下载 ZIP。
当前版本使用 Blender 5.2 验证。

- **RR Helper**：3D View 的 N 侧栏 → **RandomRealm** → **RR Helper**。
  用于建造资产、对象管理、图标/PBR 工作流及 Unity 交接，见 [说明](docs/rr-helper.md)。
- **Character Designer**：3D View 的 N 侧栏 → **Character Designer**。
  包含角色建模、骨架、权重、头发及 **Clothing → Skirt Setup**，见
  [完整指南](addons/character_designer/README.md)。

## 维护与打包

后续插件修改以 `addons/` 为入口；模型文件与真实角色集成验证留在 Blender 项目中。
Blender 安装目录通过统一脚本从这里部署并校验，见 [源码与部署流程](docs/source-workflow.md)。
现有 Character Designer 署名说明保存在 [ATTRIBUTION.md](addons/character_designer/ATTRIBUTION.md)。

修改插件后更新其 `bl_info["version"]`，用 Python 3.10 或更新版本运行：

```powershell
python tools/build_releases.py
```

脚本分别生成可安装 ZIP，并更新 `dist/SHA256SUMS.txt`。已有版本的 ZIP 内容若与源码
不同，会要求先升版本；内容一致时保留原安装包。测试方法见 [tests/README.md](tests/README.md)。
本次迁入的检查结果见 [验证记录](docs/repository-validation.md)。

## 旧代码整理

已清理旧 PhysicalDress/SplineIK 的三个重复脚本或安装包，其三点 Hook 设计已由现版
裙子工具覆盖，见 [检查记录](docs/legacy-skirt-cleanup.md)。
Language-Switcher、Vertex-Cleaner、AnimationSwitcher 及原 Blender 示例仍保留原目录。
