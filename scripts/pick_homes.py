#!/usr/bin/env python3
"""起点に選べる駅 10 を決めて web/data/homes.json に書く。

    python3 scripts/pick_homes.py

駅は下の HOMES に手で書いてある（選び方の記録はワークスペース側）。
東京駅からの方角 8 方位・距離 18〜42km に散らしてある。

⚠ **列車時刻表が提供されていない事業者に頼る駅は入れない。**
その駅だけ計算が実態と大きくずれる。
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / "web" / "data" / "base.json").read_text())
NODES = BASE["nodes"]

# 近い順でも遠い順でもなく、**方角がひと回りする順**に並べておく（選ぶときに分かりやすい）
HOMES = ["浦和", "川越", "春日部", "柏", "船橋", "千葉", "川崎", "大船", "三鷹", "立川"]


def main():
    tokyo = next(i for i, n in enumerate(NODES) if n["n"] == "東京")
    ty, tx = NODES[tokyo]["y"], NODES[tokyo]["x"]
    road = set(json.loads((ROOT / "data" / "road_nodes.json").read_text()))
    out = []
    for nm in HOMES:
        ids = [i for i, n in enumerate(NODES) if n["n"] == nm and n.get("v")]
        if not ids:
            sys.exit("%s が見つからない" % nm)
        i = ids[0]
        if i not in road:
            sys.exit("%s は道路距離の行列に入っていない" % nm)
        dy = (NODES[i]["y"] - ty) * 111.0
        dx = (NODES[i]["x"] - tx) * 91.0
        b = (math.degrees(math.atan2(dx, dy)) + 360) % 360
        d = ["北", "北東", "東", "南東", "南", "南西", "西", "北西"][int(((b + 22.5) % 360) // 45)]
        out.append({"node": i, "name": nm, "dir": d, "km": round(math.hypot(dy, dx), 1),
                    "y": NODES[i]["y"], "x": NODES[i]["x"]})
    p = ROOT / "web" / "data" / "homes.json"
    p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("書きました: %s" % p)
    for h in out:
        print("  %-5s %-3s %.1fkm" % (h["name"], h["dir"], h["km"]))


if __name__ == "__main__":
    main()
