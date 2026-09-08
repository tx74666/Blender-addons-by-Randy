# 旧裙子代码检查 · 2026-09-08

检查后清理了以下旧文件：

- `Work-In-Progress/PhysicalDress.py`
- `Work-In-Progress/PhysicalDress.zip`
- `Misc/Bone/SplineIK.py`

PhysicalDress ZIP 仅包含与旁边 Python 文件逐字节相同的脚本。两个脚本都使用
FK → SPIK → 三点 NURBS/Hook → Local Before Copy Transforms，仍依赖固定对象名、
选择顺序及当前模式，缺少重复运行识别和失败回滚。

这套结构已被恢复版及 Character Designer 的裙子工具覆盖。SplineIK 中额外的父骨接法
也已有对应处理，没有发现需要另外提取的算法。旧文件没有 Cloth、Collider 或缓存烘焙实现。

删除前已将三个文件的原始字节及 SHA256 保存到仓库外的本机备份目录：

`D:\MyRepository\.codex-backups\blender-addons-consolidation-20260908-f402949b`

备份中的 `migration-manifest.json` 记录了清理范围、来源、版本与安装包校验值。
两个 PhysicalDress 文件此前未被 Git 跟踪，因此备份也保留了这部分未提交内容。

当前裙子入口为 **Character Designer → Clothing → Skirt Setup**，支持自动拟合、
环形控制、蒙皮、物理骨架、闭合 Collider 与烘焙。大圈使用 G/R/S，局部点使用 G。
