"""eval.scenarios.heavy：跟随回路场景（H0 假 live；H1 真机 normal 是 ⑧，驱动换源即可）。"""
from __future__ import annotations

from eval.graders import ProposalGrader, RegexGrader, ToolSequenceGrader
from eval.registry import register
from eval.contracts import Project, Task
from eval.fixture import OfflineSessionFixture
from eval.runner import FakeFollowRunner
from eval.scenarios.live import setup_gas_block

register(Project(
    id="H0-fake-live-follow",
    tags=("live", "重"),
    fixture=OfflineSessionFixture(setup_gas_block),
    task=Task(
        text="跟着这局打完：该修的生产问题用提案修，游戏结束后给我终局总结",
        note="假 live 跟随（⑦）：后台 tick 到 120 游戏秒停局；observe→提案→跟随→终局总结",
    ),
    runner=FakeFollowRunner(horizon_game=120.0, tick_interval=0.05, min_wall=90.0),
    graders=[
        ToolSequenceGrader(must=["observe"], forbid=["queue_op"]),
        ProposalGrader(expect_op="build", expect_type="terran/refinery",
                       allow_invalid_attempts=True),
        RegexGrader(reply_any_of=["终局", "总结", "结束"]),
    ],
    runs=1,
))
