"""dstools - DSTCamp, a Don't Starve Together local server manager."""

import sys
from pathlib import Path

__version__ = "1.0.0"

# 源码运行时，把 Python 字节码缓存(__pycache__)集中到项目内
# reference/_cache/pycache/，跟打包中间产物 build/ 一样放在 reference/_cache/
# 下，方便随时整删，不占用 %APPDATA% 的软件运行时缓存目录。
# 打包成 exe 后(sys.frozen)跳过——exe 里 import 的模块在 sys._MEIPASS 临时目录，
# 缓存写进去退出即清，没必要再指到项目目录。本文件自己的 pycache 因为是在执行
# 到这里之前就生成的，仍会落在 dstools/__pycache__/ 里(唯一的残留)。
if not getattr(sys, "frozen", False):
    _pycache_prefix = Path(__file__).resolve().parent.parent / "reference" / "_cache" / "pycache"
    if not sys.pycache_prefix:
        sys.pycache_prefix = str(_pycache_prefix)
