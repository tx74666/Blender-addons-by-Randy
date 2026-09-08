# Blender add-ons by Randy

RR Helper 和 Character Designer 的源码、安装包及插件测试集中维护在这里。

| 插件 | 当前版本 | 源码 | Blender 安装包 |
| --- | --- | --- | --- |
| RR Helper | 0.2.5 | [random_realm_builder_exporter](addons/random_realm_builder_exporter) | [rr_helper-0.2.5.zip](dist/rr_helper-0.2.5.zip) |
| Character Designer | 0.43.1 | [character_designer](addons/character_designer) | [character_designer-0.43.1.zip](dist/character_designer-0.43.1.zip) |

两个插件独立安装，也可以同时启用。RR Helper 的内部模块名保留为
`random_realm_builder_exporter`，便于更新原来的安装。

Character Designer 0.43.2 精简绑定界面：**Character Setup** 只显示主骨架和身体权重源，不列配件清单。选中网格，在 **Weight → Quick Bind** 选择 **Surface Transfer**（最近面插值，默认）或 **Automatic Weights**，点击 **Bind Weights**。头发和裙子使用各自页面的专用绑定。**Restore Previous Binding** 保留首次绑定前的状态，多次重算和保存重开后仍可恢复；原先未绑定则恢复为未绑定。已有引用和恢复记录继续有效。

Character Designer 0.42.3 新增 **Rig → Limb IK → Simplify Bone Collections**，
把当前骨架整理为 **Original / Controls / Animation**。Animation 有控制骨时使用
控制骨，否则保留原生骨；Build、Rebuild、Remove 后自动更新。头发统一为 **Hair**，
独立裙子骨架统一为 **Skirt**，不再细分出一长串集合。

Character Designer 0.42.2 将新建 Limb IK 的 **Auto Align** 默认设为开启，
Stable 和 Direct 两种方式一致。保存重开及 Rebuild 保留已有状态，包括手动关闭；
旧 Rig 若处于关闭状态，开启一次并保存即可。

Character Designer 0.42.1 将裙子移除、前臂校准移除、旧头发副本清理统一为红色，
与已有的头发解绑、Limb IK / Spline IK 移除按钮保持一致。

Character Designer 0.42.0 新增 **Animation** 页，连接免费本地 Kimodo，支持生成、
独立骨架预览、应用为新的身体 Action 及恢复上一 Action。模型和 Python 环境独立安装，
不含付费插件或云端生成。见 [本地配置](docs/kimodo-local.md)。

Character Designer 0.41.3 移除 **Weight Flow**，Weight 页保留 **Weight Tools**
和 **Weight Symmetry**。日常权重平滑使用 Blender 原生工具。

Character Designer 0.41.2 将 Modeling 与 Reference 合并到 **Miscellaneous**，
并把 Delta Symmetry 面板和建立配对按钮统一命名为 **Build Symmetry**。

Character Designer 0.41.0 直接在原始头发上绑定，每束骨链加入角色 Armature 的
Head 下，不再生成网格副本或专用头发骨架。支持保存重开后移除本次绑定、恢复原权重，
并提供旧副本清理。头皮帽完全跟随 Head；Mirror 两侧独立，中央同一束使用单链。

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
