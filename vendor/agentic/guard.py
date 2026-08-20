"""Guard:共享 workspace 的写权归属登记(独占)。

一个文件同时只能被一个 agent 写(独占)。归属优先级:
  显式 grant(path→agent) > 子区域(`agents/<target>/` 下,子 active 时) > 默认归主(main)。
- can_write(agent,path) = owner(path) == agent。
- can_see(agent,path,hidden):非 hidden 全可见(区域外 ro);hidden 仅自己区域+grant 可见;主全可见。
- 子 done/error → release:撤其区域 + 其持有的 grant,归属回主。
这不是运行时 mutex——是个登记表(asyncio 单线程 + 写操作原子,无 race)。
"""
from __future__ import annotations


class Guard:
    def __init__(self) -> None:
        self._main: str | None = None
        self._regions: dict[str, str] = {}   # agent_target -> region prefix
        self._grants: dict[str, str] = {}    # path -> agent_target(独占)

    # ---- 配置 ----
    def set_main(self, target: str) -> None:
        self._main = target

    @property
    def main(self) -> str | None:
        return self._main

    def add_region(self, target: str, prefix: str) -> None:
        """子 active 时,其 region prefix 下文件归该子(独占写)。"""
        self._regions[target] = prefix

    def region_of(self, target: str) -> str | None:
        return self._regions.get(target)

    def grant(self, path: str, target: str) -> None:
        """把 path 的写权独占地授予 target。path 可以是文件(精确)或文件夹(末尾 '/',前缀——
        子可建/改该前缀下任何文件)。已被别的 agent 持有 → 报错(独占)。"""
        cur = self._grants.get(path)
        if cur is not None and cur != target:
            raise PermissionError(f"{path} already held by {cur}")
        self._grants[path] = target

    def release(self, target: str) -> None:
        """子 done/error:撤其区域 + 其持有的 grant(归属回主)。"""
        self._regions.pop(target, None)
        for p, t in list(self._grants.items()):
            if t == target:
                del self._grants[p]

    # ---- 查询 ----
    def owner(self, path: str) -> str | None:
        if path in self._grants:                    # 精确文件 grant
            return self._grants[path]
        for gp, gt in self._grants.items():         # 文件夹 grant(前缀,末尾 /)
            if gp.endswith("/") and path.startswith(gp):
                return gt
        for t, reg in self._regions.items():        # 子默认区域
            if reg and path.startswith(reg):
                return t
        return self._main

    def can_write(self, target: str, path: str) -> bool:
        return self.owner(path) == target

    def can_see(self, target: str, path: str, hidden: bool) -> bool:
        """非 hidden 全可见(区域外 ro);hidden 仅自己区域+grant 可见;主全可见。"""
        if not hidden or target == self._main:
            return True
        reg = self._regions.get(target)
        if reg and path.startswith(reg):
            return True
        for grant_path, grant_target in self._grants.items():
            if grant_target == target and (
                grant_path == path or (grant_path.endswith("/") and path.startswith(grant_path))
            ):
                return True
        return False
