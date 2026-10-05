#!/usr/bin/env python3
"""飲む候補の駅を選んで web/data/spots.json に書き出す。

**選び方を手で決めている理由**:
OSM の飲み屋（`amenity=pub` / `bar` / `nightclub`）の件数で順位を付けると、
大船 210 件 > 池袋 56 件 のような並びになる。OSM の整備は地域とマッパーの偏りが
大きく、**件数は「飲み屋の多さ」ではなく「誰がどれだけ地図を描いたか」を表している**。
そこで候補はよく知られた繁華街から手で選び、方角が散るようにした。
飲み屋の件数は参考値として載せるだけにしてある。

出力: web/data/spots.json
"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "web" / "data"
BARS = ROOT.parent / "nomikai-navi" / "data" / "bars_by_station.json"

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

    bars = {}
    try:
        for r in json.loads(BARS.read_text()):
            bars[r["name"]] = max(bars.get(r["name"], 0), r["bars"])
    except FileNotFoundError:
        print("  （飲み屋の件数は見つからなかったので 0 にする）")

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
            "bars": bars.get(name, 0),
        })

    # どれだけ散っているかを見ておく
    worst = min(
        (km((a["y"], a["x"]), (b["y"], b["x"])), a["name"], b["name"])
        for k, a in enumerate(out) for b in out[k + 1:]
    )
    print("候補 %d 駅" % len(out))
    for s in out:
        print("  %-6s %-4s 飲み屋 %4d 件（参考）" % (s["name"], s["dir"], s["bars"]))
    print("いちばん近い2駅: %s—%s %.1fkm" % (worst[1], worst[2], worst[0]))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "spots.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("出力 %s" % (OUT / "spots.json"))


if __name__ == "__main__":
    main()
