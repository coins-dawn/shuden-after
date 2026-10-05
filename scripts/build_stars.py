#!/usr/bin/env python3
"""夜空（実際の星と星座線）を site/data/stars.json に書く。

    python3 scripts/build_stars.py

出どころ:

  星  … Yale Bright Star Catalogue, 5th Revised Ed. (Hoffleit+, 1991) VizieR V/50
        http://cdsarc.u-strasbg.fr/ftp/V/50/catalog.gz
        赤経・赤緯（J2000）と V 等級だけを使う。**V ≤ MAGLIM の星に絞る。**

  星座線 … d3-celestial (Olaf Frohn) の data/constellations.lines.json
        https://github.com/ofrohn/d3-celestial  BSD 3-Clause
        Copyright (c) 2015, Olaf Frohn. All rights reserved.
        （この表示を残すことが再配布の条件。README と画面の
          「このサービスについて」にも出している）

書き出す形（小さくするため、度 ×10 の整数で持つ）:

    {"mag": 46,                       # 等級の上限 ×10
     "s": [[赤経×10, 赤緯×10, V等級×10], ...],
     "l": [[赤経×10, 赤緯×10, ...], ...]}   # 星座線。1 本ぶんを平らに並べる
"""
import gzip
import json
import math
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "data" / "stars.json"
BSC = "http://cdsarc.u-strasbg.fr/ftp/V/50/catalog.gz"
LINES = "https://raw.githubusercontent.com/ofrohn/d3-celestial/master/data/constellations.lines.json"
MAGLIM = 4.6          # これより暗い星は出さない（地図の上なので、出しすぎると邪魔）
CACHE = Path("/var/tmp/odc2026-stars")


def fetch(url, name):
    CACHE.mkdir(exist_ok=True)
    f = CACHE / name
    if not f.exists():
        print("取得: %s" % url, flush=True)
        with urllib.request.urlopen(url, timeout=120) as r:
            f.write_bytes(r.read())
    return f.read_bytes()


def main():
    raw = gzip.decompress(fetch(BSC, "bsc5.gz")).decode("latin-1")
    stars = []
    for line in raw.splitlines():
        if len(line) < 107:
            continue
        try:
            rah, ram, ras = int(line[75:77]), int(line[77:79]), float(line[79:83])
            sign = -1.0 if line[83] == "-" else 1.0
            ded, dem, des = int(line[84:86]), int(line[86:88]), int(line[88:90])
            mag = float(line[102:107])
        except ValueError:
            continue                      # 座標も等級も無い行（重星の片割れなど）
        if mag > MAGLIM:
            continue
        ra = (rah + ram / 60.0 + ras / 3600.0) * 15.0
        de = sign * (ded + dem / 60.0 + des / 3600.0)
        stars.append([round(ra * 10), round(de * 10), round(mag * 10)])
    print("星 %d 個（V ≤ %.1f）" % (len(stars), MAGLIM))

    # 答え合わせ（よく知られた星が、知られた場所に出ているか）
    def near(ra_h, de_d, name):
        ra = ra_h * 15.0
        best = min(stars, key=lambda s: abs(s[0] / 10.0 - ra) + abs(s[1] / 10.0 - de_d))
        d = math.hypot((best[0] / 10.0 - ra) * math.cos(math.radians(de_d)),
                       best[1] / 10.0 - de_d)
        print("  %-10s ずれ %.2f°  V=%.2f" % (name, d, best[2] / 10.0))
        assert d < 0.1, name
    near(6 + 45 / 60.0 + 8.9 / 3600, -16.716, "シリウス")
    near(18 + 36 / 60.0 + 56.3 / 3600, 38.784, "ベガ")
    near(5 + 55 / 60.0 + 10.3 / 3600, 7.407, "ベテルギウス")

    feats = json.loads(fetch(LINES, "conlines.json").decode("utf-8"))["features"]
    out_lines = []
    for ft in feats:
        for seg in ft["geometry"]["coordinates"]:
            flat = []
            for ra, de in seg:
                flat += [round((ra % 360) * 10), round(de * 10)]
            out_lines.append(flat)
    print("星座線 %d 本（%d 星座）" % (len(out_lines), len(feats)))

    OUT.write_text(json.dumps({"mag": round(MAGLIM * 10), "s": stars, "l": out_lines},
                              separators=(",", ":")), encoding="utf-8")
    print("書きました: %s  %.1f KB" % (OUT, OUT.stat().st_size / 1024))


if __name__ == "__main__":
    main()
