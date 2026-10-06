#!/usr/bin/env python3
"""候補の駅 20 を web/data/spots.json に書き出す。

駅は下の SPOTS に手で書いてある（選び方の記録はワークスペース側）。
中心からの方角と距離が散るように選んであり、いちばん近い組でも 3.5km 離れている。

出力: web/data/spots.json
"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "web" / "data"

# 終電を逃しうる駅 20。**中心からの方角と距離が散るように**選んである。
# 2026-10-06 に 10 → 20 へ。ユーザー「都心でふやすとごちゃごちゃになるので、
# いい感じにばらけさせて」。足した 10 駅のうち 9 駅は東京駅から 11km 以上の外側。
# 近すぎる組は作らない（いちばん近いのは 新宿-中野 3.5km で、元からある 新宿-渋谷 3.7km と同じくらい）。
SPOTS = [
    # 都心
    ("新宿", "西"),
    ("渋谷", "南西"),
    ("池袋", "北西"),
    ("上野", "北"),
    ("錦糸町", "東"),
    ("新橋", "南"),
    ("北千住", "北東"),
    ("中野", "西"),
    # 10〜20km
    ("赤羽", "北"),
    ("蒲田", "南"),
    ("武蔵小杉", "南西"),
    ("吉祥寺", "西"),
    ("松戸", "北東"),
    ("和光市", "北西"),
    # 20km 以遠
    ("津田沼", "東"),
    ("海浜幕張", "東"),
    ("横浜", "南"),
    ("大宮", "北"),
    ("八王子", "西"),
    ("橋本", "南西"),
]


def km(a, b):
    return math.hypot((a[0] - b[0]) * 111.0, (a[1] - b[1]) * 91.0)


def main():
    base = json.loads((OUT / "base.json").read_text())
    nodes = base["nodes"]

    by_name = {}
    for i, n in enumerate(nodes):
        if n["v"] and n["n"] not in by_name:
            by_name[n["n"]] = i

    out = []
    for name, dirn in SPOTS:
        i = by_name.get(name)
        if i is None:
            print("  ! %s がネットワークに無い" % name, file=sys.stderr)
            continue
        out.append({
            "node": i, "name": name, "dir": dirn,
            "y": nodes[i]["y"], "x": nodes[i]["x"],
        })

    # どれだけ散っているかを見ておく
    worst = min(
        (km((a["y"], a["x"]), (b["y"], b["x"])), a["name"], b["name"])
        for k, a in enumerate(out) for b in out[k + 1:]
    )
    print("候補 %d 駅" % len(out))
    for s in out:
        print("  %-6s %s" % (s["name"], s["dir"]))
    print("いちばん近い2駅: %s—%s %.1fkm" % (worst[1], worst[2], worst[0]))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "spots.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("出力 %s" % (OUT / "spots.json"))


if __name__ == "__main__":
    main()
