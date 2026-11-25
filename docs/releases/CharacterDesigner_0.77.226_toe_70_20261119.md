# Character Designer 0.77.226

脚趾控制圈的默认位置调整为从后跟向前量的鞋长 70%。生成、重建和重新拟合脚控共用这个位置。X 的左右脚均较 80% 向后移约 2.71 厘米。现场保留第 26 帧、原有姿势和选取，场景尚未保存。[现场图](D:/Blender/Projects/Character/X/validation/toe_70_20261119/artist_view.jpg)

沿用 32 边横截面轮廓，弯趾支点仍在趾根。截面在拟合时采样，播放时线框使用随趾骨变化的显示变换。在 X 的原姿势、抬脚、弯趾和抬后跟测试中，每侧各 32 条圈线与实际变形鞋面的相交数均为零。单独弯趾和抬后跟时，鞋底 IK 轮廓的测得位移为零。弯趾时无限延伸的截面也经过远处的靴筒，实际穿模检查使用有限长的圈线与鞋网格逐段相交测试。[网格验证](D:/Blender/Projects/Character/X/validation/toe_70_20261119/surface01.json)

完整包继承 0.77.225 的投影，更新版本号和截面位置。正式 ZIP、本机 Blender 5.2 和 X 各核对 298 个文件。[发布核对](D:/Blender/Projects/Character/X/validation/toe_70_20261119/delivery.json)
