#!/usr/bin/env python3
"""海岸線と県境を、画面に薄く敷くための折れ線にする。

国土数値情報「行政区域」N03 を都県ぶん取ってきて、

1. すべての市区町村ポリゴンを辺に分解する
2. **同じ都県の中で 2 回出てくる辺は市区町村の境**なので捨てる
3. 残るのは「1 回しか出てこない辺（＝海岸線・県の外周）」と
   「2 回出てくるが都県名が違う辺（＝県境）」
4. つなぎ直して折れ線にし、間引いて、表示範囲で切る

shapely のような道具を使わずに済むのは、N03 の隣り合うポリゴンが
**同じ座標を共有している**（トポロジが揃っている）ため。

出力: web/data/land.json  {"lines": [[[lon,lat], ...], ...]}
"""
import json
import math
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RAW = ROOT / "data" / "n03"
OUT = ROOT / "web" / "data"
UA = "gomu-tokyo/0.1 (map background; data collection)"

# 表示範囲（base.json の可視ノードの範囲）＋ はみ出しぶん
BBOX = (139.228 - 0.35, 35.257 - 0.30, 140.288 + 0.35, 36.113 + 0.30)
PREFS = [8, 11, 12, 13, 14]        # 茨城・埼玉・千葉・東京・神奈川
BASE = "https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2025/N03-20250101_%02d_GML.zip"
TOL = 0.0012                       # 間引きの許容（度）。約 130m
Q = 5                              # 書き出す小数桁


def fetch(pref):
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / ("N03-%02d.zip" % pref)
    if path.exists() and path.stat().st_size > 0:
        return path
    url = BASE % pref
    print("  取得 %s" % url, flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=600) as r, open(path, "wb") as f:
        f.write(r.read())
    return path


def rings_of(geom):
    t = geom.get("type")
    if t == "Polygon":
        return geom["coordinates"]
    if t == "MultiPolygon":
        return [ring for poly in geom["coordinates"] for ring in poly]
    return []


def inside(x, y):
    return BBOX[0] <= x <= BBOX[2] and BBOX[1] <= y <= BBOX[3]


def collect_edges():
    """辺 -> （出てきた回数, 都県名の集合）"""
    count = {}
    prefs = {}
    for pref in PREFS:
        path = fetch(pref)
        z = zipfile.ZipFile(path)
        name = next(n for n in z.namelist() if n.endswith(".geojson"))
        print("  読み込み %s" % name, flush=True)
        data = json.loads(z.read(name).decode("utf-8"))
        n_edge = 0
        for feat in data["features"]:
            pn = (feat.get("properties") or {}).get("N03_001") or str(pref)
            for ring in rings_of(feat.get("geometry") or {}):
                prev = None
                for pt in ring:
                    cur = (round(pt[0], 6), round(pt[1], 6))
                    if prev is not None and prev != cur:
                        # 表示範囲の外の辺は最初から持たない（量が 1 桁減る）
                        if inside(*prev) or inside(*cur):
                            k = (prev, cur) if prev < cur else (cur, prev)
                            count[k] = count.get(k, 0) + 1
                            s = prefs.get(k)
                            if s is None:
                                prefs[k] = pn
                            elif s != pn:
                                prefs[k] = "*"      # 都県をまたぐ辺
                            n_edge += 1
                    prev = cur
        del data
        print("    範囲内の辺 %d（のべ）" % n_edge, flush=True)
    return count, prefs


def chain(edges):
    """辺の集合を折れ線につなぎ直す。"""
    adj = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    used = set()
    lines = []

    def walk(start, first):
        line = [start]
        cur, nxt = start, first
        while True:
            key = (cur, nxt) if cur < nxt else (nxt, cur)
            if key in used:
                break
            used.add(key)
            line.append(nxt)
            cands = [p for p in adj.get(nxt, ()) if p != cur]
            cur = nxt
            nxt = None
            for p in cands:
                k2 = (cur, p) if cur < p else (p, cur)
                if k2 not in used:
                    nxt = p
                    break
            if nxt is None:
                break
        return line

    # まず端点（つながりが 1 本または 3 本以上）から始める
    for node, nbrs in adj.items():
        if len(nbrs) == 2:
            continue
        for p in nbrs:
            k = (node, p) if node < p else (p, node)
            if k not in used:
                lines.append(walk(node, p))
    # 残り（閉じた輪）
    for a, b in edges:
        k = (a, b) if a < b else (b, a)
        if k not in used:
            lines.append(walk(a, b))
    return [ln for ln in lines if len(ln) >= 2]


def simplify(line, tol):
    """ダグラス・ポーカー法。再帰は深くなるので自前のスタックで回す。"""
    n = len(line)
    if n <= 2:
        return line
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = line[i]
        bx, by = line[j]
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        best, bi = -1.0, -1
        for k in range(i + 1, j):
            px, py = line[k]
            if den == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / den
                t = 0.0 if t < 0 else (1.0 if t > 1 else t)
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > best:
                best, bi = d, k
        if best > tol:
            keep[bi] = True
            stack.append((i, bi))
            stack.append((bi, j))
    return [line[k] for k in range(n) if keep[k]]


def clip(line):
    """表示範囲の外を落とす。途切れたら別の折れ線に分ける。"""
    out, cur = [], []
    for pt in line:
        if inside(*pt):
            cur.append(pt)
        else:
            if cur:
                cur.append(pt)       # 1 点だけはみ出させて端を自然にする
                out.append(cur)
                cur = []
    if cur:
        out.append(cur)
    return [c for c in out if len(c) >= 2]


def main():
    print("国土数値情報 N03 から海岸線と県境をつくります")
    count, prefs = collect_edges()
    keep = [k for k, c in count.items() if c == 1 or prefs.get(k) == "*"]
    print("辺 %d 本のうち、海岸線・県境は %d 本" % (len(count), len(keep)))
    del count, prefs

    lines = chain(keep)
    print("つなぎ直して折れ線 %d 本（頂点 %d）"
          % (len(lines), sum(len(l) for l in lines)))

    out = []
    for ln in lines:
        for seg in clip(simplify(ln, TOL)):
            if len(seg) >= 2:
                out.append([[round(x, Q), round(y, Q)] for x, y in seg])
    # 短すぎる切れ端と、港の埋立地のような小さな囲いは捨てる（点が散るだけで読めない）
    def span_km(l):
        xs = [p[0] for p in l]
        ys = [p[1] for p in l]
        return math.hypot((max(xs) - min(xs)) * 91.0, (max(ys) - min(ys)) * 111.0)

    out = [l for l in out if len(l) >= 3 and span_km(l) >= 2.0]
    print("間引いて %d 本（頂点 %d）" % (len(out), sum(len(l) for l in out)))

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "land.json"
    path.write_text(json.dumps({"lines": out}, separators=(",", ":")))
    print("出力 %s (%.2fMB)" % (path, path.stat().st_size / 1e6))


if __name__ == "__main__":
    main()
