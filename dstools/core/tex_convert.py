"""Convert Klei .tex textures to PNG using a bundled copy of ktech.exe.

DST (and every mod's modicon.tex) stores images in Klei's own .tex
format. ktech.exe is Klei's own official command-line converter -- a
copy ships in tools/ktools/ next to this repo (instead of relying on a
tool installed elsewhere on the user's machine, which they may move or
delete). Note: ktech.exe fails with a cryptic
"NoDecodeDelegateForThisImageFormat" error if invoked from a working
directory containing non-ASCII characters, so callers must not move
this tool into a path with Chinese/etc. characters.
"""

import subprocess
import sys
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
    try:
        result = subprocess.run(
            [str(_KTECH_EXE), str(tex_path), str(out_path)],
            cwd=str(_TOOLS_DIR),
            capture_output=True,
            timeout=30,
            creationflags=_CREATIONFLAGS,
        )
    except Exception:
        return False
    return result.returncode == 0 and out_path.exists()
