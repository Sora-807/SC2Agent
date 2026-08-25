"""eval.scenario：@scenario 声明式注册糖 —— 加一个评测场景不碰框架代码。

用法（live/boundary 域最常见的形态，10 行一个场景）：

    @scenario("L3-supply-cap", tags=["live"],
              text="看看生产上最该修的一件事，提案解决",
              note="快卡人口：期望 propose 补给站，sim 人口不卡",
              graders=[ToolSequenceGrader(must=["observe", "propose"]),
                       ProposalGrader(expect_op="build", expect_type="terran/supplydepot"),
                       SimOutcomeGrader(final_has={"terran/supplydepot": 1})])
    def setup(client):
        ...  # TestClient 造局面（tick / 入队 / 配额）

    - setup(client)：session/start 之后跑（对局面）；想预置规划文件用 prepare(tmp)
      写 tmp/plans 等（见 OfflineSessionFixture 约定式挂载）。
    - 需要非默认 runner（假 live / 重管线）或非离线 fixture 时，退回显式
      `register(Project(...))`（两种形态并存，这个只是最常用的糖）。
"""
from __future__ import annotations

from typing import Callable

from eval.contracts import Project, Task
from eval.fixture import OfflineSessionFixture
from eval.registry import register
from eval.runner import SingleRoundRunner


def scenario(project_id: str, *, tags: list[str], text: str, note: str = "",
             graders: list, runs: int | None = None,
             max_turns: int = 24, mount_readonly: bool = False,
             prepare: Callable | None = None) -> Callable:
    def deco(setup_fn: Callable) -> Project:
        return register(Project(
            id=project_id,
            tags=tuple(tags),
            fixture=OfflineSessionFixture(setup_fn, prepare=prepare),
            task=Task(text=text, note=note),
            runner=SingleRoundRunner(max_turns=max_turns,
                                     mount_readonly=mount_readonly),
            graders=list(graders),
            runs=runs,
        ))
    return deco
