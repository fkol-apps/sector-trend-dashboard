# セクター別トレンド銘柄ダッシュボード

日本株（TOPIX500相当）と米国株（S&P500）を、GICS 11セクター × 3つの時間軸（長期／中期／短期）の
タイルで俯瞰するためのツールです。

> **本ツールは過去の値動きに基づく機械的なスクリーニング結果であり、投資助言ではありません。投資判断はご自身の責任で行ってください。**

個人の投資判断における「一次スクリーニング」用であり、売買を推奨するものではありません。

## 構成

```
.
├── config.yaml                 # 重み・しきい値・フィルタ条件（調整はここだけ）
├── data/
│   ├── sector_map.json         # GICS 11セクター定義／東証33業種→GICS／銘柄別の上書き
│   ├── universe_jp.csv         # 日本株ユニバース（ticker,name,sector）
│   └── universe_us.csv         # 米国株ユニバース（ticker,name,sector）
├── scripts/
│   ├── fetch_universe.py       # ユニバースCSVの生成（JPX / Wikipedia）
│   └── build_data.py           # 株価取得＋スコア算出＋JSON出力（Step2以降）
├── docs/                       # GitHub Pages で配信する静的サイト
│   ├── index.html
│   └── data/{jp,us}.json       # フロントが読む唯一のデータ
├── tests/                      # スコア算出ロジックの単体テスト
└── .github/workflows/          # 平日 JST 7:00 の定期実行
```

アーキテクチャはサーバーレスです。Python スクリプトが JSON を生成してリポジトリにコミットし、
フロントエンドは自分の JSON だけを読みます。**フロントから外部APIは一切叩きません。**

## セットアップ

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # macOS/Linux
```

## ユニバースの編集

`data/universe_jp.csv` / `data/universe_us.csv` は `ticker,name,sector` の3列です。
そのまま手で編集して銘柄を足し引きできます（`sector` は GICS 11セクターの英語キー）。

自動生成し直す場合:

```bash
.venv/Scripts/python.exe scripts/fetch_universe.py --market both
```

- 日本株: JPX「東証上場銘柄一覧」(`data_j.xlsx`) から **内国株式 かつ 規模区分が TOPIX Core30 / Large70 / Mid400**
  ＝ TOPIX500相当を抽出し、33業種区分を `data/sector_map.json` で GICS 11セクターに寄せます。
- 米国株: Wikipedia の S&P 500 構成銘柄表から Symbol / Security / GICS Sector を取得します。
  `BRK.B` のようなティッカーは yfinance 形式の `BRK-B` に変換します。

再実行するとCSVは**上書き**されます。手編集を残したい場合はコミットしてから実行してください。

### セクター分類の直し方

`data/sector_map.json` の2箇所で調整します。

- `tse33_to_gics`: 東証33業種 → GICS 11セクターの既定マッピング。
- `ticker_overrides`: 業種区分と実態がずれる銘柄を個別に上書き（例: `"9432.T": "Communication Services"`）。
  既定マッピングより優先されます。日本株は `7203.T` 形式、米国株は `AAPL` 形式で書きます。

## 重みの調整

`config.yaml` の `weights` を編集します。長期／中期／短期それぞれについて、各指標の重みを指定します。
合計が1でなくても内部で正規化されます。フィルタ条件（売買代金・株価・上場期間）や
RSIのしきい値、抽出銘柄数（`scoring.top_n`）も同じファイルにあります。

## データ生成（Step2以降で実装）

```bash
.venv/Scripts/python.exe scripts/build_data.py --market jp
.venv/Scripts/python.exe scripts/build_data.py --market us
```

## フロントエンドの確認（Step4以降で実装）

`docs/index.html` をブラウザで開くだけで動きます（ビルド不要）。

## 仕様上の判断メモ

作業中に仕様が明示されていなかった箇所について、以下のデフォルトを選びました。

| 項目 | 判断 | 理由 |
| --- | --- | --- |
| 日本株ユニバースの母集団 | JPXの規模区分で TOPIX500 を抽出 | 日経225やJPX日経400は機械可読な一次配布がなく、TOPIX500はJPX配布ファイルの `規模区分` 列だけで確定できる |
| 市場区分の絞り込み | プライムではなく「内国株式」で絞る | 市場再編後、TOPIX500構成銘柄の一部がスタンダードに在籍しているため（現時点で3銘柄） |
| ユニバースCSVの列 | `ticker,name,sector` の3列に固定 | 仕様どおり。元の33業種区分は保持していないので、分類を直すときは `sector_map.json` 側を見る |
| `sector` 列の値 | GICS英語キー（`Information Technology` 等） | 日米で軸を揃えるため。画面表示用の日本語名は `sector_map.json` の `ja` を使う |
| 日本株の銘柄名 | NFKC正規化して全角英数字を半角に | JPX原本が `ＩＮＰＥＸ` のような全角表記のため |
| JPXファイルのURL | 一覧ページからリンクを解決し、失敗時のみ既知URLにフォールバック | JPX側でファイル名・拡張子が変わる（実際に `.xls` → `.xlsx` に変更済み） |
