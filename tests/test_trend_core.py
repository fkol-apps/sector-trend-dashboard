"""スコア算出ロジックの単体テスト。

既知の値動きを持つダミー系列を作り、指標が期待どおりの符号・値になるかを検証する。
config.yaml をそのまま読み込むので、設定ファイルのキー不整合もここで落ちる。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.trend_core import (  # noqa: E402
    compute_metrics,
    compute_per,
    normalize_weights,
    rsi_wilder,
    rsi_zone_score,
    sanitize_per,
    score_timeframe,
    zscore,
)


@pytest.fixture(scope="module")
def config():
    with (ROOT / "config.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def make_series(values, start="2024-01-01"):
    idx = pd.bdate_range(start=start, periods=len(values))
    return pd.Series(values, index=idx, dtype="float64")


def trend_series(n=500, daily=0.001, start_price=100.0):
    """毎日一定率で動く系列。daily>0 で上昇、<0 で下落、0 で横ばい。"""
    return make_series([start_price * (1.0 + daily) ** i for i in range(n)])


# ---------------------------------------------------------------- RSI
def test_rsi_all_up_is_100():
    rsi = rsi_wilder(trend_series(60, 0.01), 14)
    assert rsi.iloc[-1] == pytest.approx(100.0)


def test_rsi_all_down_is_0():
    rsi = rsi_wilder(trend_series(60, -0.01), 14)
    assert rsi.iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_rsi_flat_is_50():
    rsi = rsi_wilder(trend_series(60, 0.0), 14)
    assert rsi.iloc[-1] == pytest.approx(50.0)


def test_rsi_symmetric_zigzag_is_near_50():
    # 上げ幅と下げ幅が等しいジグザグなので RSI は 50 付近に収束する
    vals = [100.0]
    for i in range(80):
        vals.append(vals[-1] + (1.0 if i % 2 == 0 else -1.0))
    rsi = rsi_wilder(make_series(vals), 14)
    assert 45.0 < rsi.iloc[-1] < 55.0


def test_rsi_needs_full_period():
    rsi = rsi_wilder(trend_series(10, 0.01), 14)
    assert rsi.isna().all()


# ---------------------------------------------------------------- RSIゾーン
@pytest.mark.parametrize(
    "rsi,expected",
    [
        (20.0, 0.0),    # 売られすぎ: 加点なし
        (30.0, 0.0),    # 下限アンカー
        (40.0, 0.5),    # 30 と 50 の中間
        (50.0, 1.0),    # 最良ゾーンの入口
        (60.0, 1.0),    # 最良ゾーン
        (70.0, 1.0),    # 最良ゾーンの出口
        (77.5, 0.0),    # 過熱の途中（傾き 2.0/15 なので 1.0 下がって 0.0）
        (85.0, -1.0),   # 下限で頭打ち
        (95.0, -1.0),   # 下限を割らない
    ],
)
def test_rsi_zone_score_piecewise(rsi, expected):
    got = rsi_zone_score(rsi, low_anchor=30, low=50, high=70, overheat_span=15, penalty_floor=-1.0)
    assert got == pytest.approx(expected)


def test_rsi_zone_score_is_monotonic_then_decreasing():
    rising = [rsi_zone_score(r) for r in range(30, 51, 5)]
    assert rising == sorted(rising)
    falling = [rsi_zone_score(r) for r in range(70, 91, 5)]
    assert falling == sorted(falling, reverse=True)


def test_rsi_zone_score_handles_nan():
    assert np.isnan(rsi_zone_score(np.nan))


# ---------------------------------------------------------------- 指標
def test_uptrend_metrics_are_all_bullish(config):
    close = trend_series(500, 0.001)
    volume = make_series([1_000_000.0] * 500)
    m = compute_metrics(close, volume, config["windows"], config["scoring"]["rsi"])

    assert m["above_sma200"] == 1.0
    assert m["above_sma50"] == 1.0
    assert m["above_sma20"] == 1.0
    assert m["golden_cross"] == 1.0
    assert m["sma200_slope"] > 0
    assert m["sma50_slope"] > 0
    assert m["mom_12_1"] > 0
    assert m["ret_3m"] > 0
    assert m["ret_20d"] > 0
    assert m["rsi14"] == pytest.approx(100.0)
    # 一本調子の上げは RSI100 = 過熱として減点される
    assert m["rsi_zone"] == pytest.approx(-1.0)


def test_downtrend_metrics_are_all_bearish(config):
    close = trend_series(500, -0.001)
    volume = make_series([1_000_000.0] * 500)
    m = compute_metrics(close, volume, config["windows"], config["scoring"]["rsi"])

    assert m["above_sma200"] == 0.0
    assert m["above_sma50"] == 0.0
    assert m["golden_cross"] == 0.0
    assert m["sma200_slope"] < 0
    assert m["mom_12_1"] < 0
    assert m["ret_20d"] < 0
    assert m["rsi_zone"] == 0.0


def test_flat_series_has_zero_slopes(config):
    close = trend_series(500, 0.0)
    volume = make_series([1_000_000.0] * 500)
    m = compute_metrics(close, volume, config["windows"], config["scoring"]["rsi"])

    assert m["sma200_slope"] == pytest.approx(0.0)
    assert m["sma50_slope"] == pytest.approx(0.0)
    assert m["ret_20d"] == pytest.approx(0.0)
    assert m["above_sma200"] == 0.0  # 終値 = SMA は「上抜け」ではない


def test_returns_match_known_values(config):
    """最後の1日だけ +10%、それ以前は横ばいの系列。"""
    close = make_series([100.0] * 300 + [110.0])
    volume = make_series([1_000_000.0] * 301)
    m = compute_metrics(close, volume, config["windows"], config["scoring"]["rsi"])

    assert m["ret_20d"] == pytest.approx(0.10)
    assert m["ret_3m"] == pytest.approx(0.10)
    assert m["ret_12m"] == pytest.approx(0.10)
    assert m["chg_pct"] == pytest.approx(10.0)
    # 12-1 モメンタムは直近1ヶ月を除くので、この上昇は反映されない
    assert m["mom_12_1"] == pytest.approx(0.0)


def test_volume_ratio_detects_spike(config):
    close = trend_series(300, 0.0)
    volume = make_series([1_000_000.0] * 295 + [3_000_000.0] * 5)
    m = compute_metrics(close, volume, config["windows"], config["scoring"]["rsi"])
    expected = 3_000_000.0 / ((55 * 1_000_000.0 + 5 * 3_000_000.0) / 60)
    assert m["volume_ratio"] == pytest.approx(expected)


def test_short_history_returns_nan_not_crash(config):
    close = trend_series(30, 0.001)
    volume = make_series([1_000_000.0] * 30)
    m = compute_metrics(close, volume, config["windows"], config["scoring"]["rsi"])

    assert m["trading_days"] == 30
    assert np.isnan(m["sma200"])
    assert np.isnan(m["mom_12_1"])
    assert m["ret_20d"] == pytest.approx(float(close.iloc[-1] / close.iloc[-21] - 1))


def test_too_short_returns_none(config):
    close = make_series([100.0])
    volume = make_series([1.0])
    assert compute_metrics(close, volume, config["windows"], config["scoring"]["rsi"]) is None


# ---------------------------------------------------------------- PER
@pytest.mark.parametrize(
    "value,expected",
    [
        (8.770532, 8.8),    # 通常の実績PER。小数第1位に丸める
        (54.050835, 54.1),
        (500.0, 500.0),     # 上限ちょうどは通す
        (500.1, None),      # 上限超えは業績急変による異常値として捨てる
        (-31.69, None),     # 赤字予想の負のPERは表示しない
        (0.0, None),
        (None, None),       # yfinanceは赤字企業でNoneを返す
        (float("nan"), None),
        (float("inf"), None),
        ("8.8", 8.8),       # 数値化できる文字列は受け付ける
        ("なし", None),
    ],
)
def test_sanitize_per(value, expected):
    assert sanitize_per(value) == expected


def test_sanitize_per_respects_custom_cap():
    assert sanitize_per(120.0, max_per=100) is None
    assert sanitize_per(80.0, max_per=100) == 80.0


def test_compute_per_uses_market_cap_over_reported_pe():
    """株式分割の調整ずれで trailingPE が壊れていても、時価総額基準なら正しく出る。

    実例: 日本製鉄。yfinance の trailingPE は 209 倍だったが、
    時価総額 3.62兆円 ÷ 純利益 2,880億円 = 12.6 倍が実態。
    """
    per = compute_per(market_cap=3.62e12, net_income=288e9, fallback_pe=209.0)
    assert per == pytest.approx(12.6, abs=0.05)


def test_compute_per_falls_back_when_net_income_missing():
    assert compute_per(market_cap=1e12, net_income=None, fallback_pe=18.4) == 18.4
    assert compute_per(market_cap=None, net_income=None, fallback_pe=18.4) == 18.4


def test_compute_per_returns_none_for_loss_making():
    # 赤字はフォールバックも使わない（PERが定義できないため）
    assert compute_per(market_cap=1e12, net_income=-5e10, fallback_pe=30.0) is None
    assert compute_per(market_cap=1e12, net_income=0, fallback_pe=30.0) is None


def test_compute_per_applies_cap():
    assert compute_per(market_cap=1e12, net_income=1e9, fallback_pe=None) is None  # 1000倍
    assert compute_per(market_cap=1e12, net_income=1e11, fallback_pe=None) == 10.0


def test_compute_per_handles_garbage_input():
    assert compute_per(market_cap="なし", net_income="なし", fallback_pe=12.0) == 12.0
    assert compute_per(market_cap=float("nan"), net_income=1e9, fallback_pe=None) is None


# ---------------------------------------------------------------- z-score
def test_zscore_standardizes():
    z = zscore(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert z.mean() == pytest.approx(0.0)
    assert z.std(ddof=0) == pytest.approx(1.0)
    assert z.iloc[2] == pytest.approx(0.0)


def test_zscore_clips_outliers():
    z = zscore(pd.Series([0.0] * 99 + [1000.0]), clip=3.0)
    assert z.max() == pytest.approx(3.0)


def test_zscore_constant_series_is_zero():
    z = zscore(pd.Series([7.0, 7.0, 7.0]))
    assert (z == 0.0).all()


def test_zscore_keeps_nan():
    z = zscore(pd.Series([1.0, 2.0, np.nan]))
    assert np.isnan(z.iloc[2])


# ---------------------------------------------------------------- 合成
def test_normalize_weights_sums_to_one():
    w = normalize_weights({"a": 2.0, "b": 2.0})
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["a"] == pytest.approx(0.5)


def test_score_ranks_stronger_stock_higher():
    metrics = pd.DataFrame(
        {"ret_20d": [0.20, 0.05, -0.10], "above_sma20": [1.0, 1.0, 0.0]},
        index=["STRONG", "MID", "WEAK"],
    )
    res = score_timeframe(metrics, {"ret_20d": 0.7, "above_sma20": 0.3})

    assert list(res["score"].sort_values(ascending=False).index) == ["STRONG", "MID", "WEAK"]
    # 合成スコアは各指標の寄与の合計
    assert res["score"]["STRONG"] == pytest.approx(res["contrib"].loc["STRONG"].sum())
    # z-score 化しているので合成前の平均は 0
    assert res["z"]["ret_20d"].mean() == pytest.approx(0.0)


def test_score_is_invariant_to_weight_magnitude():
    metrics = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [3.0, 2.0, 1.0]}, index=["x", "y", "z"])
    s1 = score_timeframe(metrics, {"a": 1.0, "b": 1.0})["score"]
    s2 = score_timeframe(metrics, {"a": 10.0, "b": 10.0})["score"]
    pd.testing.assert_series_equal(s1, s2)


def test_raw_scale_does_not_leak_into_score():
    """生の数値をそのまま足していないことの確認。

    b の単位だけ 1000 倍しても、z-score 化してから合成するので順位もスコアも変わらない。
    """
    base = pd.DataFrame({"a": [0.1, 0.2, 0.3], "b": [1.0, 3.0, 2.0]}, index=["x", "y", "z"])
    scaled = base.assign(b=base["b"] * 1000.0)
    s1 = score_timeframe(base, {"a": 0.5, "b": 0.5})["score"]
    s2 = score_timeframe(scaled, {"a": 0.5, "b": 0.5})["score"]
    pd.testing.assert_series_equal(s1, s2)


def test_score_treats_missing_metric_as_average():
    metrics = pd.DataFrame({"a": [1.0, 2.0, np.nan], "b": [1.0, 1.0, 1.0]}, index=["x", "y", "z"])
    scores = score_timeframe(metrics, {"a": 1.0, "b": 1.0})["score"]

    assert np.isfinite(scores["z"])          # 欠損があっても銘柄を落とさない
    assert scores["z"] == pytest.approx(0.0)  # a は平均扱い、b は分散0で0


def test_clipped_scores_tie_but_unclipped_breaks_it():
    """クリップ上限に張り付いた銘柄同士は同点になり、クリップなしスコアで順序が付く。"""
    # 2銘柄だけ極端に強い（どちらも z > 3 になる）値を持たせる
    values = [0.0] * 100 + [1000.0, 2000.0]
    metrics = pd.DataFrame({"a": values}, index=[f"T{i}" for i in range(100)] + ["BIG", "HUGE"])
    res = score_timeframe(metrics, {"a": 1.0}, clip=3.0)

    assert res["score"]["HUGE"] == pytest.approx(3.0)                      # 上限に張り付く
    assert res["score"]["BIG"] == pytest.approx(res["score"]["HUGE"])      # 表示スコアは同点
    assert res["score_unclipped"]["HUGE"] > res["score_unclipped"]["BIG"]  # それでも順序は付く


def test_score_raises_on_unknown_metric():
    metrics = pd.DataFrame({"a": [1.0, 2.0]}, index=["x", "y"])
    with pytest.raises(KeyError):
        score_timeframe(metrics, {"nonexistent": 1.0})


def test_config_weights_match_indicator_names(config):
    """config.yaml の重みキーが compute_metrics の出力に存在すること。"""
    close = trend_series(500, 0.001)
    volume = make_series([1_000_000.0] * 500)
    m = compute_metrics(close, volume, config["windows"], config["scoring"]["rsi"])

    for timeframe, weights in config["weights"].items():
        for key in weights:
            assert key in m, f"{timeframe} の重み {key} に対応する指標がありません"
