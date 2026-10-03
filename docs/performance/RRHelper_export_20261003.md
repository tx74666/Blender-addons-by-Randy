# RR Helper 四件建筑导出计时 · 2026-10-03

**主要时间花在图标渲染。** 保存的 Builder6 中，电梯、入口框、地板和墙壁在缓存预热后，模型加图标共用 28.95 秒；图标阶段占 23.08 秒，约 80%。文字快照为 2.49 秒，FBX 阶段为 0.86 秒。身份校验的 2.07 秒是嵌套包含时间，不能再与其子阶段相加。

| 测量 | 四件合计 | 证据 |
| --- | ---: | --- |
| 首次仅 Model | 6.20 秒 | [首次报告](D:/Blender/Projects/Build/WIP/Validation/export_performance_20261003/baseline/profile.json) |
| 首次 Model + Icon | 83.65 秒 | 同上；首个图标约 60 秒，冷启动样本 |
| 预热后 Model + Icon | 28.95 秒 | [重复报告](D:/Blender/Projects/Build/WIP/Validation/export_performance_20261003/baseline/profile_repeat.json) |
| 更新已有包，仅 Model | 5.73 秒 | [更新报告](D:/Blender/Projects/Build/WIP/Validation/export_performance_20261003/baseline/profile_model_update.json) |

**已有图标、只更新模型时，保留 Model 勾选，取消 Icon。** 本轮验证四张原图标的字节哈希完全相同，manifest 保留 `iconFile`，本次 `exportedResources` 为 `['model']`。[图标保留验证](D:/Blender/Projects/Build/WIP/Validation/export_performance_20261003/baseline/icon_retention_verified.json)支持此操作，不代表每次固定耗时；两个流程的差额是少生成图标的收益，并非运行时代码加速。需要更新图标时仍勾选 Icon。本轮描边关闭，单独的 CPU 描边候选未实施，其测得收益不能算入这次导出。

验证使用 RR Helper 0.2.46、后台 Blender 5.2、2 线程串行、EEVEE、1024 像素图标、1024 采样，描边关闭。只读取保存文件，未覆盖 GUI 未保存修改；源文件哈希未变，Unity 无写入、无请求。最初两次打开文件后因可用 RAM 少于 200 MiB 停止，零导出，日志保留。本次只增加[隔离计时工具](../../tools/profile_rr_export_blender.py)，未部署新版本、未提交或推送 Git。Codex 模型及推理强度未记录。

导出反馈与发布事务回归共 38 项通过；本机安装只读检查 20 个文件，差异为 0。所有后台进程已退出。

源文件：`D:/Blender/Projects/Build/WIP/Builder6.blend`。SHA-256：`408bf74d3f2d57afe2b8f26748cb2eb31a09097e9829ee11b427f97020576e36`。

首次冷启动不代表用户的 29 秒；所有数字只描述这次保存文件和系统状态。若预热后仍变慢，按相同图标设置和资产范围重新采样，并读取阶段的独占时间，避免重复累计嵌套耗时。
