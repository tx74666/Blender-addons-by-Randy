# RR Helper 0.2.14 — 可靠性与性能修复

日期：2026-09-26。修复 0.2.12 审查中重现的九项问题，并合回 Unity 镜像中独有的碰撞体关联、布局原点对齐和图标更新资源声明修复。0.2.13 为本次中间验证包，最终使用 0.2.14。

## 使用行为

- 从其他 `.blend` Append／Link 的合法群组不会在保存时被清掉 RR 身份。原生 Duplicate 的冲突身份清理、Managed Variant 原有行为继续保留。
- Surface Text 与 sampling 改名后仍能找到来源；同面创建多份文字、Duplicate／Append sampling 后的导出名保持唯一。
- 连续 FBX 导出成功后不残留临时 text／sampling mesh；创建中途失败也会回收部分结果和孤立 mesh。
- 旧版本遗留的 sampling alias，仅在角色、来源对象和 surface identity 都匹配时回收。普通同名对象不会被删除。
- Packed、尚未保存的编辑和 generated 图片以 Blender 当前内容导出；PNG 格式和扩展名一致。图片原路径、打包内容、dirty 状态保留。
- Mapping 的 Location／Rotation／Scale 有节点连线时明确拒绝不支持的 UV 导出，并在修改任何网格前完成检查。
- Standard 成功导出的项目会清出 Queue；需要 Unity 导入请求的模式若请求失败仍保留项目。
- 参考布局的旧文件迁移 handler 会跨多次打开文件继续工作；插件启停与刷新无 handler／timer 累积。
- 恢复 Unity 镜像中已有的自定义 Collider 关联、已有碰撞体保护和布局原点对齐；移动原点不会移动碰撞网格，旧布局快照会一起重定位。
- 明确关联的原生复制 Collider 会记为合法新增，保存时继续跟随其 owner。
- Skip Existing 重用已有模型并重新渲染 Icon 时，manifest 保留请求的 model／icon 声明；明确 Icon-only 的请求仍只声明 icon。

## 性能与实现

图片 I/O 抽至 `rr_image_io.py`，采用 Blender 原生编码和同目录临时文件原子替换；不再用 Python 逐 RGBA channel 编码。失败不会覆盖已有图片。

Queue 直接命中维持快速路径，其余归属查找缓存结果；通过线性关系快照识别 selection、queue、parent、群组属性、名称、collider 以及 Undo／Redo 变化。普通物件也不再触发无意义的全场碰撞网格查询。

UV Mapping 按 material recipe 预先组合变换矩阵，避免每个 loop 重读 sockets、重建旋转矩阵。Point／Vector 和多个非交换变换的顺序均有验证。

| 测量 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 单张 4K 生成 PNG 储存 | 45.10 s | 1.28 s |
| Builder6 原选取，324 objects／3 Queue，timer | 4.42 ms | 4.60 ms |
| Builder6 未入 Queue 的选取，timer | 19.52 ms | 7.39 ms |
| 合成 1,000 objects／50 Queue，timer | 641.32 ms | 12.04 ms |
| 合成 160,000 loops，UV prepare | 3.237 s | 1.767 s |

PNG 是同一基准脚本的单轮本机测量；timer 是同脚本每项五次中位数。不同轮次背景负载有差别，倍数只作参考。UV 对照同进程比较逐 loop 计算和预计算路径，最大坐标差异 `2.38e-7`。以上不代表整个场景导出的固定时间。

## 核心修复已完成验证

- Exporter contracts：53 tests。
- Preview selection：82 checks。
- Point bookmark lifecycle：通过。
- Registration lifecycle：63 checks，包括实际刷新和注册失败回滚。
- 新 Image I/O regressions：9 tests。
- 新 Export state regressions：11 tests，包括真实 FBX 成功／失败与状态恢复。
- 新 Identity／Queue regressions：27 checks，包括真实 Append、重复 Append、原生 Duplicate 和关系变化。
- 新 Surface／UV regressions：11 tests，包括同面双文字连续 FBX、导入回读、旧 alias 恢复、普通同名保护和 UV 数学验证。
- Scoped package build：1 test；新增 `build_releases.py --module`，避免单个插件发布牵连其他正在修改的插件。
- `git diff --check`：任务内文件通过。

以上运行结果来自兼容功能合并前的核心修复版。兼容合并新增 2 个 manifest 测试、13 个 Collider 测试和 2 项 Collider 身份检查；最终重跑状态见下节。静态核对确认 13 个 Collider 测试与 Unity 原测试完全一致，布局的 11 个函数与原镜像实现一致；未削弱断言。

所有 Blender 自动测试均在独立进程／测试场景执行。Builder6 性能检查只读加载，未保存使用者文件。最终 0.2.14 已在前景窗口点击 Refresh Add-on，刷新结束后两个 Collider 按钮显示正常、原选取和三个 Queue 项目保留；未进行生产资产导出或保存场景。

## 本机发布

- `dist/rr_helper-0.2.14.zip`：13 个文件，打包校验通过；`0.2.13` 保留为中间验证包。
- Blender 5.2 安装目录：通过 `tools/deploy_local.py --module random_realm_builder_exporter` 部署，`--check` 一致。
- Unity `Tools/AssetPipeline/Blender/addons` 镜像：使用相同部署工具和 `--check`，13 个文件一致。
- Unity 侧 `Sync-RRHelperAddon.ps1 -Check`：通过。
- Unity 镜像的 lifecycle 测试同步为具名 handler 断言。
- 原安装文件备份：`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260926-165825-571797bc`。
- 原 Unity 镜像备份：`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260926-165830-d05c1454`。
- 最终安装更新备份：`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260926-171225-9acb7f95`。

这些是本机文件与部署更新，未提交或推送 Git；其他任务对 Character Designer 的修改保持不动。

## 额外集成检查

Unity 侧首次 `Invoke-ExporterContracts.ps1` 执行了 72 个测试，发现镜像中独有的 Collider／Layout 功能和 manifest 修复尚未合入主仓库。这些差异已经从部署前备份移回主仓库，并补入主仓库回归测试。最终重跑未完成：最近可用内存约 1 GiB，持续低于项目规定的 2 GiB 门槛；未在低内存状态下叠加后台 Blender 重任务。不能将最终整套集成测试报告为通过。

剩余验证：主仓库 Exporter（55 tests）、Collider（13 tests）、Identity／Queue（29 checks），以及 Unity `Invoke-ExporterContracts.ps1` 全流程。静态 AST 检查覆盖 24 个插件／测试文件，打包测试通过，两个部署副本的全部文件校验一致。

Unity Editor 的场景、Play 状态和项目资产未被本次操作改动。
