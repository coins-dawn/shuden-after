#!/usr/bin/env python3
"""タクシー区間（降りる駅 → 自宅）の道の形を全部先に計算して data/taxi_paths.bin に書く。

これがあれば、アプリを動かすときに osrm-routed は要らない。

    docker run -d --rm -p 5050:5000 -v /var/tmp/odc2026-osrm:/data \
      osrm/osrm-backend osrm-routed --algorithm mld /data/tokyo.osrm
    python3 scripts/build_taxipaths.py          # 20 分ほど

必要な組は **(降りる駅, 自宅) だけで決まり、時刻には依らない**（時刻が決めるのは
「どの駅で降りるか」で、降りてしまえば道は同じ）。ただし**ひとつの時刻ぶんでは足りない**。
スライダーの範囲（20:00〜翌03:00）で実際に出てくる組を数えると 23,314 通りあり、
いちばん広くカバーできる 23:45 の表でも全体の 52.9%、22:00 の表なら 49.5% しか埋まらない。
そこで**全時刻ぶんを列挙して全部計算する**。

形式（リトルエンディアン）:

    "TXP1"            4 バイト
    uint32 count      組の数
    count × 12 バイト  uint16 降りる駅 / uint16 自宅 / uint32 データの位置 / uint16 点の数 / uint16 予備
    そのあと          点の数 × 2 × int16。1e-5 度を 1 とした「ひとつ前との差」。
                      最初の点は「降りる駅の座標」との差

点は Douglas-Peucker で 40m まで間引く（生 584 点 → 33 点）。
"""
import http.client
import json
import math
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

OSRM_HOST, OSRM_PORT = "127.0.0.1", 5050
TOL_M = 40                      # 間引きの許容誤差
SCALE = 100000                  # 1e-5 度を 1 とする
TIMES = range(1200, 1621, 5)    # 画面のスライダーと同じ範囲・刻み

print("サーバと同じ組み立てをします（1 分ほど）…", flush=True)
import server  # noqa: E402  ネットワーク・道路行列・運賃の定義をそのまま使う

NET, IDX = server.NET, server.ROAD_IDX
if IDX is None:
    sys.exit("data/road_m.bin が要ります。先に build_roadmatrix.py を動かしてください")
N = len(IDX)
POS = server.ROAD_POS
ROAD = server.ROAD
INF = server.INF


def pairs_needed():
    """全時刻・全自宅について「どの駅で降りるか」を出し、(降りる駅, 自宅) を集める。

    server.choose_off() と同じ選び方を、自宅ごとの走査が速くなるよう書き直したもの。
    結果が一致することを下で抜き取り検査する。
    """
    out = set()
    t0 = time.time()
    done = 0
    total = len(server.SPOTS) * len(TIMES)
    for s in server.SPOTS:
        for t in TIMES:
            arr = NET.earliest_arrival(s["node"], t)
            cand = []
            for v in IDX:
                a = arr[v]
                if a < INF:
                    cand.append((POS[v] * N, server.is_night(a), a, v))
            for hp in range(N):
                home = IDX[hp]
                if home == s["node"]:
                    continue
                best_f = None
                rows = []
                for base, night, a, v in cand:
                    d = ROAD[base + hp]
                    if d >= 3_000_000:
                        continue
                    f = server.taxi_fare(d, night)
                    rows.append((f, a, v))
                    if best_f is None or f < best_f:
                        best_f = f
                if best_f is None:
                    continue
                lim = best_f + server.TIE_YEN
                pick = min(((a, f, v) for f, a, v in rows if f <= lim))
                out.add((pick[2], home))
            done += 1
            if done % 85 == 0:
                print("  組の洗い出し %d/%d  %.0f 秒  いままで %d 通り"
                      % (done, total, time.time() - t0, len(out)), flush=True)
    return out


def check(pairs, n=400):
    """抜き取りで server.choose_off() と突き合わせる。"""
    import random
    random.seed(7)
    ng = 0
    for _ in range(n):
        s = random.choice(server.SPOTS)
        t = random.choice(list(TIMES))
        home = random.choice(IDX)
        if home == s["node"]:
            continue
        arr = NET.earliest_arrival(s["node"], t)
        got = server.choose_off(arr, home)
        if got is None:
            continue
        if (got[1], home) not in pairs:
            ng += 1
            print("  ×", s["name"], t, NET.node_name[home], "→", NET.node_name[got[1]])
    print("抜き取り %d 件の照合: 食い違い %d 件" % (n, ng))
    return ng == 0


def simplify(pts, tol):
    """Douglas-Peucker。"""
    if len(pts) < 3:
        return pts
    def dist(p, a, b):
        ax, ay = (a[1] - p[1]) * 91000, (a[0] - p[0]) * 111000
        bx, by = (b[1] - p[1]) * 91000, (b[0] - p[0]) * 111000
        vx, vy = bx - ax, by - ay
        l = vx * vx + vy * vy
        t = 0.0 if not l else max(0.0, min(1.0, -(ax * vx + ay * vy) / l))
        return math.hypot(ax + vx * t, ay + vy * t)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        k, dmax = i, -1.0
        for x in range(i + 1, j):
            d = dist(pts[x], pts[i], pts[j])
            if d > dmax:
                k, dmax = x, d
        if dmax > tol:
            keep[k] = True
            stack.append((i, k))
            stack.append((k, j))
    return [p for p, f in zip(pts, keep) if f]


def encode(pts, off):
    """点の列を int16 の差分にする。int16 に入らない差は途中に点を足して割る。"""
    lat0 = int(round(NET.node_pos[off][0] * SCALE))
    lon0 = int(round(NET.node_pos[off][1] * SCALE))
    out = []
    cy, cx = lat0, lon0
    for la, lo in pts:
        ty, tx = int(round(la * SCALE)), int(round(lo * SCALE))
        while True:
            dy, dx = ty - cy, tx - cx
            if -32768 <= dy <= 32767 and -32768 <= dx <= 32767:
                out.append((dy, dx))
                cy, cx = ty, tx
                break
            # 遠すぎるので半分の点を挟む
            my, mx = cy + max(-32000, min(32000, dy)), cx + max(-32000, min(32000, dx))
            out.append((my - cy, mx - cx))
            cy, cx = my, mx
    return out


def main():
    print("必要な (降りる駅, 自宅) の組を洗い出します…", flush=True)
    pairs = sorted(pairs_needed())
    print("組の数: %d" % len(pairs), flush=True)
    if not check(pairs):
        sys.exit("server.choose_off() と食い違いました。選び方を合わせてください")

    print("osrm-routed から道の形を取ります…", flush=True)
    conn = http.client.HTTPConnection(OSRM_HOST, OSRM_PORT, timeout=15)
    blobs, index, pos, raw_pts, kept_pts, miss = [], [], 0, 0, 0, 0
    t0 = time.time()
    for k, (off, home) in enumerate(pairs):
        ya, xa = NET.node_pos[off]
        yb, xb = NET.node_pos[home]
        path = ("/route/v1/driving/%f,%f;%f,%f?overview=full&geometries=geojson"
                % (xa, ya, xb, yb))
        for attempt in range(3):
            try:
                conn.request("GET", path)
                j = json.loads(conn.getresponse().read())
                break
            except Exception:
                conn.close()
                conn = http.client.HTTPConnection(OSRM_HOST, OSRM_PORT, timeout=15)
                j = None
        if not j or j.get("code") != "Ok":
            miss += 1
            continue
        g = [[c[1], c[0]] for c in j["routes"][0]["geometry"]["coordinates"]]
        raw_pts += len(g)
        g = simplify(g, TOL_M)
        kept_pts += len(g)
        d = encode(g, off)
        index.append((off, home, pos, len(d)))
        blobs.append(b"".join(struct.pack("<hh", dy, dx) for dy, dx in d))
        pos += len(d) * 4
        if (k + 1) % 2000 == 0:
            print("  %d/%d  %.0f 秒  %.1f MB"
                  % (k + 1, len(pairs), time.time() - t0, pos / 1e6), flush=True)

    out = ROOT / "data" / "taxi_paths.bin"
    with open(out, "wb") as f:
        f.write(b"TXP1")
        f.write(struct.pack("<I", len(index)))
        head = 8 + len(index) * 12
        for off, home, p, n in index:
            f.write(struct.pack("<HHIHH", off, home, head + p, n, 0))
        for b in blobs:
            f.write(b)
    print("書きました: %s  %.1f MB  %d 組  取れなかった組 %d"
          % (out, out.stat().st_size / 1e6, len(index), miss))
    print("点の数: 生 %d → 間引き後 %d（1 本あたり %.0f → %.0f）"
          % (raw_pts, kept_pts, raw_pts / max(1, len(index)), kept_pts / max(1, len(index))))


if __name__ == "__main__":
    main()
