# shuden-after

首都圏の鉄道の**終電**と、終電を逃したあとの移動を地図に出す Web アプリ。

このアプリは**サーバを使わない**。`site/` を静的に配るだけで動く。

```bash
python3 -m http.server 8003 --directory site     # http://127.0.0.1:8003/
```

`site/` は git に入っているので、作り直さなくても動く。

### 作り直すとき

```bash
python3 scripts/fetch_odpt.py       # 駅・路線・列車時刻表を取得（要トークン、下記）
python3 scripts/build_base.py       # 駅・路線 → web/data/base.json
python3 scripts/build_land.py       # 海岸線・県境 → web/data/land.json
python3 scripts/pick_spots.py       # 終電を逃しうる 20 駅 → web/data/spots.json
python3 scripts/pick_homes.py       # 自宅に選べる 10 駅 → web/data/homes.json
python3 scripts/build_roadmatrix.py # 駅間の道路距離 → data/road_m.bin（要 OSRM）
python3 scripts/build_taxipaths.py  # タクシー区間の道の形 → data/taxi_paths.bin（要 OSRM）
python3 scripts/build_static.py     # 答えを全部計算 → site/data/（3 分ほど）
python3 scripts/build_stars.py      # 夜空（実際の星と星座線）→ site/data/stars.json

python3 scripts/verify_static.py 60 # site/ の答えとその場の計算が一致するか
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
  build_base.py        駅ノード・路線の線・ラインカラー → web/data/base.json
  build_land.py        国土数値情報 N03 から海岸線と県境 → web/data/land.json
  pick_spots.py        終電を逃しうる 20 駅 → web/data/spots.json
  pick_homes.py        自宅に選べる 10 駅 → web/data/homes.json
  build_roadmatrix.py  OSRM の /table で駅間の道路距離行列 → data/road_m.bin
  build_taxipaths.py   OSRM の /route でタクシー区間の道の形 → data/taxi_paths.bin
  build_static.py      答えを全部計算して site/data/ に書く
  build_stars.py       Yale Bright Star Catalogue と星座線 → site/data/stars.json
  verify_static.py     site/ の答えがその場の計算と一致するか確かめる
server.py              開発用。site/ の配信と /api/night（答え合わせ用）
site/index.html        描画と操作。Canvas 2D だけで、地図タイルもライブラリも使わない
site/data/home/*.bin   自宅ごとの答え（1 ファイル 35KB ほど）
.github/workflows/     site/ を GitHub Pages に出す
```

### 配るもの

| | |
|---|---|
| 最初に落ちるもの | **252KB**（画面 57KB・地図 85KB・海岸線 54KB・星 22KB・自宅 1 件 35KB ほか） |
| 自宅を変えたとき | **その自宅のファイル 1 つだけ**（31〜41KB） |
| 時刻を変えたとき | **通信なし**（手元のデータだけで引ける） |
| `site/` 全体 | **624KB / 18 ファイル** |

### タクシー運賃

東京都特別区・武蔵野市・三鷹市（武三交通圏）の普通車の認可運賃で概算している。

| | |
|---|---|
| 初乗り | 最初の 1.0km まで 500 円 |
| 加算 | 以後 232m ごとに 100 円 |
| 深夜早朝割増 | 22 時〜5 時は 2 割増 |

出典: [東京無線協同組合「認可運賃表」](https://www.tokyomusen.or.jp/pay/carriage)

**時間距離併用運賃（時速 10km 以下の走行 85 秒ごとに 100 円）と高速代は見ていない**ので、
出てくる金額は実際より安め。営業区域の外（横浜・大宮など）も同じ運賃で計算している。

降りる駅の候補は、**道路距離を測ってある 829 駅に限る**。`data/road_m.bin` は東京駅から
50km 以内ぶんしかなく、それ以外は直線×1.35 で代用していたが、運賃はこのアプリの主役の
数字なので測っていない距離で出さない（直線近似は 2 割ほど高く出る）。
この制限で答えが変わるのは 33,120 通り中 3 通り（相模線の香川・宮山・寒川を自宅にしたとき）。

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
| 駅・路線・列車時刻表 | [公共交通オープンデータセンター](https://www.odpt.org/) | 事業者ごとに異なる（下記） |
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

**配るのは「答え」だけで、時刻表は配らない。**

| | 置き場所 | git |
|---|---|---|
| 列車時刻表（25,671 便） | `data/raw/` | **除外** |
| 駅間の道路距離行列・タクシー経路の元データ | `data/` | **除外** |
| 中間データ（base/land/spots の生成先） | `web/data/` | **除外** |
| **自宅ごとの答え**（終電リミット・降りる駅・運賃・経路の形） | `site/data/home/` | 入れる |
| 駅と路線の線・海岸線・候補駅 | `site/data/` | 入れる |

自宅ごとのファイルに入っているのは、**自宅 1 駅 × 候補 20 駅 × 時刻 85 通り**に対する
終電リミット・降りる駅・到着時刻・道路距離・運賃と、その経路の駅の並びだけ。
**ここから 25,671 便の時刻表を復元することはできない。**

**駅の名前は、画面に名前が出る駅ぶんしか配らない。**
地図の下地（`site/data/base.json`）に名前が入っているのは **174 駅**
（自宅の候補 10・出発する 20・降りる駅・乗り降りと乗換の駅）で、
残り 1,058 駅は**名前を空にしてある**（座標とつながりは線を描くのに要るので残す）。
`scripts/build_static.py` が書き出すときに落としている。

⚠ **それでも座標と路線のつながりは残る。**
駅と路線の情報を公開することになるので、**公開前に各事業者のライセンスを確認すること。**

画面下に、提供データである旨・正確性を保証しない旨・交通事業者へ直接問い合わせない旨・
静的データの取得日を出している（開発者ガイドライン 3.1 / 2.2.1）。消さないこと。
**ガイドライン 2.2.2 はデータ更新の通知から 1 週間以内の更新を求めているが、
先に計算した静的サイトは自動では追随しない。**作り直して push する運用が要る。

## 分かっている制約

- 画面は **PC が主**。820px 未満では右の一覧を出さない（地図の札はタップできる）。
  地図の余白はパネルの実際の位置から取るので、幅 390 でも札は読める（20 駅だと都心は重なる）。
- 平日ダイヤのみ。乗換は一律 5 分。
- **深夜バス（深夜急行バス）は GTFS に無いので考慮していない。**
  手元の GTFS では 24 時台の発車が 都営バス 0 本・京王バス 19・関東バス 10 しかない。
  実際には深夜バスで帰れる区間があり、そのぶん**運賃を高く見積もっている**。
- 東急・小田急・京急・西武は `odpt:TrainTimetable` が 0 件でネットワークに入っていない。
- タクシーの待ち時間・乗車拒否・配車アプリの迎車料金は見ていない。
- 運賃は東京の 1 地区の認可運賃で全域を計算している（地区ごとの運賃差は未反映）。
