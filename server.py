#!/usr/bin/env python3
"""site/ を配信する開発用サーバ。/api/night は答え合わせ用に残してある。

画面そのものはサーバを使わない（site/data/home/<駅>.bin を読んで自分で計算する）。
GitHub Pages に置くのは site/ の中身だけ。

  GET /                        web/index.html
  GET /api/night?home=12&t=1420
      自宅を 12 番の駅にして、1420 分（23:40）に飲み屋を出たときの、
      候補 10 駅それぞれの状態を返す。

状態は 2 つ。
  train … まだ終電に間に合う。あと何分かを返す
  taxi  … 終電が尽きた。**電車で行けるところまで行き、いちばん安く済む駅で降りて
          タクシー**に乗ったときの運賃・降りる駅・距離を返す
"""
import json
import math
import os
import struct
import sys
import threading
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
import network  # noqa: E402

SRC = ROOT / "web" / "data"      # build_*.py が作る中間データ
WEB = ROOT / "site"              # 配信する（＝ GitHub Pages に置く）ほう
INF = 10 ** 6

# ---- タクシー運賃（東京都特別区・武蔵野市・三鷹市／普通車）----
# 出典: 東京無線協同組合「認可運賃表」https://www.tokyomusen.or.jp/pay/carriage
#   距離制 … 最初の 1.0km まで 500 円、以後 232m ごとに 100 円
#   深夜早朝割増 … 22 時〜5 時は 2 割増
# 時間距離併用（時速 10km 以下の走行 85 秒ごとに 100 円）は見ていない＝**安めに出る**。
FARE_BASE = 500
FARE_BASE_M = 1000
FARE_STEP_M = 232
FARE_STEP = 100
NIGHT_RATE = 1.2
# 運賃がこの差に収まるなら、安さより「早く着く」を採る
TIE_YEN = 600


def taxi_fare(meters, night=True):
    if meters is None or meters >= 3_000_000:
        return None
    f = FARE_BASE
    if meters > FARE_BASE_M:
        f += math.ceil((meters - FARE_BASE_M) / FARE_STEP_M) * FARE_STEP
    if night:
        f = int(round(f * NIGHT_RATE / 10.0)) * 10
    return f


def is_night(minute):
    """22:00〜翌5:00 が深夜割増。minute は 24 時以降も続く分数。"""
    m = minute % (24 * 60)
    return m >= 22 * 60 or m < 5 * 60


print("ネットワークを組み立てています …", flush=True)
NET = network.Network()
print("ノード %d / 便 %d" % (len(NET.node_name), len(NET.trips)), flush=True)

SPOTS = json.loads((SRC / "spots.json").read_text())

# ---- 道路距離の行列 ----
ROAD_IDX, ROAD, ROAD_POS = None, None, None
try:
    ROAD_IDX = json.loads((ROOT / "data" / "road_nodes.json").read_text())
    raw = (ROOT / "data" / "road_m.bin").read_bytes()
    n = len(ROAD_IDX)
    assert len(raw) == n * n * 4, "road_m.bin の大きさが合わない"
    ROAD = struct.unpack("<%dI" % (n * n), raw)
    ROAD_POS = {v: k for k, v in enumerate(ROAD_IDX)}
    print("道路距離の行列 %d×%d" % (n, n), flush=True)
except (FileNotFoundError, AssertionError) as e:
    print("道路距離の行列が無いので直線距離×1.35 で代用します（%s）" % e, flush=True)


def road_m(a, b):
    """駅 a から駅 b までの道路距離（メートル）。分からなければ None。

    行列は東京駅から 50km 以内の 829 駅ぶんしかない。**それ以外の駅は候補にしない。**
    もともとは直線×1.35 で代用していたが、運賃はこのアプリの主役の数字なので、
    測っていない距離で出すのはよくない（直線近似は 2 割ほど高く出る）。
    実際、南の自宅（寒川・香川）で **茅ケ崎に降りる**という答えが出ることがあり、
    その運賃だけ推定値になっていた。行列そのものが無いとき（開発中）は従来どおり代用する。
    """
    if ROAD is not None:
        ia, ib = ROAD_POS.get(a), ROAD_POS.get(b)
        if ia is None or ib is None:
            return None
        v = ROAD[ia * len(ROAD_IDX) + ib]
        return None if v >= 3_000_000 else v
    ya, xa = NET.node_pos[a]
    yb, xb = NET.node_pos[b]
    return math.hypot((ya - yb) * 111000, (xa - xb) * 91000) * 1.35


_lock = threading.Lock()
_limit_cache = {}
_arr_cache = {}
_road_cache = {}

# ---- タクシー区間の道路の形 ----
# 先に全部計算して data/taxi_paths.bin に入れてある（scripts/build_taxipaths.py）。
# **ふだんはこれだけを読む。osrm-routed は要らない。**
# 表に無い組だけ osrm-routed に聞き、それも居なければ None（画面は直線で描く）。
OSRM = os.environ.get("OSRM_URL", "http://127.0.0.1:5050")
SCALE = 100000          # 1e-5 度を 1 とする

TAXI_BLOB, TAXI_IDX = None, {}
try:
    _b = (ROOT / "data" / "taxi_paths.bin").read_bytes()
    assert _b[:4] == b"TXP1", "taxi_paths.bin の形式が違う"
    _n = struct.unpack_from("<I", _b, 4)[0]
    for _i in range(_n):
        _off, _home, _at, _cnt, _ = struct.unpack_from("<HHIHH", _b, 8 + _i * 12)
        TAXI_IDX[(_off, _home)] = (_at, _cnt)
    TAXI_BLOB = _b
    print("タクシー経路の形 %d 組（%.1f MB）" % (_n, len(_b) / 1e6), flush=True)
except (FileNotFoundError, AssertionError) as e:
    print("タクシー経路の形が無いので osrm-routed に聞きます（%s）" % e, flush=True)


def simplify(pts, tol_m):
    """Douglas-Peucker。1000 点の経路をそのまま送ると重いので間引く。"""
    if len(pts) < 3:
        return pts
    def dist(p, a, b):
        ax, ay = (a[1] - p[1]) * 91000, (a[0] - p[0]) * 111000
        bx, by = (b[1] - p[1]) * 91000, (b[0] - p[0]) * 111000
        vx, vy = bx - ax, by - ay
        l = vx * vx + vy * vy
        t = 0 if not l else max(0.0, min(1.0, -(ax * vx + ay * vy) / l))
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
        if dmax > tol_m:
            keep[k] = True
            stack.append((i, k))
            stack.append((k, j))
    return [p for p, f in zip(pts, keep) if f]


def stored_path(a, b):
    """先に計算してある道の形。[[lat, lon], ...]。無ければ None。"""
    v = TAXI_IDX.get((a, b))
    if v is None or TAXI_BLOB is None:
        return None
    at, cnt = v
    cy = int(round(NET.node_pos[a][0] * SCALE))
    cx = int(round(NET.node_pos[a][1] * SCALE))
    out = []
    for i in range(cnt):
        dy, dx = struct.unpack_from("<hh", TAXI_BLOB, at + i * 4)
        cy += dy
        cx += dx
        out.append([round(cy / SCALE, 5), round(cx / SCALE, 5)])
    return out


def road_path(a, b):
    """駅 a → 駅 b の道の形。[[lat, lon], ...]。取れなければ None。"""
    v = stored_path(a, b)
    if v is not None:
        return v
    with _lock:
        if (a, b) in _road_cache:
            return _road_cache[(a, b)]
    ya, xa = NET.node_pos[a]
    yb, xb = NET.node_pos[b]
    url = ("%s/route/v1/driving/%f,%f;%f,%f?overview=full&geometries=geojson"
           % (OSRM, xa, ya, xb, yb))
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            j = json.loads(r.read())
        pts = [[round(c[1], 5), round(c[0], 5)]
               for c in j["routes"][0]["geometry"]["coordinates"]] if j["code"] == "Ok" else None
        if pts:
            pts = simplify(pts, 40)
    except Exception:
        pts = None
    with _lock:
        if len(_road_cache) > 4000:
            _road_cache.clear()
        _road_cache[(a, b)] = pts
    return pts


def limits(home):
    """各駅から home まで電車で帰れる最終出発時刻（分）。1 回の逆向き探索で全駅ぶん出る。"""
    with _lock:
        v = _limit_cache.get(home)
    if v is None:
        v = NET.latest_departure(home)
        with _lock:
            if len(_limit_cache) > 200:
                _limit_cache.clear()
            _limit_cache[home] = v
    return v


def reach(spot, t):
    """spot を t に出たときの各駅への最早到着と、経路の親。"""
    with _lock:
        v = _arr_cache.get((spot, t))
    if v is None:
        v = NET.earliest_arrival_paths(spot, t)
        with _lock:
            if len(_arr_cache) > 4000:
                _arr_cache.clear()
            _arr_cache[(spot, t)] = v
    return v


def choose_off(arr, home):
    """行ける駅のうち、自宅までのタクシーがいちばん安く済む駅を選ぶ。

    (運賃, 降りる駅, 道路距離, その駅に着く時刻) を返す。選べなければ None。
    scripts/build_taxipaths.py も同じ選び方をする必要があるので、ここに切り出してある。
    """
    cands = []
    for v in range(len(NET.node_name)):
        if arr[v] >= INF:
            continue
        d = road_m(v, home)
        if d is None:
            continue
        f = taxi_fare(d, is_night(arr[v]))
        if f is not None:
            cands.append((f, arr[v], v, d))
    if not cands:
        return None
    cheapest = min(c[0] for c in cands)
    # いちばん安い駅だけを見ると、「25 分乗って 360 円だけ安い」ような
    # 誰もやらない選択が出る。**ほぼ同額なら早く着くほうを採る。**
    near = [c for c in cands if c[0] <= cheapest + TIE_YEN]
    f, a, v, d = min(near, key=lambda c: (c[1], c[0]))
    return f, v, d, a


def night(home, t):
    lim = limits(home)
    out = []
    for s in SPOTS:
        c = s["node"]
        row = {"node": c, "name": s["name"]}
        if c == home:
            row["state"] = "home"
            out.append(row)
            continue
        lv = lim[c]
        row["limit"] = None if lv <= -INF or lv >= INF else lv
        if row["limit"] is not None and t <= row["limit"]:
            row["state"] = "train"
            row["left"] = row["limit"] - t
            out.append(row)
            continue

        # 終電が尽きた。電車で行けるところまで行って、いちばん安い駅で降りる
        row["state"] = "taxi"
        arr, par = reach(c, t)
        best = choose_off(arr, home)
        if best is None:
            row["state"] = "unknown"
            out.append(row)
            continue
        row["fare"] = best[0]
        row["off"] = best[1]
        row["offName"] = NET.node_name[best[1]]
        row["offTime"] = best[3]
        row["km"] = round(best[2] / 1000.0, 1)
        row["trainMin"] = best[3] - t
        # 線を実際の形で描くための座標列
        if best[1] != c:
            ids = NET.forward_path(par, c, best[1])
            if ids:
                row["trainPath"] = [[round(NET.node_pos[v][0], 5),
                                     round(NET.node_pos[v][1], 5)] for v in ids]
        row["taxiPath"] = road_path(best[1], home)
        # 全部タクシーで帰ったらいくらか（比較用）
        dc = road_m(c, home)
        row["allTaxi"] = taxi_fare(dc, is_night(t)) if dc is not None else None
        out.append(row)
    return {"home": home, "homeName": NET.node_name[home], "t": t, "spots": out}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path != "/api/night":
            return super().do_GET()
        q = urllib.parse.parse_qs(p.query)
        try:
            home = int(q["home"][0])
            t = int(q["t"][0])
        except (KeyError, IndexError, ValueError):
            return self.send_error(400, "need home and t")
        if not (0 <= home < len(NET.node_name)):
            return self.send_error(400, "bad home")
        body = json.dumps(night(home, t), ensure_ascii=False,
                          separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8003
    print("http://127.0.0.1:%d/" % port, flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
