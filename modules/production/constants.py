"""production 常量单源：worker.py（工兵归属）与 economy.py（经济维持器）共用的真机常数。

此前两份拷贝互相同步靠注释（economy 自注「与 worker.py 同源」）——改一处漏一处
就是两个模块对「一个矿脉几个人采」给出不同答案。单源后谁也别再抄。
"""
from __future__ import annotations

MINERAL_SATURATION = 2  # 每个矿脉的采集上限（P0）
GAS_SATURATION = 3  # 每个气井的采集上限（P0）
NODE_RADIUS = 20.0  # 资源节点归属半径：只取距主基锚点此距离内的矿脉/气井
                     # （真机教训：全图选节点会把农民派到敌方基地送死）
