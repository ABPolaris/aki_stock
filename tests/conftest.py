"""让测试在插件目录内直接运行，不依赖宿主或安装为包。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
