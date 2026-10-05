#!/usr/bin/env python3
"""ODPT の駅・列車時刻表から、時刻表ベースの探索ネットワークを組み立てる。

- 駅は「同じ駅名かつ 1.5km 以内」をひとつのノード（乗換できる単位）にまとめる。
- さらに 400m 以内のノード同士は徒歩連絡としてつなぐ（御徒町⇔上野御徒町 など）。
- 乗換には既定 5 分かかるものとする（同一ノード内でも別会社・別ホームを想定）。
"""
import json, math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

TRANSFER_MIN = 5       # 同一ノード内の乗換
WALK_SPEED_M_MIN = 70  # 徒歩 70m/分
WALK_LINK_M = 400      # この距離以内のノードは徒歩連絡


def to_min(t):
    h, m = t.split(":")
    h, m = int(h), int(m)
    if h < 4:
        h += 24        # 深夜帯は 24 時以降として扱う
    return h * 60 + m


def fmt(v):
    if v is None or v < 0:
        return "-"
    return f"{v // 60 % 24:02d}:{v % 60:02d}"


def dist_m(a, b):
    dy = (a[0] - b[0]) * 111000
    dx = (a[1] - b[1]) * 91000
    return math.hypot(dx, dy)


class Network:
    def __init__(self, since=None):
        """since（分）を渡すと、その時刻より前に全部走り終わる便を捨てる。

        集合時刻より後に出発する経路しか使わないので、朝・昼の便は結果に影響しない。
        事前計算を現実的な時間で終わらせるためのもの。
        """
        stations = json.loads((RAW / "stations.json").read_text())
        # --- 駅をノードにまとめる ---
        by_name = defaultdict(list)
        for s in stations:
            if "geo:lat" not in s:
                continue
            by_name[s["odpt:stationTitle"]["ja"]].append(s)

        self.node_name, self.node_pos = [], []
        self.sta2node = {}
        for name, group in by_name.items():
            clusters = []   # [(代表座標, [駅])]
            for s in group:
                p = (s["geo:lat"], s["geo:long"])
                for c in clusters:
                    if dist_m(c[0], p) <= 1500:
                        c[1].append(s)
                        break
                else:
                    clusters.append((p, [s]))
            for pos, members in clusters:
                nid = len(self.node_name)
                self.node_name.append(name)
                self.node_pos.append(pos)
                for s in members:
                    self.sta2node[s["owl:sameAs"]] = nid

        # --- 徒歩連絡（400m 以内）---
        self.foot = defaultdict(list)
        grid = defaultdict(list)
        for i, p in enumerate(self.node_pos):
            grid[(round(p[0], 2), round(p[1], 2))].append(i)
        for i, p in enumerate(self.node_pos):
            for dy in (-0.01, 0, 0.01):
                for dx in (-0.01, 0, 0.01):
                    for j in grid.get((round(p[0] + dy, 2), round(p[1] + dx, 2)), []):
                        if j <= i:
                            continue
                        d = dist_m(p, self.node_pos[j])
                        if d <= WALK_LINK_M:
                            cost = TRANSFER_MIN + int(d / WALK_SPEED_M_MIN)
                            self.foot[i].append((j, cost))
                            self.foot[j].append((i, cost))

        # --- 列車を (ノード列, 着時刻列, 発時刻列) に変換 ---
        railways = {r["owl:sameAs"]: r.get("dc:title") or r["owl:sameAs"].split(".")[-1]
                    for r in json.loads((RAW / "railways.json").read_text())}
        self.trips, self.trip_line, self.trip_span = [], [], []
        by_node = defaultdict(list)     # ノード -> [(trip_idx, 停車位置)]
        for tt in json.loads((RAW / "train_timetables.json").read_text()):
            seq = []
            for o in tt["odpt:trainTimetableObject"]:
                sid = o.get("odpt:departureStation") or o.get("odpt:arrivalStation")
                nid = self.sta2node.get(sid)
                if nid is None:
                    continue
                dep = o.get("odpt:departureTime") or o.get("odpt:arrivalTime")
                arr = o.get("odpt:arrivalTime") or o.get("odpt:departureTime")
                if not dep or not arr:
                    continue
                seq.append((nid, to_min(arr), to_min(dep)))
            if len(seq) < 2:
                continue
            # 日跨ぎで時刻が巻き戻る便は捨てる（データの揺れ対策）
            if any(seq[i][1] > seq[i + 1][1] for i in range(len(seq) - 1)):
                continue
            if since is not None and seq[-1][2] < since:
                continue
            idx = len(self.trips)
            self.trips.append(seq)
            # 便の走っている時間帯。探索でその時刻に関係ない便を飛ばすのに使う
            self.trip_span.append((seq[0][2], seq[-1][1]))
            self.trip_line.append(railways.get(tt["odpt:railway"], ""))
            for pos, (nid, _, _) in enumerate(seq):
                by_node[nid].append((idx, pos))
        self.by_node = by_node

    # ---------- 逆向き探索: 「自宅 home に帰れる最終出発時刻」 ----------
    def latest_departure(self, home, deadline=None, rounds=5):
        """各ノードについて、そこを何時までに出れば home に着けるかを返す（分）。

        deadline を渡すと「home にその時刻までに着く」制約になる。
        最寄り駅から最終バスに乗り継ぐ人の帰宅リミットはこれで表せる。
        """
        INF = 10**6
        label = [-INF] * len(self.node_name)
        label[home] = INF if deadline is None else deadline
        for _ in range(rounds):
            improved = False
            nxt = label[:]
            for seq in self.trips:
                usable = False
                for j in range(len(seq) - 1, -1, -1):
                    nid, arr, dep = seq[j]
                    if usable and dep > nxt[nid]:
                        nxt[nid] = dep
                        improved = True
                    # 降りてから次に乗るまでの時間を見る（自宅に着いたらそこで終わり）
                    if arr + (0 if nid == home else TRANSFER_MIN) <= label[nid]:
                        usable = True
            # 徒歩・乗換
            for i in range(len(self.node_name)):
                if nxt[i] <= -INF:
                    continue
                for j, cost in self.foot[i]:
                    v = nxt[i] - cost
                    if v > nxt[j]:
                        nxt[j] = v
                        improved = True
            label = nxt
            if not improved:
                break
        return label

    # ---------- 順方向探索: 出発時刻 t0 での所要時間 ----------
    def earliest_arrival(self, origin, t0, rounds=5, horizon=240):
        """origin を t0 に出たときの、各ノードへの最早到着時刻（分）を返す。

        horizon より長くかかる経路は見ない。1 日ぶんの便を毎回なめると遅いので、
        **その時間帯に走っていない便は最初に外す**。首都圏 50km 圏なら 240 分で足りる。
        """
        INF = 10**6
        label = [INF] * len(self.node_name)
        label[origin] = t0
        limit = t0 + horizon
        use = [seq for seq, (dep0, arrN) in zip(self.trips, self.trip_span)
               if arrN >= t0 and dep0 <= limit]
        for _ in range(rounds):
            improved = False
            nxt = label[:]
            for seq in use:
                riding = False
                for nid, arr, dep in seq:
                    if riding and arr < nxt[nid]:
                        nxt[nid] = arr
                        improved = True
                    if dep >= label[nid] + (0 if nid == origin else TRANSFER_MIN):
                        riding = True
            for i in range(len(self.node_name)):
                if nxt[i] >= INF:
                    continue
                for j, cost in self.foot[i]:
                    v = nxt[i] + cost
                    if v < nxt[j]:
                        nxt[j] = v
                        improved = True
            label = nxt
            if not improved:
                break
        return label

    def earliest_arrival_paths(self, origin, t0, rounds=5, horizon=240):
        """earliest_arrival と同じ枝刈りのまま、経路の復元に必要な親も返す。

        journey() は 1 日ぶんの便をすべてなめるので 1 回 0.35 秒かかる。
        こちらは earliest_arrival と同じ値を返しつつ、親を残すだけなので同じ速さ。
        """
        INF = 10**6
        label = [INF] * len(self.node_name)
        label[origin] = t0
        parent = [None] * len(self.node_name)
        limit = t0 + horizon
        use = [(ti, seq) for ti, (seq, (dep0, arrN))
               in enumerate(zip(self.trips, self.trip_span))
               if arrN >= t0 and dep0 <= limit]
        for _ in range(rounds):
            improved = False
            nxt, par = label[:], parent[:]
            for ti, seq in use:
                board = None
                for k, (nid, arr, dep) in enumerate(seq):
                    if board is not None and arr < nxt[nid]:
                        nxt[nid], par[nid] = arr, ("t", ti, board, k)
                        improved = True
                    if board is None and label[nid] + (0 if nid == origin else TRANSFER_MIN) <= dep:
                        board = k
            for i in range(len(self.node_name)):
                if nxt[i] >= INF:
                    continue
                for j, cost in self.foot[i]:
                    v = nxt[i] + cost
                    if v < nxt[j]:
                        nxt[j], par[j] = v, ("w", i)
                        improved = True
            label, parent = nxt, par
            if not improved:
                break
        return label, parent

    def forward_depart(self, parent, origin, target):
        """origin → target の経路で、**最初に乗る便の発車時刻と、その駅**を返す。

        待っている時間の終わりは「着く時刻」ではなく「乗れる時刻」なので、
        始発待ちの表示にはこちらが要る。乗る便が無ければ None。
        """
        cur, guard, first = target, 0, None
        while cur != origin and guard < 60:
            guard += 1
            p = parent[cur]
            if p is None:
                return None
            if p[0] == "w":
                cur = p[1]
            else:
                _, ti, b, k = p
                seq = self.trips[ti]
                first = (seq[b][2], seq[b][0])     # (発車時刻, 乗る駅)
                cur = seq[b][0]
        return first

    def forward_path(self, parent, origin, target):
        """earliest_arrival_paths の親から、origin → target の通る駅を並べて返す。"""
        out, cur, guard = [], target, 0
        while cur != origin and guard < 60:
            guard += 1
            p = parent[cur]
            if p is None:
                return None
            if p[0] == "w":
                out.append([cur, p[1]])
                cur = p[1]
            else:
                _, ti, b, k = p
                seq = self.trips[ti]
                out.append([n for n, _, _ in reversed(seq[b:k + 1])])
                cur = seq[b][0]
        pts = []
        for part in reversed(out):
            for nid in reversed(part):
                if not pts or pts[-1] != nid:
                    pts.append(nid)
        return pts

    # ---------- 逆向き探索（経路つき） ----------
    def latest_departure_paths(self, home, deadline=None, rounds=5):
        """latest_departure と同じ計算をしながら、経路の復元に必要な親を残す。

        1 回の逆向き探索で「全ノード → home」の経路が取れるので、
        静的サイト用の事前計算はこれ 1 本で足りる。
        """
        INF = 10**6
        label = [-INF] * len(self.node_name)
        label[home] = INF if deadline is None else deadline
        parent = [None] * len(self.node_name)
        for _ in range(rounds):
            nxt, par = label[:], parent[:]
            for ti, seq in enumerate(self.trips):
                use_k = None
                for j in range(len(seq) - 1, -1, -1):
                    nid, arr, dep = seq[j]
                    if use_k is not None and dep > nxt[nid]:
                        nxt[nid], par[nid] = dep, ("t", ti, j, use_k)
                    if arr + (0 if nid == home else TRANSFER_MIN) <= label[nid]:
                        use_k = j
            for i in range(len(self.node_name)):
                if nxt[i] <= -INF:
                    continue
                for j, cost in self.foot[i]:
                    if nxt[i] - cost > nxt[j]:
                        nxt[j], par[j] = nxt[i] - cost, ("w", i)
            label, parent = nxt, par
        return label, parent

    def rebuild(self, parent, label, start, home):
        """逆向き探索の親をたどって start → home の経路を組み立てる。"""
        legs, cur, t, guard = [], start, label[start], 0
        while cur != home and guard < 40:
            guard += 1
            p = parent[cur]
            if p is None:
                return None
            if p[0] == "w":
                legs.append({"kind": "walk", "line": "徒歩", "nodes": [cur, p[1]]})
                cur = p[1]
            else:
                _, ti, b, k = p
                seq = self.trips[ti]
                if seq[b][0] != cur or seq[b][2] < t:
                    return None            # 親が今の時刻と合わない
                legs.append({"kind": "train", "line": self.trip_line[ti],
                             "nodes": [n for n, _, _ in seq[b:k + 1]],
                             "dep": seq[b][2], "arr": seq[k][1]})
                t = seq[k][1] + TRANSFER_MIN
                cur = seq[k][0]
        return legs if cur == home else None

    # ---------- 経路つきの順方向探索 ----------
    def journey(self, origin, t0, target, rounds=5):
        """origin を t0 に出て target に着く経路を返す。着けなければ None。

        区間は {"kind": "train"/"walk", "line", "stops":[{"name","lat","lon","time"}...]}。
        """
        INF = 10**6
        label = [INF] * len(self.node_name)
        label[origin] = t0
        parent = [None] * len(self.node_name)
        for _ in range(rounds):
            nxt, par = label[:], parent[:]
            for ti, seq in enumerate(self.trips):
                board = None
                for k, (nid, arr, dep) in enumerate(seq):
                    if board is not None and arr < nxt[nid]:
                        nxt[nid], par[nid] = arr, ("t", ti, board, k)
                    if board is None and label[nid] + (0 if nid == origin else TRANSFER_MIN) <= dep:
                        board = k
            for i in range(len(self.node_name)):
                if nxt[i] >= INF:
                    continue
                for j, cost in self.foot[i]:
                    if nxt[i] + cost < nxt[j]:
                        nxt[j], par[j] = nxt[i] + cost, ("w", i)
            label, parent = nxt, par
        if label[target] >= INF:
            return None

        legs, cur, guard = [], target, 0
        while cur != origin and guard < 60:
            guard += 1
            p = parent[cur]
            if p is None:
                return None
            if p[0] == "w":
                legs.append({"kind": "walk", "line": "徒歩",
                             "stops": [self._stop(p[1], None), self._stop(cur, None)]})
                cur = p[1]
            else:
                _, ti, b, k = p
                seq = self.trips[ti]
                legs.append({"kind": "train", "line": self.trip_line[ti],
                             "stops": [self._stop(n, tm) for n, tm, _ in seq[b:k + 1]]})
                cur = seq[b][0]
        legs.reverse()
        return {"arrive": label[target], "legs": legs}

    def _stop(self, nid, t):
        return {"name": self.node_name[nid], "lat": self.node_pos[nid][0],
                "lon": self.node_pos[nid][1], "time": fmt(t) if t is not None else None}

    def find(self, name):
        hits = [i for i, n in enumerate(self.node_name) if n == name]
        if not hits:
            raise KeyError(name)
        return hits[0]
