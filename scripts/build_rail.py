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
    {"st": {"<ノード id>": [緯度, 経度, "駅名（N02 のもの）", 路線数]},  突き合わせた駅
     "li": [[色番号, 緯度, 経度, 緯度, 経度, ...], ...],      路線の線（折れ線）
     "co": ["#rrggbb", ...],                                  色（**自前の配色**）
     "odpt": {"山手線": 0, ...},                              明細の路線名 → 色番号
     "op": {"JR-East": 0, ...}}                               事業者 → 色番号（予備）

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

# ---- 路線の色 ----
# **手で書いた「その路線のイメージ色」**。案内サインや路線図でおなじみの色を、
# 一般に知られているとおりに置いただけで、**どこかのデータの色の値を写したものではない**。
# 分からない路線は事業者の色にする。
PALETTE = {
    # JR東日本
    "yamanote": "#9acd32", "keihin": "#00b2e5", "chuo": "#f15a22", "sobu": "#ffd400",
    "yokosuka": "#0067c0", "joban": "#00b48d", "keiyo": "#c9252f", "musashino": "#ff7e1e",
    "saikyo": "#00ac9a", "yokohama": "#7ac143", "nambu": "#f5d13a", "takasaki": "#f68b1e",
    "tokaido": "#f68b1e", "hachiko": "#a9a9a9", "sagami": "#00a56e", "tsurumi": "#ffd400",
    "uchibo": "#0072bc", "sotobo": "#d93a49", "narita": "#00a650", "togane": "#ffb400",
    "kururi": "#8ab0c8",
    # 東京メトロ
    "ginza": "#ff9500", "marunouchi": "#f62e36", "hibiya": "#b5b5ac", "tozai": "#009bbf",
    "chiyoda": "#00bb85", "yurakucho": "#c1a470", "hanzomon": "#8f76d6",
    "namboku": "#00ac9b", "fukutoshin": "#9c5e31",
    # 都営
    "asakusa": "#e85298", "mita": "#0079c2", "shinjuku": "#6cbb5a", "oedo": "#b6007a",
    "nippori": "#e8b000", "arakawa": "#ef8200",
    # 東武・京王・相鉄・TX・りんかい・モノレール・横浜市
    "tobu_skytree": "#1b7cc1", "tobu_nikko": "#c47a3d", "tobu_noda": "#00a650",
    "tobu_tojo": "#004da0", "keio": "#dd0077", "inokashira": "#0075c2",
    "sotetsu": "#16488b", "tx": "#0f6cbd", "rinkai": "#00559e",
    "tamamono": "#ea5520", "yoko_blue": "#0071bc", "yoko_green": "#00a650",
}

# N02 の（運営会社, 路線名）→ 上の色
N02_LINE = {
    ("東日本旅客鉄道", "山手線"): "yamanote",
    ("東日本旅客鉄道", "東北線"): "keihin",
    ("東日本旅客鉄道", "根岸線"): "keihin",
    ("東日本旅客鉄道", "東海道線"): "tokaido",
    ("東日本旅客鉄道", "高崎線"): "takasaki",
    ("東日本旅客鉄道", "中央線"): "chuo",
    ("東日本旅客鉄道", "青梅線"): "chuo",
    ("東日本旅客鉄道", "五日市線"): "chuo",
    ("東日本旅客鉄道", "総武線"): "sobu",
    ("東日本旅客鉄道", "横須賀線"): "yokosuka",
    ("東日本旅客鉄道", "常磐線"): "joban",
    ("東日本旅客鉄道", "京葉線"): "keiyo",
    ("東日本旅客鉄道", "武蔵野線"): "musashino",
    ("東日本旅客鉄道", "赤羽線"): "saikyo",
    ("東日本旅客鉄道", "川越線"): "saikyo",
    ("東日本旅客鉄道", "横浜線"): "yokohama",
    ("東日本旅客鉄道", "南武線"): "nambu",
    ("東日本旅客鉄道", "鶴見線"): "tsurumi",
    ("東日本旅客鉄道", "八高線"): "hachiko",
    ("東日本旅客鉄道", "相模線"): "sagami",
    ("東日本旅客鉄道", "内房線"): "uchibo",
    ("東日本旅客鉄道", "外房線"): "sotobo",
    ("東日本旅客鉄道", "成田線"): "narita",
    ("東日本旅客鉄道", "東金線"): "togane",
    ("東日本旅客鉄道", "久留里線"): "kururi",
    ("東京地下鉄", "3号線銀座線"): "ginza",
    ("東京地下鉄", "4号線丸ノ内線"): "marunouchi",
    ("東京地下鉄", "4号線丸ノ内線分岐線"): "marunouchi",
    ("東京地下鉄", "2号線日比谷線"): "hibiya",
    ("東京地下鉄", "5号線東西線"): "tozai",
    ("東京地下鉄", "9号線千代田線"): "chiyoda",
    ("東京地下鉄", "8号線有楽町線"): "yurakucho",
    ("東京地下鉄", "11号線半蔵門線"): "hanzomon",
    ("東京地下鉄", "7号線南北線"): "namboku",
    ("東京地下鉄", "13号線副都心線"): "fukutoshin",
    ("東京都", "1号線浅草線"): "asakusa",
    ("東京都", "6号線三田線"): "mita",
    ("東京都", "10号線新宿線"): "shinjuku",
    ("東京都", "12号線大江戸線"): "oedo",
    ("東京都", "日暮里・舎人ライナー"): "nippori",
    ("東京都", "荒川線"): "arakawa",
    ("東武鉄道", "伊勢崎線"): "tobu_skytree",
    ("東武鉄道", "亀戸線"): "tobu_skytree",
    ("東武鉄道", "大師線"): "tobu_skytree",
    ("東武鉄道", "佐野線"): "tobu_skytree",
    ("東武鉄道", "小泉線"): "tobu_skytree",
    ("東武鉄道", "日光線"): "tobu_nikko",
    ("東武鉄道", "野田線"): "tobu_noda",
    ("東武鉄道", "東上本線"): "tobu_tojo",
    ("東武鉄道", "越生線"): "tobu_tojo",
    ("京王電鉄", "京王線"): "keio",
    ("京王電鉄", "相模原線"): "keio",
    ("京王電鉄", "高尾線"): "keio",
    ("京王電鉄", "競馬場線"): "keio",
    ("京王電鉄", "動物園線"): "keio",
    ("京王電鉄", "井の頭線"): "inokashira",
    ("相模鉄道", "相鉄本線"): "sotetsu",
    ("相模鉄道", "相鉄いずみ野線"): "sotetsu",
    ("相模鉄道", "相鉄新横浜線"): "sotetsu",
    ("首都圏新都市鉄道", "常磐新線"): "tx",
    ("東京臨海高速鉄道", "臨海副都心線"): "rinkai",
    ("多摩都市モノレール", "多摩都市モノレール線"): "tamamono",
    ("横浜市", "1号線"): "yoko_blue",
    ("横浜市", "3号線"): "yoko_blue",
    ("横浜市", "4号線"): "yoko_green",
}

# 明細に出す路線名（ODPT の路線名）→ 上の色
ODPT_LINE = {
    "山手線": "yamanote", "京浜東北線・根岸線": "keihin", "中央線快速": "chuo",
    "中央・総武各駅停車": "sobu", "総武快速線": "yokosuka", "横須賀線": "yokosuka",
    "常磐線快速": "joban", "常磐線各駅停車": "chiyoda", "京葉線": "keiyo",
    "武蔵野線": "musashino", "高崎線": "takasaki", "宇都宮線": "takasaki",
    "東海道線": "tokaido", "南武線": "nambu", "横浜線": "yokohama", "埼京線": "saikyo",
    "川越線": "saikyo", "青梅線": "chuo", "八高線": "hachiko", "相模線": "sagami",
    "銀座線": "ginza", "丸ノ内線": "marunouchi", "日比谷線": "hibiya", "東西線": "tozai",
    "千代田線": "chiyoda", "有楽町線": "yurakucho", "半蔵門線": "hanzomon",
    "南北線": "namboku", "副都心線": "fukutoshin",
    "浅草線": "asakusa", "三田線": "mita", "新宿線": "shinjuku", "大江戸線": "oedo",
    "東武アーバンパークライン": "tobu_noda", "東上線": "tobu_tojo",
    "東武スカイツリーライン": "tobu_skytree", "亀戸線": "tobu_skytree",
    "日光線": "tobu_nikko",
    "京王線": "keio", "京王新線": "keio", "相模原線": "keio", "井の頭線": "inokashira",
    "りんかい線": "rinkai", "つくばエクスプレス": "tx", "ブルーライン": "yoko_blue",
    "グリーンライン": "yoko_green", "多摩都市モノレール線": "tamamono",
    "相鉄本線": "sotetsu", "いずみ野線": "sotetsu",
}

# 路線が分からないときの、事業者ごとの色
OP_FALLBACK = [
    ("東日本旅客鉄道", "JR-East", "#5bb08a"),
    ("東京地下鉄", "TokyoMetro", "#e59a4b"),
    ("東京都", "Toei", "#5f9ed8"),
    ("東武鉄道", "Tobu", "#1b7cc1"),
    ("京王電鉄", "Keio", "#dd0077"),
    ("相模鉄道", "Sotetsu", "#16488b"),
    ("首都圏新都市鉄道", "MIR", "#0f6cbd"),
    ("東京臨海高速鉄道", "TWR", "#00559e"),
    ("多摩都市モノレール", "TamaMonorail", "#ea5520"),
    ("横浜市", "YokohamaMunicipal", "#0071bc"),
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
        key = (norm(p["N02_005"]), p["N02_004"], p["N02_003"], p["N02_005"])
        st.setdefault(key, []).append((lat, lon))
    by_name = {}
    for (nm, op, li, raw), pts in st.items():
        la = sum(q[0] for q in pts) / len(pts)
        lo = sum(q[1] for q in pts) / len(pts)
        by_name.setdefault(nm, []).append((la, lo, op, li, raw))
    print("N02 の駅（範囲内）: 名前 %d 種 / のべ %d 件"
          % (len(by_name), sum(len(v) for v in by_name.values())))

    # ---- 手元のノードと突き合わせる ----
    out_st, miss = {}, []
    for i, nd in enumerate(nodes):
        if not nd.get("v"):
            continue
        cands = by_name.get(norm(nd["n"]), [])
        best, bd, bname = None, 1e9, None
        for la, lo, op, li, raw in cands:
            d = km((la, lo), (nd["y"], nd["x"]))
            if d < bd:
                bd, best, bname = d, (la, lo), raw
        if best is None or bd > MAX_MATCH_KM:
            miss.append((nd["n"], round(bd, 1) if best else None))
            continue
        lines = len({(op, li) for la, lo, op, li, raw in cands if km((la, lo), best) < 1.2})
        # **名前も N02 のものを使う**（ODPT の駅名は公開物に出さない）
        out_st[i] = [round(best[0], 5), round(best[1], 5), bname, lines]
    vis = sum(1 for nd in nodes if nd.get("v"))
    print("突き合わせ: %d / %d 駅（%.1f%%）" % (len(out_st), vis, 100.0 * len(out_st) / vis))
    if miss:
        print("  合わなかった %d 駅: %s" % (len(miss), "・".join(m[0] for m in miss[:40])))

    # ---- N02 の線 ----
    # 線は**この 10 社ぶんだけ**（探索に入っている事業者）。新幹線（種別 1）は出さない。
    # 表示しない会社の線まで描くと「ここも終電が出るのか」と読めてしまう。
    # 色の一覧を組む（路線ごとの色 ＋ 事業者ごとの予備）
    cols, cpos = [], {}
    def color_idx(c):
        if c not in cpos:
            cpos[c] = len(cols)
            cols.append(c)
        return cpos[c]
    op_idx = {op: color_idx(c) for op, _, c in OP_FALLBACK}
    odpt_idx = {t: color_idx(PALETTE[k]) for t, k in ODPT_LINE.items() if k in PALETTE}
    tol = TOL_M / 111000.0
    li_out, raw, kept, unknown = [], 0, 0, set()
    inside = lambda c: BBOX[0] <= c[0] <= BBOX[2] and BBOX[1] <= c[1] <= BBOX[3]
    for ft in load(z, "RailroadSection"):
        pr = ft["properties"]
        if pr["N02_004"] not in op_idx or pr["N02_002"] == "1":
            continue
        key = N02_LINE.get((pr["N02_004"], pr["N02_003"]))
        ci = color_idx(PALETTE[key]) if key in PALETTE else op_idx[pr["N02_004"]]
        cs = ft["geometry"]["coordinates"]
        raw += len(cs)
        # 枠からはみ出すところで切る（枠外だけの切れ端は捨てる）
        run, used = [], False
        for c in cs:
            if inside(c):
                run.append((c[0], c[1]))
            else:
                if len(run) > 1:
                    li_out.append((ci, dp(run, tol)))
                    used = True
                run = []
        if len(run) > 1:
            li_out.append((ci, dp(run, tol)))
            used = True
        if used and key is None:        # 枠の中に出てくるのに色を決めていない路線
            unknown.add((pr["N02_004"], pr["N02_003"]))
    flat_out = []
    for ci, pts in li_out:
        kept += len(pts)
        f = [ci]
        for lo, la in pts:
            f += [round(la, 5), round(lo, 5)]
        flat_out.append(f)
    li_out = flat_out
    print("路線の線: %d 本  点 %d → %d（10 社・新幹線を除く）  色 %d 種"
          % (len(li_out), raw, kept, len(cols)))
    if unknown:
        print("  色が決まっていない路線（事業者の色にした）: %s"
              % "・".join("%s %s" % u for u in sorted(unknown)))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rail.json").write_text(
        json.dumps({"st": out_st, "li": li_out, "co": cols,
                    "odpt": odpt_idx,
                    "op": {o: op_idx[n] for n, o, _ in OP_FALLBACK}},
                   separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print("書きました: %s  %.1f KB"
          % (OUT / "rail.json", (OUT / "rail.json").stat().st_size / 1024))


if __name__ == "__main__":
    sys.setrecursionlimit(10000)
    main()
