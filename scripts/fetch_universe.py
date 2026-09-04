"""銘柄ユニバースCSV（data/universe_jp.csv, data/universe_us.csv）を生成する。

日本株: JPXの「東証上場銘柄一覧」(data_j.xlsx) から 内国株式 かつ
        規模区分が TOPIX Core30 / Large70 / Mid400 の銘柄 = TOPIX500 相当を抽出し、
        33業種区分を data/sector_map.json で GICS 11セクターに寄せる。
米国株: Wikipedia の S&P 500 構成銘柄表から Symbol / Security / GICS Sector を取得する。

生成物はリポジトリにコミットして使う。構成銘柄を手で足し引きしたい場合は
CSV を直接編集してよい（このスクリプトを再実行すると上書きされる点に注意）。

    python scripts/fetch_universe.py --market both
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
SECTOR_MAP_PATH = ROOT / "data" / "sector_map.json"

# JPXは配布ファイル名・拡張子をときどき変えるため、一覧ページからリンクを解決する
JPX_INDEX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
JPX_FALLBACK_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities/misc/"
    "tvdivq0000001vg2-att/data_j.xlsx"
)
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# TOPIX500 に相当する規模区分
TOPIX500_TIERS = ("TOPIX Core30", "TOPIX Large70", "TOPIX Mid400")

# S&P500 は同一企業の複数株式クラスを含み503ティッカーある。
# 同じ会社がセクター上位を2枠占めてしまうため、流動性の低い方のクラスを落として500社にする。
US_SECONDARY_SHARE_CLASSES = ("GOOG", "FOX", "NWS")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) sector-trend-dashboard/0.1 "
    "(+https://github.com/fkol-apps)"
)


def load_sector_map() -> dict:
    with SECTOR_MAP_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def http_get(url: str, timeout: int = 60) -> bytes:
    res = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    res.raise_for_status()
    return res.content


def resolve_jpx_url() -> str:
    """一覧ページから data_j.xls(x) の実URLを拾う。失敗したら既知のURLにフォールバック。"""
    try:
        html = http_get(JPX_INDEX_URL).decode("utf-8", errors="replace")
        hits = re.findall(r'href="([^"]*data_j\.xlsx?)"', html)
        if hits:
            return urljoin(JPX_INDEX_URL, hits[0])
        print("[jp] 一覧ページに data_j へのリンクが見つかりません。既知のURLを使います")
    except Exception as e:
        print(f"[jp] 一覧ページの取得に失敗（{e}）。既知のURLを使います")
    return JPX_FALLBACK_URL


def build_jp(sector_map: dict) -> pd.DataFrame:
    url = resolve_jpx_url()
    print(f"[jp] downloading {url}")
    raw = http_get(url)
    df = pd.read_excel(io.BytesIO(raw))
    print(f"[jp] 上場銘柄一覧: {len(df)} 行")

    required = {"コード", "銘柄名", "市場・商品区分", "33業種区分", "規模区分"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"JPXファイルの列構成が想定と違います: 不足={sorted(missing)} 実際={list(df.columns)}")

    # 市場再編で一部の TOPIX500 構成銘柄がスタンダードにいるため、市場ではなく内国株式かどうかで絞る
    df = df[df["市場・商品区分"].astype(str).str.contains("内国株式")]
    df = df[df["規模区分"].isin(TOPIX500_TIERS)]
    print(f"[jp] 内国株式 × TOPIX500規模区分: {len(df)} 銘柄")

    tse33 = sector_map["tse33_to_gics"]
    overrides = sector_map["ticker_overrides"]

    rows = []
    unmapped = set()
    for _, r in df.iterrows():
        ticker = f"{str(r['コード']).strip()}.T"
        industry = str(r["33業種区分"]).strip()
        sector = tse33.get(industry)
        if sector is None:
            unmapped.add(industry)
            continue
        sector = overrides.get(ticker, sector)
        # JPXの銘柄名は全角英数字混じりなので NFKC で半角に寄せる（漢字・カナはそのまま）
        name = unicodedata.normalize("NFKC", str(r["銘柄名"]).strip())
        rows.append({"ticker": ticker, "name": name, "sector": sector})

    if unmapped:
        print(f"[jp] 警告: sector_map.json に無い33業種区分をスキップしました: {sorted(unmapped)}")

    out = pd.DataFrame(rows).sort_values("ticker").reset_index(drop=True)
    return out


def build_us(sector_map: dict) -> pd.DataFrame:
    print(f"[us] downloading {SP500_URL}")
    html = http_get(SP500_URL).decode("utf-8", errors="replace")
    tables = pd.read_html(io.StringIO(html), match="Symbol")
    if not tables:
        raise RuntimeError("Wikipedia から S&P500 構成銘柄の表を取得できませんでした")
    df = tables[0]
    print(f"[us] 構成銘柄表: {len(df)} 行")

    required = {"Symbol", "Security", "GICS Sector"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"Wikipediaの表の列構成が想定と違います: 不足={sorted(missing)} 実際={list(df.columns)}")

    valid_sectors = {s["key"] for s in sector_map["gics_sectors"]}
    overrides = sector_map["ticker_overrides"]

    rows = []
    unknown = set()
    dropped = []
    for _, r in df.iterrows():
        # BRK.B / BF.B は yfinance では BRK-B / BF-B
        ticker = str(r["Symbol"]).strip().replace(".", "-")
        if ticker in US_SECONDARY_SHARE_CLASSES:
            dropped.append(ticker)
            continue
        sector = str(r["GICS Sector"]).strip()
        if sector not in valid_sectors:
            unknown.add(sector)
            continue
        sector = overrides.get(ticker, sector)
        rows.append({"ticker": ticker, "name": str(r["Security"]).strip(), "sector": sector})

    if unknown:
        print(f"[us] 警告: GICS 11セクターに無い分類をスキップしました: {sorted(unknown)}")
    if dropped:
        print(f"[us] 同一企業の2本目の株式クラスを除外しました: {dropped}")

    out = pd.DataFrame(rows).drop_duplicates("ticker").sort_values("ticker").reset_index(drop=True)
    return out


def summarize(label: str, df: pd.DataFrame) -> None:
    print(f"\n[{label}] 合計 {len(df)} 銘柄")
    counts = df["sector"].value_counts().sort_index()
    for sector, n in counts.items():
        print(f"    {sector:<24} {n:>4}")


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")
    print(f"    -> {path.relative_to(ROOT)} を書き出しました")


def main() -> int:
    p = argparse.ArgumentParser(description="銘柄ユニバースCSVを生成する")
    p.add_argument("--market", choices=["jp", "us", "both"], default="both")
    args = p.parse_args()

    sector_map = load_sector_map()
    failed = []

    if args.market in ("jp", "both"):
        try:
            jp = build_jp(sector_map)
            summarize("jp", jp)
            write_csv(jp, ROOT / "data" / "universe_jp.csv")
        except Exception as e:  # 片方が落ちても他方は処理する
            print(f"[jp] 取得に失敗しました: {e}", file=sys.stderr)
            failed.append("jp")

    if args.market in ("us", "both"):
        try:
            us = build_us(sector_map)
            summarize("us", us)
            write_csv(us, ROOT / "data" / "universe_us.csv")
        except Exception as e:
            print(f"[us] 取得に失敗しました: {e}", file=sys.stderr)
            failed.append("us")

    if failed:
        print(f"\n失敗: {failed} — 既存のCSVはそのまま残しています", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
