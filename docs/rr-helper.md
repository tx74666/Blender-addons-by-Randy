# RR Helper 0.2.5

插件显示名为 **RR Helper**，Python 模块名为 `random_realm_builder_exporter`，
界面位于 N 侧栏的 **RandomRealm** 标签。

本仓库的八个运行源码文件来自已安装的 0.2.5，并与现用安装包逐字节核对。
较早的 `_package_random_realm_builder_exporter` 暂存目录仍是 0.2.2，本次未采用。

主要工作包括对象/父组管理、建造资产身份与命名、图标和 PBR 工作流、Unity 导出、
建模原点及点位书签。0.2.5 的具体变化和此前验证见
[升级记录](releases/RRHelper_0.2.5_upgrade_20260908.md)。

## 本机项目配置

RR Helper 当前仍使用 Randy 的本机项目路径，例如
`D:\Unity Projects\RandomRealm2` 与
`D:\Blender\Projects\Character\Animation\Animation.blend`。
复制仓库到其他机器后，应先配置实际项目路径再使用导出或动画同步。
相关常量主要位于 `rr_builder_constants.py` 和 `__init__.py`。

迁入保留现用版本的行为与内部模块名。源码目录不包含安装缓存、历史备份或私人场景文件。

## 验证范围

仓库的共同加载检查在临时 Blender 场景中启用、卸载并重新启用两个插件。
此前 RR Helper 的 Builder6 导出对比依赖本机素材，其结果保存在升级记录中；
本次目录整理无需再次触发 Unity 导出。
