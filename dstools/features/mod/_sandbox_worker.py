"""sandbox.run_lua_snippet 用的子进程 worker。

从 stdin 读一段 Lua 代码，在一个精简过全局环境的 Lua 5.1 解释器（跟饥荒
联机版自己的引擎版本一致——见 sandbox.py）里运行，把结果以 JSON 形式打
印到 stdout。

从不被 import 或直接运行——总是由 sandbox.run_lua_snippet() 作为子进程
启动，超时/杀进程这部分沙箱逻辑由它负责。跑在*独立进程*里（而不是把
lupa 的解释器直接嵌进主程序）才让卡死的代码片段（比如某个 mod 写挂了
的死循环）真正可恢复：父进程直接把这个进程整个杀掉就行，这对在某个
Python 线程里原地失控的调用是做不到的。
"""

import json
import sys

# 父进程（sandbox.run_lua_snippet）显式按 UTF-8 编解码这条管道，但这个
# 子进程自己的 sys.stdin/stdout/stderr 默认编码取决于操作系统区域设置
# （中文 Windows 上确认是 cp936/GBK）——不强制改成一致的话，mod 源码里
# 的非 ASCII 文本（非常常见：dynamic_preamble 里的中文标签/悬浮提示/局
# 部变量内容）在读入时会被悄悄解码错。往好里说只是 Lua 源码在执行前就
# 已经被静默破坏；往坏里说一个解码错的字符往回写时会撞上严格 UTF-8 写
# 不出来的东西，直接让这个进程崩掉。
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
