# Character Designer 0.77.258 · 动作列表计数

Prepare Remaining Clips 现在按当前角色和工作区一次匹配控制器记录。使用 X 的 26 条动作，在普通模式下，每次计数的中位数从 3.050 降至 0.837 毫秒。开启 Generate Full IK Tracks 后，两项计数合计从 6.161 降至 1.831 毫秒。数据来自独立 Blender 5.2 中 14 轮交替测量，每组调用 15 次。视口播放帧率另有独立记录。[计数与七组功能验证](<D:/Blender/Projects/Character/X/validation/worklist_count_20261127/native258.json>) · [播放测试](<D:/Blender/Projects/Character/X/validation/worklist_playback_20261127/summary.json>)

两版在七种状态下返回相同的有序动作列表，覆盖已准备、待准备、需要重试、重复记录、其他工作区、其他角色和缺记录。两个版本均通过全部 63 项队列测试。通用 UI 测试在两个版本的相同条目上均有 24 项错误和 2 项断言失败。[旧版测试](<D:/Blender/Projects/Character/X/validation/worklist_count_20261127/suites_stage257_05.json>) · [新版测试](<D:/Blender/Projects/Character/X/validation/worklist_count_20261127/suites_stage258_01.json>)

正式 ZIP 与两处安装各核对 320 个文件。正式 X 通过 Refresh Add-on 刷新，前后均为第 0 帧、Pose 模式、左腿 IK，选中左脚底控制器，保留未保存标记。[交付回执](<D:/Blender/Projects/Character/X/validation/worklist_count_20261127/delivery258.json>)
