"""在独立 Lua 5.1 子进程中执行片段并通过 JSON 返回结果。

独立进程是超时边界：父进程可以终止死循环，而线程内嵌解释器无法可靠恢复。
"""

import json
import sys

# 中文 Windows 默认使用 GBK；子进程管道必须与父进程统一为 UTF-8。
sys.stdin.reconfigure(encoding="utf-8", errors="replace")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


def _to_plain(value, _seen=None):
    """把一张 Lua 表（dict 形状或数组形状）递归转换成能直接 JSON 化的
    Python 普通数据。其它任何东西（函数、userdata 等）都退化成
    str()，而不是让整个结果失败——具体需要什么形状由调用方事后自己
    校验。"""
    _seen = _seen if _seen is not None else set()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if id(value) in _seen:
        return None  # 循环引用的表——直接放弃，不无限递归下去
    try:
        items = list(value.items())
    except AttributeError:
        return str(value)
    _seen = _seen | {id(value)}
    keys = [k for k, _ in items]
    if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
        return [_to_plain(v, _seen) for _, v in sorted(items)]
    return {str(k): _to_plain(v, _seen) for k, v in items}


def main():
    lua_code = sys.stdin.read()

    from lupa.lua51 import LuaRuntime
    rt = LuaRuntime(unpack_returned_tuples=True, register_eval=False)
    g = rt.globals()
    # 纵深防御：调用方只会喂给它 mod 的 modinfo.lua 里
    # configuration_options 被赋值*之前*的那部分内容（局部辅助函数/表/
    # for 循环），从不会喂整个 mod——但这段文本终究是不可信的第三方内
    # 容，所以不管怎样，任何能碰到文件系统/操作系统/进程/另一段 Lua 代
    # 码的东西都要在运行前清空。
    for name in ("os", "io", "require", "dofile", "loadfile", "load",
                 "loadstring", "package", "debug", "collectgarbage"):
        g[name] = None

    # 真实游戏引擎会给每份 modinfo.lua 提供 `locale`（通过
    # LOC.GetLocaleCode()），mod 大量依赖它做双语文本——到处都是
    # `local isCh = locale == "zh" or locale == "zhr"` 接着
    # `isCh and "中文" or "English"` 这种写法。这里不设置的话，所有这类
    # 判断都会算成 false，解析出来的每个选项都会悄悄变成英文而不是中
    # 文——设成 "zh"（简体中文的真实代码，对照过真实 mod 源码确认，不
    # 是猜的）能让沙箱按游戏对中文语言玩家的方式解析选项，跟本工具整
    # 体中文优先的界面一致。
    g["locale"] = "zh"

    # 引擎还会给每份 modinfo.lua 的执行环境注入一个
    # `ChooseTranslationTable(tbl) -> tbl[locale] or tbl[1]` 辅助函数
    # （对照过游戏自己的 modindex.lua 源码确认，不是猜的）——有些 mod
    # 直接用它（而不是/或者同时用）`cond and "a" or "b"` 这种写法，其中
    # 一个真实 mod（Insight）做得更绝：它把引擎注入的这份复制到一个局
    # 部变量后清掉了全局引用，而它*自己*的辅助函数在全局不存在时会回
    # 退成英文——所以这里不设置的话，不只是少一个功能，还会让所有用到
    # 这个套路的 mod 悄悄把文本解析成英文而不是中文。多余的参数（有些
    # mod 会调用 ChooseTranslationTable(tbl, key)，这里用不到）直接忽
    # 略，跟真实 Lua 的行为一致。
    def _choose_translation_table(tbl, *_args):
        try:
            val = tbl["zh"]
        except Exception:
            val = None
        if val is None:
            try:
                val = tbl[1]
            except Exception:
                val = None
        return val

    g["ChooseTranslationTable"] = _choose_translation_table

    # 真实引擎的 KnownModIndex:InitializeModInfo(id) 会重新解析目标 mod
    # 的 modinfo.lua 并返回一份完整信息表——沙箱这边没有能力（也不需要）
    # 真的重新解析，只给一个"什么都没有"的空表占位，够用的原因是：调用
    # 方（真实抓到的用例是"Chinese++ Pro"的翻译文件）只会取它的
    # .description 字段做字符串替换，取不到真实描述就是空字符串，不影响
    # 这条沙箱真正关心的 configuration_options 字段（在这类调用之后独立
    # 赋值，不依赖这次调用的返回值）。批量跑过 116 份真实翻译文件验证过：
    # 加这一个桩，成功率从需要它的近一半文件直接失败，变成整体 84% 成功。
    def _known_mod_index_stub(_self, *_args):
        return rt.table_from({"description": ""})

    g["KnownModIndex"] = rt.table_from({"InitializeModInfo": _known_mod_index_stub})

    result = rt.execute(lua_code)
    sys.stdout.write(json.dumps(_to_plain(result)))


def run_worker_main() -> None:
    """崩溃安全的入口函数——这个文件作为脚本直接运行时
    （`python _sandbox_worker.py`，即 sandbox._worker_command() 在开发
    模式下启动的子进程）和打包后的 exe 带 `--lua-sandbox-worker` 参数重
    新调用自己时（见 run_gui.py）都会调用它。一个 mod 的 Lua 代码片段
    执行失败（真实的 Lua 运行时错误——比如引用了这个沙箱没提供的引擎全
    局变量——是预期内的常见情况，不是 bug）绝不能表现成一个可见的崩溃
    弹窗；父进程只会检查退出码，从不读 stderr，所以这里退出码为 1 就已
    经是"沙箱解析不出来"这条路径的完整处理结果了。

    这段逻辑原来是直接写在下面 `if __name__ == "__main__":` 里的，只有
    这个文件作为顶层脚本执行时才会跑到——开发模式下没问题，但
    run_gui.py 打包模式的分支是直接 import 并调用 `main()`，完全绕过了
    这道判断，导致一个未处理的 LuaError 会一路往上抛，PyInstaller 自己
    的"Unhandled exception in script"弹窗会直接甩到用户面前。两条入口
    路径都必须走同一层包装。
    """
    try:
        main()
    except Exception as e:
        # 父进程（sandbox.run_lua_snippet）从不读 stderr——只检查退出
        # 码——但这里写入本身不能再抛出*第二个*未处理的异常。上面已经
        # 把 stderr 配成 errors="backslashreplace"，所以不管 str(e) 产
        # 生什么都能表示出来（Lua 错误消息里可能内嵌来自 mod 源码的原
        # 始/解码异常字节），但这里的写入仍然加了保护，以防 str(e) 本
        # 身出问题。
        try:
            sys.stderr.write(str(e))
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    run_worker_main()
