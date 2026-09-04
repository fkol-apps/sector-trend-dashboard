"""トレンド指標の算出とスコア合成。

ネットワークもファイルI/Oも触らない純粋な計算だけを置く（テスト可能にするため）。
価格は yfinance の auto_adjust=True 前提（分割・配当調整済みの終値）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# 時間軸ごとに使う指標と、画面に出す日本語ラベル
INDICATOR_LABELS = {
    "mom_12_1": "12-1モメンタム",
    "above_sma200": "終値 > 200日SMA",
    "sma200_slope": "200日SMAの傾き",
    "ret_3m": "3ヶ月リターン",
    "golden_cross": "50日SMA > 200日SMA",
    "above_sma50": "終値 > 50日SMA",
    "sma50_slope": "50日SMAの傾き",
    "ret_20d": "20日リターン",
    "above_sma20": "終値 > 20日SMA",
    "rsi_zone": "RSI(14)の位置",
    "volume_ratio": "出来高比(5日/60日)",
}

# タイルに出す「その時間軸のリターン」
TIMEFRAME_RETURN = {"long": "ret_12m", "mid": "ret_3m", "short": "ret_20d"}

TIMEFRAME_LABELS = {"long": "長期", "mid": "中期", "short": "短期"}


# --------------------------------------------------------------------------
# 個別指標
# --------------------------------------------------------------------------
def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder方式のRSI。値動きのない系列では50を返す。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rsi = pd.Series(np.nan, index=close.index, dtype="float64")
    both_zero = (avg_gain == 0) & (avg_loss == 0)
    only_gain = (avg_loss == 0) & (avg_gain > 0)
    normal = avg_loss > 0

    rs = avg_gain[normal] / avg_loss[normal]
    rsi[normal] = 100.0 - 100.0 / (1.0 + rs)
    rsi[only_gain] = 100.0
    rsi[both_zero] = 50.0
    return rsi


def rsi_zone_score(
    rsi: float,
    low_anchor: float = 30.0,
    low: float = 50.0,
    high: float = 70.0,
    overheat_span: float = 15.0,
    penalty_floor: float = -1.0,
) -> float:
    """RSIを「押し目〜順行が最良、過熱は減点」の折れ線スコアに変換する。

        RSI <= low_anchor : 0
        low_anchor〜low   : 0 → 1 に線形増加
        low〜high         : 1（満点）
        high 超           : 1 から線形に下げ、high+overheat_span で penalty_floor
    """
    if rsi is None or not np.isfinite(rsi):
        return np.nan
    if rsi <= low_anchor:
        return 0.0
    if rsi < low:
        return (rsi - low_anchor) / (low - low_anchor)
    if rsi <= high:
        return 1.0
    drop = (rsi - high) / overheat_span * (1.0 - penalty_floor)
    return max(penalty_floor, 1.0 - drop)


def _ratio(series: pd.Series, lag: int, offset: int = 0) -> float:
    """series[-1-offset] / series[-1-offset-lag] - 1。データ不足なら NaN。"""
    n = len(series)
    if n < lag + offset + 1:
        return np.nan
    end = series.iloc[-1 - offset]
    start = series.iloc[-1 - offset - lag]
    if not np.isfinite(end) or not np.isfinite(start) or start <= 0:
        return np.nan
    return float(end / start - 1.0)


def compute_metrics(close: pd.Series, volume: pd.Series, windows: dict, rsi_cfg: dict) -> dict | None:
    """1銘柄の日足から全指標を算出する。データが足りなければ None。"""
    close = pd.Series(close).astype("float64").dropna()
    volume = pd.Series(volume).astype("float64").reindex(close.index).fillna(0.0)
    if len(close) < 2:
        return None

    w = windows
    sma20 = sma(close, w["sma_short"])
    sma50 = sma(close, w["sma_mid"])
    sma200 = sma(close, w["sma_long"])
    rsi = rsi_wilder(close, w["rsi_period"])

    price = float(close.iloc[-1])
    prev = float(close.iloc[-2])

    last = lambda s: float(s.iloc[-1]) if len(s) and np.isfinite(s.iloc[-1]) else np.nan  # noqa: E731
    v20, v50, v200 = last(sma20), last(sma50), last(sma200)
    rsi14 = last(rsi)

    vol_recent = float(volume.iloc[-w["volume_recent"]:].mean()) if len(volume) >= w["volume_recent"] else np.nan
    vol_base = float(volume.iloc[-w["volume_base"]:].mean()) if len(volume) >= w["volume_base"] else np.nan
    volume_ratio = vol_recent / vol_base if vol_base and np.isfinite(vol_base) and vol_base > 0 else np.nan

    turnover_window = min(w["turnover_window"], len(close))
    turnover = float((close.iloc[-turnover_window:] * volume.iloc[-turnover_window:]).mean())

    def slope(series: pd.Series, lag: int) -> float:
        s = series.dropna()
        return _ratio(s, lag)

    return {
        "price": price,
        "prev_close": prev,
        "chg_pct": (price / prev - 1.0) * 100.0 if prev > 0 else np.nan,
        "trading_days": int(len(close)),
        "turnover": turnover,
        "sma20": v20,
        "sma50": v50,
        "sma200": v200,
        "rsi14": rsi14,
        # 長期
        "mom_12_1": _ratio(close, w["mom_lookback"] - w["mom_skip"], offset=w["mom_skip"]),
        "ret_12m": _ratio(close, w["mom_lookback"]),
        "above_sma200": float(price > v200) if np.isfinite(v200) else np.nan,
        "sma200_slope": slope(sma200, w["sma_long_slope"]),
        # 中期
        "ret_3m": _ratio(close, w["ret_mid"]),
        "golden_cross": float(v50 > v200) if np.isfinite(v50) and np.isfinite(v200) else np.nan,
        "above_sma50": float(price > v50) if np.isfinite(v50) else np.nan,
        "sma50_slope": slope(sma50, w["sma_mid_slope"]),
        # 短期
        "ret_20d": _ratio(close, w["ret_short"]),
        "above_sma20": float(price > v20) if np.isfinite(v20) else np.nan,
        "rsi_zone": rsi_zone_score(
            rsi14,
            low_anchor=rsi_cfg["low_anchor"],
            low=rsi_cfg["low"],
            high=rsi_cfg["high"],
            overheat_span=rsi_cfg["overheat_span"],
            penalty_floor=rsi_cfg["penalty_floor"],
        ),
        "volume_ratio": volume_ratio,
    }


# --------------------------------------------------------------------------
# スコア合成
# --------------------------------------------------------------------------
def zscore(series: pd.Series, clip: float = 3.0) -> pd.Series:
    """ユニバース全体で標準化し、外れ値を ±clip に丸める。分散0なら全て0。"""
    s = pd.Series(series, dtype="float64")
    mean = s.mean(skipna=True)
    std = s.std(ddof=0, skipna=True)
    if not np.isfinite(std) or std == 0:
        z = pd.Series(0.0, index=s.index)
        return z.where(s.notna(), np.nan)
    return ((s - mean) / std).clip(-clip, clip)


def normalize_weights(weights: dict) -> dict:
    total = sum(abs(v) for v in weights.values())
    if total == 0:
        raise ValueError("重みの合計が0です")
    return {k: v / total for k, v in weights.items()}


def score_timeframe(metrics: pd.DataFrame, weights: dict, clip: float = 3.0):
    """指標DataFrame（index=ticker）を z-score 化して合成スコアを返す。

    戻り値: (scores: Series, zs: DataFrame, contributions: DataFrame)
    欠損した指標は z=0（=ユニバース平均）として扱い、その銘柄を落とさない。
    """
    missing = [k for k in weights if k not in metrics.columns]
    if missing:
        raise KeyError(f"指標が見つかりません: {missing}")

    w = normalize_weights(weights)
    zs = pd.DataFrame({k: zscore(metrics[k], clip) for k in weights}, index=metrics.index)
    contributions = zs.fillna(0.0).mul(pd.Series(w), axis=1)
    scores = contributions.sum(axis=1)
    return scores, zs, contributions
