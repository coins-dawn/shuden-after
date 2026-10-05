#!/usr/bin/env python3
"""サーバ無しで動くように、答えを全部先に計算して site/data/ に書く。

GitHub Pages は静的ファイルしか置けないので、画面が要るものを自宅ごとのファイルにしておく。
**時刻表そのものは配らない**（基本ライセンス第8条4項(1)。元データの大部分を復元できる
派生データの再配布は禁止）。配るのは「自宅ごとの答え」＝探索結果だけ。

    python3 scripts/build_static.py        # 15 分ほど

できるもの:

    site/data/index.json      駅の一覧（名前・座標）、候補駅、時刻の刻み
    site/data/home/<id>.bin   自宅ごとの答え（1 ファイル 15KB ほど）
    site/data/base.json       地図（駅と路線の線）
    site/data/land.json       海岸線・県境
    site/data/spots.json      候補の繁華街

自宅ごとのファイルの中身（リトルエンディアン）:

    "SHDN" uint16 版
    uint16 自宅のノード id
    uint8  候補の数 / uint16 時刻の数 / uint16 最初の時刻 / uint8 刻み
    int16  候補ごとの終電リミット（分。-1 = 帰れない）
    uint16 候補ごとの「全部タクシー」の道路距離（10m 単位。0xFFFF = 不明）
    uint16 候補ごとの「全部タクシー」の運賃（深夜）／uint16 同（昼）。0xFFFF = 不明
    uint16 候補ごとの「始発を待ったときの自宅への到着時刻」（翌朝の分。0xFFFF = 帰れない）
    以下 候補 × 時刻 ぶんの配列が 6 本:
      uint16 降りる駅のノード id（0xFFFF = 該当なし）
      uint16 降りる駅に着く時刻（分）
      uint16 降りる駅 → 自宅 の道路距離（10m 単位。**表示用**）
      uint16 タクシー運賃（円）。**丸めの食い違いを避けるため、ここで計算して持つ**
      uint16 電車の経路の番号（0xFFFF = 無し）
      uint16 タクシーの経路の番号（0xFFFF = 無し）
    uint16 電車の経路の数、続けて uint8 駅数 + uint16 駅 × 駅数
    uint16 タクシーの経路の数、続けて uint16 点数 + int16 緯度差,経度差 × 点数
           （最初の点は「降りる駅の座標」との差。1e-5 度を 1 とする）
"""
import json
import shutil
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

print("サーバと同じ組み立てをします…", flush=True)
import server  # noqa: E402

NET = server.NET
IDX = server.ROAD_IDX              # 道路距離を測ってある 829 駅＝自宅に選べる駅
if IDX is None:
    sys.exit("data/road_m.bin が要ります")
SPOTS = server.SPOTS
INF = server.INF
T_FROM, T_TO, T_STEP = 1200, 1620, 5
TIMES = list(range(T_FROM, T_TO + 1, T_STEP))
NONE16 = 0xFFFF
OUT = ROOT / "site" / "data"
SCALE = 100000


def km10(m):
    """メートル → 10m 単位。入らなければ 0xFFFF。"""
    if m is None:
        return NONE16
    v = int(round(m / 10.0))
    return v if 0 <= v < NONE16 else NONE16


def main():
    (OUT / "home").mkdir(parents=True, exist_ok=True)

    # 1) 終電リミット（時刻に依らない）
    print("終電リミットを全自宅ぶん計算します…", flush=True)
    t0 = time.time()
    LIM = {}
    for k, h in enumerate(IDX):
        LIM[h] = server.limits(h)
        if (k + 1) % 200 == 0:
            print("  %d/%d  %.0f 秒" % (k + 1, len(IDX), time.time() - t0), flush=True)
    print("  終電リミット %.0f 秒" % (time.time() - t0), flush=True)

    # 2) 始発を待ったときの到着時刻（時刻には依らないので、候補ごとに 1 回でよい）
    #    手元のダイヤは 04:18 が最初の発車。終電後に駅で粘るなら、ここから帰ることになる。
    print("始発を待ったときの到着時刻を計算します…", flush=True)
    FIRST = {}
    for s in SPOTS:
        a = NET.earliest_arrival(s["node"], 4 * 60, horizon=300)
        FIRST[s["node"]] = {h: (None if a[h] >= INF else a[h]) for h in IDX}
        ok = sum(1 for v in FIRST[s["node"]].values() if v is not None)
        print("  %s → 帰れる自宅 %d/%d" % (s["name"], ok, len(IDX)), flush=True)

    # 3) 自宅ごとに貯める箱
    ns, nt = len(SPOTS), len(TIMES)
    data = {h: {"off": [NONE16] * (ns * nt), "at": [0] * (ns * nt),
                "km": [NONE16] * (ns * nt), "fare": [NONE16] * (ns * nt),
                "tr": [NONE16] * (ns * nt), "tx": [NONE16] * (ns * nt),
                "trpaths": [], "trmap": {}, "txpaths": [], "txmap": {}}
            for h in IDX}

    print("候補 × 時刻 × 自宅 を回します…", flush=True)
    t0 = time.time()
    for si, s in enumerate(SPOTS):
        c = s["node"]
        for ti, t in enumerate(TIMES):
            arr, par = server.reach(c, t)
            path_cache = {}
            for h in IDX:
                if h == c:
                    continue
                lv = LIM[h][c]
                if -INF < lv < INF and t <= lv:
                    continue                      # まだ終電に間に合う
                b = server.choose_off(arr, h)
                if b is None:
                    continue
                fare, off, dist, at = b
                d = data[h]
                i = si * nt + ti
                d["off"][i] = off
                d["at"][i] = at
                d["km"][i] = km10(dist)
                d["fare"][i] = fare if fare is not None and fare < NONE16 else NONE16
                # 電車の経路（降りる駅ごとに 1 回だけ復元して使い回す）
                if off != c:
                    if off not in path_cache:
                        ids = server.NET.forward_path(par, c, off)
                        path_cache[off] = tuple(ids) if ids else None
                    seq = path_cache[off]
                    if seq:
                        key = seq
                        pid = d["trmap"].get(key)
                        if pid is None:
                            pid = len(d["trpaths"])
                            d["trmap"][key] = pid
                            d["trpaths"].append(seq)
                        d["tr"][i] = pid
                # タクシーの経路
                key = (off, h)
                pid = d["txmap"].get(key)
                if pid is None:
                    pts = server.stored_path(off, h)
                    if pts is None:
                        pid = NONE16
                    else:
                        pid = len(d["txpaths"])
                        d["txpaths"].append((off, pts))
                    d["txmap"][key] = pid
                d["tx"][i] = pid
        print("  %s 済み  %.0f 秒" % (s["name"], time.time() - t0), flush=True)

    # 4) 書き出し
    print("自宅ごとのファイルを書きます…", flush=True)
    total = 0
    for h in IDX:
        d = data[h]
        buf = bytearray()
        buf += b"SHDN" + struct.pack("<H", 3)
        buf += struct.pack("<HBHHB", h, ns, nt, T_FROM, T_STEP)
        for s in SPOTS:
            lv = LIM[h][s["node"]]
            buf += struct.pack("<h", -1 if (lv <= -INF or lv >= INF) else max(-1, min(32767, lv)))
        allm = [server.road_m(s["node"], h) for s in SPOTS]
        for m in allm:
            buf += struct.pack("<H", km10(m))
        for night in (True, False):
            for m in allm:
                f = server.taxi_fare(m, night) if m is not None else None
                buf += struct.pack("<H", NONE16 if f is None or f >= NONE16 else f)
        for s in SPOTS:
            v = FIRST[s["node"]][h]
            buf += struct.pack("<H", NONE16 if v is None else v)
        for key in ("off", "at", "km", "fare", "tr", "tx"):
            buf += struct.pack("<%dH" % (ns * nt), *d[key])
        buf += struct.pack("<H", len(d["trpaths"]))
        for seq in d["trpaths"]:
            buf += struct.pack("<B", min(255, len(seq)))
            buf += struct.pack("<%dH" % len(seq[:255]), *seq[:255])
        buf += struct.pack("<H", len(d["txpaths"]))
        for off, pts in d["txpaths"]:
            cy = int(round(NET.node_pos[off][0] * SCALE))
            cx = int(round(NET.node_pos[off][1] * SCALE))
            out = []
            for la, lo in pts:
                ty, tx = int(round(la * SCALE)), int(round(lo * SCALE))
                out.append((ty - cy, tx - cx))
                cy, cx = ty, tx
            buf += struct.pack("<H", len(out))
            for dy, dx in out:
                buf += struct.pack("<hh", dy, dx)
        (OUT / "home" / ("%d.bin" % h)).write_bytes(bytes(buf))
        total += len(buf)
    print("  %d ファイル  合計 %.1f MB  1 自宅あたり %.1f KB"
          % (len(IDX), total / 1e6, total / len(IDX) / 1024))

    # 5) 索引と、地図まわりのファイル
    idx = {
        "homes": IDX,
        "spots": [s["node"] for s in SPOTS],
        "time": {"from": T_FROM, "to": T_TO, "step": T_STEP},
        "fare": {"base": server.FARE_BASE, "baseM": server.FARE_BASE_M,
                 "stepM": server.FARE_STEP_M, "step": server.FARE_STEP,
                 "night": server.NIGHT_RATE},
    }
    (OUT / "index.json").write_text(json.dumps(idx, separators=(",", ":")), encoding="utf-8")
    for f in ("base.json", "land.json", "spots.json"):
        src = ROOT / "web" / "data" / f
        if src.exists():
            shutil.copy(src, OUT / f)
    (OUT.parent / ".nojekyll").write_text("")
    print("書きました: %s" % OUT)


if __name__ == "__main__":
    main()
