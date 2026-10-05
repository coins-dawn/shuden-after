#!/usr/bin/env python3
"""site/data/home/*.bin から読んだ答えが、サーバの計算と一致するか確かめる。

    python3 scripts/verify_static.py [件数]
"""
import json
import random
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import server  # noqa: E402

OUT = ROOT / "site" / "data"
NONE16 = 0xFFFF


def read_home(h):
    b = (OUT / "home" / ("%d.bin" % h)).read_bytes()
    assert b[:4] == b"SHDN"
    p = 6
    node, ns, nt, t_from, t_step = struct.unpack_from("<HBHHB", b, p)
    p += 8
    limit = list(struct.unpack_from("<%dh" % ns, b, p)); p += ns * 2
    allkm = list(struct.unpack_from("<%dH" % ns, b, p)); p += ns * 2
    allnight = list(struct.unpack_from("<%dH" % ns, b, p)); p += ns * 2
    allday = list(struct.unpack_from("<%dH" % ns, b, p)); p += ns * 2
    first = list(struct.unpack_from("<%dH" % ns, b, p)); p += ns * 2
    firstdep = list(struct.unpack_from("<%dH" % ns, b, p)); p += ns * 2
    cols = {}
    for k in ("off", "at", "km", "fare", "tr", "tx"):
        cols[k] = list(struct.unpack_from("<%dH" % (ns * nt), b, p)); p += ns * nt * 2
    trp = []
    (m,) = struct.unpack_from("<H", b, p); p += 2
    for _ in range(m):
        (c,) = struct.unpack_from("<B", b, p); p += 1
        trp.append(list(struct.unpack_from("<%dH" % c, b, p))); p += c * 2
    txp = []
    (m,) = struct.unpack_from("<H", b, p); p += 2
    for _ in range(m):
        (c,) = struct.unpack_from("<H", b, p); p += 2
        txp.append(list(struct.unpack_from("<%dh" % (c * 2), b, p))); p += c * 4
    # 版 5: 道のり（何時に何線に乗って、どこで乗り換えるか）
    cols["leg"] = list(struct.unpack_from("<%dH" % (ns * nt), b, p)); p += ns * nt * 2
    legs = []
    (m,) = struct.unpack_from("<H", b, p); p += 2
    for _ in range(m):
        (c,) = struct.unpack_from("<B", b, p); p += 1
        legs.append([struct.unpack_from("<BBHHHHB", b, p + k * 11) for k in range(c)])
        p += c * 11
    assert p == len(b), "読み残し %d" % (len(b) - p)
    return dict(node=node, ns=ns, nt=nt, t_from=t_from, t_step=t_step,
                limit=limit, allkm=allkm, allnight=allnight, allday=allday,
                first=first, firstdep=firstdep, trp=trp, txp=txp, legs=legs, **cols)


def static_row(H, si, t):
    ti = (t - H["t_from"]) // H["t_step"]
    i = si * H["nt"] + ti
    lv = H["limit"][si]
    if lv >= 0 and t <= lv:
        return ("train", lv - t, None, None)
    off = H["off"][i]
    if off == NONE16:
        return ("unknown", None, None, None)
    fare = None if H["fare"][i] == NONE16 else H["fare"][i]
    return ("taxi", None, fare, off)


def main():
    import json as _json
    from pathlib import Path as _P
    allh = [h["node"] for h in _json.loads(
        (_P(__file__).resolve().parents[1] / "web" / "data" / "homes.json").read_text())]
    nh = min(int(sys.argv[1]) if len(sys.argv) > 1 else len(allh), len(allh))
    random.seed(29)
    homes = allh[:nh]
    times = list(range(1200, 1621, 5))
    # 逆向き探索は自宅ごとに 0.4 秒かかるので、自宅をまとめて回す
    bad = tot = 0
    for k, h in enumerate(homes):
        H = read_home(h)
        lim = server.limits(h)
        for si, sp in enumerate(server.SPOTS):
            c = sp["node"]
            if c == h:
                continue
            for t in times:
                got = static_row(H, si, t)
                lv = lim[c]
                if -server.INF < lv < server.INF and t <= lv:
                    want = ("train", lv - t, None, None)
                else:
                    arr, _ = server.reach(c, t)
                    b = server.choose_off(arr, h)
                    want = ("unknown", None, None, None) if b is None \
                        else ("taxi", None, b[0], b[1])
                tot += 1
                if got != want:
                    bad += 1
                    if bad <= 5:
                        print("  x %s / %s / %d  static %s  server %s"
                              % (server.NET.node_name[h], sp["name"], t, got, want))
        if (k + 1) % 10 == 0:
            print("  %d/%d 自宅  これまで %d 件  食い違い %d"
                  % (k + 1, nh, tot, bad), flush=True)
    print("自宅 %d x 候補 %d x 時刻 %d = %d 件を照合  食い違い %d 件"
          % (nh, len(server.SPOTS), len(times), tot, bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
