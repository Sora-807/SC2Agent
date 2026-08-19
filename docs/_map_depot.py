"""临时：打印 BL 扫描区 depot(2x2) 可建造地图。"""
import re

depot = set()
cc = None
for line in open("docs/slot_scan_a.log", encoding="utf-8", errors="replace"):
    if "spawn CC=" in line:
        nums = re.findall(r"[\d.]+", line.split("CC=")[1])
        cc = (float(nums[0]), float(nums[1]))
    m = re.match(r"BUILDABLE depot world=\(([\d.]+),([\d.]+)\)", line)
    if m:
        depot.add((float(m.group(1)), float(m.group(2))))
xs = [cc[0] + dx for dx in range(-16, 17)]
ys = [cc[1] + dy for dy in range(-16, 27)]
print("   " + "".join(f"{int(x % 10)}" for x in xs))
for y in reversed(ys):
    row = "".join("#" if (x, y) in depot else "." for x in xs)
    print(f"{int(y):2d} {row}")
