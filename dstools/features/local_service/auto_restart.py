"""专服崩溃自动重启的判定规则（纯逻辑，不操作界面、不读写设置）。

难点在 Klei 的新令牌：服务器异常断开后，Klei 端的房间注册不会立刻释放，
马上用同一个新令牌重启会注册冲突（日志 E_ROWID_EXIST）。Klei 没有公开释放
时长，实测一次重启从拉起到注册就要 2 分钟以上，所以：

- 先等原令牌：按 TOKEN_RETRY_DELAYS 逐级拉长间隔重试，每崩一次就换令牌
  会很快把令牌池占满；
- 距崩溃超过 TOKEN_SWITCH_AFTER 仍没恢复，才换用池里其它可用令牌；
- 超过 TOKEN_WAIT_LIMIT 放弃；每次结果写日志，方便以后按真实释放时长调整。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# 不会因为重启而自愈的失败：重启只会原样再失败一次
NON_RECOVERABLE_CATEGORIES = frozenset({"runtime", "permission", "port", "world_generation"})

CRASH_RESTART_DELAY = 15.0        # 崩溃后稍等再拉起，让旧进程、frpc 与日志收尾
CRASH_WINDOW = 30 * 60            # 统计崩溃次数的时间窗
MAX_CRASH_RESTARTS = 3            # 时间窗内最多自动重启几次，防止 Mod 报错导致无限循环
TOKEN_RETRY_DELAYS = (5 * 60, 10 * 60, 15 * 60, 30 * 60)  # 原令牌第 n 次重试前的等待
TOKEN_WAIT_LIMIT = 2 * 60 * 60    # 从崩溃起最多等待 Klei 释放令牌的总时长
TOKEN_SWITCH_AFTER = 15 * 60      # 从崩溃起等原令牌多久仍没恢复，才允许换用池中其它令牌


def is_restartable(category: str, world_ready: bool, auto_attempt: bool) -> bool:
    """这次失败是否触发自动重启。

    只重启"跑起来之后崩掉"的世界：从没就绪过的手动启动多半是配置或 Mod
    问题，自动重启只会反复失败。自动重启拉起的那一轮在就绪前又失败，
    计入崩溃次数（由 CrashBudget 限流）。令牌冲突不走这里，由重试流程单独处理。"""
    if category == "token_conflict" or category in NON_RECOVERABLE_CATEGORIES:
        return False
    return world_ready or auto_attempt


def token_retry_delay(failures: int) -> float:
    """原令牌已连续冲突 failures 次（0 表示刚崩溃）时，下一次重试前要等多久。"""
    index = min(max(failures, 0), len(TOKEN_RETRY_DELAYS) - 1)
    return float(TOKEN_RETRY_DELAYS[index])


@dataclass
class CrashBudget:
    """时间窗内的自动重启次数限制。"""

    times: list[float] = field(default_factory=list)

    def allow(self, now: float) -> bool:
        """记一次崩溃；超出次数上限返回 False（不再自动重启）。"""
        self.times = [moment for moment in self.times if now - moment < CRASH_WINDOW]
        if len(self.times) >= MAX_CRASH_RESTARTS:
            return False
        self.times.append(now)
        return True

    def reset(self) -> None:
        self.times.clear()


def append_log(log_path: Path, cluster_name: str, message: str) -> None:
    """每次自动重启的动作与结果追加到日志，用于统计 Klei 实际的令牌释放时长。"""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{stamp} [{cluster_name}] {message}\n")
    except OSError:
        pass  # 写日志失败不能影响重启本身
