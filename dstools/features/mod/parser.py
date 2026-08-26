"""Mod 信息读取器——解析已下载的 DST mod 的 modinfo.lua。

发现 mod 安装目录，读取配置项定义，供 GUI 提供合适的下拉框编辑器。
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dstools.shared.lua_parser import parse_lua_value
from dstools.shared.steam_discovery import find_all_steam_libraries
from dstools.models import Platform

# 双引号 Lua 字符串的内容：任意非引号/反斜杠字符，或反斜杠转义字符（这样
# 字符串里的 `\"`，例如 `hover = "default is \"detailed\""`，不会被误判
# 成字符串的结束引号——一个简单的 `[^"]*` 会在那里就截断）。
_QSTR = r'(?:[^"\\]|\\.)*'
# 单引号字符串同理——Lua 把 ' 和 " 一视同仁，不少 mod 整个文件都用单引号。
_QSTR_SINGLE = r"(?:[^'\\]|\\.)*"
# 两种引号风格作为两个可选捕获组——配合 _pick_quoted() 取实际命中的那个。
_QUOTED_ALT = rf'"({_QSTR})"|\'({_QSTR_SINGLE})\''


def _pick_quoted(m: re.Match) -> str:
    """给定一个匹配了包含 _QUOTED_ALT 的模式的 re.Match，返回两个可选捕获
    组里实际命中的那一个。"""
    return m.group(1) if m.group(1) is not None else m.group(2)


def _contains_cjk(s: str) -> bool:
    """字符串里是否含有 CJK 统一表意文字（汉字）——用于判断一个字符串字
    面量是不是中文，见 _extract_quoted 的三元双语名处理。"""
    return any("一" <= ch <= "鿿" for ch in s)


_LUA_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}

# 一整个带引号的字符串（任一风格），供 _replace_idents_outside_strings 整
# 段跳过字符串内容用，避免误匹配到字符串内部一个长得像标识符的子串。
_ANY_STRING = re.compile(rf'"{_QSTR}"|\'{_QSTR_SINGLE}\'')


def _replace_idents_outside_strings(text: str, subst_map: dict[str, str]) -> str:
    """一次扫描把 `subst_map` 里的每个标识符都换成对应的替换值，绝不会
    匹配到带引号字符串内部。

    一个普通的标识符边界正则没法区分"这是对该参数的引用"和"这个参数名恰
    好是同一个函数体里*另一个*字符串字面量内容中出现的英文单词"——不这样
    处理的话，一个名叫比如 "default" 的辅助函数参数会悄悄破坏同一函数体
    内恰好包含单词 "default" 的任何兜底字符串（一个真实 mod 的英文选项
    标签 'default' 就是这样变成了字面文本 'nil'）。

    **性能坑（真机复现+cProfile 定位过）**：以前是每个标识符单独调一次
    `re.sub()`，被一个真实 mod 暴露出问题——那个 mod 用共享辅助函数批量
    生成了 421 个配置选项，`_inline_helper_call()` 每次调用平均要替换
    11+ 个参数，421×11 ≈ 4857 次独立的正则扫描，仅这一个 mod 的解析就
    吃掉 700ms+，占那次全量 Mod 列表加载耗时的大头。改成一次正则扫描命
    中全部标识符（交替分支 `\\b(?:ident1|ident2|...)\\b`），把
    O(调用次数 × 参数个数) 次扫描收成 O(调用次数) 次。
    """
    if not subst_map:
        return text
    idents = "|".join(re.escape(i) for i in subst_map)
    pattern = re.compile(rf"{_ANY_STRING.pattern}|\b(?:{idents})\b(?!\s*=(?!=))")

    def repl(m):
        s = m.group(0)
        if s and s[0] in ('"', "'"):
            return s
        return subst_map.get(s, s)

    return pattern.sub(repl, text)


_LONG_BRACKET_OPEN = re.compile(r"\[(=*)\[")


def _strip_lua_comments(text: str) -> str:
    """把 Lua 注释（`-- 行注释` 和 `--[[ 块注释 ]]`/`--[=[ ... ]=]`）替换
    成等长的空白文本，其它字符——包括换行符——原封不动留在原位。

    这个函数在本模块读取的每份文件上都会先跑一遍，*正是因为*本文件里其
    它每个函数（_find_local_tables、_extract_choices、
    _extract_field_raw 等）都是用朴素的花括号/圆括号深度计数来定位内
    容，根本不知道 `--` 注释的存在。有个真实 mod 在一份正常的可选项列表
    后面注释掉了一条尾部选项，类似
    `--, {description = "Areborestone", data = 2, hover = "..."}}`——注
    释里这些多出来的 `{`/`}` 被当成普通字符计数，提前把外层表*过早*闭
    合，悄悄截断了几行之后*下一个*选项的内容（这正是这个 bug 暴露的方
    式：几行之后一个完全不相干的选项解析出来选项数是零）。先把注释剥离
    掉，就能让本文件里所有按位置扫描的逻辑都不用管注释，仍然保持正确。

    对引号敏感（两种引号风格和 `[[...]]`/`[=[...]=]` 长括号字符串都原样
    跳过），这样一个恰好包含字面 "--"（用作破折号分隔符在普通文本里很常
    见）的 hover/description 字符串就不会被误判成注释。
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ('"', "'"):
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                c = text[i]
                out.append(c)
                if c == "\\" and i + 1 < n:
                    i += 1
                    out.append(text[i])
                elif c == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "[":
            lm = _LONG_BRACKET_OPEN.match(text, i)
            if lm:
                closer = "]" + lm.group(1) + "]"
                close_idx = text.find(closer, lm.end())
                end = close_idx + len(closer) if close_idx != -1 else n
                out.append(text[i:end])
                i = end
                continue
        if text.startswith("--", i):
            j = i + 2
            lm = _LONG_BRACKET_OPEN.match(text, j)
            if lm:
                closer = "]" + lm.group(1) + "]"
                close_idx = text.find(closer, lm.end())
                end = close_idx + len(closer) if close_idx != -1 else n
            else:
                nl = text.find("\n", j)
                end = nl if nl != -1 else n
            out.append("".join(c if c == "\n" else " " for c in text[i:end]))
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _unescape_lua_string(s: str) -> str:
    """解码 Lua 字符串转义序列（\\n、\\t 等）——用正则而不是真正的 Lua
    tokenizer 捕获字符串时，这些转义会原样留在字符串里，变成字面的两字
    符序列，不解码的话，比如本该含制表符的标题标签就会显示成字面的反斜
    杠加 t。"""
    return re.sub(r"\\(.)", lambda m: _LUA_ESCAPES.get(m.group(1), m.group(0)), s)


@dataclass
class ModConfigOption:
    """来自 modinfo.lua 的单条 mod 配置项定义。

    **加/删/改字段时记得把 mod_resolve_cache._CACHE_FORMAT_VERSION 加
    一**——真机复现过的坑：磁盘缓存只按 modinfo.lua 的 mtime 判断新鲜
    度，不知道"DSTCamp 自己的解析代码变了"，字段形状一变，缓存里没有新
    字段时 `ModConfigOption(**o)` 会用默认值悄悄补上（不报错，不会触发
    重新解析），表现为"明明修了 bug，但界面还是老样子"。"""

    name: str = ""  # 配置键名
    label: str = ""  # 显示标签
    hover: str = ""  # 悬浮提示
    default: Any = None  # 默认值
    choices: list[dict] = field(default_factory=list)
    # 每个选项：{"description": "...", "data": value, "hover": "..."}
    is_header: bool = False  # 纯视觉分区标题，不是真实设置项
    # mod 声明了一个 `options` 表，但解析不出一份固定的可选项列表——比如
    # `options = GenerateFontSizeOptions(x)`（一个函数调用，结果依赖
    # modinfo.lua 里没有字面写出的数据/逻辑），或者 `options = someVar`
    # 而 someVar 是用 for 循环拼出来的，不是赋值成一张字面量表。这跟
    # "选项列表本来就没有/是空的"不同：选项确实存在，只是要在 Lua 运行
    # 时才能算出来，文本解析器无法重现——resolve_config_value() 的调用
    # 方据此把这个情况展示出来，而不是悄悄显示一个空下拉框。
    is_dynamic: bool = False
    # `options = ...` 未解析的原始右侧表达式（例如 "cleancycle" 或
    # "GenerateOptionsFromList(true, FONTS)"），只在 is_dynamic 为真时才
    # 保留——基于 lua_sandbox 的解析（见下面的 resolve_dynamic_option()）
    # 用它连同 ModInfo.dynamic_preamble，按需真正丢进一个沙箱化 Lua 解
    # 释器跑一遍，不需要重新解析整个文件。
    raw_options_expr: str = ""
    # 这个选项自己的 `client = true`——不是引擎字段，是部分 mod 作者用来
    # 标记"这个选项只在玩家自己客户端有意义"（快捷键、HUD 位置等）的约
    # 定。专用服务器管理工具只编辑 modoverrides.lua（服务端配置），显示/
    # 编辑纯客户端选项只会误导，见 visible_config_options() 整个隐藏掉。
    client: bool = False
    # 下面四个也不是引擎字段，是共享库 mod "Configs Extended"（创意工坊
    # 3317960157）的约定，最终仍然写回同一份 modoverrides.lua，只是值的
    # 形状原生下拉框表达不了，ModConfigDialog 改用专门编辑控件：
    # - is_set_config：字符串当 key 的集合（{["heatrock"]=true, ...}）
    # - is_array_config：普通有序数组
    # - is_text_config：纯字符串
    # - is_dictionary_config：字符串键值对表（{["草"]="6个", ...}）
    is_set_config: bool = False
    is_array_config: bool = False
    is_text_config: bool = False
    is_dictionary_config: bool = False


@dataclass
class ModInfo:
    """来自 modinfo.lua 的 mod 元数据与配置。"""

    name: str = ""
    author: str = ""
    version: str = ""
    # Mod 管理列表展示版本时只信任完整 Lua 沙箱成功执行后的最终值。
    # pending/confirmed/undeclared/unresolved 分别表示等待解析、已确认、
    # 作者未声明、无法在当前沙箱环境确认；静态解析到的 version 不会因此
    # 丢失，但不能冒充 confirmed。
    version_status: str = "pending"
    version_source: str = ""
    version_compatible: str = ""
    version_compatible_status: str = "pending"
    description: str = ""
    workshop_id: str = ""  # 从文件夹名派生
    icon: str = ""  # 例如 "modicon.tex"，相对于 icon_atlas 所在文件夹
    icon_atlas: str = ""  # 例如 "images/modicon.xml"，相对于 mod_folder
    config_options: list[ModConfigOption] = field(default_factory=list)
    # modinfo.lua 里 `configuration_options` 被赋值*之前*的全部内容——mod
    # 常用来以编程方式构建选项列表的局部辅助函数/表/for 循环。保留下来，
    # 供之后按需调用的 resolve_dynamic_option() 在 Lua 沙箱里执行它（加
    # 一句 `return <raw_options_expr>`），不需要到那时再重新读取/切分源
    # 文件。
    dynamic_preamble: str = ""
    # mod 声明了 `configuration_options` 块，但没有一条条目匹配本解析
    # 器认识的形状（比如 Insight 直接用选项名当键 `display_timers =
    # {label=...}`，而不是数组条目 `{name="display_timers", label=...}`）
    # ——整套 schema 没识别出来，config_options 会是空的，界面要能说明
    # 原因，不能暗示这个 mod 没有配置。
    unsupported_schema: bool = False
    # 本次会话是否已经为这个 mod 尝试过 resolve_full_modinfo()（不管成
    # 败都会设置），避免弹窗反复打开时重复跑一遍较慢的沙箱解析。
    full_sandbox_tried: bool = False
    # modinfo.lua 的 `client_only_mod = true`：这个 mod 只影响客户端，
    # 不需要通过 modoverrides.lua 同步，是游戏 mod 界面用来标"本地模组"
    # 的字段。
    # **坑**（DontStarveLuaJIT2 作者确认过）：`server_only_mod` 引擎本身
    # 不读，是给第三方开服工具用的约定——`client_only_mod=true` 同时写
    # `server_only_mod=true`，是想让这类工具仍当"服务器 mod"处理（配置
    # 能在 modoverrides.lua 编辑），只是不进游戏内"服务器 mod 列表"。判
    # 定要不要走"客户端专属、只读"分支时，`server_only_mod`/
    # `all_clients_require_mod` 有一个为真就要盖过 `client_only_mod`。
    client_only: bool = False
    # 本次会话是否已经为这个 mod 尝试过叠加"Chinese++ Pro"（创意工坊
    # workshop-2941527805）的配置项翻译——同 full_sandbox_tried 一样，
    # 只是个会话内的一次性开关，不管有没有成功叠加都会设置，避免弹窗
    # 重复打开时反复起沙箱子进程。见 features/mod/chs_translation.py。
    chs_translation_tried: bool = False


def visible_config_options(
    config_options: list[ModConfigOption],
) -> list[ModConfigOption]:
    """过滤掉标记为 client=true 的纯客户端配置项（见 ModConfigOption.client
    上的说明），供 ModConfigDialog 渲染前调用。

    按"标题 + 紧随其后的选项"分组处理，不是简单地逐条丢弃：如果某个分
    组标题（比如这个模组自己的"Client Settings"分区标题）底下的选项全
    部因为是纯客户端设置被过滤掉了，这个标题本身也一起去掉，不留一个
    后面空空如也的孤立标题。分组边界就是 config_options 列表里天然的
    顺序（is_header 的条目本身没有真实设置，只是视觉分隔符）。"""
    sections: list[tuple[ModConfigOption | None, list[ModConfigOption]]] = []
    current_header: ModConfigOption | None = None
    current_options: list[ModConfigOption] = []
    for opt in config_options:
        if opt.is_header:
            sections.append((current_header, current_options))
            current_header, current_options = opt, []
        else:
            current_options.append(opt)
    sections.append((current_header, current_options))

    result: list[ModConfigOption] = []
    for header, options in sections:
        visible = [o for o in options if not o.client]
        if not visible:
            continue
        if header is not None:
            result.append(header)
        result.extend(visible)
    return result


# ── Steam / Mod 路径发现 ─────────────────────────────────────────────

# 已知的 DST Steam Workshop App ID
DST_APP_ID = "322330"
_MAX_PUBLISHED_FILE_ID = (1 << 64) - 1


def is_workshop_content_id(value: str | int) -> bool:
    """是否为 Steam ``content/322330/<PublishedFileId_t>`` 的标准目录名。

    Steam Workshop ID 是非零 ``uint64``，落盘时使用无前导零的 ASCII
    十进制字符串。不能用 ``str.isdigit()``：它也接受全角数字等 Unicode
    字符；也不能限制十位，PublishedFileId_t 并没有这个长度约束。
    """
    text = str(value)
    if not text or not text.isascii() or not text.isdecimal():
        return False
    if text[0] == "0":
        return False
    try:
        return int(text) <= _MAX_PUBLISHED_FILE_ID
    except ValueError:
        return False


def is_custom_steam_mod_id(value: str | int) -> bool:
    """是否为 Steam 游戏 ``mods/`` 下非标准 Workshop 命名的自定义 Mod。

    Steam 扫描会把创意工坊项目统一成 ``workshop-<PublishedFileId_t>``，
    手动放进游戏 ``mods/`` 目录的 Mod 则保留真实文件夹名，例如
    ``CommonModSets``。分类不依赖某台机器的盘符或 Steam 安装位置。
    """
    text = str(value)
    prefix = "workshop-"
    return not (text.startswith(prefix) and is_workshop_content_id(text[len(prefix) :]))


def split_installed_mod_counts(mod_ids, platform: Platform) -> tuple[int, int]:
    """返回 ``(普通模组数, 自定义模组数)``，供相关 Mod 页统一统计。

    自定义目录是 Steam ``mods/`` 的命名约定；WeGame 的 Mod ID 本来就不
    带 ``workshop-`` 前缀，不能套用这条规则，因此全部计入普通模组。
    """
    ids = list(mod_ids)
    if platform != Platform.STEAM:
        return len(ids), 0
    custom = sum(1 for mod_id in ids if is_custom_steam_mod_id(mod_id))
    return len(ids) - custom, custom


def find_workshop_dir() -> Path | None:
    """查找 DST workshop 内容目录。

    **坑**：这里以前只查 find_steam_root() 返回的"随便一个"根目录（还是
    硬编码猜开发者自己机器路径的弱版本），DST 装在非默认 Steam 库、或者
    Steam 装在别的机器上跟这里硬编码的路径对不上时，就永远找不到——mod
    图标/名称/配置项全都读不出来，正是这个原因。现在遍历
    find_all_steam_libraries()（注册表读真实值，含全部库文件夹）里每一个
    库，不只是第一个。"""
    for steam in find_all_steam_libraries():
        workshop = steam / "steamapps" / "workshop" / "content" / DST_APP_ID
        if workshop.exists():
            return workshop
    return None


def is_mod_subscribed(workshop_id: str) -> bool:
    """判断某个 workshop mod 是否已订阅。本地判断依据：workshop 内容目录
    下有没有对应子文件夹、且带 modinfo.lua（确认下载完整，不是空目录/半
    途）——订阅是 Steam 账号操作，DSTCamp 没有 API 能代劳，也没有比"本地
    内容在不在"更权威的判断。"""
    workshop_dir = find_workshop_dir()
    if workshop_dir is None or not is_workshop_content_id(workshop_id):
        return False
    candidate = workshop_dir / workshop_id
    return candidate.exists() and (candidate / "modinfo.lua").exists()


def find_shared_ugc_directory() -> Path | None:
    """专用服务器 `-ugc_directory` 启动参数要用的路径——真机验证过：直接
    传这台机器 Steam 自己维护的 `steamapps/workshop` 目录（`content/322330/
    <id>/` + `appworkshop_322330.acf` 都已经在这儿），服务器会直接读取，
    完全不会在每个 cluster/shard 下再各建一份 `ugc_mods` 副本——之前
    features/mod/sync.py 把每个 V2 Mod 的内容复制进
    `ugc_mods/<cluster>/<shard>/content/322330/<id>/`（外加复制校验文件）
    的做法已经被这个参数取代：一份内容所有存档共享，客户端更新了服务器
    立刻用到最新版本，不用重新同步。找不到 Steam 库就返回 None，调用方
    （dedicated_server.py 的 build_launch_args）按"不传这个参数，服务器
    退回默认的按 cluster/shard 各自建 ugc_mods"处理，不是错误。"""
    for steam in find_all_steam_libraries():
        workshop = steam / "steamapps" / "workshop"
        if (workshop / "content" / DST_APP_ID).exists():
            return workshop
    return None


def find_game_mods_dir() -> Path | None:
    """查找 DST 游戏 mods 目录（手动安装的 mod）。

    用户手动确认过的覆盖路径（app_settings.get_steam_mods_path()，"Mod管
    理"页签"更换路径"按钮设置）优先——跟 dedicated_server.
    find_dedicated_server_dir() 先查 get_dedicated_server_path() 是同一个
    "手动兜底"套路。没设置过/设置的路径不存在了才走自动识别。
    """
    from dstools.shared import app_settings

    override = app_settings.get_steam_mods_path()
    if override and override.exists() and not is_dedicated_server_mods_dir(override):
        return override

    for steam in find_all_steam_libraries():
        mods = steam / "steamapps" / "common" / "Don't Starve Together" / "mods"
        if mods.exists():
            return mods
    return None


def is_dedicated_server_mods_dir(path: Path) -> bool:
    """路径是否就是独立专服的目标 ``mods``，用于阻止源目标自指。

    未安装客户端时，旧逻辑会把专服 ``mods`` 回退成“客户端源目录”，最终
    让软连接的源和目标完全相同。这里同时识别用户保存的专服安装路径和
    Steam 各库中的标准安装路径；只做路径比较，不修改用户设置。
    """
    from dstools.shared import app_settings

    candidate = Path(path)
    targets: list[Path] = []
    configured = app_settings.get_dedicated_server_path()
    if configured is not None:
        targets.append(Path(configured) / "mods")
    for steam in find_all_steam_libraries():
        targets.append(
            steam
            / "steamapps"
            / "common"
            / "Don't Starve Together Dedicated Server"
            / "mods"
        )
    for target in targets:
        try:
            if candidate.resolve(strict=False) == target.resolve(strict=False):
                return True
        except OSError:
            if (
                str(candidate.absolute()).casefold()
                == str(target.absolute()).casefold()
            ):
                return True
    return False


# ── WeGame(Rail) / Mod 路径发现 ──────────────────────────────────────
#
# WeGame 没有 Steam Workshop 那套独立内容缓存（steamapps/workshop/content/
# <appid>/ 这种）——真机验证 + 多方社区资料互相印证过：所有 mod 内容都
# 直接放在两个产品各自的 mods/ 文件夹里，没有第二套机制，也就用不上
# -ugc_directory 那一套。WeGame 的 rail_apps 安装根目录没有可靠的注册表
# 项能查（不像 Steam），只能读用户手动确认过的路径。


def _find_wegame_product_dir(root: Path, name_prefix: str) -> Path | None:
    """在 WeGame 根目录(rail_apps)下按前缀通配匹配"饥荒：联机版(数字)"/
    "饥荒联机版专用服务器(数字)"这类文件夹——具体数字 ID 不同安装可能不
    一样，不能写死，用 glob 通配，选第一个真的有 mods/ 子目录的匹配项。"""
    if not root.exists():
        return None
    for candidate in sorted(root.glob(f"{name_prefix}(*)")):
        if (candidate / "mods").exists():
            return candidate
    return None


def find_wegame_client_dir(wegame_root: Path) -> Path | None:
    """WeGame 版《饥荒：联机版》客户端安装目录（wegame_root 是 rail_apps
    那一层，来自 app_settings.get_wegame_root_path()，调用方负责取）。"""
    return _find_wegame_product_dir(wegame_root, "饥荒：联机版")


def find_wegame_server_dir(wegame_root: Path) -> Path | None:
    """WeGame 版《饥荒联机版专用服务器》安装目录。"""
    return _find_wegame_product_dir(wegame_root, "饥荒联机版专用服务器")


def resolve_wegame_client_mods_dir(platform: Platform) -> Path | None:
    """给 find_mod_folder() 用的 wegame_client_mods_dir 参数——Steam 平台
    不需要这个参数，永远返回 None；WeGame 平台读用户手动选过的
    app_settings.get_wegame_root_path()，没设置过就是 None（调用方应优雅
    处理成"这个 mod 没有名字/图标"，不弹目录选择框打扰用户，真要设置见
    "Mod管理"页签的"同步到服务器"按钮）。"""
    if platform != Platform.WEGAME:
        return None
    from dstools.shared.app_settings import get_wegame_root_path

    root = get_wegame_root_path()
    if not root:
        return None
    client_dir = find_wegame_client_dir(root)
    return client_dir / "mods" if client_dir else None


def find_mod_folder(
    workshop_id: str,
    platform: Platform = Platform.STEAM,
    wegame_client_mods_dir: Path | None = None,
) -> Path | None:
    """按给定的 workshop ID 查找 mod 文件夹。

    Steam(默认): Workshop content dir (<steam>/steamapps/workshop/content/
    322330/<id>/) 优先，再退回 game mods dir (<steam>/steamapps/common/
    Don't Starve Together/mods/<id>/)。

    WeGame: 没有 Workshop 内容缓存那一套（真机验证过），只查
    wegame_client_mods_dir（调用方传入，来自
    find_wegame_client_dir(root)/"mods"，root 是用户手动选过的 WeGame 安
    装根目录）——**坑**：以前这里不分平台，一律走 Steam 这两条路径，导致
    WeGame 存档的 mod 图标/名称/配置项全都解析到了错误（或者根本不存在）
    的 Steam 目录下。

    Args:
        workshop_id: 完整的 workshop ID，如 "workshop-2797939615"，
                    或者只是数字部分 "2797939615"。

    Returns:
        mod 文件夹路径，找不到则返回 None。
    """
    raw_id = str(workshop_id)
    mod_id = raw_id.removeprefix("workshop-")
    canonical_id = f"workshop-{mod_id}" if is_workshop_content_id(mod_id) else raw_id

    if platform == Platform.WEGAME:
        game_mods = wegame_client_mods_dir
    else:
        workshop_dir = find_workshop_dir()
        if workshop_dir and is_workshop_content_id(mod_id):
            candidate = workshop_dir / mod_id
            if candidate.exists() and (candidate / "modinfo.lua").exists():
                return candidate
        game_mods = find_game_mods_dir()

    if game_mods:
        candidate = game_mods / canonical_id  # Workshop 纯数字输入也规范为带前缀
        if candidate.exists() and (candidate / "modinfo.lua").exists():
            return candidate
        # 也试一下不带前缀的
        candidate = game_mods / mod_id
        if candidate.exists() and (candidate / "modinfo.lua").exists():
            return candidate

    return None


def list_installed_mod_ids(
    platform: Platform = Platform.STEAM, wegame_client_mods_dir: Path | None = None
) -> list[str]:
    """枚举每一个已安装 mod 的 ID（形式跟它作为 modoverrides.lua 键时一
    致）——同时扫描 Steam Workshop 内容目录和游戏本地 mods/ 目录。

    modoverrides.lua 里只会列出玩家*碰过*的 mod（启用过，或者启用后又
    显式禁用过)——一个刚订阅、玩家从没打开过配置/开关的 mod 根本不会出
    现在里面。游戏内 mod 界面仍然会显示它（显示为禁用），做法是列出每
    个已安装的 mod 文件夹再跟 modoverrides.lua 交叉核对，而不是直接遍
    历 modoverrides.lua 本身。这个函数照搬了同样的做法。

    **坑**：以前这里不分平台，一律扫 Steam 的两个目录，导致查看 WeGame
    存档时，Steam 本地装的 mod 也会混进"已安装"列表里显示出来（WeGame 的
    mod id 是 19 位长数字，跟 Steam 数字 ID 长度明显不同，混进去很显眼）。
    platform=Platform.WEGAME 时只扫 wegame_client_mods_dir（调用方传入，
    见 find_mod_folder() 同款参数的说明），不碰 Steam 那两个目录。
    """
    ids = []
    seen = set()

    if platform == Platform.WEGAME:
        if wegame_client_mods_dir and wegame_client_mods_dir.exists():
            for child in sorted(wegame_client_mods_dir.iterdir()):
                if (
                    child.is_dir()
                    and (child / "modinfo.lua").exists()
                    and child.name not in seen
                ):
                    seen.add(child.name)
                    ids.append(child.name)
        return ids

    workshop_dir = find_workshop_dir()
    if workshop_dir and workshop_dir.exists():
        for child in sorted(workshop_dir.iterdir()):
            if (
                child.is_dir()
                and is_workshop_content_id(child.name)
                and (child / "modinfo.lua").exists()
            ):
                wid = "workshop-" + child.name
                if wid not in seen:
                    seen.add(wid)
                    ids.append(wid)

    game_mods = find_game_mods_dir()
    if game_mods and game_mods.exists():
        for child in sorted(game_mods.iterdir()):
            if child.is_dir() and (child / "modinfo.lua").exists():
                wid = child.name
                if wid not in seen:
                    seen.add(wid)
                    ids.append(wid)

    return ids


# ── modinfo.lua 解析器 ───────────────────────────────────────────────


def _workshop_id_from_folder(mod_folder: Path) -> str:
    """按标准 Workshop 命名把 mod 文件夹名换成 "workshop-<id>"——本地/手动
    装的 mod 文件夹名本来就没有这个前缀，Workshop 订阅内容的文件夹名是
    裸的数字 ID，两种情况统一成同一个约定。这也是真实游戏引擎注入进每个
    modinfo.lua 执行环境的 `folder_name` 全局变量的值（真机验证过：
    `modindex.lua` 的 `ModIndex:InitializeModInfo()` 直接把这个 mod 的标
    识符设成 `env.folder_name`），沙箱执行 modinfo.lua 时也要提供同一个
    值，见 resolve_full_modinfo() 调用 lua_sandbox.resolve_full_config_
    options() 时传的 folder_name 参数。"""
    return (
        "workshop-" + mod_folder.name
        if not mod_folder.name.startswith("workshop-")
        else mod_folder.name
    )


def parse_modinfo(mod_folder: Path) -> ModInfo | None:
    """解析一个 mod 的 modinfo.lua，提取元数据和配置项。

    Args:
        mod_folder: 含 modinfo.lua 的 mod 文件夹路径。

    Returns:
        ModInfo 对象，若 modinfo.lua 无法解析则返回 None。
    """
    modinfo_path = mod_folder / "modinfo.lua"
    if not modinfo_path.exists():
        return None

    text = modinfo_path.read_text(encoding="utf-8", errors="replace")
    text = _strip_lua_comments(text)

    workshop_id = _workshop_id_from_folder(mod_folder)
    info = ModInfo(workshop_id=workshop_id)

    # 简单的顶层字段（name/author/version/icon/.../description）通常只
    # 在 configuration_options 之前被有意义地赋值一次——在*整个*文件里
    # 搜索比如 `name = "..."` 有风险，可能匹配到 configuration_options
    # 深处某个选项里同名的字段（有个真实 mod 就踩了这个坑：它顶层的
    # `name` 用了 _extract_string 认不出的语法
    # `name = Ch and [[中文]] or [[English]]`，导致搜索落空、继续找文
    # 件里*下一个* `name = "..."`，而那恰好是一个字面叫 "Language" 的
    # 配置选项——结果是悄悄取到一个错误值，而不是干脆没找到名字）。把搜
    # 索范围限制在 configuration_options 之前的文本能彻底排除这种情况。
    idx = text.find("configuration_options")
    header = text[:idx] if idx != -1 else text

    _extract_string(header, "name", info)
    _extract_string(header, "author", info)
    _extract_string(header, "version", info)
    _extract_string(header, "icon", info)
    _extract_string(header, "icon_atlas", info)
    _extract_description(header, info)

    def _flag(name: str) -> bool:
        fm = re.search(rf"\b{name}\s*=\s*(true|false)\b", header)
        return bool(fm) and fm.group(1) == "true"

    # server_only_mod/all_clients_require_mod 只要有一个为真，就说明作者
    # 明确想让这个 mod 被"开服工具"当服务器 mod 处理（配置走
    # modoverrides.lua，不是只读）——盖过 client_only_mod 的默认结论。见
    # ModInfo.client_only 字段上的说明。
    if _flag("client_only_mod") and not (
        _flag("server_only_mod") or _flag("all_clients_require_mod")
    ):
        info.client_only = True

    # 解析 configuration_options 表
    config_opts = _extract_configuration_options(text)
    if config_opts is not None:
        info.config_options = config_opts

    if idx != -1:
        info.dynamic_preamble = header
        if not info.config_options and _has_nontrivial_table(text, idx):
            info.unsupported_schema = True

    return info


def _extract_quoted(text: str, key: str) -> str | None:
    """查找 `key = "字面量"`（任一引号风格）——如果值用的是 DST 自己的某
    种本地化约定，则取其中的中文变体：

    - 常见的双语三元惯用写法（Lua 没有 ?: 运算符，mod 通常写成
      `key = Ch and "中文" or "English"`，按语言选一个字符串）——取第一
      个字面量，按约定，当条件变量命名像是语言检查（Ch/isCh/ZH/或从
      locale 派生）时，第一个就是中文那个。
    - `key = ChooseTranslationTable({["zh"]="中文", ["en"]="English"})`
      或形状相同的裸表 `key = {"default", ["zh"]="中文", ...}`——这是
      DST 自己*官方*的约定，从游戏实际的 modindex.lua 里确认过：
      ModIndex:InitializeModInfo() 会专门为此给每份 modinfo.lua 的执行
      环境提供一个 `ChooseTranslationTable(tbl) -> tbl[locale] or
      tbl[1]` 辅助函数。见 _extract_localized_table()。

    对三元惯用写法刻意收得很窄：只有紧跟在 `=` 后面的
    `IDENTIFIER and <字面量>` 才算数——`=` 和字面量之间但凡有更复杂的东
    西（字符串拼接、没有字面量的裸变量引用、循环下标），一律不匹配而不
    是去猜，因为盲目抓取"表达式里第一个字面量"可能悄悄得出一个错误（而
    不只是不精确）的答案——例如一个 for 循环里每次迭代都构造
    `i .. (ZH and "(默认)" or "(Default)") or i`，这确实需要真正执行
    Lua 才能算出来，必须保持未解析状态。

    返回原始（仍带 Lua 转义）的字符串内容，或 None。
    """
    m = re.search(rf"\b{re.escape(key)}\s*=\s*\w+\s+and\s+(?:{_QUOTED_ALT})", text)
    if m:
        first = _pick_quoted(m)
        # 三元惯用写法 `key = IDENT and A or B`：多数 mod 写 `Ch/ZH and
        # "中文" or "英文"`（A=中文），但也有 `L/EN and "英文" or "中文"`
        # （A=英文）这种反过来的。不靠条件变量命名去猜，直接看 A/B 哪个含
        # 汉字，取中文那个（都没有或都是中文就维持原样取 A）——这样两种写
        # 法都能拿到中文名。
        if not _contains_cjk(first):
            or_m = re.search(rf"\s+or\s+(?:{_QUOTED_ALT})", text[m.end() :])
            if or_m and _contains_cjk(_pick_quoted(or_m)):
                return _pick_quoted(or_m)
        return first
    # Island Adventures 等 Mod 使用一个非常直接的双语辅助函数：
    # ``name = en_zh("English", "中文")``。批量列表扫描不会执行任意
    # modinfo.lua，因此只识别这个已经从真实源码确认过、两个参数都是
    # 字符串字面量的安全形状，并取第二个中文参数；更复杂的函数调用仍
    # 保持未解析，交给按需 Lua 沙箱处理。
    m = re.search(
        rf"\b{re.escape(key)}\s*=\s*en_zh\s*\(\s*"
        rf"(?:{_QUOTED_ALT})\s*,\s*(?:{_QUOTED_ALT})\s*\)",
        text,
        re.DOTALL,
    )
    if m:
        return m.group(3) if m.group(3) is not None else m.group(4)
    # 同样的惯用写法，但用 `[[...]]` 长括号字符串而不是带引号的——例如
    # `name =\nCh and\n[[ 卡尼猫]] or\n[[ Carney]]`（真实 mod 的例子；
    # 也说明这可以跨多行，\s* 本来就能匹配换行符，天然兼容）。
    m = re.search(
        rf"\b{re.escape(key)}\s*=\s*\w+\s+and\s+\[\[(.*?)\]\]", text, re.DOTALL
    )
    if m:
        return m.group(1)
    m = re.search(rf"\b{re.escape(key)}\s*=\s*(?:{_QUOTED_ALT})", text)
    if m:
        return _pick_quoted(m)
    return _extract_localized_table(text, key)


def _extract_localized_table(text: str, key: str) -> str | None:
    """查找 `key = ChooseTranslationTable({...})` 或裸表
    `key = {"default", ["zh"] = "...", ...}`——DST 自己官方的逐字段本地
    化约定（见 _extract_quoted 的 docstring）。优先取显式的
    `["zh"]`/`['zh']` 条目；否则退回到表里第一个裸（无键）字符串，跟
    ChooseTranslationTable 自己的 `tbl[locale] or tbl[1]` 兜底逻辑一致。

    返回原始（仍带 Lua 转义）的字符串内容，或 None。
    """
    m = re.search(
        rf"\b{re.escape(key)}\s*=\s*(?:ChooseTranslationTable\s*\(\s*)?(\{{)", text
    )
    if not m:
        return None
    brace_start = m.start(1)
    depth = 0
    end = None
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        return None
    block = text[brace_start : end + 1]

    zm = re.search(rf'\[\s*(?:"zh"|\'zh\')\s*\]\s*=\s*(?:{_QUOTED_ALT})', block)
    if zm:
        return _pick_quoted(zm)

    # 没有 zh 条目——退回到第一个裸（无键）字符串，也就是 tbl[1]
    # （`[key] = value` 形式的条目永远不算"裸"字符串，所以这天然会跳过
    # 其它每个语言的条目，找到目标）。
    for entry_m in re.finditer(rf"{_QUOTED_ALT}", block):
        preceding = block[: entry_m.start()]
        if re.search(r'\[\s*[\'"]?\w*[\'"]?\s*\]\s*=\s*$', preceding):
            continue  # 这个字符串是某个 `[key] = "..."` 条目的值
        return _pick_quoted(entry_m)
    return None


def _extract_label_or_hover(
    block: str, key: str, local_tables: dict | None
) -> str | None:
    """按常规方式提取一个选项的 `label`/`hover`（_extract_quoted——字面
    量字符串，或 DST 自己的三元/ChooseTranslationTable 本地化惯用写
    法）——如果它是对一个本地表的单层点号引用（`label = configs.language`，
    真实 mod 自己"每个选项标签共享同一个字典"的约定——`options` 用同样
    约定的情况见 _extract_choices 的 docstring），先解析这个引用，再对
    解析出来的值重新走一遍同样的提取。
    """
    val = _extract_quoted(block, key)
    if val is not None:
        return val
    raw = _extract_field_raw(block, key)
    if raw is None or "." not in raw:
        return None
    resolved = _resolve_dotted_ref(raw, local_tables)
    if resolved is None:
        return None
    # 把解析出来的值包装成一句合成的赋值语句，对它重新走一遍同样的字面
    # 量/三元/本地化表提取——复用 _extract_quoted，不用把它那三种兜底
    # 形状再抄一遍。
    return _extract_quoted(f"__resolved__ = {resolved}", "__resolved__")


def _extract_string(text: str, key: str, info: ModInfo):
    """提取一个简单的字符串字段，比如 name = \"...\" 或 author = \"...\"。"""
    quoted = _extract_quoted(text, key)
    if quoted is not None:
        setattr(info, key, _unescape_lua_string(quoted).strip())
        return
    # 匹配：key = 'value' 或 key = [[value]]
    patterns = [
        rf"{key}\s*=\s*\'([^\']*)\'",
        rf"{key}\s*=\s*\[\[(.*?)\]\]",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.DOTALL)
        if m:
            setattr(info, key, _unescape_lua_string(m.group(1)).strip())
            return


def _extract_description(text: str, info: ModInfo):
    """提取 description，可能是拼接起来的字符串。"""
    localized = _extract_quoted(text, "description")
    if localized is not None:
        info.description = _unescape_lua_string(localized).strip()
        return
    # 先试简单的带引号形式
    for pat in [rf'description\s*=\s*"({_QSTR})"', r"description\s*=\s*'([^']*)'"]:
        m = re.search(pat, text, re.DOTALL)
        if m:
            info.description = _unescape_lua_string(m.group(1)).strip()
            return

    # 再试 [[...]] 多行形式
    m = re.search(r"description\s*=\s*\[\[(.*?)\]\]", text, re.DOTALL)
    if m:
        info.description = m.group(1).strip()


def _extract_configuration_options(text: str) -> list[ModConfigOption] | None:
    """从 modinfo.lua 里提取并解析 configuration_options = { ... }。

    用基于文本的方式：找到 configuration_options 赋值语句，提取表内容
    块，再解析出各条选项。
    """
    # 查找 configuration_options = {
    idx = text.find("configuration_options")
    if idx == -1:
        return None

    # 找开花括号
    brace_start = text.find("{", idx)
    if brace_start == -1:
        return None

    # 找匹配的闭花括号（计数深度）
    depth = 0
    brace_end = brace_start
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                brace_end = i
                break

    if depth != 0:
        return None  # 花括号不匹配

    table_text = text[brace_start : brace_end + 1]
    local_functions = _find_local_functions(text)
    local_tables = _find_local_tables(text)

    # 从这张表里解析出各条选项
    return _parse_options_table(table_text, local_functions, local_tables)


def _has_nontrivial_table(text: str, idx: int) -> bool:
    """如果 `configuration_options`（位于 `idx`）后面的 `{ ... }` 里有任
    何实质内容则返回 True——用于区分"这个 mod 确实声明了零个选项"
    （`configuration_options = {}`）和"这个 mod 有选项，但没有一个匹配
    本解析器认识的形状"（ModInfo.unsupported_schema）两种情况。"""
    brace_start = text.find("{", idx)
    if brace_start == -1:
        return False
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return len(text[brace_start + 1 : i].strip()) > 10
    return False


def _find_local_tables(text: str) -> dict:
    """查找 `local NAME = { ... }` 形式的表字面量定义。

    有些 mod 通过一个局部变量在多个选项之间共享一份可选项列表（比如一
    张 `color_options` 表被红/绿/蓝三个滑块共用），而不是在每个选项里
    重复写一遍字面量表。
    返回：dict，名字 -> 表文本（含外层花括号）。
    """
    tables = {}
    for m in re.finditer(r"local\s+(\w+)\s*=\s*\n?\s*\{", text):
        name = m.group(1)
        brace_start = m.end() - 1
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    tables[name] = text[brace_start : i + 1]
                    break
    return tables


def _resolve_dotted_ref(expr: str, local_tables: dict | None) -> str | None:
    """针对 _find_local_tables 找到的 `local IDENT = {...}` 表，解析一个
    单层的 `IDENT.FIELD` 引用（例如 `configs.language`、
    `options.retrofit`）——返回该表内 FIELD 对应值的原始文本（一个带引
    号的字符串，或一张嵌套表），如果 `expr` 不是纯粹的单层点号查找、或
    者 IDENT 不是已知的本地表，则返回 None。

    这正是 mod 自己 `local options = {toggle = {...}, ...}` 这种"按名
    字索引的可选项列表字典"约定所需要的：`options.toggle` 必须精确解析
    到 "toggle" 这一条，而不是整张 "options" 表（把点号引用当成裸的
    "options" 处理——这里恰好也确实有一张叫这个名字的本地表——会悄悄把
    这个 mod 里*每一个*选项都解析成同一份合并后的列表，原因见
    _extract_choices 的 docstring）。
    """
    m = re.match(r"^(\w+)\.(\w+)$", expr.strip())
    if not m or not local_tables:
        return None
    ident, field = m.groups()
    table_text = local_tables.get(ident)
    if table_text is None:
        return None
    return _extract_field_raw(table_text, field)


def _find_local_functions(text: str) -> dict:
    """查找 `local function NAME(params) ... end` 形式的定义。

    很多 mod 会定义一个小的辅助函数（常见命名如 AddOption/MakeOption
    等），从几个位置字面量参数构建出一张选项表，然后在
    configuration_options 里反复调用它，而不是手写每一张表。这为
    _inline_helper_call() 把这类调用解析回它们会产生的表提供了支持，让
    这样定义的选项不会被悄悄丢掉（且保留它们在文件里的原始顺序）。

    返回：dict，名字 -> (params: list[str], body_text: str)
    """
    functions = {}
    for m in re.finditer(r"local\s+function\s+(\w+)\s*\(([^)]*)\)", text):
        name = m.group(1)
        params = [p.strip() for p in m.group(2).split(",") if p.strip()]
        start = m.end()
        # 粗略的基于关键字的深度计数器，用来找到这个函数自己对应的
        # "end"——对 mod 实际这样写的、短小单一用途的辅助函数体（几个字
        # 面量表字段，最多一个 if/then/else）已经够用。不是真正的 Lua
        # 解析器。
        depth = 1
        body_end = None
        for line_m in re.finditer(r".*\n?", text[start:]):
            line = line_m.group(0)
            if not line:
                break
            depth += len(re.findall(r"\b(?:if|for|while|function)\b", line))
            depth -= len(re.findall(r"\bend\b", line))
            if depth <= 0:
                body_end = start + line_m.end()
                break
        if body_end is None:
            continue
        functions[name] = (params, text[start:body_end])
    return functions


def _split_call_args(text: str, open_paren_idx: int):
    """给定一次调用的开圆括号 '(' 的下标，返回 (原始参数字符串列表, 紧
    跟在匹配的闭圆括号 ')' 之后的下标)——按顶层逗号切分，同时正确处理嵌
    套的圆括号/花括号和带引号字符串。"""
    depth = 1  # 已经在调用自己的开圆括号内部了
    i = open_paren_idx + 1
    in_str = None
    current = []
    args = []
    while i < len(text):
        ch = text[i]
        if in_str:
            current.append(ch)
            if ch == "\\" and i + 1 < len(text):
                i += 1
                current.append(text[i])
            elif ch == in_str:
                in_str = None
        elif ch in ('"', "'"):
            in_str = ch
            current.append(ch)
        elif ch in "({":
            depth += 1
            current.append(ch)
        elif ch in ")}":
            depth -= 1
            if depth == 0 and ch == ")":
                tail = "".join(current).strip()
                if tail:
                    args.append(tail)
                return args, i + 1
            current.append(ch)
        elif ch == "," and depth == 1:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    return None, None


def _inline_helper_call(name: str, args: list, local_functions: dict) -> str | None:
    """把一次类似 AddOption("key", "Label", "Hover", false) 的调用解析
    成它函数体本来会产生的字面量选项表文本——用调用处的字面量参数文本
    替换函数的形参，并解析任何单层的字面量 if/else。

    返回合成出来的表文本（能被 _parse_single_option 解析），如果被调用
    的不是一个认识的本地辅助函数、某个参数不是能安全替换的纯字面量、
    或者函数体化简不到一张单独的字面量表，则返回 None——不管哪种
    "None" 情况，调用方都会跳过这条记录，而不是去猜它的值。
    """
    if name not in local_functions:
        return None
    params, body = local_functions[name]
    if len(args) > len(params):
        return None

    subst = dict(zip(params, args))
    # 调用处没提供的形参（比如一个可选的尾部参数）按 Lua 隐式 `nil`
    # 处理。
    for param in params[len(args) :]:
        subst[param] = "nil"

    # 参数名（比如 "name"、"hover"）经常跟表自己的字段键
    # （"name = ..."、"hover = ..."）撞名，这些绝不能被替换——只有那些
    # 不紧跟裸 "=" 的引用才是参数被*使用*的地方，用负向先行断言跳过
    # "key = " 这种位置。它还经常跟函数体里*另一个*字符串字面量内容里
    # 出现的普通英文单词撞名（一个真实例子：参数名叫 "default"，同一函
    # 数体里恰好有个内容是 "default" 的英文兜底字符串）——
    # _replace_idents_outside_strings 会跳过引号内的一切内容，这种情况
    # 也不会发生。替换分两遍进行，先换成不透明的占位符，这样第二遍替
    # 换时，一个参数的字面量文本内容永远不会被误当成另一个参数的名字
    # （比如一个恰好含有 "hover" 内容的悬浮提示字符串）。
    placeholders = {param: f"\x00{i}\x00" for i, param in enumerate(subst)}
    result = _replace_idents_outside_strings(body, placeholders)
    for param, placeholder in placeholders.items():
        result = result.replace(placeholder, subst[param])

    # 解析单条字面量 "if <字面量> == <字面量> then A else B end"（这是
    # AddOption 风格辅助函数选取默认开/关措辞常用的形状）。只处理已经
    # 替换完成的字面量比较；其它情况都意味着这次调用没法安全化简，直
    # 接放弃。
    if_m = re.search(r"if\s+(.+?)\s+then\b(.*?)\belse\b(.*?)\bend\b", result, re.DOTALL)
    if if_m:
        cond, then_branch, else_branch = if_m.groups()
        cond_m = re.match(r"\s*(\S+)\s*(==|~=)\s*(\S+)\s*$", cond.strip())
        if not cond_m:
            return None
        lhs, op, rhs = cond_m.groups()
        lhs, rhs = lhs.strip("\"'"), rhs.strip("\"'")
        is_eq = lhs == rhs
        taken = then_branch if (is_eq == (op == "==")) else else_branch
        result = result[: if_m.start()] + taken + result[if_m.end() :]

    ret_m = re.search(r"return\s*(\{.*)", result, re.DOTALL)
    if not ret_m:
        return None
    brace_start = ret_m.start(1)
    depth = 0
    for i in range(brace_start, len(result)):
        if result[i] == "{":
            depth += 1
        elif result[i] == "}":
            depth -= 1
            if depth == 0:
                return result[brace_start : i + 1]
    return None


def _parse_options_table(
    table_text: str,
    local_functions: dict | None = None,
    local_tables: dict | None = None,
) -> list[ModConfigOption]:
    """用基于文本的提取方式解析 configuration_options 表里的各条条目。

    每条条目要么是一张字面量表：
    {
        name = "option_name",
        label = "Display Label",
        hover = "Tooltip text",
        options = {
            {description = "Desc1", data = value1},
            {description = "Desc2", data = value2},
        },
        default = value,
    }
    要么是对本地定义的辅助函数的调用（比如 AddOption(...)、
    AddOptionHeader(...)），由 _inline_helper_call() 解析回同样的形状
    ——见其 docstring。返回列表里的顺序总是跟条目在源文件里出现的顺序
    一致。
    """
    local_functions = local_functions or {}
    options = []
    inner = table_text[1:-1]  # 去掉外层的 { }

    i = 0
    while i < len(inner):
        while i < len(inner) and inner[i] in " \t\n\r,":
            i += 1
        if i >= len(inner):
            break

        if inner[i] == "{":
            depth = 0
            start = i
            while i < len(inner):
                if inner[i] == "{":
                    depth += 1
                elif inner[i] == "}":
                    depth -= 1
                    if depth == 0:
                        block = inner[start : i + 1]
                        opt = _parse_single_option(block, local_tables)
                        if opt:
                            options.append(opt)
                        i += 1
                        break
                i += 1
        else:
            call_m = re.match(r"(\w+)\s*\(", inner[i:])
            if call_m:
                paren_idx = i + call_m.end() - 1
                args, after = _split_call_args(inner, paren_idx)
                if args is not None:
                    block = _inline_helper_call(call_m.group(1), args, local_functions)
                    if block:
                        opt = _parse_single_option(block, local_tables)
                        if opt:
                            if opt.is_header and not opt.label.strip() and args:
                                opt.label = _unescape_lua_string(args[0].strip("\"'"))
                            options.append(opt)
                    i = after
                    continue
            i += 1

    return options


def _parse_single_option(
    block: str, local_tables: dict | None = None
) -> ModConfigOption | None:
    """解析单个配置选项块。"""
    opt = ModConfigOption(name="")

    # 提取 name
    m = re.search(rf"name\s*=\s*(?:{_QUOTED_ALT})", block)
    if not m:
        return None  # 没有 name 的选项是标题/分隔符，跳过
    opt.name = _unescape_lua_string(_pick_quoted(m))

    # 提取 label
    label = _extract_label_or_hover(block, "label", local_tables)
    if label is not None:
        opt.label = _unescape_lua_string(label)

    # 提取 hover
    m = re.search(r"hover\s*=\s*\[\[(.*?)\]\]", block, re.DOTALL)
    if m:
        opt.hover = m.group(1).strip()
    else:
        hover = _extract_label_or_hover(block, "hover", local_tables)
        if hover is not None:
            opt.hover = _unescape_lua_string(hover)

    # 提取 default。这里要感知花括号/引号深度（不是简单的"匹配到下一个
    # 逗号为止"正则），因为 default 本身可能是一张 Lua 表，比如
    # `default = {}` 或 `default = {["1"] = 8}`——朴素的 `[^,\n}]+` 模式
    # 会在第一个内部逗号/花括号处就停下，悄悄截断它（只捕获到 "{"）。
    default_raw = _extract_field_raw(block, "default")
    if default_raw is not None:
        opt.default = _coerce_lua_value(default_raw)

    opt.client = bool(re.search(r"\bclient\s*=\s*true\b", block))
    opt.is_set_config = bool(re.search(r"\bis_set_config\s*=\s*true\b", block))
    opt.is_array_config = bool(re.search(r"\bis_array_config\s*=\s*true\b", block))
    opt.is_text_config = bool(re.search(r"\bis_text_config\s*=\s*true\b", block))
    opt.is_dictionary_config = bool(
        re.search(r"\bis_dictionary_config\s*=\s*true\b", block)
    )

    opt.choices = _extract_choices(block, local_tables)

    # 分区标题/分隔符条目，不是真实设置——两个独立的信号，任一成立即可：
    #  - name == ""：没有键，永远没法存回 modoverrides.lua，所以游戏自
    #    己不管 `options` 长什么样，都会把它当成纯展示用。
    #  - 单个描述为空的选项：有些 mod 自己写了个标题辅助函数（比如一个
    #    自定义的 `AddTitle(title)`，返回
    #    `{name="null", label=title, options={{description="",data=0}}}`），
    #    用 "null" 这样的占位名字而不是 ""——这种空描述单选项跟
    #    AddOptionHeader 产生的非交互式标题是同一种形状，只是写法不同。
    if opt.name == "" or (
        len(opt.choices) == 1 and opt.choices[0].get("description") == ""
    ):
        opt.is_header = True
    elif not opt.choices and re.search(r"\boptions\s*=", block):
        # 作者确实声明了一个 `options` 表，但 _extract_choices 解析出来
        # 是空的——不是"没有可选项"，而是"解析不出它们是什么"。两种已知
        # 形状：`options = SomeFunction(args)`（一个在运行时构建列表的
        # 辅助函数，比如从字号表生成）和 `options = someVar`，其中
        # someVar 不是字面量 `local someVar = {...}`，而是用 for 循环一
        # 点点拼出来的——两者都需要真正执行 Lua 才能解析，这个基于文本
        # 的解析器刻意不去尝试（按需真正执行的沙箱化解析见
        # lua_sandbox.resolve_dynamic_option()）。
        opt.is_dynamic = True
        opt.raw_options_expr = _extract_field_raw(block, "options") or ""

    return opt


def _extract_field_raw(block: str, key: str) -> str | None:
    """在一个 Lua 表块里查找 `key = <value>`，只返回 <value> 的原始文
    本——在该字段自己的顶层逗号或外层块的闭花括号处停止。

    这里对花括号/方括号/圆括号/引号深度都敏感，不是简单的"匹配到下一个
    逗号或花括号为止"正则：一个值本身是 Lua 表的字段（例如
    `data = {["1"] = "World One", ["2"] = "World Two"}`）或者带多个参数
    的函数调用（例如 `options = GenerateOptionsFromList(true, FONTS)`）
    自己就含有逗号和括号，朴素的 `[^,}]+` 模式会立刻在那里停下——悄悄截
    断/破坏这个值（这正是以前每个 `data`/`default` 是表而不是简单标量
    的 mod 选项都会解析出错的原因）。
    """
    m = re.search(rf"\b{re.escape(key)}\s*=\s*", block)
    if m is None:
        return None
    i = m.end()
    n = len(block)
    start = i
    depth = 0
    in_str = None
    while i < n:
        ch = block[i]
        if in_str:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ('"', "'"):
            in_str = ch
        elif ch in "{[(":
            depth += 1
        elif ch in "}])":
            if depth == 0:
                break  # 外层块自己的闭括号
            depth -= 1
        elif ch == "," and depth == 0:
            break
        i += 1
    return block[start:i].strip()


def _extract_choices(block: str, local_tables: dict | None = None) -> list[dict]:
    """从一个配置选项块里提取 options 可选项。

    `options` 通常是一张字面量 `{ ... }` 表，但有些 mod 通过局部变量在
    多个选项之间共享可选项——要么是整体共享（`local color_options = {...}`
    然后 `options = color_options`），要么更常见的是共享一张*按名字*索
    引的可选项列表字典，每个选项按字段取自己那份
    （`local options = {toggle = {...}, volume = {...}, ...}` 然后
    `options = options.toggle`、`options = options.volume` 等——确实有
    真实 mod 这样写，而且把这张共享表本身命名为 "options" 相当常见，所
    以必须处理带字段访问的后缀，不能只处理裸表查找：如果只认
    `options = options` 而忽略 `.toggle`/`.volume` 部分，会把每一个选
    项都解析成*同一张*整个共享表，而不是它自己对应的那一条）。
    """
    # 锚定在紧跟 `=` 的 `options`（不只是"这个块里某处出现了 options 这
    # 个单词"）——一个简单的 block.find("options") 可能匹配到一段完全
    # 不相干的 hover/label 字符串里的这个单词（一个真实 mod 的悬浮提示
    # 文本写着 "Note: Some options below may affect..."，结果匹配到的
    # 是这里，而不是几行之后真正的 `options = {...}` 字段，导致整个可
    # 选项列表悄悄变成空的）。
    field_m = re.search(r"\boptions\s*=", block)
    if not field_m:
        return []

    # 找出 "options" 被赋值成了什么：一个字面量 "{"，或者对某个本地表
    # 变量的裸标识符/`identifier.field` 引用。
    m = re.match(r"\s*(\{)|\s*(\w+(?:\.\w+)?)", block[field_m.end() :])
    if not m:
        return []

    if m.group(1):
        brace_start = field_m.end() + m.start(1)
        depth = 0
        brace_end = brace_start
        for i in range(brace_start, len(block)):
            if block[i] == "{":
                depth += 1
            elif block[i] == "}":
                depth -= 1
                if depth == 0:
                    brace_end = i
                    break
        options_text = block[brace_start : brace_end + 1]
    else:
        ref = m.group(2)
        if "." in ref:
            options_text = _resolve_dotted_ref(ref, local_tables)
        else:
            options_text = local_tables.get(ref) if local_tables else None
        if options_text is None:
            return []

    # 用花括号深度遍历（跟外层选项表循环同样的做法）来解析每条
    # `{description=..., data=..., hover=...}`，而不是对整个 options_text
    # 用一个单一的正则——一条选项自己的 `data` 可能是一张嵌套表，含有花
    # 括号/逗号，否则单趟正则会分不清一条选项在哪里结束、下一条从哪里
    # 开始。
    choices = []
    inner = options_text[1:-1] if len(options_text) >= 2 else ""
    i = 0
    n = len(inner)
    while i < n:
        while i < n and inner[i] in " \t\r\n,":
            i += 1
        if i >= n or inner[i] != "{":
            i += 1
            continue
        depth = 0
        start = i
        while i < n:
            if inner[i] == "{":
                depth += 1
            elif inner[i] == "}":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1
        choice_block = inner[start:i]

        desc_raw = _extract_quoted(choice_block, "description")
        if desc_raw is None:
            continue
        desc = _unescape_lua_string(desc_raw)
        data_raw = _extract_field_raw(choice_block, "data")
        data = _coerce_lua_value(data_raw) if data_raw is not None else None
        hover_raw = _extract_quoted(choice_block, "hover")
        hover = _unescape_lua_string(hover_raw) if hover_raw is not None else ""
        choices.append({"description": desc, "data": data, "hover": hover})

    return choices


def _coerce_lua_value(val_str: str) -> Any:
    """把一个 Lua 字面量字符串（标量或表）转换成 Python 值。

    交给真正的 Lua tokenizer/parser（parse_lua_value）处理，而不是手写
    标量判断逻辑，这样表形式的 default/data（例如 `{}`、
    `{["1"] = "World One"}`）解析出来的嵌套 dict 形状，跟
    load_mod_overrides() 对真实保存值产生的形状一致——否则两者在
    resolve_config_value() 里永远没法比较相等。
    """
    val_str = val_str.strip()
    try:
        return parse_lua_value(val_str)
    except Exception:
        return val_str


# ── 配置值解析 ────────────────────────────────────────────────────────


def resolve_config_value(mod_info: ModInfo, key: str, current_value: Any) -> tuple:
    """对某个 mod 配置键，确定其合法可选项列表和当前值。

    Args:
        mod_info: 已解析的 ModInfo。
        key: 配置键名。
        current_value: modoverrides.lua 里当前存储的值。

    Returns:
        (choices_list, current_display_value, is_valid) 元组。
        choices_list：{"description": str, "data": Any} 字典组成的列表。
    """
    for opt in mod_info.config_options:
        if opt.name == key:
            choices = opt.choices
            # 找出哪个选项匹配当前值
            current_display = str(current_value)
            for c in choices:
                if c["data"] == current_value:
                    current_display = c["description"]
                    break
            return choices, current_display, True

    # modinfo 里没有这个配置键——自由形式的值
    return [], str(current_value), False


# ── 整份文件的 Lua 沙箱解析 ───────────────────────────────────────────
#
# 下面全部内容都是"先尝试用真正的 Lua 解释器跑一遍整个 mod"这条路径
# （见 lua_sandbox.resolve_full_config_options），由 ModConfigDialog 按
# 需调用，代替/优先于上面基于静态正则的解析器。这里刻意跟静态解析器重
# 复了一点逻辑（标题检测、本地化值解析），而不是共用同一份：两边输入
# 的形状不同（这里是已经执行完的 Python 值，那边是原始源码文本），共
# 用一个辅助函数反而得同时兼容两种形状，实际省不下多少代码。


def _resolve_localized_value(val: Any) -> str:
    """给定一个 label/hover/description 字段已经执行完的值——可能是纯
    字符串，也可能是 DST 自己的逐字段本地化约定
    （`{"English", ["zh"] = "中文", ...}`，经过沙箱的 JSON 往返之后是
    形如 {"1": "English", "zh": "中文", ...} 的 dict，因为一张 Lua 数
    组的第一个元素和一个 "zh" 键能共存于同一张表）——如果有中文字符串
    就返回它，否则返回第一个位置元素，都没有就返回一个合理的字符串兜
    底。绝不抛异常。
    """
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        zh = val.get("zh")
        if isinstance(zh, str) and zh:
            return zh
        one = val.get("1")
        if isinstance(one, str):
            return one
        for v in val.values():
            if isinstance(v, str) and v:
                return v
        return ""
    return str(val)


def _choices_from_lua_value(val: Any) -> list[dict]:
    """把一个已经执行完的 `options` 值转换成 _extract_choices() 产出的
    同样形状。处理两种真实存在的 schema：普通的
    `{description=..., data=..., hover=...}` 表数组（常见约定），以及
    DST 的按标识符做键的约定（`{[false] = {description=...}, [true] = {...}}`，
    Insight 就用这个），这种约定经过 JSON 往返后变成一个按 data 值的
    *字符串*形式（"false"/"true"/"0" 等）为键的 dict —— _coerce_lua_value()
    （真正的 Lua tokenizer）把这个键转换回正确类型的值，这样才能跟
    modoverrides.lua 里实际保存的值比较相等。
    """
    if isinstance(val, list):
        choices = []
        for item in val:
            if not isinstance(item, dict) or "description" not in item:
                continue
            choices.append(
                {
                    "description": _resolve_localized_value(item.get("description")),
                    "data": item.get("data", item.get("description")),
                    "hover": _resolve_localized_value(item.get("hover")),
                }
            )
        return choices
    if isinstance(val, dict):
        choices = []
        for key, item in val.items():
            if not isinstance(item, dict):
                continue
            choices.append(
                {
                    "description": _resolve_localized_value(item.get("description")),
                    "data": _coerce_lua_value(key),
                    "hover": _resolve_localized_value(item.get("hover")),
                }
            )
        return choices
    return []


def _options_from_lua_result(result: Any) -> list[ModConfigOption] | None:
    """把一个 mod 的 `configuration_options` 全局变量已经执行完的值转换
    成 ModConfigOption 对象列表。

    处理两种真实存在的顶层形状：标准的
    `{name=..., label=..., options=..., default=...}` 表数组，以及
    DST 的按标识符做键的约定，每个选项直接用自己的名字做键
    （`{display_timers = {label=..., ...}, ...}`，Insight 就用这个）
    ——对这种形状，当表本身没有另外声明 name 字段时，dict 的键就成为
    这个选项的名字。

    如果 `result` 两种形状都不像（既不是列表也不是 dict，或者根本不含
    任何表条目），则返回 None——这种情况下调用方应该保留静态解析器已
    经算出来的结果，跟其它任何解析失败的处理方式一致。
    """
    entries: list[tuple[Any, dict]] = []
    if isinstance(result, list):
        entries = [(None, item) for item in result if isinstance(item, dict)]
    elif isinstance(result, dict):
        entries = [
            (key, item) for key, item in result.items() if isinstance(item, dict)
        ]
    else:
        return None
    if not entries:
        return None

    options = []
    for key, d in entries:
        opt = ModConfigOption()
        name = d.get("name")
        opt.name = (
            str(name)
            if name not in (None, "")
            else (str(key) if key is not None else "")
        )
        opt.label = _resolve_localized_value(d.get("label"))
        opt.hover = _resolve_localized_value(d.get("hover"))
        if "default" in d:
            opt.default = d["default"]
        opt.client = bool(d.get("client"))
        opt.is_set_config = bool(d.get("is_set_config"))
        opt.is_array_config = bool(d.get("is_array_config"))
        opt.is_text_config = bool(d.get("is_text_config"))
        opt.is_dictionary_config = bool(d.get("is_dictionary_config"))
        opt.choices = _choices_from_lua_value(d.get("options"))

        # 跟 _parse_single_option 同样的两个标题信号（见其 docstring）：
        # 没有名字可存，或者 mod 作者自己的标题辅助函数常见的单个空描
        # 述选项这种形状。
        if opt.name == "" or (
            len(opt.choices) == 1 and opt.choices[0].get("description") == ""
        ):
            opt.is_header = True
        elif not opt.choices and "options" in d:
            opt.is_dynamic = True

        options.append(opt)
    return options


def resolve_full_modinfo(mod_folder: Path, timeout: float | None = None) -> dict | None:
    """尝试通过真正把整份 modinfo.lua 丢进 Lua 沙箱运行（见 lua_sandbox.py）
    来解析一个 mod 的元数据和整个 `configuration_options`，代替本模块
    平常用的基于静态正则的解析器。

    应该按需尝试（用户打开某个具体 mod 的配置弹窗时——见 gui/app.py 的
    ModConfigDialog），绝不在批量扫描 mod 列表时调用；对这里解析不出来
    的部分，保留静态解析器已经算好的 ModInfo 原样不动——大多数
    modinfo.lua 会引用这个沙箱没有提供的 DST 引擎全局变量（GLOBAL、
    STRINGS、TheNet 等），这些会直接失败（很快）并照旧走兜底路径。一旦
    *真的*成功，它能一次性绕开所有静态解析的边界情况（Lua 注释、引号
    风格、共享表的点号引用、ChooseTranslationTable、有条件地重新赋值
    的局部变量/字段等）——让真正的 Lua 5.1 解释器去处理实际语法，而不
    是本模块一条正则一条正则地重新推导——这不仅覆盖配置选项，还覆盖比
    如某个 mod 的 `name` 在 `if locale == "zh" then ... end` 里被有条
    件地重新赋值成中文变体的情况，这是只抓文件里*第一个*
    `name = "..."` 的静态解析器跟不上的。

    返回一个 dict，含 "name"/"author"/"version"/"version_compatible"/
    "description"/"icon"/"icon_atlas" 中的任意几个（只有 mod 实际设置过的字段才会出
    现，且已经本地化成纯字符串）和 "config_options"（一个
    list[ModConfigOption]，只有 configuration_options 解析出可识别的
    形状时才会出现）——如果文件读取失败，或者执行整体失败/超时，则返
    回 None。调用方应该应用其中出现的键，其余部分保持现有 ModInfo 不变。
    """
    modinfo_path = mod_folder / "modinfo.lua"
    if not modinfo_path.exists():
        return None
    try:
        text = modinfo_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    text = _strip_lua_comments(text)

    from dstools.features.mod.sandbox import (
        FULL_FILE_TIMEOUT,
        resolve_full_config_options,
    )

    folder_name = _workshop_id_from_folder(mod_folder)
    result = resolve_full_config_options(
        text, timeout=timeout or FULL_FILE_TIMEOUT, folder_name=folder_name
    )
    if not isinstance(result, dict):
        return None

    out = {}
    for key in (
        "name",
        "author",
        "version",
        "version_compatible",
        "description",
        "icon",
        "icon_atlas",
    ):
        val = result.get(key)
        if val is not None:
            out[key] = val if isinstance(val, str) else _resolve_localized_value(val)
    options = _options_from_lua_result(result.get("configuration_options"))
    if options is not None:
        out["config_options"] = options
    return out or None
