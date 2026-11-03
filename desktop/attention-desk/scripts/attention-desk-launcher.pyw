"""Shortcut target: pythonw keeps the launcher and its services windowless."""
import ctypes
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from server.desktop_launcher import main
    raise SystemExit(main())
except Exception:
    ctypes.windll.user32.MessageBoxW(None, 'Attention Desk 的启动文件不完整，请重新安装桌面入口。', 'Attention Desk', 0x30)
