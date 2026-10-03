#!/usr/bin/env python3
"""駅と駅のあいだの**道路距離**の行列を作る（タクシー運賃の計算に使う）。

OSRM の `/table` を使う。直線距離に係数を掛ける手もあるが、東京湾や川で
大きく外れる（例: 豊洲→浦安は直線だと海を渡ってしまう）ので道路で測る。

事前に OSRM のグラフを用意しておくこと:

    osmium extract -b 138.90,35.10,140.70,36.30 -s smart -o /var/tmp/odc2026-osrm/tokyo.osm.pbf <kanto.osm.pbf>
    docker run --rm -v /var/tmp/odc2026-osrm:/data osrm/osrm-backend osrm-extract   -p /opt/car.lua /data/tokyo.osm.pbf
    docker run --rm -v /var/tmp/odc2026-osrm:/data osrm/osrm-backend osrm-partition /data/tokyo.osrm
    docker run --rm -v /var/tmp/odc2026-osrm:/data osrm/osrm-backend osrm-customize /data/tokyo.osrm

このスクリプトが osrm-routed を立ち上げて、終わったら落とす。
**OSRM のグラフは mmap を使うので、共有フォルダ（/vagrant）の上では落ちる。**
ローカルディスク（既定 /var/tmp/odc2026-osrm）に置くこと。

出力:
  data/road_nodes.json  対象にしたノード番号の並び
  data/road_m.bin       uint32 の距離行列（メートル）。N×N、行＝出発、列＝到着
"""
import json
import os
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
WEB = ROOT / "web" / "data"
OSRM_DIR = os.environ.get("OSRM_DIR", "/var/tmp/odc2026-osrm")
IMAGE = "osrm/osrm-backend:latest"
PORT = 5111
CHUNK = 80          # 1 回の /table で投げる出発地の数


def wait_ready(timeout=120):
    for _ in range(timeout * 2):
        try:
            urllib.request.urlopen(
                "http://127.0.0.1:%d/route/v1/driving/139.70,35.69;139.71,35.70" % PORT,
                timeout=2).read()
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    base = json.loads((WEB / "base.json").read_text())
    nodes = base["nodes"]
    idx = [i for i, n in enumerate(nodes) if n["v"]]
    coords = [(nodes[i]["x"], nodes[i]["y"]) for i in idx]
    N = len(idx)
    print("対象 %d 駅" % N)

    graph = Path(OSRM_DIR) / "tokyo.osrm.mldgr"
    if not graph.exists():
        sys.exit("OSRM のグラフが無い: %s（上の手順で作る）" % graph)

    print("osrm-routed を立ち上げます（ポート %d）" % PORT)
    proc = subprocess.Popen(
        ["docker", "run", "--rm", "-p", "%d:5000" % PORT,
         "-v", "%s:/data" % OSRM_DIR, IMAGE,
         "osrm-routed", "--algorithm", "mld", "--max-table-size", "20000",
         "/data/tokyo.osrm"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_ready():
            sys.exit("osrm-routed が立ち上がらなかった")
        print("  立ち上がりました")

        allc = ";".join("%.6f,%.6f" % c for c in coords)
        out = bytearray()
        t0 = time.time()
        for s in range(0, N, CHUNK):
            e = min(s + CHUNK, N)
            url = ("http://127.0.0.1:%d/table/v1/driving/%s?annotations=distance&sources=%s"
                   % (PORT, allc, ";".join(str(k) for k in range(s, e))))
            with urllib.request.urlopen(url, timeout=600) as r:
                d = json.loads(r.read())
            if d.get("code") != "Ok":
                sys.exit("OSRM が返した: %s" % d.get("code"))
            for row in d["distances"]:
                for v in row:
                    # 届かない組は 0 ではなく「とても遠い」にしておく
                    out += struct.pack("<I", 4_000_000_000 if v is None else int(v))
            print("  %d/%d  (%.0f秒)" % (e, N, time.time() - t0), flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()

    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "road_nodes.json").write_text(json.dumps(idx))
    (DATA / "road_m.bin").write_bytes(bytes(out))
    print("出力 data/road_m.bin (%.1fMB)" % (len(out) / 1e6))


if __name__ == "__main__":
    main()
