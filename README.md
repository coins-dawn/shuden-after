# shuden-after

首都圏の鉄道データを地図に出す Web アプリ。**サーバを使わない**。
`site/` を静的に配るだけで動く（Canvas 2D だけで描いていて、地図タイルもライブラリも使わない）。

```bash
python3 -m http.server 8003 --directory site     # http://127.0.0.1:8003/
```

`site/` は git に入っているので、作り直さなくても動く。

### 作り直すとき

```bash
python3 scripts/fetch_odpt.py       # 駅・路線・列車時刻表を取得（要トークン、下記）
python3 scripts/build_base.py       # 駅・路線（探索用の内部データ）→ web/data/base.json
python3 scripts/build_rail.py       # 地図の下地（N02 の駅と線）→ web/data/rail.json
python3 scripts/build_land.py       # 海岸線・県境 → web/data/land.json
python3 scripts/pick_spots.py       # 候補の駅 20 → web/data/spots.json
python3 scripts/pick_homes.py       # 起点に選べる駅 10 → web/data/homes.json
python3 scripts/build_roadmatrix.py # 駅間の道路距離 → data/road_m.bin（要 OSRM）
python3 scripts/build_taxipaths.py  # 道路の経路の形 → data/taxi_paths.bin（要 OSRM）
python3 scripts/build_static.py     # 表示に使う値を先に全部計算 → site/data/（3 分ほど）
python3 scripts/build_stars.py      # 夜空（実際の星と星座線）→ site/data/stars.json

python3 scripts/verify_static.py 60 # site/ の値とその場の計算が一致するか
python3 server.py 8003              # 開発用。/api/night で任意の条件を試せる
```

`fetch_odpt.py` はワークスペース直下の `.env` から `ODPT_ACCESS_TOKEN` と
`ODPT_CHALLENGE_TOKEN` を読む。値はコードにもログにも書かない。

`build_roadmatrix.py` には OSRM のグラフが要る（手順はそのファイルの先頭に書いてある）。
**無い場合は直線距離 × 1.35 で代用する**ので、まず動かすだけなら省略してよい。

## 構成

```
scripts/
  fetch_odpt.py        ODPT から駅・路線・列車時刻表（平日ダイヤ）を取る
  network.py           時刻表ベースの探索ネットワークと RAPTOR
  build_base.py        探索用の駅ノード（**公開しない内部データ**）→ web/data/base.json
  build_rail.py        地図の下地を国土数値情報 N02 から作る → web/data/rail.json
  build_land.py        国土数値情報 N03 から海岸線と県境 → web/data/land.json
  pick_spots.py        候補の駅 20 → web/data/spots.json
  pick_homes.py        起点に選べる駅 10 → web/data/homes.json
  build_roadmatrix.py  OSRM の /table で駅間の道路距離行列 → data/road_m.bin
  build_taxipaths.py   OSRM の /route で道路の経路の形 → data/taxi_paths.bin
  build_static.py      表示に使う値を先に全部計算して site/data/ に書く
  build_stars.py       Yale Bright Star Catalogue と星座線 → site/data/stars.json
  verify_static.py     site/ の答えがその場の計算と一致するか確かめる
server.py              開発用。site/ の配信と /api/night（答え合わせ用）
site/index.html        描画と操作。Canvas 2D だけで、地図タイルもライブラリも使わない
site/data/home/*.bin   起点ごとの計算結果（1 ファイル 35KB ほど）
.github/workflows/     site/ を GitHub Pages に出す
```

### 配るもの

| | |
|---|---|
| 最初に落ちるもの | **400KB**（地図 199KB・画面 84KB・海岸線 54KB・星 22KB・起点 1 件 35KB ほか）。**gzip で 105KB** |
| 起点を変えたとき | **その起点のファイル 1 つだけ**（31〜41KB） |
| 時刻を変えたとき | **通信なし**（手元のデータだけで引ける） |
| `site/` 全体 | **747KB / 18 ファイル** |

## 公開について

`site/` の中身をそのまま GitHub Pages に出す（`.github/workflows/pages.yml`。
`master` か `main` への push で動く）。相対パスだけで書いてあるので、
`https://<user>.github.io/<repo>/` のようなサブパス配信でも動く。

⚠ **公開する前に、下の「データの出典とライセンス」と「再配布についての注意」を必ず読むこと。**
画面に出している出典・取得日・問い合わせ先の表示（開発者ガイドライン 3.1 / 2.2.1）は**消さない**。

`site/index.html` に `<meta name="robots" content="noindex">` を入れてある
（検索結果には出さず、URL を知っている人だけが見る想定）。外すときはここ。

## データの出典とライセンス

| データ | 提供元 | ライセンス |
|---|---|---|
| 列車時刻表 | [公共交通オープンデータセンター](https://www.odpt.org/) | 事業者ごとに異なる（下記） |
| 駅の位置・名前と路線の線 | [国土数値情報 鉄道 N02（2025年）](https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N02-v3_1.html) | 国土数値情報 利用約款 |
| 星 | [Yale Bright Star Catalogue, 5th Revised Ed.](https://cdsarc.cds.unistra.fr/viz-bin/cat/V/50)（Hoffleit & Warren 1991 / VizieR V/50） | 出典表示 |
| 星座線 | [d3-celestial](https://github.com/ofrohn/d3-celestial) | BSD 3-Clause（下記） |
| 海岸線・県境 | [国土数値情報 行政区域 N03](https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-v3_1.html) | 国土数値情報 利用約款 |
| 道路網 | [OpenStreetMap](https://www.openstreetmap.org/) | ODbL |

- **東京都交通局**: CC BY 4.0
- **東京メトロ・つくばエクスプレス・東京臨海高速鉄道・多摩都市モノレール・横浜市交通局**:
  公共交通オープンデータ基本ライセンス
- **JR東日本・京王電鉄・東武鉄道・相模鉄道**: チャレンジ限定ライセンス

### d3-celestial（星座線）の表示

> Copyright (c) 2015, Olaf Frohn. All rights reserved.
> Redistribution and use in source and binary forms, with or without modification,
> are permitted provided that the conditions of the BSD 3-Clause License are met.

BSD 3-Clause は**著作権表示を残すこと**を条件にしている。
`scripts/build_stars.py` と画面の「このサービスについて」にも出してある。**消さないこと。**

### 再配布についての注意

基本ライセンス第 8 条 4 項 (1) は、**元のデータの大部分を復元できる派生データ**を
第三者が再利用可能な状態で公開・再配布することを禁じている（チャレンジ限定ライセンスにも同条項）。

**配るのは計算結果だけで、時刻表は配らない。**

| | 置き場所 | git |
|---|---|---|
| 列車時刻表（25,671 便） | `data/raw/` | **除外** |
| 駅間の道路距離行列・道路の経路の元データ | `data/` | **除外** |
| 中間データ（base/land/spots の生成先） | `web/data/` | **除外** |
| **起点ごとの計算結果** | `site/data/home/` | 入れる |
| 地図の下地（N02）・海岸線・候補駅 | `site/data/` | 入れる |

起点ごとのファイルに入っているのは、**起点 1 × 候補 20 × 時刻 85 通り**に対する
探索結果の要約（時刻・駅・距離・金額と、通る駅の並び）だけ。
**ここから 25,671 便の時刻表を復元することはできない。**

**地図の下地に ODPT のデータは使わない。**
`site/data/base.json`（駅の位置・名前と路線の線）は**国土数値情報 N02（鉄道）から作る**
（`scripts/build_rail.py`）。N02 は国土数値情報利用約款で再配布できる。

- 駅の位置と名前 … N02 の駅（手元の 829 駅は**全部 N02 と突き合わせられた**）
- 路線の線 … N02 の鉄道区間の**実際の線形**。探索に入っている 10 社ぶんだけ（新幹線は除く）
- 路線の色 … N02 に色は無いので**自前の配色**（事業者ごと）

⚠ **`odpt:Station` と `odpt:Railway` は配らない。** 探索には使うが、公開物には出さない。
`web/data/base.json`（ODPT 由来の内部データ）は `.gitignore` で除外してある。

⚠ **形式を変えても逃げられない。** 第8条4項(1) は「第三者が再利用できる状態での公開」を
禁じていて、**ファイル形式のことは言っていない**。独自のバイナリにしても、読む手順は
画面の JavaScript に書いてあるのだから、再利用できる状態であることは変わらない。

画面下に、提供データである旨・正確性を保証しない旨・交通事業者へ直接問い合わせない旨・
静的データの取得日を出している（開発者ガイドライン 3.1 / 2.2.1）。消さないこと。
**ガイドライン 2.2.2 はデータ更新の通知から 1 週間以内の更新を求めているが、
先に計算した静的サイトは自動では追随しない。**作り直して push する運用が要る。
