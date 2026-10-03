#!/usr/bin/env python3
"""web/ を配信しつつ、「終電までの残り」と「終電後のタクシー代」をその場で計算する。

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
import struct
import sys
import threading
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
import network  # noqa: E402

WEB = ROOT / "web"
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

SPOTS = json.loads((WEB / "data" / "spots.json").read_text())

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
    """駅 a から駅 b までの道路距離（メートル）。行列が無ければ直線×1.35。"""
    if ROAD is not None:
        ia, ib = ROAD_POS.get(a), ROAD_POS.get(b)
        if ia is not None and ib is not None:
            v = ROAD[ia * len(ROAD_IDX) + ib]
            return None if v >= 3_000_000 else v
    ya, xa = NET.node_pos[a]
    yb, xb = NET.node_pos[b]
    return math.hypot((ya - yb) * 111000, (xa - xb) * 91000) * 1.35


_lock = threading.Lock()
_limit_cache = {}
_arr_cache = {}


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
    with _lock:
        v = _arr_cache.get((spot, t))
    if v is None:
        v = NET.earliest_arrival(spot, t)
        with _lock:
            if len(_arr_cache) > 4000:
                _arr_cache.clear()
            _arr_cache[(spot, t)] = v
    return v


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
        arr = reach(c, t)
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
        best = None
        if cands:
            cheapest = min(c0[0] for c0 in cands)
            # いちばん安い駅だけを見ると、「25 分乗って 360 円だけ安い」ような
            # 誰もやらない選択が出る。**ほぼ同額なら早く着くほうを採る。**
            near = [c0 for c0 in cands if c0[0] <= cheapest + TIE_YEN]
            f, a, v, d = min(near, key=lambda c0: (c0[1], c0[0]))
            best = (f, v, d, a)
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
