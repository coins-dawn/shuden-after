#!/usr/bin/env python3
"""自宅に選べる 10 駅を決めて web/data/homes.json に書く。

    python3 scripts/pick_homes.py

**方角と距離が散るように手で選んである。** 選び方の記録:

- 東京駅からの方角 8 方位と、距離 18〜42km に散らす
- **飲む候補の 10 駅とは重ねない**（重ねるとその自宅のとき候補が 9 駅に減る）
- 都心（10km 以内）は入れない。歩いて帰れる距離で「終電を逃す」話をしても仕方がない
- ⚠ **町田は外した。** 小田急の列車時刻表が提供されていないため横浜線だけで計算され、
  終電が 22:39 と実際よりかなり早く出てしまう。事業者の不利になる見せ方になる
- 結果が一様にならないことを確認して選んだ（00:15 に店を出たときの運賃の幅）:

    三鷹   終電 23:31〜00:55  ¥600〜¥12,240（20.4 倍）  いちばん楽な家
    川崎   終電 23:12〜00:19  ¥600〜¥16,680（27.8 倍）
    船橋   終電 23:35〜00:34  ¥600〜¥17,760（29.6 倍）
    浦和   終電 23:20〜00:15  ¥600〜¥22,440（37.4 倍）  4 駅が同額になる例
    柏     終電 23:20〜00:22  ¥600〜¥23,760（39.6 倍）
    千葉   終電 23:22〜00:15  ¥960〜¥25,200（26.2 倍）
    立川   終電 23:14〜00:27  ¥600〜¥19,200（32.0 倍）
    川越   終電 23:30〜00:30  ¥600〜¥26,520（44.2 倍）
    春日部 終電 23:10〜00:37  ¥960〜¥30,480（31.8 倍）  いちばん高くつく
    大船   終電 23:05〜00:24  ¥600〜¥30,960（51.6 倍）  いちばん差が大きい
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
