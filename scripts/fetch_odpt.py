#!/usr/bin/env python3
"""ODPT から鉄道の駅・路線・列車時刻表を取得して data/raw/ に置く。

トークンはワークスペース直下の .env から読む（値はコードにもログにも書かない）。
チャレンジ限定の事業者は api-challenge.odpt.org、それ以外は api.odpt.org。
"""
import json, os, sys, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
ENV = ROOT.parents[1] / ".env"

PUBLIC = "https://api.odpt.org/api/v4/"
CHALLENGE = "https://api-challenge.odpt.org/api/v4/"

# 事業者 -> チャレンジ限定か
OPERATORS = {
    "TokyoMetro": False,
    "Toei": False,
    "MIR": False,          # つくばエクスプレス
    "TWR": False,          # りんかい線
    "TamaMonorail": False,
    "YokohamaMunicipal": False,
    "JR-East": True,
    "Keio": True,
    "Tobu": True,
    "Sotetsu": True,
}

# JR東日本は路線が多いので首都圏の通勤路線だけに絞る
JR_EAST_LINES = {
    "Yamanote", "ChuoRapid", "ChuoSobuLocal", "KeihinTohokuNegishi", "Saikyo",
    "JobanRapid", "JobanLocal", "SobuRapid", "Tokaido", "Yokosuka", "Keiyo",
    "Musashino", "Nambu", "Yokohama", "Ome", "Itsukaichi", "Takasaki",
    "Utsunomiya", "ShonanShinjuku", "UenoTokyo", "Tsurumi", "Negishi",
}


def env_token(name):
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    sys.exit(f"{name} が .env にありません")


TOKENS = {False: env_token("ODPT_ACCESS_TOKEN"), True: env_token("ODPT_CHALLENGE_TOKEN")}


def get(kind, challenge, **params):
    base = CHALLENGE if challenge else PUBLIC
    params["acl:consumerKey"] = TOKENS[challenge]
    url = base + kind + "?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if attempt == 2:
                print(f"  !! {kind} {params.get('odpt:railway') or params.get('odpt:operator')}: {e}")
                return []
            time.sleep(5)


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    railways, stations = [], []
    for op, ch in OPERATORS.items():
        rws = get("odpt:Railway", ch, **{"odpt:operator": f"odpt.Operator:{op}"})
        if op == "JR-East":
            rws = [r for r in rws if r["owl:sameAs"].split(".")[-1] in JR_EAST_LINES]
        railways += rws
        sts = get("odpt:Station", ch, **{"odpt:operator": f"odpt.Operator:{op}"})
        stations += sts
        print(f"{op}: 路線 {len(rws)} / 駅 {len(sts)}")
        time.sleep(1.1)
    (RAW / "railways.json").write_text(json.dumps(railways, ensure_ascii=False))
    (RAW / "stations.json").write_text(json.dumps(stations, ensure_ascii=False))

    # 列車時刻表は路線ごと・平日ダイヤだけ
    tt = []
    for i, rw in enumerate(railways, 1):
        rid = rw["owl:sameAs"]
        op = rid.split(":")[1].split(".")[0]
        if op not in OPERATORS:
            continue
        rows = get("odpt:TrainTimetable", OPERATORS[op],
                   **{"odpt:railway": rid, "odpt:calendar": "odpt.Calendar:Weekday"})
        if len(rows) >= 1000:
            print(f"  ** {rid} は 1000 件で頭打ち（分割が必要）")
        tt += rows
        print(f"[{i}/{len(railways)}] {rid}: {len(rows)}")
        time.sleep(1.1)
    (RAW / "train_timetables.json").write_text(json.dumps(tt, ensure_ascii=False))
    print(f"列車時刻表 合計 {len(tt)} 本")


if __name__ == "__main__":
    main()
