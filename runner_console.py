"""共享 rich console 单例：避免多处重复实例化导致样式/布局错乱。"""
from __future__ import annotations

from rich.console import Console

console = Console()
