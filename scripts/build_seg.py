#!/usr/bin/env python3
"""帰り道の電車の線を、**路線の形**に沿わせるための区間データを作る。

    python3 scripts/build_seg.py        # build_static.py のあとに実行する

答えの表（site/data/home/*.bin）が持っている電車の経路は「**停まる駅の並び**」なので、
そのまま結ぶと駅と駅のあいだが直線になる。急行や中距離電車は停車駅が離れているので、
横浜 → 東京 が一本の弦になってしまっていた。

ここでは N02（鉄道）の線から**鉄道のグラフ**を組み、経路に出てくる
**隣り合う 2 駅のあいだを線路沿いにたどった形**を書き出す。
地図に描いてある線と同じ点を使うので、帰り道は路線にぴったり重なる。

出力: site/data/seg.json
    {"<小さいノード id>,<大きいノード id>": [[緯度, 経度], ...]}   ※向きは問わない
"""
import heapq
import json
import math
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import build_rail as BR  # noqa: E402

SITE = ROOT / "site" / "data"
NONE16 = 0xFFFF
SNAP_R = 500              # 駅のまわりこの距離の点を、ぜんぶ入口にする
SNAP_N = 14               # ただし近いほうから最大この数まで
DETOUR = 2.6              # 直線距離のこれ倍を超えたら、たどり方がおかしいので捨てる


def mdist(a, b):
    """度 → おおよそのメートル。"""
    dy = (a[0] - b[0]) * 111000.0
    dx = (a[1] - b[1]) * 111000.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dy, dx)


def rail_polylines(z):
    """build_rail.main() と**同じ条件**で、枠に切って間引いた折れ線を返す。"""
    ops = {t[0] for t in BR.OP_FALLBACK}
    tol = BR.TOL_M / 111000.0
    lo0, la0, lo1, la1 = BR.BBOX
    inside = lambda c: lo0 <= c[0] <= lo1 and la0 <= c[1] <= la1
    out = []
    for ft in BR.load(z, "RailroadSection"):
        pr = ft["properties"]
        if pr["N02_004"] not in ops or pr["N02_002"] == "1":
            continue
        run = []
        for c in ft["geometry"]["coordinates"]:
            if inside(c):
                run.append((c[0], c[1]))
            else:
                if len(run) > 1:
                    out.append(BR.dp(run, tol))
                run = []
        if len(run) > 1:
            out.append(BR.dp(run, tol))
    return out


def build_graph(polys):
    """近い点はひとつにまとめて、隣り合う点をつなぐ。

    ⚠ **1e-5 度（1.1m）でまとめると線がつながらない。** N02 の区間どうしは
    端点の座標がぴったり同じとは限らないので、**1e-4 度（11m）でまとめる**。
    線はもともと 35m で間引いてあるので、この粗さで困ることはない。
    座標は最初に出てきたもの（5 桁）をそのまま使う＝地図の線と同じ点になる。
    """
    vid, V, adj = {}, [], []
    def node(lat, lon):
        k = (round(lat, 4), round(lon, 4))
        i = vid.get(k)
        if i is None:
            i = len(V)
            vid[k] = i
            V.append((round(lat, 5), round(lon, 5)))
            adj.append([])
        return i
    for pts in polys:
        prev = None
        for lon, lat in pts:
            cur = node(lat, lon)
            if prev is not None and prev != cur:
                w = mdist(V[prev], V[cur])
                adj[prev].append((cur, w))
                adj[cur].append((prev, w))
            prev = cur
    return V, adj


def grid_index(V, cell=0.01):
    g = {}
    for i, (la, lo) in enumerate(V):
        g.setdefault((int(la / cell), int(lo / cell)), []).append(i)
    return g, cell


def near_all(g, cell, V, la, lo):
    """駅のまわりの点を近い順に返す。

    ⚠ **いちばん近い 1 点だけに寄せてはいけない。** 池袋のような駅では
    地下鉄の線がいちばん近いことがあり、そこへ寄せると JR の線とつながらず
    「たどれない」になる。**まわりの点をぜんぶ入口にして**探す。
    """
    out = []
    cy, cx = int(la / cell), int(lo / cell)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            for i in g.get((cy + dy, cx + dx), ()):
                d = mdist(V[i], (la, lo))
                if d <= SNAP_R:
                    out.append((d, i))
    out.sort()
    return [i for _, i in out[:SNAP_N]]


def astar(adj, V, srcs, dsts, goal):
    """入口の集合から出口の集合へ。残りは goal（出る駅の座標）までの直線で見積もる。"""
    dset = set(dsts)
    dist, prev, pq, seen = {}, {}, [], set()
    for i in srcs:
        dist[i] = 0.0
        heapq.heappush(pq, (mdist(V[i], goal), 0.0, i))
    while pq:
        _, d, u = heapq.heappop(pq)
        if u in seen:
            continue
        seen.add(u)
        if u in dset:
            out, cur = [], u
            while cur in prev:
                out.append(cur)
                cur = prev[cur]
            out.append(cur)
            out.reverse()
            return out
        for v, w in adj[u]:
            nd = d + w
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd + mdist(V[v], goal), nd, v))
    return None


def read_trpaths(path):
    """.bin から電車の経路（停まる駅の並び）だけ取り出す。"""
    b = path.read_bytes()
    assert b[:4] == b"SHDN"
    p = 6
    p += 2                                     # 自宅
    ns = b[p]; p += 1
    nt = struct.unpack_from("<H", b, p)[0]; p += 2
    p += 2 + 1                                 # 最初の時刻・刻み
    p += ns * 2                                # 終電リミット
    p += ns * 2 * 4                            # 全部タクシー（距離・夜・昼）＋始発
    p += ns * 2                                # 始発の発車
    p += ns * nt * 2 * 6                       # off/at/km/fare/tr/tx
    ntr = struct.unpack_from("<H", b, p)[0]; p += 2
    out = []
    for _ in range(ntr):
        c = b[p]; p += 1
        out.append(list(struct.unpack_from("<%dH" % c, b, p)))
        p += c * 2
    return out


def main():
    nodes = json.loads((SITE / "base.json").read_text())["nodes"]

    pairs = set()
    files = sorted((SITE / "home").glob("*.bin"))
    for f in files:
        for seq in read_trpaths(f):
            for a, c in zip(seq, seq[1:]):
                if a != c:
                    pairs.add((min(a, c), max(a, c)))
    print("経路に出てくる隣り合う 2 駅: %d 組（%d ファイル）" % (len(pairs), len(files)))

    print("N02 の線を読みます…", flush=True)
    polys = rail_polylines(BR.fetch())
    V, adj = build_graph(polys)
    print("鉄道のグラフ: 点 %d / 辺 %d" % (len(V), sum(len(a) for a in adj) // 2))

    g, cell = grid_index(V)
    snap, far = {}, []
    for a, c in pairs:
        for n in (a, c):
            if n in snap:
                continue
            nd = nodes[n]
            snap[n] = near_all(g, cell, V, nd["y"], nd["x"])
            if not snap[n]:
                far.append(nd["n"])
    if far:
        print("  線が近くに無い駅 %d: %s" % (len(far), "・".join(far[:20])))

    out, miss, short, detour = {}, 0, 0, []
    for k, (a, c) in enumerate(sorted(pairs)):
        sa, sc = snap[a], snap[c]
        if not sa or not sc:
            miss += 1
            continue
        p = astar(adj, V, sa, sc, (nodes[c]["y"], nodes[c]["x"]))
        if not p:
            miss += 1
            continue
        if len(p) < 3:          # 隣どうしで線に曲がりが無い。直線のままでよい
            short += 1
            continue
        straight = mdist((nodes[a]["y"], nodes[a]["x"]), (nodes[c]["y"], nodes[c]["x"]))
        along = sum(mdist(V[p[i]], V[p[i + 1]]) for i in range(len(p) - 1))
        if along > straight * DETOUR + 400:
            detour.append((nodes[a]["n"], nodes[c]["n"], round(straight), round(along)))
            continue
        out["%d,%d" % (a, c)] = [[V[i][0], V[i][1]] for i in p[1:-1]]
        if (k + 1) % 400 == 0:
            print("  %d/%d" % (k + 1, len(pairs)), flush=True)

    (SITE / "seg.json").write_text(
        json.dumps(out, separators=(",", ":")), encoding="utf-8")
    kb = (SITE / "seg.json").stat().st_size / 1024
    pts = sum(len(v) for v in out.values())
    print("書きました: %s  %d 組・点 %d  %.1f KB"
          % (SITE / "seg.json", len(out), pts, kb))
    print("  たどれず %d／曲がりなし %d／遠回りで捨てた %d" % (miss, short, len(detour)))
    for x in detour[:15]:
        print("    %s〜%s  直線 %dm → たどると %dm" % x)


if __name__ == "__main__":
    sys.setrecursionlimit(10000)
    main()
