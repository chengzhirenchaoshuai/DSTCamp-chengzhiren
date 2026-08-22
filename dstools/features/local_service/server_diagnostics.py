"""专服异常退出的离线日志诊断。

这里只做保守的证据归类，不修改存档、Mod 或服务器配置。诊断结果区分
“明确错误”和“疑似原因”，避免把任意一行 Lua Error 都武断地说成 Mod
冲突；UI 层可以据此显示摘要、建议和原始证据。
"""

from dataclasses import dataclass
import re
from typing import Iterable


_WORKSHOP_RE = re.compile(r"workshop-\d+", re.IGNORECASE)
_STARTUP_FAILURE_MARKERS = (
    "server failed to start!",
    "unhandled exception during server startup:",
    "socket_port_already_in_use",
    "error loading worldgen_main.lua",
    "error loading main.lua",
    "failed msimulation->reset()",
    "error during game initialization!",
    "luaerror but no error string",
)


@dataclass(frozen=True)
class DiagnosticReport:
    category: str
    title: str
    summary: str
    suggestions: tuple[str, ...]
    evidence: tuple[str, ...] = ()
    related_mods: tuple[str, ...] = ()
    certain: bool = False

    @property
    def banner_text(self) -> str:
        """控制台顶部一行摘要；详情后续可由弹窗继续展示。"""
        return f"{self.title}：{self.summary}"


@dataclass(frozen=True)
class ModLoadStatus:
    """世界就绪后 Mod 的最终加载状态，供控制台横幅使用。"""

    failed_mods: tuple[str, ...] = ()
    visible_mod_count: int = 0


def analyze_mod_loading(
    *,
    enabled_mods: Iterable[str],
    loaded_mods: Iterable[str],
    failed_mods: Iterable[str] = (),
    visible_mod_count: int = 0,

) -> ModLoadStatus:
    """归纳世界就绪后的 Mod 加载结果。

    集合差集和服务器明确报告的禁用结果都在这里合并；控制台页签只根据
    返回的失败列表选择成功或失败横幅，不再把它当作服务器启动诊断类别。
    """
    missing = (set(enabled_mods) - set(loaded_mods)) | set(failed_mods)
    return ModLoadStatus(
        failed_mods=tuple(sorted(missing, key=str.lower)),
        visible_mod_count=visible_mod_count,
    )


def _evidence(lines: list[str], patterns: tuple[str, ...], limit: int = 3) -> tuple[str, ...]:
    matched = [line.strip() for line in lines if any(p in line.lower() for p in patterns)]
    return tuple(matched[-limit:])


def _lua_evidence(lines: list[str], limit: int = 14) -> tuple[str, ...]:
    """截取最后一段 Lua 堆栈，避免把更早的普通 Mod 日志混进来。"""
    markers = [i for i, line in enumerate(lines) if "lua error" in line.lower()
               or "stack traceback" in line.lower()]
    if not markers:
        return _evidence(lines, ("error loading", "../mods/", "attempt to", "wrong number"), limit)
    marker = markers[-1]
    window = lines[max(0, marker - 4): marker + 45]
    result = []
    seen = set()
    for line in window:
        text = line.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _mods(lines: list[str], enabled: Iterable[str], loaded: Iterable[str]) -> tuple[str, ...]:
    found = set(enabled) | set(loaded)
    stack_mods = set()
    traceback_indexes = [i for i, line in enumerate(lines)
                         if "lua error" in line.lower() or "stack traceback" in line.lower()]
    for index, line in enumerate(lines):
        ids = {m.group(0) for m in _WORKSHOP_RE.finditer(line)}
        found.update(ids)
        # 只接受明确的错误归因行；模块搜索失败列表里的路径只是加载器
        # 的尝试顺序，不能把其中列出的所有 Mod 都误报成嫌疑对象。
        lower = line.lower()
        if "mod error:" in lower or "error calling" in lower and "mod workshop-" in lower:
            stack_mods.update(ids)
        if any(index >= marker and index <= marker + 80 for marker in traceback_indexes):
            if "../mods/" in lower and re.search(r"../mods/workshop-\d+[/\\].*:\d+", lower):
                stack_mods.update(ids)
        # Lua 堆栈前一行常用 [string "../mods/.../modmain.lua"] 写出根错误。
        if any(index < marker and marker - index <= 6 for marker in traceback_indexes):
            if "[string \"../mods/" in lower and re.search(r"../mods/workshop-\d+[/\\].*:\d+", lower):
                stack_mods.update(ids)
    # Lua 堆栈里出现的 Mod 路径是最有价值的归因证据；有堆栈证据时不把
    # 整个启用列表都显示成“疑似相关”，避免用户误以为每个 Mod 都有问题。
    if stack_mods:
        found = stack_mods
    return tuple(sorted(found, key=str.lower))


def diagnose_server_failure(
    *,
    shard_name: str,
    exit_code: int | None,
    world_ready: bool,
    log_lines: Iterable[str],
    enabled_mods: Iterable[str] = (),
    loaded_mods: Iterable[str] = (),
    intentional_stop: bool = False,
) -> DiagnosticReport | None:
    """根据一次世界进程退出前的日志生成保守诊断报告。

    ``None`` 表示没有异常退出需要提醒；正常停止和已经进入世界后由用户
    主动停止的进程不会生成报告。规则顺序从证据最明确的系统错误到一般
    Lua/Mod 错误，避免通用规则抢走更具体的分类。
    """
    lines = [str(line) for line in log_lines]
    if intentional_stop:
        return None
    if exit_code in (None, 0) and world_ready:
        return None

    lower = "\n".join(lines).lower()
    related_mods = _mods(lines, enabled_mods, loaded_mods)

    if any(token in lower for token in (
        "vcruntime140.dll", "msvcp140.dll", "vcomp120.dll", "cannot find the module",
        "找不到指定模块", "找不到 vcruntime", "找不到 vcomp",
    )):
        return DiagnosticReport(
            "runtime", "运行库缺失", "服务器启动时找不到 Visual C++ 运行库。",
            ("确认专服位数与运行库位数匹配。", "LuaJIT 模式请安装 VC++ 2023 x64；Mod 图标问题请安装 VC++ 2013 x86。"),
            _evidence(lines, ("dll", "找不到指定模块", "cannot find")), related_mods, True,
        )

    if any(token in lower for token in (
        "address already in use", "bind failed", "socket_port_already_in_use",
        "port_already_in_use", "端口已被占用", "only one usage",
    )):
        return DiagnosticReport(
            "port", "端口占用", "服务器需要使用的网络端口已被其他进程占用。",
            ("检查服务器配置中的端口。", "关闭占用该端口的程序，或为当前存档分配新的端口。"),
            _evidence(lines, ("address already", "bind", "端口")), related_mods, True,
        )

    if any(token in lower for token in (
        "access is denied", "permission denied", "拒绝访问", "permissionerror",
        "unable to write to config directory", "config_dir_write_permission",
        "check for write access: false", "check for read access: false",
    )):
        return DiagnosticReport(
            "permission", "文件访问失败", "服务器没有权限读取或写入所需文件。",
            ("确认当前用户对专服安装目录和存档目录有读写权限。", "检查杀毒软件是否拦截了专服或 LuaJIT 副本。"),
            _evidence(lines, ("access is denied", "permission", "拒绝访问")), related_mods, True,
        )

    if "must specify the task set for a level" in lower or "error loading worldgen_main.lua" in lower:
        return DiagnosticReport(
            "world_generation", "世界生成配置错误", f"{shard_name} 在世界生成阶段缺少有效的世界预设或任务集。",
            ("检查当前世界类型与世界生成预设是否匹配。", "如果刚卸载或更新了世界配置 Mod，请重新扫描 Mod 并重新保存世界设置。"),
            _evidence(lines, ("task set", "worldgen_main.lua")), related_mods, True,
        )

    if "lua error" in lower or "lua error stack traceback" in lower or "stack traceback" in lower:
        phase = "运行中" if world_ready else "启动阶段"
        return DiagnosticReport(
            "mod_conflict", "疑似 Mod 冲突",
            f"{shard_name} 在{phase}检测到 Lua 运行时错误，可能由 Mod Bug 或兼容性冲突导致。",
            ("优先禁用日志中列出的疑似 Mod，并重新启动服务器。",
             "如果禁用后恢复，再逐个启用最近更新或新增的 Mod。"),
            _lua_evidence(lines), related_mods, False,
        )

    return DiagnosticReport(
        "unknown", "服务器启动失败", "服务器进程异常退出，但暂时无法从日志确定单一原因。",
        ("先查看控制台末尾日志。", "检查令牌、端口、存档权限和最近更新的 Mod。"),
        tuple(line.strip() for line in lines[-3:] if line.strip()), related_mods, False,
    )


def contains_startup_failure(lines: Iterable[str]) -> bool:
    """判断日志是否已经明确进入启动失败状态。

    DST 某些启动错误会打印失败信息后继续存活，不能只靠 Popen.poll() 变成
    非空来触发诊断。
    """
    text = "\n".join(str(line) for line in lines).lower()
    return any(marker in text for marker in _STARTUP_FAILURE_MARKERS)
