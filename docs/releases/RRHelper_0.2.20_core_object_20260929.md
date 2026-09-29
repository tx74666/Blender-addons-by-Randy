# RR Helper 0.2.20 — Core Object

2026-09-29。Export Queue 顶部固定显示 Core 名称与星标。点击物体旁的空心星设为唯一 Core，实心星保持按下的主题高亮；再次点击当前 Core 取消标记，点击其他物体切换 Core。普通选取变化不会改变标记。

新标记自动用于相对布局，界面移除独立的 Use Layout。`Include Mesh` 改为 `Export Core Object`：开启时自动补入本批，即使队列为空也可以只导出 Core；关闭时排除 Core 本身，仍保留部件的相对布局，不触碰此前 Core 文件。Core 即使未入队或本批不导出，也保持在队列顶部显示。Star 同时出现在 Add Selection to Queue 旁与各队列物体行。

Core 复用原 Reference 指针、自定义属性和稳定 ID；`referenceLayout` version 1、role、矩阵及 Unity 协议不变。Save/Restore Layout 功能独立保留。显式的图标渲染操作仍处理用户指定的对象；本次 Core 开关控制模型/图标批次导出范围。

## 旧文件

- 更早的自动布局文件保留原来的启用行为。
- 已明确关闭 Use Layout 的旧标记保持原普通导出行为，显示旧标记名称及 `Use as Core`；由用户激活后进入新规则。
- 旧版本中，Reference 已在队列时，即使 Include Mesh 关闭也会实际导出它。迁移对此保留实际范围，将 Export Core Object 设为开启并显示迁移说明；之后可直接关闭新开关。
- 所有迁移可重复执行，保存后重开保持结果。新文件没有重复的布局确认开关。

## 边界

Variants 必须整组发布，不能把其组或成员标为 Core。排除 Core 时，如果待导出的父对象/Assembly 仍包含 Core 几何，会在写文件前提示改为单独导出其他部件或开启 Core；不悄悄删改分组几何。

## 验证

- Core 回归 10 项：真实临时 FBX 两批导出、Core 文件哈希不变、Core 单独导出与去重、星标切换、稳定 ID、保存重开、旧文件迁移、置顶 UI、父对象夹带 Core 的预检。
- 原身份与队列回归 29 项检查通过；注册/刷新/失败回滚生命周期 63 项检查通过。
- 原导出状态回归 11 项通过，包括 Standard/Modular 完成清理及实际 FBX 失败恢复。
- 测试均在独立 factory-startup Blender 5.2 中串行运行，未打开生产场景、烘焙或请求真实 Unity 导入。

00:03–00:04 已部署并逐文件 `--check` 校验三处各14文件、差异0：Blender 5.2 `scripts/addons_core`、用户 `scripts/addons`、Unity 项目 `Tools/AssetPipeline/Blender/addons` 镜像。旧文件备份在 `C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260929-000308-caaffaf0` 及相邻两次部署记录。

在现有 Builder6 窗口点击 Refresh Add-on 后，实测 `Hub_Floor_Circular_A` 名称与蓝星置顶；Use Layout/Include Mesh 旧控件消失；取消 Export Core Object 后星标保持，再恢复迁移后的开启选项。两项原队列及选中 Core 保留。该 Core 之前已经入队，故迁移按旧版本实际行为保留开启，并展示说明。未保存生产场景、未触发生产导出；所有后台测试进程已正常退出。

本轮为本机源码、发布包与部署更新，未提交或推送 Git。
