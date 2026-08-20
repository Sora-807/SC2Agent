"""Store:共享级持久化(可选)+ DirStore 参考实现。

共享 workspace 模型下持久化是整空间的:
- load():启动时载入初始共享 workspace 文件(主 agent 全权接管)。
- save(files):start 结束时落盘整个共享 workspace 快照({path: content})。
归属(grant/区域)是运行时态,不持久化——重载后全部归主(默认)。

DirStore:把 workspace 落盘到一个目录(每文件 = <root>/<path>),load/save 读写该目录。
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Store(Protocol):
    async def load(self) -> dict[str, str]: ...
    async def save(self, files: dict[str, str]) -> None: ...


class DirStore:
    """把共享 workspace 落盘到一个目录。load 读目录下所有文件;save 写 {path: content}。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    async def load(self) -> dict[str, str]:
        """读目录下所有文件为 {relpath: content}(主 agent 全权接管)。"""
        if not self.root.exists():
            return {}
        out: dict[str, str] = {}
        for p in self.root.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(self.root)).replace("\\", "/")
                try:
                    out[rel] = p.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    pass  # 跳过非文本/不可读
        return out

    async def save(self, files: dict[str, str]) -> None:
        """把 {path: content} 写到目录(每文件 = <root>/<path>)。"""
        self.root.mkdir(parents=True, exist_ok=True)
        for path, content in files.items():
            p = self.root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
