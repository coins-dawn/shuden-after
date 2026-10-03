#!/usr/bin/env python3
"""駅ノード・路線の線・色を、ブラウザが読む 1 ファイルにまとめる。

入力: data/raw/{stations,railways,train_timetables}.json（fetch_odpt.py）
出力: web/data/base.json

内容:
  nodes  [{n: 駅名, y: 緯度, x: 経度, w: 重み（通る路線の数）, v: 画面に出すか}]
  edges  [[ノードa, ノードb, 色の添字]]  隣り合う停車駅を結んだもの（重複は除く）
  colors ["#f62e36", ...]  路線のラインカラー
  lines  ["丸ノ内線", ...] 色と同じ並びの路線名
"""
import json
import math
from collections import defaultdict
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import network  # noqa: E402

RAW = ROOT / "data" / "raw"
OUT = ROOT / "web" / "data"

# ラインカラーが入っていない事業者ぶんの控えめな既定色
FALLBACK = "#8d93a0"

# 画面に出す範囲。東京駅からこの距離まで。
# ネットワークにはこの外（仙台・高崎・熱海など）も入っていて探索には使うが、
# 全部描くと首都圏が豆粒になるので、描くのはここまでにする。
VIEW_CENTER = (35.6812, 139.7671)   # 東京駅
VIEW_RADIUS_KM = 50.0


def main():
    net = network.Network()
    stations = json.loads((RAW / "stations.json").read_text())
    railways = json.loads((RAW / "railways.json").read_text())

    rw_info = {}
    for r in railways:
        rw_info[r["owl:sameAs"]] = (
            r.get("dc:title") or r["owl:sameAs"].split(".")[-1],
            r.get("odpt:color") or FALLBACK,
        )

    # 色は路線ごと。使った路線だけを並べる
    line_idx, lines, colors = {}, [], []

    def cid(rid):
        if rid not in line_idx:
            title, color = rw_info.get(rid, (rid.split(".")[-1], FALLBACK))
            line_idx[rid] = len(lines)
            lines.append(title)
            colors.append(color)
        return line_idx[rid]

    # ノードの重み = そのノードを通る路線の数（ラベルを出す順に使う）
    node_lines = defaultdict(set)
    for s in stations:
        nid = net.sta2node.get(s["owl:sameAs"])
        if nid is not None:
            node_lines[nid].add(s.get("odpt:railway", ""))

    # 路線の線は `odpt:stationOrder`（その路線の正しい駅の並び）から引く。
    #
    # 以前は列車時刻表の「隣り合う停車駅」から引いていたが、**速達便が通過駅を飛ばすため、
    # 同じ区間に長短 2 本の辺ができて三角形になっていた**
    # （例: 大宮—宮原—上尾 に対して 大宮—上尾 が重なる）。
    edges = {}
    for r in railways:
        rid = r["owl:sameAs"]
        so = r.get("odpt:stationOrder") or []
        seq = []
        for o in sorted(so, key=lambda o: o.get("odpt:index", 0)):
            nid = net.sta2node.get(o.get("odpt:station"))
            if nid is not None and (not seq or seq[-1] != nid):
                seq.append(nid)
        c = cid(rid)
        for a, b in zip(seq, seq[1:]):
            key = (a, b) if a < b else (b, a)
            edges.setdefault(key, c)

    def dist(a, b):
        return network.dist_m(net.node_pos[a], net.node_pos[b])

    # 新幹線のような飛びを除く
    dropped_long = [k for k in edges if dist(*k) > 25000]
    for k in dropped_long:
        del edges[k]

    # それでも残る近道の辺を落とす。
    # a—b があって、別の駅 c を経由する a—c—b がほぼ同じ長さで引けるなら、a—b は近道。
    adj = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    shortcut = []
    for a, b in edges:
        d = dist(a, b)
        if d < 600:          # もともと短い辺は、三角に見えないので触らない
            continue
        for c in adj[a]:
            if c == b or c not in adj[b]:
                continue
            if dist(a, c) + dist(c, b) < d * 1.3:
                shortcut.append((a, b))
                break
    for k in shortcut:
        del edges[k]

    def from_center_km(i):
        y, x = net.node_pos[i]
        return math.hypot((y - VIEW_CENTER[0]) * 111.0, (x - VIEW_CENTER[1]) * 91.0)

    nodes = [
        {
            "n": net.node_name[i],
            "y": round(net.node_pos[i][0], 6),
            "x": round(net.node_pos[i][1], 6),
            "w": len(node_lines.get(i, ())),
            "v": 1 if from_center_km(i) <= VIEW_RADIUS_KM else 0,
        }
        for i in range(len(net.node_name))
    ]

    OUT.mkdir(parents=True, exist_ok=True)
    base = {
        "nodes": nodes,
        "edges": [[a, b, c] for (a, b), c in edges.items()],
        "lines": lines,
        "colors": colors,
        "foot": [[i, j, c] for i, lst in net.foot.items() for j, c in lst if i < j],
    }
    (OUT / "base.json").write_text(json.dumps(base, ensure_ascii=False, separators=(",", ":")))

    vis = sum(n["v"] for n in nodes)
    print("ノード %d（うち画面に出す %d／東京駅から %.0fkm 以内）/ 辺 %d / 路線 %d"
          % (len(nodes), vis, VIEW_RADIUS_KM, len(base["edges"]), len(lines)))
    print("除外: 25km超 %d 本 / 近道の辺 %d 本" % (len(dropped_long), len(shortcut)))
    print("徒歩連絡 %d 本" % len(base["foot"]))
    print("出力 %s (%.1fMB)" % (OUT / "base.json", (OUT / "base.json").stat().st_size / 1e6))


if __name__ == "__main__":
    main()
