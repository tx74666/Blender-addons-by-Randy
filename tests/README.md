# 插件测试

这里保留 20 个已有 Character Designer 自建夹具测试，以及两个插件共同加载的检查。
测试目录保持平面结构，便于现有测试互相导入夹具。

使用 Blender 5.2，在本仓库根目录的 PowerShell 中运行：

```powershell
$blenderExe = 'D:\Blender5.2\blender.exe'
$selectedTests = @(
    'test_addons_together_blender.py'
    'test_forearm_twist_addon_enable_blender.py'
    'test_skirt_ui_blender.py'
    'test_skirt_physics_blender.py'
    'test_hair_bones_mirror_controls_blender.py'
)
foreach ($testName in $selectedTests) {
    & $blenderExe --background --factory-startup --disable-autoexec --threads 2 --python-exit-code 1 --python (Join-Path 'tests' $testName)
    if ($LASTEXITCODE -ne 0) { throw "Failed: $testName" }
}
```

每项在独立的临时 Blender 场景中运行；不要在工作中的实时场景里执行测试脚本。
测试覆盖共同启用/卸载、Forearm 注册、裙子创建/回滚/碰撞体/烘焙、头发镜像的左右独立控制和中央单链。
需要保存重开的测试会使用临时文件。

其他已带入的测试覆盖基础角色工具、UI 分页、权重、Spline IK、Limb IK、裙子拓扑和头发分组。
`test_skirt_rig_service.py` 是不依赖 Blender 的 Python 测试，可直接运行。
`test_skirt_topology_blender.py` 有可选的真实模型检查，找不到 X/Elaina 素材时会跳过该部分，
其自建网格测试仍可执行。

私人角色模型、真实场景验收脚本及实时 GUI 测试保留在 Blender 项目中。
RR Helper 此前使用 Builder6 的完整导出对比属于历史验证，见
[升级记录](../docs/releases/RRHelper_0.2.5_upgrade_20260908.md)。
