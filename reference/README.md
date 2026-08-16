# reference/

人工核对 `dstools/features/world/` 下 `categories.py`、`value_sets.py`、
`render.py` 准确性用的游戏原始数据，**不是任何代码在运行时会读取的文件**——
三个模块的分类/中文名/取值表/翻译都是照着这里的数据核对进代码的。改动世界
设置相关的显示/排序/取值/翻译逻辑时，可以拿这里的数据核对森林/洞穴两边是否
一致。

## 目录说明

- `config_json/`：森林/洞穴的「世界规则」「世界生成」四个中文 JSON，已逐项
  人工核对到与当前游戏版本一致，是**世界设置的权威参考**。每项结构为
  `key`（代码 key）、`name`（中文名）、`en`（英文名）、`default`（默认档）、
  `values`（逐档 `raw` 代码值 + `zh` 中文 + `en` 英文三列对齐）。
- `scripts-260814/`：当前版本（260814）游戏的脚本解包，是核对取值/翻译的
  **权威源**，重点文件：
  - `map/customize.lua`：UI 取值表定义。`WORLDGEN_GROUP`/`WORLDSETTINGS_GROUP`
    里每个设置项（`items`）的 `desc` 指向一张取值表（`xxx_descriptions`），
    `value` 字段是默认值——用来确定每个 key 的合法取值顺序和默认值。
  - `worldsettings_overrides.lua`：tuning 覆盖源（`applyoverrides_pre/post`）。
    **注意**：这里的 `few`/`many` 是 tuning 层 key，UI 层（`customize.lua` 和
    存档 `leveldataoverride.lua`）才用 `rare`/`often`，两套命名不要混用。
  - `languages/strings.pot`（英文）、`chinese_s.po`（中文）：官方文案。取值表
    里的 `text` 名（如 `SLIDEOFTEN`）在这里对应官方中英文（`often`=较多/More、
    `always`=大量/Tons）。
- `带注释版本的cluster.ini`：`cluster.ini` 字段说明的带注释参考。
- `新型mod配置项适配.docx`：新型 mod 配置项适配记录（人工核对参考）。
- `_cache/`：开发/打包缓存集中目录（Python 字节码缓存 `pycache/`、PyInstaller
  中间产物 `build/`），由 `dstools/__init__.py` 和 `scripts/build_exe.py` 自动
  生成，可随时整删，已 gitignore。
- `icon_source.png`：生成 `icons/app/icon.ico`/`icon.png` 用的高分辨率源图，
  仅供人工重新导出图标时使用，代码不会读取，不参与打包。

## 核对方法

世界设置的取值/默认值/中英文，按下面的优先级核对：

1. **取值列表与默认值**：以 `scripts-260814/map/customize.lua` 为准——每个
   设置项的 `desc` 指向取值表，`value` 字段是默认值；`task_set`/`start_location`
   是动态取值（`tasksets`/`startlocations` 的 `GetGen*` 函数，森林/洞穴各是
   不同子集）。
2. **中英文文案**：以 `scripts-260814/languages/` 的 `strings.pot`（英文）和
   `chinese_s.po`（中文）为准——取值表的 `text` 名对应官方文案。
3. **真机交叉验证**：真实存档的 `leveldataoverride.lua`（森林 `Master/`、洞穴
   `Caves/` 各一份）的 `overrides` 表，能交叉验证取值是否正确。
