# 源码与 Blender 安装路径

唯一维护源码位于本仓库：

- `addons/character_designer`
- `addons/random_realm_builder_exporter`（显示名称 RR Helper）

Blender 的 `scripts/addons` 与 X 项目的验证目录目前仍是实体副本，不是目录联接。
修改始终从仓库开始，完成后单向部署到 Blender。GitHub Desktop 因此能直接看到每次源码修改。

## 每次修改的流程

在本仓库根目录修改源码，完成相关测试与版本更新，然后运行：

```powershell
python tools/build_releases.py
python tools/deploy_local.py --module character_designer --project-addons 'D:\Blender\Projects\Character\X\addons'
python tools/deploy_local.py --module character_designer --project-addons 'D:\Blender\Projects\Character\X\addons' --check
```

`SOURCE_DEPLOYMENT_MATCH` 表示部署文件与仓库逐字节一致。
上面的示例只部署 Character Designer。更新 RR Helper 时选择
`--module random_realm_builder_exporter`；明确同时部署两个插件时才省略 `--module`。
部署会拒绝覆盖比当前源码更新的已安装版本，避免影响另一项正在进行的工作。
部署只更新不同的文件，覆盖前保留旧文件备份；发现多余的 Python 模块会停止，供检查。
Blender 正在运行时，修改代码后使用插件的 Refresh Add-on，或重启 Blender，加载新代码。
随后在 GitHub Desktop 对本仓库 Commit，再 Push origin。

只想检查时运行 `--check`，不会写文件。另一台机器可以用 `--addons-dir` 指定安装目录。
ZIP 是带版本号的发布产物；改变源码后需重新打包，它不会随源文件自动更新。

仓库与 X 项目的 `AGENTS.md` 已记录上述开发入口，避免以后又从安装副本开始修改。
