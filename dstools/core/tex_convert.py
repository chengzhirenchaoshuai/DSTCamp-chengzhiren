"""Convert Klei .tex textures to PNG using a bundled copy of ktech.exe.

DST (and every mod's modicon.tex) stores images in Klei's own .tex
format. ktech.exe is Klei's own official command-line converter -- a
copy ships in tools/ktools/ next to this repo (instead of relying on a
tool installed elsewhere on the user's machine, which they may move or
delete).

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

from dstools.core.resource_paths import bundled_resource_dir

_TOOLS_DIR = bundled_resource_dir() / "tools" / "ktools"
_KTECH_EXE = _TOOLS_DIR / "ktech.exe"

# ktech.exe is a console app -- without this, every call briefly flashes a
# black console window on top of the GUI (visible whenever an icon/avatar
# needs converting for the first time, e.g. right after discovering a
# newly-copied server save).
_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def tex_to_png(tex_path: Path, out_path: Path) -> bool:
    """Convert a single .tex file to a PNG.

    Returns True on success, False if the tool is missing or conversion
    failed (corrupt/missing source file etc.). Never raises -- callers
    should treat failure as "no icon available".
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
