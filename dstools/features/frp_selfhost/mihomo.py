"""大厅加速使用的 Mihomo TUN 配置和进程生命周期。"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import queue
import subprocess
import threading
import time
from enum import Enum
from pathlib import Path

from dstools.features.frp_selfhost.wireguard import WireGuardClientConfig
from dstools.shared.resource_paths import cache_dir, security_dir


_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_SERVER_EXE_NAMES = (
    "dontstarve_dedicated_server_nullrenderer_x64.exe",
    "dontstarve_dedicated_server_nullrenderer.exe",
)


class MihomoError(RuntimeError):
    """Mihomo 配置、权限或进程启动失败。"""


class MihomoStatus(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASHED = "crashed"


def is_windows_admin() -> bool:
    if os.name != "nt":
        return os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def build_mihomo_config(wireguard: WireGuardClientConfig) -> str:
    """把 DST 专服的 TCP/UDP 都交给 WireGuard，其它进程保持直连。"""

    port = int(wireguard.port)
    if not 1 <= port <= 65535:
        raise ValueError("WireGuard 端口必须在 1..65535")
    rules = [
        f"  - PROCESS-NAME,{name},DST-WG"
        for name in _SERVER_EXE_NAMES
    ]
    return "\n".join(
        [
            "mode: rule",
            "log-level: info",
            "find-process-mode: always",
            "ipv6: false",
            "",
            "tun:",
            "  enable: true",
            "  stack: system",
            "  auto-route: true",
            "  auto-detect-interface: true",
            "  strict-route: false",
            "  device: DSTCampMihomo",
            "",
            "proxies:",
            "  - name: DST-WG",
            "    type: wireguard",
            f"    server: {json.dumps(wireguard.server)}",
            f"    port: {port}",
            f"    ip: {wireguard.address}",
            f"    private-key: {wireguard.private_key}",
            f"    public-key: {wireguard.server_public_key}",
            "    allowed-ips: ['0.0.0.0/0']",
            "    persistent-keepalive: 25",
            f"    mtu: {int(wireguard.mtu)}",
            "    udp: true",
            "",
            "rules:",
            *rules,
            "  - MATCH,DIRECT",
            "",
        ]
    )


def validate_mihomo_executable(path: str | Path) -> Path:
    executable = Path(path)
    if not executable.is_file():
        raise MihomoError("请选择存在的 mihomo.exe")
    if os.name == "nt" and executable.suffix.lower() != ".exe":
        raise MihomoError("Windows 上请选择 mihomo.exe")
    return executable


def sha256_file(path: str | Path) -> str:
    """计算用户选定 Mihomo 的内容指纹，防止路径下的程序被替换。"""

    executable = validate_mihomo_executable(path)
    digest = hashlib.sha256()
    with executable.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class MihomoProcess:
    _READY_GRACE_SECONDS = 1.2

    def __init__(self):
        self.status = MihomoStatus.STOPPED
        self.error: str | None = None
        self.proc: subprocess.Popen | None = None
        self.config_path: Path | None = None
        self._out_queue: "queue.Queue[str]" = queue.Queue()

    def _work_dir(self) -> Path:
        target = cache_dir("lobby_accel_mihomo")
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _config_dir(self) -> Path:
        target = security_dir("lobby_accel_mihomo")
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _validate_config(self, executable: Path, config_path: Path) -> None:
        completed = subprocess.run(
            [str(executable), "-t", "-f", str(config_path)],
            cwd=str(config_path.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_SUBPROCESS_FLAGS,
            timeout=15,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stdout.strip()[-1200:]
            raise MihomoError(f"Mihomo 配置检查失败：{detail or completed.returncode}")

    def start(
        self,
        executable_path: str | Path,
        wireguard: WireGuardClientConfig,
    ) -> None:
        if self.status == MihomoStatus.RUNNING and self.poll_exit_code() is None:
            return
        executable = validate_mihomo_executable(executable_path)
        if os.name == "nt" and not is_windows_admin():
            raise MihomoError("Mihomo TUN 需要管理员权限，请以管理员身份运行 DSTCamp")
        self.status = MihomoStatus.STARTING
        self.error = None
        work_dir = self._work_dir()
        config_dir = self._config_dir()
        config_path = config_dir / "config.yaml"
        temporary = config_dir / f".config.{os.getpid()}.tmp"
        temporary.write_text(build_mihomo_config(wireguard), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, config_path)
        self.config_path = config_path
        try:
            self._validate_config(executable, config_path)
            self.proc = subprocess.Popen(
                [str(executable), "-d", str(work_dir), "-f", str(config_path)],
                cwd=str(work_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_SUBPROCESS_FLAGS,
            )
        except (OSError, subprocess.SubprocessError, MihomoError) as exc:
            self.status = MihomoStatus.CRASHED
            self.error = str(exc)
            raise MihomoError(f"Mihomo 启动失败：{exc}") from exc

        threading.Thread(target=self._read_loop, name="dstcamp-mihomo-log", daemon=True).start()
        deadline = time.monotonic() + self._READY_GRACE_SECONDS
        while time.monotonic() < deadline:
            code = self.poll_exit_code()
            if code is not None:
                detail = "\n".join(self.read_available_lines())[-1200:]
                self.status = MihomoStatus.CRASHED
                self.error = f"Mihomo 提前退出（{code}）：{detail}"
                raise MihomoError(self.error)
            time.sleep(0.1)
        self.status = MihomoStatus.RUNNING

    def _read_loop(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                self._out_queue.put(line.rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        if self.status not in {MihomoStatus.STOPPING, MihomoStatus.STOPPED}:
            code = proc.poll()
            self.status = MihomoStatus.CRASHED
            self.error = f"Mihomo 意外退出（{code}）"

    def read_available_lines(self, limit: int = 500) -> list[str]:
        lines = []
        while len(lines) < limit:
            try:
                lines.append(self._out_queue.get_nowait())
            except queue.Empty:
                break
        return lines

    def poll_exit_code(self) -> int | None:
        return self.proc.poll() if self.proc is not None else None

    def is_healthy(self) -> bool:
        return self.status == MihomoStatus.RUNNING and self.poll_exit_code() is None

    def stop(self, timeout: float = 5.0) -> None:
        proc = self.proc
        if proc is None:
            self.status = MihomoStatus.STOPPED
            return
        self.status = MihomoStatus.STOPPING
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
        except OSError:
            pass
        finally:
            if proc.stdout is not None:
                try:
                    proc.stdout.close()
                except OSError:
                    pass
            self.proc = None
            self.status = MihomoStatus.STOPPED
            self.error = None
