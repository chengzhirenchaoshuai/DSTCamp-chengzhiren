"""当 mod 用代码动态拼出配置项（一个 for 循环、一个小的局部辅助函数）而
不是直接写一张字面量表时，解析出它的可选项——这是纯文本模式匹配做不到
的事，因为这些值只有真正跑一遍代码才存在。

这是对项目"不用 Lua 运行时"这条一般规则（见 CLAUDE.md 的"无运行时 Lua
解析"一节，以及 modinfo_reader.py 的静态解析器，它不依赖本模块就能处理
绝大多数 mod）刻意开的一个小口子：

- 只在用户打开某个具体 mod 的配置弹窗、且这个 mod 有静态解析器解不出来
  的选项时才会被调用——mod 列表批量扫描阶段绝不会用到（parse_modinfo()
  本身从不 import 本模块），不会拖慢常规场景。
- 只会喂给它 modinfo.lua 里 configuration_options 被赋值*之前*出现的文
  本——考察过的每个 mod 在写构建选项列表的辅助函数/局部表/for 循环时都
  遵循这个惯例——外加一句 `return <expr>`，对应那个没解析出来的
  `options = ...` 引用的具体表达式。绝不会跑 configuration_options 表
  本身，绝不会跑 modmain.lua，绝不会跑任何在真实游戏里执行的代码。
- 运行 Lua 5.1——跟 DST 引擎自身版本精确对应（通过 `lupa` 包的 lua51
  后端，不是 LuaJIT 或更新的 Lua 版本）——放在一个*独立的子进程*里跑，
  带硬性挂钟超时。进程内嵌入式解释器一旦 mod 代码卡死（死循环、无界的
  表）没法安全杀掉；子进程直接 terminate 就行。
- 任何失败——mod 的辅助函数引用了未定义的 DST 引擎全局变量（GLOBAL、
  STRINGS 等）、真实的 Lua 运行时错误、超时，或者结果形状跟预期不符
  ——都跟"静态解析不出来"一视同仁：返回 None，调用方继续显示诚实的
  "这个选项在这里不能编辑"兜底提示。这里绝不猜测，这也正好天然区分开
  "只是个局部 for 循环"（能正常解析出来）和"需要真实游戏引擎"（立刻、
  低代价地失败——引用未定义全局变量在 Lua 尝试调用/取下标的那一刻就报
  错，不会卡住）两种情况。
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_WORKER = Path(__file__).parent / "_sandbox_worker.py"

DEFAULT_TIMEOUT = 1.5

# 子进程是个控制台进程（普通 `python` 或重新执行打包后的 exe）——不加
# 这个标志，每次调用都会在 GUI 上方一闪而过一个黑色控制台窗口。
_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_BLOCK_OPENERS = re.compile(r'\b(?:if|for|while|function)\b')
_BLOCK_CLOSERS = re.compile(r'\bend\b')
_LONG_BRACKET_OPEN = re.compile(r'\[(=*)\[')


def _blank_strings(text: str) -> str:
    """返回一份跟 `text` 等长的副本，把每个引号字符串或长括号字符串
    （`[[...]]`/`[=[...]=]`）的*内容*替换成空格——引号/括号本身和换行符
    都保留，所以在这份"挖空"副本上算出来的下标仍然能对得上原文。

    本模块任何关键字/花括号计数之前都先过一遍这个函数：mod 自己的描述
    文本里常有普通英文句子恰好包含一个也是 Lua 关键字的单词（比如某个
    真实 mod 的原文 "A timer **for** Don't Starve Together events..."）
    ——不这样处理的话，_looks_balanced/_largest_balanced_prefix 会把它
    误算成一个真的 `for` 循环，整个配平计算就全错了。
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
                if c == '\\' and i + 1 < n:
                    out.append('  ')
                    i += 2
                    continue
                if c == quote:
                    out.append(c)
                    i += 1
                    break
                out.append(c if c == '\n' else ' ')
                i += 1
            continue
        if ch == '[':
            lm = _LONG_BRACKET_OPEN.match(text, i)
            if lm:
                closer = ']' + lm.group(1) + ']'
                close_idx = text.find(closer, lm.end())
                if close_idx == -1:
                    inner_end, end, closer_text = n, n, ''
                else:
                    inner_end, end, closer_text = close_idx, close_idx + len(closer), closer
                out.append(text[i:lm.end()])  # 保留开括号本身，例如 "[["
                out.extend(c if c == '\n' else ' ' for c in text[lm.end():inner_end])
                out.append(closer_text)
                i = end
                continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _looks_balanced(text: str) -> bool:
    """粗略的基于关键字的检查：`text` 是不是一段自成一体、语法完整的
    Lua 代码块——每个 if/for/while/function 都有对应的 `end`，每个 `{`
    都有对应的 `}`，且只在字符串字面量之外计数（见 _blank_strings）。
    不是真正的解析器，只是一道保守的前置检查：ModInfo.dynamic_preamble
    是从源文件里切出"configuration_options 之前的所有内容"拼出来的，
    这一刀可能正好切在一个还没闭合的代码块中间——比如某个 mod 把整段
    `configuration_options = {...}` 赋值包在
    `if locale == "zh" then ... end` 里面。把这种半截代码丢进沙箱只会
    得到一个 Lua 语法错误；这里先拦一道能省掉这趟白跑的 subprocess。
    往哪个方向判断错都无伤大雅——误判为"配平"顶多在沙箱里干净地失败、
    照样走兜底；误判为"不配平"顶多让一个本来能解析出来的选项继续显示
    诚实的兜底提示。
    """
    text = _blank_strings(text)
    if text.count('{') != text.count('}'):
        return False
    return len(_BLOCK_OPENERS.findall(text)) == len(_BLOCK_CLOSERS.findall(text))


def _largest_balanced_prefix(text: str) -> str:
    """把 `text` 截断到最后一个"所有已打开的 if/for/while/function 代码
    块（以及每个 `{`）都已完全闭合"的位置——也就是能自成一体、语法完整
    的最大前缀。

    ModInfo.dynamic_preamble 是"configuration_options 之前的所有内
    容"——通常自身已经是个完整的代码块，但如果 configuration_options
    本身被包在一个条件语句里包住整段代码就不是了（确实有真实 mod这样
    写：`if locale == "zh" then configuration_options = {...} else ...
    end`，切在 `configuration_options` 前面正好落在这个还没闭合的
    `if` 中间）。一个选项的 `options = ...` 表达式真正需要的那些无条件
    局部辅助函数/表/for 循环，通常都声明在这种尾部未闭合代码块*之前*，
    所以退回到仍然完全闭合的最大前缀，既保留了这些依赖，又只丢掉让整
    段 preamble 本身语法不完整的那一部分。

    跟 _looks_balanced 同一套粗略的关键字/花括号计数，同样容忍两个方
    向的误判：切错了顶多让截断后的 preamble 仍然跑不通（照旧回退到
    None，跟完全不尝试没有区别），或者多丢了一些其实用不着的内容——
    绝不会崩溃，也绝不会给出一个错得看起来还挺像样的答案。
    """
    # _blank_strings 精确保留长度/换行符，所以在挖空副本（关键字匹配在
    # 这份副本上进行，为了忽略字符串内容里长得像关键字的普通英文单词）
    # 上找到的位置，同样是下面对原始 `text` 切片时的有效下标。
    blanked = _blank_strings(text)
    block_depth = 0
    brace_depth = 0
    last_safe = 0
    for m in re.finditer(r'\b(?:if|for|while|function|end)\b|[{}]', blanked):
        tok = m.group(0)
        if tok == 'end':
            block_depth -= 1
        elif tok in ('if', 'for', 'while', 'function'):
            block_depth += 1
        elif tok == '{':
            brace_depth += 1
        elif tok == '}':
            brace_depth -= 1
        if block_depth == 0 and brace_depth == 0:
            last_safe = m.end()
    return text[:last_safe]


def _worker_command() -> list:
    """算出把 sandbox worker 作为子进程启动所需的命令。

    正常（非打包）运行时，就是简单的 `python _sandbox_worker.py`。在
    PyInstaller --onefile 打包版里，sys.executable *就是* DSTCamp.exe
    本身——没有单独的解释器可以指向一份散装 .py 文件，于是改成重新启动
    同一个 exe 并带上一个特殊标志，run_gui.py 启动时检查到这个标志就直
    接分发给 worker 的 main()，而不是打开 GUI（见 run_gui.py）。不管哪
    种情况，子进程都是一个全新的、可以被杀掉的操作系统进程，这正是这
    个沙箱真正依赖的特性。
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--lua-sandbox-worker"]
    return [sys.executable, str(_WORKER)]


def run_lua_snippet(lua_code: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """在子进程里的沙箱化 Lua 5.1 解释器中执行 `lua_code`（必须以
    `return <expr>` 结尾）。

    返回解码后的 Python 值（嵌套的 dict/list/str/int/float/bool/None），
    如果执行失败、超时，或者结果没法表示成 JSON，则返回 None——绝不
    抛异常。
    """
    import json

    try:
        proc = subprocess.run(
            _worker_command(),
            input=lua_code, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
            creationflags=_CREATIONFLAGS,
        )
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


FULL_FILE_TIMEOUT = 3.0


_FIELDS_TO_READ_BACK = (
    "name", "author", "version", "description", "icon", "icon_atlas",
    "configuration_options",
)


def resolve_full_config_options(file_text: str, timeout: float = FULL_FILE_TIMEOUT,
                                 folder_name: str | None = None) -> Any:
    """把整份 modinfo.lua 的文本丢进沙箱跑一遍，一次执行读回本项目关心
    的全部顶层字段（name、author、version、description、icon、
    icon_atlas、configuration_options），打包成一个 dict。

    folder_name：真实游戏引擎会把这个全局变量原样注入每份 modinfo.lua
    的执行环境（对照过引擎源码确认，modindex.lua 里
    ModIndex:InitializeModInfo() 的 `env.folder_name = modname`）——mod
    常用它来区分"这是 Workshop 订阅的副本"还是"手动安装/GitHub 下载的
    副本"（真实 mod 代码：`if not folder_name:find("workshop-") then
    ... end`）。不设置的话，对它取下标/调用（`folder_name:find(...)`）
    会抛出 Lua 错误，中止*整个*脚本——包括同一份文件里更早已经正确赋值
    过的字段（某个真实 mod 的 `name` 在 `folder_name` 检查之前几行就已
    经算好了，但整次执行仍然会因为后面这个错误而彻底失败，那个已经算
    好的 name 也跟着悄悄丢失）。调用方传入的是 modinfo_reader.py 别处
    已经从 mod 文件夹名派生出来的同一个 "workshop-<id>" 字符串（见
    modinfo_reader._workshop_id_from_folder()）；以一句普通 Lua 赋值语
    句的形式拼在 file_text 前面，因为这个沙箱的 stdin 协议就是原始 Lua
    源码，不是每次调用单独传结构化参数。

    跟 resolve_dynamic_option()（只跑 configuration_options 赋值之前的
    那部分文件，加一个未解析的表达式，作为逐个选项的窄范围兜底）不同，
    这里跑的是*整份*文件——包括这些字段后续任何重新赋值——然后再读回
    来。这不仅对 configuration_options 有意义：某个真实 mod 在
    `if locale == "zh" then name = "中文名" ... end` 里重新赋值了
    `name`，只抓文件里*第一个* `name = "..."` 的静态解析器永远跟不上这
    种写法，标题/名字会显示成错误语言的兜底文本，即便这个 mod 明明有
    中文名。一次完整执行统一读回所有字段，而不是分别用各自的正则重新
    推导，意味着每个字段都能享受到真实 Lua 解释器算对的结果。

    应该优先尝试这个（调用方实际该用的入口见
    modinfo_reader.resolve_full_modinfo()），失败时保留现有静态解析器
    的结果作为兜底——大多数 modinfo.lua 会引用这个沙箱没有提供的
    DST 引擎全局变量（GLOBAL、STRINGS、TheNet 等），这些会直接失败
    （很快——对未定义全局变量取下标/调用会立刻报错）并照旧走兜底路径。

    返回一个 dict（如果 mod 从未设置过某个字段，对应值可能缺失/为
    None），如果执行整体失败/超时则返回 None——解读 configuration_options
    具体是什么形状是调用方的事，不是这个函数的职责。
    """
    fields = ", ".join(f"{f} = {f}" for f in _FIELDS_TO_READ_BACK)
    preamble = ""
    if folder_name is not None:
        escaped = folder_name.replace("\\", "\\\\").replace('"', '\\"')
        preamble = f'folder_name = "{escaped}"\n'
    return run_lua_snippet(f"{preamble}{file_text}\nreturn {{{fields}}}\n", timeout=timeout)


def resolve_dynamic_option(preamble: str, raw_options_expr: str,
                           timeout: float = DEFAULT_TIMEOUT) -> list[dict] | None:
    """尝试通过真正运行 mod 自己构建选项的代码来解析出可选项，而不是把
    它当文本解析。

    `preamble` 是 modinfo.lua 里 `configuration_options` 被赋值之前的
    全部内容（ModInfo.dynamic_preamble）；`raw_options_expr` 是该选项
    `options = ...` 没解析出来的右侧表达式（ModConfigOption.raw_options_expr）。
    两者合起来：先跑 preamble，再返回该表达式求值的结果。

    返回一个跟 _extract_choices() 产出同样形状的可选项列表
    （[{"description": ..., "data": ..., "hover": ...}, ...]），如果结果
    形状不像可选项列表（或执行失败）则返回 None——绝不会对不匹配的形状
    做猜测。
    """
    if not preamble or not raw_options_expr:
        return None
    if not _looks_balanced(preamble):
        # 完整的 preamble 本身不是一段完整代码块（原因见
        # _largest_balanced_prefix 的 docstring）——退回到它最大的完全
        # 闭合前缀而不是直接放弃，因为这个未解析选项真正需要的内容通
        # 常在还没闭合的那部分之前就已经声明好了。
        preamble = _largest_balanced_prefix(preamble)
        if not preamble:
            return None
    result = run_lua_snippet(f"{preamble}\nreturn ({raw_options_expr})\n", timeout=timeout)
    if not isinstance(result, list) or not result:
        return None
    choices = []
    for item in result:
        if not isinstance(item, dict) or "description" not in item:
            return None  # 形状不对——不对部分结果做猜测
        choices.append({
            "description": str(item["description"]),
            "data": item.get("data", item["description"]),
            "hover": item.get("hover") or "",
        })
    return choices
