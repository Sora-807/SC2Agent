"""eval.registry：场景注册（D5：@register 装饰器 + 拒绝重复 id + 闭集标签校验）。"""
from __future__ import annotations

from eval.contracts import Project

#: 闭集标签（防漂移；加新标签 = 显式改这里）
TAGS = frozenset({"live", "planning", "boundary", "real", "轻", "重", "语音"})


class Registry:
    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}

    def add(self, project: Project) -> Project:
        if project.id in self._projects:
            raise ValueError(f"评测项目 id 重复：{project.id}")
        bad = set(project.tags) - TAGS
        if bad:
            raise ValueError(f"{project.id} 标签不在闭集：{sorted(bad)}（可用：{sorted(TAGS)}）")
        self._projects[project.id] = project
        return project

    def get(self, project_id: str) -> Project:
        if project_id not in self._projects:
            raise KeyError(f"没有评测项目 {project_id!r}（可用：{sorted(self._projects)}）")
        return self._projects[project_id]

    def select(self, ids: list[str] | None = None, tags: list[str] | None = None) -> list[Project]:
        out = list(self._projects.values())
        if ids:
            out = [self.get(i) for i in ids]
        if tags:
            want = set(tags)
            out = [p for p in out if want & set(p.tags)]
        return out

    def ids(self) -> list[str]:
        return sorted(self._projects)


REGISTRY = Registry()


def register(project: Project) -> Project:
    return REGISTRY.add(project)
