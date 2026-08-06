"""用内置的 ktech.exe 把 Klei 的 .tex 贴图转换成 PNG。

DST（以及每个 mod 的 modicon.tex）用 Klei 自己的 .tex 格式存图片。
ktech.exe 是 Klei 官方的命令行转换工具，tools/ktools/ 下带了一份，不依
赖用户机器上别处装的工具（那种依赖用户可能会挪动或删掉）。

**中文路径问题（已用"输出到临时目录再搬过去"绕开）**：ktech.exe（底层是
老版 ImageMagick）处理命令行参数用的是系统 ANSI 代码页，不是
UTF-8/UTF-16，遇到非 ASCII 字符会用错误的编码重新解读参数字节。真机实
测过两种情况完全不对称：**输入路径**带中文能正常读取（"Loading KTEX
from"这一行日志虽然在控制台里显示成乱码，但底层字节在这台机器的 ANSI
代码页下依然能正确定位到源文件，转换成功）；**输出路径**带中文则必现
失败，报 `WriteBlob Failed ... error/png.c/MagickPNGErrorHandler`。也就
是说"素材在中文路径下也能读"，但"结果不能写到中文路径"——而这个项目的
缓存目录（mod 图标/角色头像的落地位置）默认在
`%APPDATA%/DSTCamp/cache/`，用户还可以在"设置"里改成 exe 所在目录，如
果用户把 exe 放进了中文命名的文件夹，缓存目录就会带中文，之前会导致每
个图标转换都静默失败（`tex_to_png()` 返回 False，界面上退化成"无图标"
占位，不会崩溃，但功能上就是转不出来）。

第一版尝试过给输出路径转换成 Windows 8.3 短文件名（NTFS 传统上给每个长
文件名自动维护一份纯 ASCII 别名）绕开编码问题，真机测试直接失败——
`GetShortPathNameW` 对这台机器上的目标目录返回的"短名"里那段中文部分根
本没被转换，说明**这台机器全局关掉了短文件名生成**（Windows 7+/性能优
化的常见默认配置，不是这台机器独有的特殊情况，不能依赖这个特性）。

现在的做法：**永远先让 ktech.exe 输出到一个保证纯 ASCII 的系统临时目
录**（`tempfile.TemporaryDirectory()`，文件名只用项目自己生成的 ASCII
字符串，如 workshop id），转换成功后再用 Python 自己的文件操作
（`shutil.move`，底层走 Windows 宽字符 API `MoveFileW`，对路径里的字符
没有 ktech.exe 那种编码限制）把结果搬到用户实际配置的缓存目录，哪怕那
个目录路径本身带中文。这样 ktech.exe 自始至终只接触我们控制得住、保证
是 ASCII 的路径，不依赖任何"这台机器凑巧支持"的系统特性。输入路径没有
做同样的预拷贝——实测这台机器上直接读中文路径没问题，没必要为一个已经
验证工作正常的路径多一次没必要的文件拷贝（未来如果在别的语言/代码页环
境上遇到输入路径也失败的反馈，再照这个思路加一份"先拷进临时目录"）。
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dstools.shared.resource_paths import bundled_resource_dir

_TOOLS_DIR = bundled_resource_dir() / "tools" / "ktools"
_KTECH_EXE = _TOOLS_DIR / "ktech.exe"

# 微软官方原始文件（下载自 download.microsoft.com，装前核实过数字签名
# 确实是 Microsoft Corporation 签发），随软件本体一起打包，用户点安装
# 提示时全程不需要联网，绕开"官方下载页在国内访问不稳定"的问题。
_VCREDIST_EXE = bundled_resource_dir() / "tools" / "vcredist" / "vcredist_x86.exe"

# ktech.exe 是控制台程序，不加这个每次调用都会在 GUI 上方一闪而过一个黑色
# 控制台窗口（首次转换某个图标/头像时能看到，比如刚发现一个新拷贝进来的
# 服务器存档）。
_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# ktech.exe 依赖同目录下一批老版本 ImageMagick 的 DLL（CORE_RL_*/IM_MOD_
# RL_*），这批 DLL 又依赖 MSVCR120.dll/MSVCP120.dll（Visual C++ 2013 运
# 行库，Windows 不自带，真机上碰到过用户机器没装，表现是 ktech.exe 弹
# "0xc000007b 应用程序无法正常启动"）。这个退出码是操作系统层面固定
# 的信号（STATUS_INVALID_IMAGE_FORMAT），Python 里读到的是它的有符号
# 32 位表示。
_MISSING_RUNTIME_EXIT_CODE = -1073741701  # 0xC000007B

_runtime_probed = False
_runtime_missing = False


def probe_ktech_runtime() -> bool:
    """探测 ktech.exe 能不能正常启动（缺 VC++ 2013 运行库时会在加载阶段
    直接崩溃，退出码固定是 `_MISSING_RUNTIME_EXIT_CODE`）。真正探测一次
    的开销跑一次子进程，整个程序生命周期内只探测一次，结果缓存复用。

    返回 True 表示确认缺运行库，调用方（GUI 层）据此提示用户安装；
    返回 False 涵盖"运行库正常"和"没法判断"（ktech.exe 缺失等）两种
    情况——后者不属于这个函数要处理的问题，交给 tex_to_png() 正常报错。
    """
    global _runtime_probed, _runtime_missing
    if _runtime_probed:
        return _runtime_missing
    _runtime_probed = True
    if not _KTECH_EXE.exists():
        return False
    try:
        result = subprocess.run(
            [str(_KTECH_EXE)],
            cwd=str(_TOOLS_DIR),
            capture_output=True,
            timeout=10,
            creationflags=_CREATIONFLAGS,
        )
    except Exception:
        return False
    _runtime_missing = result.returncode == _MISSING_RUNTIME_EXIT_CODE
    return _runtime_missing


def launch_vcredist_installer() -> bool:
    """本地拉起内置的 Visual C++ 2013 (x86) 运行库安装程序。这是一个带
    自己 GUI 向导的独立进程，装完需要用户自己点完成，这里只负责拉起、
    不等待也不轮询安装结果——**不能**带 `_CREATIONFLAGS`（那是给 ktech.
    exe 这种控制台程序隐藏黑框用的，装到这个安装向导上没有意义）。装完
    需要重启 DSTCamp 才会生效（下次进页签重新探测）。

    返回是否成功拉起了安装进程；找不到安装包（理论上不该发生，除非打
    包缺失）才返回 False，调用方据此提示用户去官网自己下载。
    """
    if not _VCREDIST_EXE.exists():
        return False
    try:
        subprocess.Popen([str(_VCREDIST_EXE)])
    except Exception:
        return False
    return True


def tex_to_png(tex_path: Path, out_path: Path) -> bool:
    """把单个 .tex 文件转换成 PNG。

    成功返回 True；工具缺失或转换失败（源文件损坏/不存在等）返回 False，
    从不抛异常——调用方应把失败一律当作"没有图标可用"处理。
    """
    if not _KTECH_EXE.exists() or not Path(tex_path).exists():
        return False
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # ktech.exe 只在这个临时目录（保证纯 ASCII）里落地，真正的目标路径
    # 哪怕带中文也不会直接交给它，见本文件顶部说明。
    with tempfile.TemporaryDirectory(prefix="dstools_ktech_") as tmp_dir:
        staged_out = Path(tmp_dir) / out_path.name
        try:
            result = subprocess.run(
                [str(_KTECH_EXE), str(tex_path), str(staged_out)],
                cwd=str(_TOOLS_DIR),
                capture_output=True,
                timeout=30,
                creationflags=_CREATIONFLAGS,
            )
        except Exception:
            return False
        if result.returncode != 0 or not staged_out.exists():
            return False
        try:
            shutil.move(str(staged_out), str(out_path))
        except OSError:
            return False
    return out_path.exists()
