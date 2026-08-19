"""driver 模块：唯一 SC2 适配器（红线 R2：只依赖 game + sc2，零业务规则）。

- sc2_adapter：真实现（SC2GamePort + Raw 抽取 + Operation→burnysc2 命令翻译）
- fake：FakeGamePort（脚本驱动、不连 SC2，供 world/flow/engine 测试用）
- recorder：StateRecorder（按整秒存 state trace，调试/发现用）
"""