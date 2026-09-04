"""株価を取得してスコアを算出し、フロントが読む JSON を書き出す。

    python scripts/build_data.py --market jp
    python scripts/build_data.py --market us
    python scripts/build_data.py --market jp --tickers 7203.T,6758.T --dry-run

一部銘柄の取得に失敗しても全体は完走し、失敗した銘柄はログと JSON の failed に残す。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.trend_core import (  # noqa: E402
    INDICATOR_LABELS,
    TIMEFRAME_LABELS,
    TIMEFRAME_RETURN,
    compute_metrics,
    score_timeframe,
    sma,
)

JST = timezone(timedelta(hours=9))


# --------------------------------------------------------------------------
# 入出力
# --------------------------------------------------------------------------
def load_config() -> dict:
    with (ROOT / "config.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_sector_map() -> dict:
    with (ROOT / "data" / "sector_map.json").open(encoding="utf-8") as f:
        return json.load(f)


def load_universe(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str).dropna(subset=["ticker"])
    df["ticker"] = df["ticker"].str.strip()
    df["name"] = df["name"].fillna(df["ticker"]).str.strip()
    df["sector"] = df["sector"].str.strip()
    return df.drop_duplicates("ticker").reset_index(drop=True)


# --------------------------------------------------------------------------
# 価格取得
# --------------------------------------------------------------------------
def _extract(df: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """yfinance の戻り値から1銘柄分の Close/Volume を取り出す。"""
    if df is None or len(df) == 0:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        if ticker in df.columns.get_level_values(0):
            sub = df[ticker]
        elif ticker in df.columns.get_level_values(1):
            sub = df.xs(ticker, axis=1, level=1)
        else:
            return None
    else:
        sub = df
    if "Close" not in sub.columns:
        return None
    out = sub[["Close", "Volume"]].dropna(subset=["Close"]) if "Volume" in sub.columns else None
    if out is None:
        out = sub[["Close"]].dropna()
        out["Volume"] = 0.0
    return out if len(out) else None


def download_prices(tickers: list[str], cfg: dict, log=print) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """バッチ + リトライで日足を取得する。失敗した銘柄はスキップして一覧を返す。"""
    import yfinance as yf

    d = cfg["data"]
    prices: dict[str, pd.DataFrame] = {}
    failed: list[str] = []
    batches = [tickers[i : i + d["batch_size"]] for i in range(0, len(tickers), d["batch_size"])]

    for bi, batch in enumerate(batches, 1):
        raw = None
        for attempt in range(1, d["max_retries"] + 1):
            try:
                raw = yf.download(
                    batch,
                    period=d["download_period"],
                    interval="1d",
                    auto_adjust=True,
                    group_by="ticker",
                    progress=False,
                    threads=True,
                )
                break
            except Exception as e:  # ネットワーク・レート制限
                wait = d["retry_backoff"] * attempt
                log(f"  バッチ{bi}/{len(batches)} 取得失敗 (試行{attempt}): {e} -> {wait:.0f}秒待機")
                time.sleep(wait)

        got = 0
        for t in batch:
            sub = _extract(raw, t) if raw is not None else None
            if sub is None:
                failed.append(t)
            else:
                prices[t] = sub
                got += 1
        log(f"  バッチ{bi}/{len(batches)}: {got}/{len(batch)} 銘柄取得")
        if bi < len(batches):
            time.sleep(d["sleep_between_batches"])

    # 失敗分だけ1銘柄ずつ拾い直す（バッチ内の1銘柄が原因で落ちるケースの救済）
    if failed:
        log(f"  失敗 {len(failed)} 銘柄を個別に再取得します")
        retry, failed = failed, []
        for t in retry:
            try:
                raw = yf.download(
                    t, period=d["download_period"], interval="1d",
                    auto_adjust=True, progress=False, threads=False,
                )
                sub = _extract(raw, t)
            except Exception:
                sub = None
            if sub is None:
                failed.append(t)
            else:
                prices[t] = sub
            time.sleep(0.4)

    return prices, failed


# --------------------------------------------------------------------------
# 集計
# --------------------------------------------------------------------------
def build_metrics_frame(prices: dict[str, pd.DataFrame], cfg: dict, log=print) -> pd.DataFrame:
    rows = {}
    for t, df in prices.items():
        hist = df.tail(cfg["data"]["history_days"])
        m = compute_metrics(hist["Close"], hist["Volume"], cfg["windows"], cfg["scoring"]["rsi"])
        if m is None:
            log(f"  {t}: データ不足でスキップ")
            continue
        m["last_date"] = hist.index[-1].strftime("%Y-%m-%d")
        rows[t] = m
    return pd.DataFrame.from_dict(rows, orient="index")


def apply_filters(metrics: pd.DataFrame, cfg: dict, market: str, log=print) -> pd.DataFrame:
    f = cfg["filters"]
    mf = f[market]
    before = len(metrics)

    cond_days = metrics["trading_days"] >= f["min_trading_days"]
    cond_turnover = metrics["turnover"] >= mf["min_avg_turnover"]
    cond_price = metrics["price"] >= mf["min_price"]

    log(f"  フィルタ: 上場{f['min_trading_days']}日未満 {int((~cond_days).sum())}件 / "
        f"売買代金 {mf['min_avg_turnover']:,} 未満 {int((~cond_turnover).sum())}件 / "
        f"株価 {mf['min_price']} 未満 {int((~cond_price).sum())}件 を除外")

    out = metrics[cond_days & cond_turnover & cond_price].copy()
    log(f"  スコア算出対象: {len(out)} / {before} 銘柄")
    return out


def score_all(metrics: pd.DataFrame, cfg: dict) -> dict:
    """時間軸ごとに z-score 合成し、スコア・z・寄与を返す。"""
    clip = cfg["scoring"]["zscore_clip"]
    return {tf: score_timeframe(metrics, weights, clip) for tf, weights in cfg["weights"].items()}


def pick_top(metrics: pd.DataFrame, universe: pd.DataFrame, scored: dict, cfg: dict) -> dict:
    """セクター × 時間軸ごとに上位 top_n 銘柄を選ぶ。足りないセクターは埋めない。"""
    top_n = cfg["scoring"]["top_n"]
    sector_of = universe.set_index("ticker")["sector"].to_dict()
    sectors = sorted({sector_of[t] for t in metrics.index if t in sector_of})

    picks = {}
    for sector in sectors:
        members = [t for t in metrics.index if sector_of.get(t) == sector]
        by_tf = {}
        for tf, res in scored.items():
            # 同点（z-scoreのクリップ上限に張り付いた銘柄同士）はクリップなしスコアで並べ替える
            ranked = (
                pd.DataFrame({"s": res["score"], "tb": res["score_unclipped"]})
                .loc[members]
                .sort_values(["s", "tb"], ascending=False)
            )
            by_tf[tf] = list(ranked.head(top_n).index)
        picks[sector] = {"members": members, "timeframes": by_tf}
    return picks


# --------------------------------------------------------------------------
# JSON 組み立て
# --------------------------------------------------------------------------
def _r(x, nd=2):
    """JSON に入れる数値を丸める。NaN/inf は None にする。"""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def _series_list(s: pd.Series, nd=2) -> list:
    return [_r(v, nd) for v in s.tolist()]


def build_stock_entry(
    ticker: str, universe_row: dict, metrics_row: pd.Series, prices: pd.DataFrame,
    scored: dict, ranks: dict, cfg: dict,
) -> dict:
    out_cfg = cfg["output"]
    w = cfg["windows"]
    hist = prices.tail(cfg["data"]["history_days"])
    close = hist["Close"].astype("float64")

    chart_n = min(out_cfg["chart_days"], len(close))
    chart_close = close.iloc[-chart_n:]
    chart = {
        "dates": [d.strftime("%Y-%m-%d") for d in chart_close.index],
        "close": _series_list(chart_close),
        "sma20": _series_list(sma(close, w["sma_short"]).iloc[-chart_n:]),
        "sma50": _series_list(sma(close, w["sma_mid"]).iloc[-chart_n:]),
        "sma200": _series_list(sma(close, w["sma_long"]).iloc[-chart_n:]),
    }

    scores = {}
    for tf, res in scored.items():
        breakdown = []
        for key, weight in res["weights"].items():
            breakdown.append({
                "key": key,
                "label": INDICATOR_LABELS.get(key, key),
                "raw": _r(metrics_row.get(key), 4),
                "z": _r(res["z"].at[ticker, key], 2),
                "weight": _r(weight, 3),
                "contrib": _r(res["contrib"].at[ticker, key], 3),
            })
        scores[tf] = {
            "score": _r(res["score"].at[ticker], 3),
            "rank": ranks[tf].get(ticker),
            "return": _r(metrics_row.get(TIMEFRAME_RETURN[tf]), 4),
            "breakdown": breakdown,
        }

    return {
        "ticker": ticker,
        "name": universe_row["name"],
        "sector": universe_row["sector"],
        "price": _r(metrics_row["price"]),
        "chg_pct": _r(metrics_row["chg_pct"]),
        "last_date": metrics_row["last_date"],
        "metrics": {
            "rsi14": _r(metrics_row.get("rsi14"), 1),
            "volume_ratio": _r(metrics_row.get("volume_ratio"), 2),
            "turnover": _r(metrics_row.get("turnover"), 0),
            "sma20": _r(metrics_row.get("sma20")),
            "sma50": _r(metrics_row.get("sma50")),
            "sma200": _r(metrics_row.get("sma200")),
            "ret_12m": _r(metrics_row.get("ret_12m"), 4),
            "ret_3m": _r(metrics_row.get("ret_3m"), 4),
            "ret_20d": _r(metrics_row.get("ret_20d"), 4),
        },
        "spark": _series_list(close.iloc[-min(out_cfg["sparkline_days"], len(close)):]),
        "chart": chart,
        "scores": scores,
    }


def build_payload(
    market: str, cfg: dict, sector_map: dict, universe: pd.DataFrame,
    metrics: pd.DataFrame, prices: dict, scored: dict, picks: dict,
    failed: list[str], universe_size: int,
) -> dict:
    ranks = {}
    for tf, res in scored.items():
        # 同点はクリップなしスコアで解いてから順位を付ける
        order = pd.DataFrame({"s": res["score"], "tb": res["score_unclipped"]}).sort_values(
            ["s", "tb"], ascending=False
        )
        ranks[tf] = {t: i for i, t in enumerate(order.index, start=1)}

    uni = universe.set_index("ticker").to_dict(orient="index")
    sector_meta = {s["key"]: s for s in sector_map["gics_sectors"]}

    needed = sorted({t for p in picks.values() for lst in p["timeframes"].values() for t in lst})
    stocks = {
        t: build_stock_entry(t, uni[t], metrics.loc[t], prices[t], scored, ranks, cfg)
        for t in needed
    }

    sectors = []
    for key, p in picks.items():
        meta = sector_meta.get(key, {"key": key, "ja": key, "order": 99})
        sectors.append({
            "key": key,
            "ja": meta["ja"],
            "order": meta["order"],
            "count": len(p["members"]),
            "timeframes": p["timeframes"],
        })
    sectors.sort(key=lambda s: s["order"])

    market_cfg = cfg["markets"][market]
    price_date = max(metrics["last_date"]) if len(metrics) else None

    return {
        "market": market,
        "label": market_cfg["label"],
        "currency": market_cfg["currency"],
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "price_date": price_date,
        "counts": {
            "universe": universe_size,
            "downloaded": len(prices),
            "scored": len(metrics),
            "failed": len(failed),
            "displayed": len(stocks),
        },
        "failed": failed,
        "top_n": cfg["scoring"]["top_n"],
        "timeframe_labels": TIMEFRAME_LABELS,
        "timeframe_return_key": TIMEFRAME_RETURN,
        "indicator_labels": INDICATOR_LABELS,
        "weights": {tf: {k: _r(v, 3) for k, v in res["weights"].items()} for tf, res in scored.items()},
        "sectors": sectors,
        "stocks": stocks,
    }


def write_history_snapshot(market: str, payload: dict, cfg: dict, log=print) -> Path | None:
    """その日の選定結果だけを軽量なJSONで残す。

    表示用のフルJSONは毎日上書きされるが、こちらは1日1ファイルで永久に積み上げる。
    チャート系列は含めない（後から株価を取り直せば再現できるため）。1日あたり約28KB。
    同じ日に再実行した場合は、その市場の分だけ差し替える。
    """
    hcfg = cfg.get("history", {})
    if not hcfg.get("enabled", False):
        return None

    picks = []
    for sector in payload["sectors"]:
        for tf, tickers in sector["timeframes"].items():
            for pos, ticker in enumerate(tickers, start=1):
                stock = payload["stocks"][ticker]
                sc = stock["scores"][tf]
                picks.append({
                    "sector": sector["key"],
                    "tf": tf,
                    "pos": pos,
                    "ticker": ticker,
                    "name": stock["name"],
                    "price": stock["price"],
                    "score": sc["score"],
                    "rank": sc["rank"],
                    "ret": sc["return"],
                })

    entry = {
        "price_date": payload["price_date"],
        "generated_at": payload["generated_at"],
        "scored": payload["counts"]["scored"],
        "failed": payload["counts"]["failed"],
        "picks": picks,
    }

    day = datetime.now(JST).strftime("%Y-%m-%d")
    path = ROOT / hcfg.get("dir", "data/history") / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        with path.open(encoding="utf-8") as f:
            doc = json.load(f)
    else:
        doc = {"date": day, "markets": {}}
    doc["markets"][market] = entry

    with path.open("w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    log(f"  -> {path.relative_to(ROOT)} に選定{len(picks)}件を記録 ({path.stat().st_size / 1024:,.0f} KB)")
    return path


# --------------------------------------------------------------------------
# 検証用のコンソール出力
# --------------------------------------------------------------------------
def print_metrics_table(metrics: pd.DataFrame, universe: pd.DataFrame, scored: dict) -> None:
    names = universe.set_index("ticker")["name"].to_dict()
    cols = ["price", "chg_pct", "ret_12m", "mom_12_1", "ret_3m", "ret_20d",
            "rsi14", "rsi_zone", "volume_ratio", "sma200_slope", "sma50_slope",
            "above_sma200", "above_sma50", "above_sma20", "golden_cross"]
    df = metrics[cols].copy()
    df.insert(0, "name", [names.get(t, "")[:12] for t in df.index])
    for tf in scored:
        df[f"score_{tf}"] = scored[tf]["score"]

    with pd.option_context("display.width", 250, "display.max_columns", 50,
                           "display.float_format", lambda v: f"{v:,.3f}"):
        print("\n=== 指標 ===")
        print(df[["name"] + cols].to_string())
        print("\n=== 合成スコア（高い順: 長期） ===")
        print(df[["name", "score_long", "score_mid", "score_short"]]
              .sort_values("score_long", ascending=False).to_string())


def print_score_breakdown(ticker: str, metrics: pd.DataFrame, scored: dict) -> None:
    print(f"\n=== {ticker} のスコア内訳 ===")
    for tf, res in scored.items():
        print(f"  [{TIMEFRAME_LABELS[tf]}] 合成スコア = {res['score'].at[ticker]:.3f}")
        for key, weight in res["weights"].items():
            raw = metrics.at[ticker, key]
            z = res["z"].at[ticker, key]
            c = res["contrib"].at[ticker, key]
            print(f"      {INDICATOR_LABELS[key]:<22} raw={raw:>9.4f}  z={z:>6.2f}  w={weight:.2f}  寄与={c:>6.3f}")


# --------------------------------------------------------------------------
def run(market: str, args, cfg: dict, sector_map: dict) -> int:
    market_cfg = cfg["markets"][market]
    log = print
    log(f"\n===== {market_cfg['label']} ({market}) =====")

    universe = load_universe(ROOT / market_cfg["universe"])
    if args.tickers:
        wanted = [t.strip() for t in args.tickers.split(",") if t.strip()]
        universe = universe[universe["ticker"].isin(wanted)].reset_index(drop=True)
        missing = set(wanted) - set(universe["ticker"])
        if missing:
            log(f"  ユニバースCSVに無いティッカーを補います: {sorted(missing)}")
            extra = pd.DataFrame([{"ticker": t, "name": t, "sector": "Unknown"} for t in sorted(missing)])
            universe = pd.concat([universe, extra], ignore_index=True)
    if args.limit:
        universe = universe.head(args.limit)
    log(f"  ユニバース: {len(universe)} 銘柄")

    prices, failed = download_prices(universe["ticker"].tolist(), cfg, log)
    if failed:
        log(f"  取得失敗 {len(failed)} 銘柄: {failed}")
    if not prices:
        log("  取得できた銘柄がありません", file=sys.stderr)
        return 1

    metrics = build_metrics_frame(prices, cfg, log)
    if not args.no_filter:
        metrics = apply_filters(metrics, cfg, market, log)
    if metrics.empty:
        log("  フィルタ後に銘柄が残りませんでした", file=sys.stderr)
        return 1

    scored = score_all(metrics, cfg)

    if args.dry_run:
        print_metrics_table(metrics, universe, scored)
        top = scored["long"]["score"].idxmax()
        print_score_breakdown(top, metrics, scored)
        return 0

    picks = pick_top(metrics, universe, scored, cfg)
    payload = build_payload(market, cfg, sector_map, universe, metrics, prices,
                            scored, picks, failed, len(universe))

    out_path = ROOT / (args.output or market_cfg["output"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = out_path.stat().st_size / 1024
    log(f"  -> {out_path.relative_to(ROOT)} ({size_kb:,.0f} KB, "
        f"{len(payload['sectors'])}セクター / {len(payload['stocks'])}銘柄掲載)")

    if not args.no_history:
        write_history_snapshot(market, payload, cfg, log)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="株価を取得してスコアJSONを生成する")
    p.add_argument("--market", choices=["jp", "us", "both"], required=True)
    p.add_argument("--limit", type=int, help="ユニバースの先頭N銘柄だけ処理する（動作確認用）")
    p.add_argument("--tickers", help="カンマ区切りのティッカー指定（動作確認用）")
    p.add_argument("--output", help="出力先の上書き（リポジトリルートからの相対パス）")
    p.add_argument("--dry-run", action="store_true", help="JSONを書かず、指標とスコアを表で出す")
    p.add_argument("--no-filter", action="store_true", help="事前フィルタを適用しない（動作確認用）")
    p.add_argument("--no-history", action="store_true", help="履歴スナップショットを書かない")
    args = p.parse_args()

    cfg = load_config()
    sector_map = load_sector_map()
    markets = ["jp", "us"] if args.market == "both" else [args.market]

    rc = 0
    for m in markets:
        rc |= run(m, args, cfg, sector_map)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
