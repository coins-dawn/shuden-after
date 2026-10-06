#!/usr/bin/env python3
"""地図の下地（駅の位置・名前と路線の線）を**国土数値情報 N02（鉄道）**から作る。

    python3 scripts/build_rail.py

**なぜ ODPT のデータを使わないのか**:
`odpt:Station`（駅名・座標）と `odpt:Railway`（路線名・色・駅順）をそのまま配ると、
公共交通オープンデータ基本ライセンス第8条4項(1)（元データの大部分を復元できる
派生データの再配布）に触れるおそれがある。**地図の下地は N02 に置き換える。**
N02 は国土数値情報利用約款で再配布できる（海岸線の N03 と同じ約款）。

探索（終電・運賃・経路）は今までどおり ODPT の列車時刻表で行う。
**置き換えるのは「配る地図」だけ。**

出力: web/data/rail.json
    {"st": {"<ノード id>": [緯度, 経度, "駅名", 路線数]},     N02 と突き合わせた駅
     "li": [[色番号, 緯度, 経度, 緯度, 経度, ...], ...],      路線の線（折れ線）
     "co": ["#rrggbb", ...],                                  色（**自前の配色**）
     "op": {"JR-East": 0, ...}}                               事業者 → 色番号

N02 の路線色は提供されていないので、**運営会社ごとに自分で色を決める**。
"""
import json
import math
import sys
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = Path("/var/tmp/shuden-n02")
OUT = ROOT / "web" / "data"
YEAR = "25"
URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-%s/N02-%s_GML.zip" % (YEAR, YEAR)
BBOX = (139.10, 35.15, 140.40, 36.25)   # 表示範囲よりすこし広め（はみ出しは切る）
TOL_M = 35                              # 線の間引き
MAX_MATCH_KM = 2.5                      # 同じ名前でもこれより遠ければ別の駅

# 運営会社ごとの色。**自前の配色**（N02 に色は入っていない）
COLORS = [
    ("東日本旅客鉄道", "JR-East", "#35c17a"),
    ("東京地下鉄", "TokyoMetro", "#ff9838"),
    ("東京都", "Toei", "#3f9bf0"),
    ("東武鉄道", "Tobu", "#9b7bff"),
    ("京王電鉄", "Keio", "#ff6fa8"),
    ("相模鉄道", "Sotetsu", "#18c2d8"),
    ("首都圏新都市鉄道", "MIR", "#e0b63a"),
    ("東京臨海高速鉄道", "TWR", "#2fd3a8"),
    ("多摩都市モノレール", "TamaMonorail", "#c3d13a"),
    ("横浜市", "YokohamaMunicipal", "#6f86ff"),
]


def fetch():
    CACHE.mkdir(exist_ok=True)
    f = CACHE / ("N02-%s_GML.zip" % YEAR)
    if not f.exists():
        print("取得: %s（15MB ほど）" % URL, flush=True)
        with urllib.request.urlopen(URL, timeout=600) as r:
            f.write_bytes(r.read())
    return zipfile.ZipFile(f)


def load(z, kind):
    p = "N02-%s_GML/UTF-8/N02-%s_%s.geojson" % (YEAR, YEAR, kind)
    with z.open(p) as f:
        return json.load(f)["features"]


def norm(s):
    """駅名を突き合わせる形にそろえる。〈 〉（ ）の中と空白を落とす。"""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("麴", "麹").replace("舘", "館").replace("淸", "清")
    for a, b in (("〈", "("), ("〉", ")"), ("＜", "("), ("＞", ")"), ("<", "("), (">", ")")):
        s = s.replace(a, b)
    while "(" in s and ")" in s:
        i, j = s.index("("), s.index(")")
        if i > j:
            break
        s = s[:i] + s[j + 1:]
    return s.replace(" ", "").replace("　", "").replace("ヶ", "ケ").replace("ヵ", "カ")


def km(a, b):
    return math.hypot((a[0] - b[0]) * 111.0, (a[1] - b[1]) * 91.0)


def dp(pts, tol_deg):
    """Douglas-Peucker。"""
    if len(pts) < 3:
        return pts
    ax, ay = pts[0]
    bx, by = pts[-1]
    dx, dy = bx - ax, by - ay
    n = dx * dx + dy * dy
    worst, wi = -1.0, 0
    for i in range(1, len(pts) - 1):
        px, py = pts[i]
        t = 0.0 if n == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / n))
        d = math.hypot(px - ax - dx * t, py - ay - dy * t)
        if d > worst:
            worst, wi = d, i
    if worst <= tol_deg:
        return [pts[0], pts[-1]]
    return dp(pts[:wi + 1], tol_deg)[:-1] + dp(pts[wi:], tol_deg)


def main():
    z = fetch()
    base = json.loads((OUT / "base.json").read_text())
    nodes = base["nodes"]

    # ---- N02 の駅 ----
    st = {}
    for ft in load(z, "Station"):
        p = ft["properties"]
        cs = ft["geometry"]["coordinates"]
        lon = sum(c[0] for c in cs) / len(cs)
        lat = sum(c[1] for c in cs) / len(cs)
        if not (BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]):
            continue
        key = (norm(p["N02_005"]), p["N02_004"], p["N02_003"])
        st.setdefault(key, []).append((lat, lon))
    by_name = {}
    for (nm, op, li), pts in st.items():
        la = sum(q[0] for q in pts) / len(pts)
        lo = sum(q[1] for q in pts) / len(pts)
        by_name.setdefault(nm, []).append((la, lo, op, li))
    print("N02 の駅（範囲内）: 名前 %d 種 / のべ %d 件"
          % (len(by_name), sum(len(v) for v in by_name.values())))

    # ---- 手元のノードと突き合わせる ----
    out_st, miss = {}, []
    for i, nd in enumerate(nodes):
        if not nd.get("v"):
            continue
        cands = by_name.get(norm(nd["n"]), [])
        best, bd = None, 1e9
        for la, lo, op, li in cands:
            d = km((la, lo), (nd["y"], nd["x"]))
            if d < bd:
                bd, best = d, (la, lo)
        if best is None or bd > MAX_MATCH_KM:
            miss.append((nd["n"], round(bd, 1) if best else None))
            continue
        lines = len({(op, li) for la, lo, op, li in cands if km((la, lo), best) < 1.2})
        out_st[i] = [round(best[0], 5), round(best[1], 5), nd["n"], lines]
    vis = sum(1 for nd in nodes if nd.get("v"))
    print("突き合わせ: %d / %d 駅（%.1f%%）" % (len(out_st), vis, 100.0 * len(out_st) / vis))
    if miss:
        print("  合わなかった %d 駅: %s" % (len(miss), "・".join(m[0] for m in miss[:40])))

    # ---- N02 の線 ----
    # 線は**この 10 社ぶんだけ**（探索に入っている事業者）。新幹線（種別 1）は出さない。
    # 表示しない会社の線まで描くと「ここも終電が出るのか」と読めてしまう。
    cidx = {op: i for i, (op, _, _) in enumerate(COLORS)}
    cols = [c for _, _, c in COLORS]
    tol = TOL_M / 111000.0
    li_out, raw, kept = [], 0, 0
    inside = lambda c: BBOX[0] <= c[0] <= BBOX[2] and BBOX[1] <= c[1] <= BBOX[3]
    for ft in load(z, "RailroadSection"):
        pr = ft["properties"]
        ci = cidx.get(pr["N02_004"])
        if ci is None or pr["N02_002"] == "1":
            continue
        cs = ft["geometry"]["coordinates"]
        raw += len(cs)
        # 枠からはみ出すところで切る（枠外だけの切れ端は捨てる）
        run = []
        for c in cs:
            if inside(c):
                run.append((c[0], c[1]))
            else:
                if len(run) > 1:
                    li_out.append((ci, dp(run, tol)))
                run = []
        if len(run) > 1:
            li_out.append((ci, dp(run, tol)))
    flat_out = []
    for ci, pts in li_out:
        kept += len(pts)
        f = [ci]
        for lo, la in pts:
            f += [round(la, 5), round(lo, 5)]
        flat_out.append(f)
    li_out = flat_out
    print("路線の線: %d 本  点 %d → %d（10 社・新幹線を除く）" % (len(li_out), raw, kept))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rail.json").write_text(
        json.dumps({"st": out_st, "li": li_out, "co": cols,
                    "op": {o: i for i, (_, o, _) in enumerate(COLORS)}},
                   separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print("書きました: %s  %.1f KB"
          % (OUT / "rail.json", (OUT / "rail.json").stat().st_size / 1024))


if __name__ == "__main__":
    sys.setrecursionlimit(10000)
    main()
