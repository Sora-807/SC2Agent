"""trace 事件词汇表(ADR-0004):类型常量 + 载重键名常量,单一词汇源。

emit 方(runner/engine,经 ``tracer.event``)和消费方(``Tracer.finalize_summary``、
``timeline``/``render``)引用这些常量,不写字面量。

payload 仍 freeform kwargs(``tracer.event`` 仍接受 ``**payload``);这里只约束"类型名"
和"读方关心的键名"。emit 的 kwarg 名本身是 Python 标识符(拼错即 TypeError,自带保护),
所以键名常量主要给读方用。
"""
from __future__ import annotations

# ---- 事件类型(emit 的 type 参;读方按此分支)----
RUN_START = "run_start"          # engine.create_or_get
TURN_START = "turn_start"         # runner 每轮开始
LLM_CALL = "llm_call"             # runner 一次 LLM complete
TOOL_CALL = "tool_call"           # runner 一次工具执行(含 done,done 现是普通工具)
TURN_END = "turn_end"             # runner 每轮结束
RUN_END = "run_end"               # runner 一次 run 结束(done/paused)
DISPATCH = "dispatch"             # engine 派子(tracer.dispatch 记父子边 + 发事件)
LLM_TIMEOUT = "llm_timeout"      # runner LLM 调用超时

ALL_TYPES = (RUN_START, TURN_START, LLM_CALL, TOOL_CALL, TURN_END, RUN_END, DISPATCH, LLM_TIMEOUT)

# ---- 载重键名(读方 finalize_summary / viz 按名取)----
# 通用
TURN_NO = "turn_no"
STARTED_AT = "started_at"
DURATION_MS = "duration_ms"
TIMESTAMP = "ts"
AGENT_ID = "agent_id"
SEQUENCE = "seq"
# llm_call
MODEL = "model"
INPUT_TOKENS = "input_tokens"
OUTPUT_TOKENS = "output_tokens"
CACHED_TOKENS = "cached_tokens"
UNCACHED_TOKENS = "uncached_tokens"
INPUT_COUNT = "input_count"
REASONING_REF = "reasoning_ref"
REASONING_LEN = "reasoning_len"
RESPONSE_PREVIEW = "response_preview"
# tool_call
CALL_ID = "call_id"
TOOL = "tool"
ARGS = "args"
RESULT_PREVIEW = "result_preview"
# run_start / run_end / dispatch
TYPE_KEY = "type_key"
TARGET = "target"
VERSION = "version"
CALLER_TARGET = "caller_target"
OUTCOME = "outcome"
REASON = "reason"
TOTAL_INPUT_TOKENS = "total_input_tokens"
TOTAL_OUTPUT_TOKENS = "total_output_tokens"
CALLEE_TYPE = "callee_type"
