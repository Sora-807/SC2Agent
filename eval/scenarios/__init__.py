"""场景包：import 即注册（registry 闭集标签校验在 add 时跑）。

加新场景 = 在对应域文件里写一个 @scenario（或显式 register(Project(...))），
再在这里补一行 import —— 框架代码零改动（「轻松注册」约定）。
"""
from eval.scenarios import boundary, live, planning  # noqa: F401
