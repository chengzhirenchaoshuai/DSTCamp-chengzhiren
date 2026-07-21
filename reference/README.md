# reference/

人工核对 `dstools/core/world_categories.py`、`world_value_sets.py`、
`world_icons.py` 准确性用的游戏原始数据快照，**不是任何代码在运行时会
读取的文件**——三个模块的分类/中文名/取值表都是照着这里的数据手抄进代码
的，改动世界设置相关的显示/排序/取值逻辑时可以拿这里的数据核对森林/洞穴
两边是否还一致。

- `config_json/`、`config_txt/`：游戏内"自定义世界"设置界面的原始导出
  （英文原版 + 中文对照各一份），森林/洞穴的"世界规则"/"世界生成"各一份。
- `worldsettings_overrides.lua`：游戏自身的取值定义源文件，
  `world_value_sets.py` 的 `VALUE_SETS` 就是照这份文件手抄的。
- `洞穴设置核对清单.md`/`森林设置核对清单.md`：对着本机真实存档
  `leveldataoverride.lua` 的全部 override key 做的人工核对记录。
- 两个 `.bat`：饥荒启动/Mod 更新的个人本地脚本，跟这个项目本身无关，
  留档而已。
