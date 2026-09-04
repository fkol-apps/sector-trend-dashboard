/* セクター別トレンド銘柄ダッシュボード
   読むのは自分の docs/data/{jp,us}.json だけ。外部APIは一切叩かない。 */
(() => {
  "use strict";

  const MARKETS = ["jp", "us"];
  const MARKET_LABEL = { jp: "日本株", us: "米国株" };
  const TIMEFRAME_ORDER = ["long", "mid", "short"];
  const TIMEFRAME_LABEL = { long: "長期", mid: "中期", short: "短期" };
  const TIMEFRAME_NOTE = { long: "6〜12ヶ月", mid: "1〜3ヶ月", short: "1〜4週" };
  const RETURN_LABEL = { long: "12ヶ月", mid: "3ヶ月", short: "20日" };
  // スコアがこの絶対値で色の濃さが最大になる（z-score合成なので概ね±2.5に収まる）
  const TINT_SCALE = 2.0;

  const SVGNS = "http://www.w3.org/2000/svg";

  const state = {
    market: "jp",
    timeframes: new Set(TIMEFRAME_ORDER),
    data: null,
  };

  const cache = new Map();

  const el = {
    board: document.getElementById("board"),
    boardHead: document.getElementById("board-head"),
    status: document.getElementById("status"),
    meta: document.getElementById("meta"),
    subtitle: document.getElementById("subtitle"),
    marketToggle: document.getElementById("market-toggle"),
    chips: document.getElementById("timeframe-chips"),
    dialog: document.getElementById("detail"),
    dialogBody: document.getElementById("detail-body"),
  };

  // ------------------------------------------------------------ 表示ヘルパ
  const nf = (digits) =>
    new Intl.NumberFormat("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits });

  function formatPrice(value, currency) {
    if (value == null) return "—";
    if (currency === "JPY") {
      const digits = value >= 1000 ? 0 : value >= 100 ? 1 : 2;
      return nf(digits).format(value) + "円";
    }
    return "$" + nf(2).format(value);
  }

  /** 丸めた結果を符号付きで返す。-0.001 が "+-0.00" になるのを避けるため -0 を 0 に潰す。 */
  function roundForDisplay(value, digits) {
    const r = Number(value.toFixed(digits));
    return Object.is(r, -0) ? 0 : r;
  }

  function formatPct(ratio, digits = 1) {
    if (ratio == null) return "—";
    const v = roundForDisplay(ratio * 100, digits);
    return (v >= 0 ? "+" : "") + nf(digits).format(v) + "%";
  }

  function formatSigned(value, digits = 2) {
    if (value == null) return "—";
    const v = roundForDisplay(value, digits);
    return (v >= 0 ? "+" : "") + nf(digits).format(v);
  }

  function formatDateTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  /** 指標の生値を、指標の性質に合わせて読める形にする。 */
  const RAW_FORMATTERS = {
    mom_12_1: (v) => formatPct(v),
    ret_3m: (v) => formatPct(v),
    ret_20d: (v) => formatPct(v),
    sma200_slope: (v) => formatPct(v),
    sma50_slope: (v) => formatPct(v),
    above_sma200: (v) => (v >= 0.5 ? "はい" : "いいえ"),
    above_sma50: (v) => (v >= 0.5 ? "はい" : "いいえ"),
    above_sma20: (v) => (v >= 0.5 ? "はい" : "いいえ"),
    golden_cross: (v) => (v >= 0.5 ? "はい" : "いいえ"),
    rsi_zone: (v) => nf(2).format(v),
    volume_ratio: (v) => "×" + nf(2).format(v),
  };

  function formatRaw(key, value) {
    if (value == null) return "—";
    const f = RAW_FORMATTERS[key];
    return f ? f(value) : nf(2).format(value);
  }

  /** スコアを色（青=強い / オレンジ=弱い）と濃さに変換する。色は補助で、数値は必ず併記する。 */
  function tintFor(score) {
    const s = score == null ? 0 : score;
    const t = Math.min(Math.abs(s) / TINT_SCALE, 1);
    return {
      rgb: s >= 0 ? "var(--pos)" : "var(--neg)",
      tintAlpha: (0.04 + 0.18 * t).toFixed(3),
      barAlpha: (0.25 + 0.75 * t).toFixed(3),
    };
  }

  function yahooUrl(ticker, market) {
    return market === "jp"
      ? `https://finance.yahoo.co.jp/quote/${encodeURIComponent(ticker)}`
      : `https://finance.yahoo.com/quote/${encodeURIComponent(ticker)}`;
  }

  // ------------------------------------------------------------ タイル
  function buildTile(stock, timeframe, data) {
    const sc = stock.scores[timeframe] || {};
    const tint = tintFor(sc.score);

    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "tile";
    tile.dataset.ticker = stock.ticker;
    tile.dataset.timeframe = timeframe;
    tile.style.setProperty("--tint-rgb", tint.rgb);
    tile.style.setProperty("--tint-a", tint.tintAlpha);
    tile.style.setProperty("--bar-a", tint.barAlpha);

    const parts = [
      ["t-ticker", stock.ticker],
      ["t-name", stock.name],
      ["t-score", formatSigned(sc.score)],
      ["t-price", formatPrice(stock.price, data.currency)],
      [
        "t-chg " + (stock.chg_pct > 0 ? "up" : stock.chg_pct < 0 ? "down" : "flat"),
        stock.chg_pct == null ? "—" : formatSigned(stock.chg_pct, 2) + "%",
      ],
    ];
    for (const [cls, text] of parts) {
      const span = document.createElement("span");
      span.className = cls;
      span.textContent = text;
      if (cls === "t-name") span.title = stock.name;
      tile.appendChild(span);
    }

    const ret = document.createElement("span");
    ret.className = "t-ret";
    const retLabel = document.createElement("span");
    retLabel.textContent = RETURN_LABEL[timeframe] + " ";
    const retValue = document.createElement("b");
    retValue.textContent = formatPct(sc.return);
    ret.append(retLabel, retValue);
    tile.appendChild(ret);

    tile.setAttribute(
      "aria-label",
      `${stock.name} ${stock.ticker} スコア${formatSigned(sc.score)} ` +
      `${RETURN_LABEL[timeframe]}リターン${formatPct(sc.return)} 詳細を開く`
    );
    tile.addEventListener("click", () => openDetail(stock.ticker, timeframe));
    return tile;
  }

  // ------------------------------------------------------------ 盤面描画
  function activeTimeframes() {
    return TIMEFRAME_ORDER.filter((tf) => state.timeframes.has(tf));
  }

  function renderBoardHead(shown) {
    el.boardHead.textContent = "";
    el.boardHead.style.setProperty("--tf-count", String(shown.length));

    const rail = document.createElement("div");
    rail.className = "rail-head";
    rail.textContent = "セクター";
    el.boardHead.appendChild(rail);

    for (const tf of shown) {
      const cell = document.createElement("div");
      const label = document.createElement("span");
      label.className = "tf-label";
      label.textContent = state.data.timeframe_labels?.[tf] ?? TIMEFRAME_LABEL[tf];
      const note = document.createElement("span");
      note.className = "tf-note";
      note.textContent = TIMEFRAME_NOTE[tf];
      cell.append(label, note);
      el.boardHead.appendChild(cell);
    }
  }

  function render() {
    const data = state.data;
    const shown = activeTimeframes();
    renderBoardHead(shown);
    el.board.textContent = "";

    for (const sector of data.sectors) {
      const row = document.createElement("section");
      row.className = "sector";
      row.dataset.sector = sector.key;
      row.style.setProperty("--tf-count", String(shown.length));

      const rail = document.createElement("div");
      rail.className = "sector-rail";
      const nameEl = document.createElement("span");
      nameEl.className = "sector-name";
      nameEl.textContent = sector.ja;
      const countEl = document.createElement("span");
      countEl.className = "sector-count";
      countEl.textContent = `${sector.count}銘柄`;
      rail.append(nameEl, countEl);
      row.appendChild(rail);

      for (const tf of shown) {
        const col = document.createElement("div");
        col.className = "tf-column";
        col.dataset.timeframe = tf;

        // 1カラムに折り返す画面幅でだけ見える時間軸ラベル
        const inline = document.createElement("div");
        inline.className = "tf-inline-label";
        inline.textContent = data.timeframe_labels?.[tf] ?? TIMEFRAME_LABEL[tf];
        const inlineNote = document.createElement("span");
        inlineNote.textContent = TIMEFRAME_NOTE[tf];
        inline.appendChild(inlineNote);
        col.appendChild(inline);

        const list = document.createElement("div");
        list.className = "tf-list";
        const tickers = sector.timeframes?.[tf] ?? [];

        if (tickers.length === 0) {
          const empty = document.createElement("p");
          empty.className = "tf-empty";
          empty.textContent = "条件を満たす銘柄なし";
          list.appendChild(empty);
        } else {
          for (const t of tickers) {
            const stock = data.stocks[t];
            if (stock) list.appendChild(buildTile(stock, tf, data));
          }
          // 上位3銘柄に満たないセクターは埋めずにその旨だけ出す
          if (tickers.length < data.top_n) {
            const short = document.createElement("p");
            short.className = "tf-empty";
            short.textContent = `該当は${tickers.length}銘柄（対象${sector.count}銘柄）`;
            list.appendChild(short);
          }
        }

        col.appendChild(list);
        row.appendChild(col);
      }

      el.board.appendChild(row);
    }
  }

  function renderMeta() {
    const data = state.data;
    el.subtitle.textContent =
      `${data.label} / GICS 11セクター × 長期・中期・短期 の上位${data.top_n}銘柄`;

    el.meta.textContent = "";
    const line1 = document.createElement("div");
    line1.append(
      document.createTextNode("株価: "),
      bold(data.price_date ?? "—"),
      document.createTextNode(" 時点 ／ 対象 "),
      bold(String(data.counts.scored)),
      document.createTextNode(" 銘柄")
    );
    if (data.counts.failed) {
      const failed = document.createElement("span");
      failed.className = "meta-warn";
      failed.textContent = ` ／ 取得失敗 ${data.counts.failed}件`;
      failed.title = data.failed.join(", ");
      line1.appendChild(failed);
    }
    const line2 = document.createElement("div");
    line2.append(document.createTextNode("最終更新: "), bold(formatDateTime(data.generated_at)));
    el.meta.append(line1, line2);
  }

  function bold(text) {
    const b = document.createElement("b");
    b.textContent = text;
    return b;
  }

  // ------------------------------------------------------------ 詳細チャート
  function niceTicks(min, max, count = 4) {
    const span = max - min;
    if (!(span > 0)) return [min];
    const rough = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(rough)));
    const norm = rough / mag;
    const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
    const ticks = [];
    for (let v = Math.ceil(min / step) * step; v <= max; v += step) ticks.push(v);
    return ticks;
  }

  const SERIES = [
    { key: "close", label: "終値", color: "var(--line-close)", width: 1.6, dash: null },
    { key: "sma20", label: "20日SMA", color: "var(--line-20)", width: 1.2, dash: null },
    { key: "sma50", label: "50日SMA", color: "var(--line-50)", width: 1.2, dash: "5 3" },
    { key: "sma200", label: "200日SMA", color: "var(--line-200)", width: 1.2, dash: "2 3" },
  ];

  function buildChart(stock, currency) {
    const chart = stock.chart || {};
    const dates = chart.dates || [];
    const W = 760, H = 300, PL = 58, PR = 14, PT = 12, PB = 26;

    const svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("class", "chart");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${stock.name} の1年株価チャート（20/50/200日移動平均つき）`);

    const all = [];
    for (const s of SERIES) for (const v of chart[s.key] || []) if (typeof v === "number") all.push(v);
    if (!all.length) return svg;

    const min = Math.min(...all);
    const max = Math.max(...all);
    const pad = (max - min) * 0.06 || 1;
    const lo = min - pad, hi = max + pad;
    const n = dates.length || (chart.close || []).length;

    const x = (i) => PL + (i / Math.max(n - 1, 1)) * (W - PL - PR);
    const y = (v) => PT + (1 - (v - lo) / (hi - lo)) * (H - PT - PB);

    // 横のグリッドと価格目盛り
    for (const t of niceTicks(lo, hi, 4)) {
      const gy = y(t);
      const line = document.createElementNS(SVGNS, "line");
      line.setAttribute("x1", PL); line.setAttribute("x2", W - PR);
      line.setAttribute("y1", gy.toFixed(1)); line.setAttribute("y2", gy.toFixed(1));
      line.setAttribute("class", "grid");
      svg.appendChild(line);

      const label = document.createElementNS(SVGNS, "text");
      label.setAttribute("x", PL - 7);
      label.setAttribute("y", (gy + 3.5).toFixed(1));
      label.setAttribute("text-anchor", "end");
      label.setAttribute("class", "axis");
      label.textContent = currency === "JPY" ? nf(0).format(t) : nf(0).format(t);
      svg.appendChild(label);
    }

    // 日付の目盛り（およそ2ヶ月ごと）
    const stepX = Math.max(Math.floor(n / 6), 1);
    for (let i = 0; i < n; i += stepX) {
      const label = document.createElementNS(SVGNS, "text");
      label.setAttribute("x", x(i).toFixed(1));
      label.setAttribute("y", H - 8);
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("class", "axis");
      label.textContent = (dates[i] || "").slice(2).replace(/-/g, "/");
      svg.appendChild(label);
    }

    // 各系列
    for (const s of SERIES) {
      const values = chart[s.key] || [];
      const d = [];
      let pen = false;
      values.forEach((v, i) => {
        if (typeof v !== "number") { pen = false; return; }
        d.push(`${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`);
        pen = true;
      });
      if (!d.length) continue;
      const path = document.createElementNS(SVGNS, "path");
      path.setAttribute("d", d.join(" "));
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", s.color);
      path.setAttribute("stroke-width", String(s.width));
      path.setAttribute("stroke-linejoin", "round");
      if (s.dash) path.setAttribute("stroke-dasharray", s.dash);
      path.setAttribute("vector-effect", "non-scaling-stroke");
      svg.appendChild(path);
    }

    return svg;
  }

  function buildLegend(stock, currency) {
    const legend = document.createElement("div");
    legend.className = "legend";
    for (const s of SERIES) {
      const values = stock.chart?.[s.key] || [];
      const last = [...values].reverse().find((v) => typeof v === "number");
      const item = document.createElement("span");
      item.className = "legend-item";

      const swatch = document.createElementNS(SVGNS, "svg");
      swatch.setAttribute("width", "22"); swatch.setAttribute("height", "8");
      swatch.setAttribute("aria-hidden", "true");
      const line = document.createElementNS(SVGNS, "line");
      line.setAttribute("x1", "0"); line.setAttribute("x2", "22");
      line.setAttribute("y1", "4"); line.setAttribute("y2", "4");
      line.setAttribute("stroke", s.color);
      line.setAttribute("stroke-width", String(s.width + 0.4));
      if (s.dash) line.setAttribute("stroke-dasharray", s.dash);
      swatch.appendChild(line);

      const text = document.createElement("span");
      text.textContent = `${s.label} ${last == null ? "—" : formatPrice(last, currency)}`;
      item.append(swatch, text);
      legend.appendChild(item);
    }
    return legend;
  }

  // ------------------------------------------------------------ スコア内訳
  function buildBreakdown(stock, timeframe, highlight) {
    const sc = stock.scores[timeframe] || {};
    const card = document.createElement("section");
    card.className = "score-card" + (highlight ? " is-active" : "");

    const head = document.createElement("header");
    head.className = "score-card-head";
    const title = document.createElement("span");
    title.className = "score-card-title";
    title.textContent = `${TIMEFRAME_LABEL[timeframe]}（${TIMEFRAME_NOTE[timeframe]}）`;
    const total = document.createElement("span");
    const tint = tintFor(sc.score);
    total.className = "score";
    total.style.setProperty("--tint-rgb", tint.rgb);
    total.textContent = formatSigned(sc.score);
    head.append(title, total);

    const sub = document.createElement("p");
    sub.className = "score-card-sub";
    sub.textContent =
      `${RETURN_LABEL[timeframe]}リターン ${formatPct(sc.return)} ／ 市場全体 ${sc.rank ?? "—"}位`;

    const table = document.createElement("table");
    table.className = "breakdown";
    table.innerHTML =
      "<thead><tr><th>指標</th><th>実測値</th><th>z</th><th>重み</th><th>寄与</th></tr></thead>";
    const tbody = document.createElement("tbody");

    const maxAbs = Math.max(
      ...(sc.breakdown || []).map((b) => Math.abs(b.contrib ?? 0)),
      0.01
    );

    for (const b of sc.breakdown || []) {
      const tr = document.createElement("tr");
      const cells = [
        b.label,
        formatRaw(b.key, b.raw),
        b.z == null ? "—" : formatSigned(b.z, 2),
        nf(2).format(b.weight ?? 0),
      ];
      for (const [i, text] of cells.entries()) {
        const td = document.createElement("td");
        td.textContent = text;
        if (i > 0) td.className = "num";
        tr.appendChild(td);
      }

      const td = document.createElement("td");
      td.className = "num contrib";
      const value = document.createElement("span");
      value.textContent = formatSigned(b.contrib, 2);
      const bar = document.createElement("span");
      bar.className = "contrib-bar";
      bar.style.setProperty("--w", `${(Math.abs(b.contrib ?? 0) / maxAbs) * 100}%`);
      bar.style.setProperty("--c", (b.contrib ?? 0) >= 0 ? "var(--pos)" : "var(--neg)");
      td.append(value, bar);
      tr.appendChild(td);
      tbody.appendChild(tr);
    }

    table.appendChild(tbody);
    card.append(head, sub, table);
    return card;
  }

  // ------------------------------------------------------------ モーダル
  function openDetail(ticker, timeframe) {
    const data = state.data;
    const stock = data.stocks[ticker];
    if (!stock) return;

    el.dialogBody.textContent = "";

    const head = document.createElement("header");
    head.className = "modal-head";

    const titles = document.createElement("div");
    const h2 = document.createElement("h2");
    h2.textContent = stock.name;
    const sub = document.createElement("p");
    sub.className = "modal-sub";
    const sectorJa = data.sectors.find((s) => s.key === stock.sector)?.ja ?? stock.sector;
    sub.textContent = `${stock.ticker} ／ ${sectorJa} ／ ${data.label}`;
    titles.append(h2, sub);

    const close = document.createElement("button");
    close.type = "button";
    close.className = "modal-close";
    close.setAttribute("aria-label", "閉じる");
    close.textContent = "×";
    close.addEventListener("click", () => el.dialog.close());
    head.append(titles, close);

    const summary = document.createElement("div");
    summary.className = "modal-summary";
    const chg = stock.chg_pct;
    const items = [
      ["現在値", formatPrice(stock.price, data.currency)],
      ["前日比", chg == null ? "—" : formatSigned(chg, 2) + "%"],
      ["RSI(14)", stock.metrics.rsi14 == null ? "—" : nf(1).format(stock.metrics.rsi14)],
      ["出来高比", stock.metrics.volume_ratio == null ? "—" : "×" + nf(2).format(stock.metrics.volume_ratio)],
      ["12ヶ月", formatPct(stock.metrics.ret_12m)],
      ["3ヶ月", formatPct(stock.metrics.ret_3m)],
      ["20日", formatPct(stock.metrics.ret_20d)],
    ];
    for (const [label, value] of items) {
      const cell = document.createElement("div");
      cell.className = "summary-item";
      const l = document.createElement("span");
      l.className = "summary-label";
      l.textContent = label;
      const v = document.createElement("span");
      v.className = "summary-value";
      v.textContent = value;
      cell.append(l, v);
      summary.appendChild(cell);
    }

    const figure = document.createElement("figure");
    figure.className = "chart-figure";
    figure.appendChild(buildChart(stock, data.currency));
    figure.appendChild(buildLegend(stock, data.currency));
    const cap = document.createElement("figcaption");
    cap.textContent = `直近1年（${stock.chart?.dates?.[0] ?? ""} 〜 ${stock.last_date}）の終値と移動平均`;
    figure.appendChild(cap);

    const cards = document.createElement("div");
    cards.className = "score-cards";
    for (const tf of TIMEFRAME_ORDER) cards.appendChild(buildBreakdown(stock, tf, tf === timeframe));

    const note = document.createElement("p");
    note.className = "modal-note";
    note.textContent =
      "z はユニバース全体を平均0・標準偏差1に揃えた値（±3で頭打ち）、寄与は z × 重み。" +
      "スコアはこの市場のユニバース内での相対評価です。";

    const footer = document.createElement("footer");
    footer.className = "modal-foot";
    const link = document.createElement("a");
    link.href = yahooUrl(stock.ticker, data.market);
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.className = "external-link";
    link.textContent = "Yahoo Finance で見る ↗";
    footer.appendChild(link);

    el.dialogBody.append(head, summary, figure, cards, note, footer);
    if (!el.dialog.open) el.dialog.showModal();
    close.focus();
  }

  // ------------------------------------------------------------ コントロール
  function buildControls() {
    el.marketToggle.textContent = "";
    for (const m of MARKETS) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "segment";
      btn.dataset.market = m;
      btn.textContent = MARKET_LABEL[m];
      btn.setAttribute("aria-pressed", String(state.market === m));
      btn.addEventListener("click", () => switchMarket(m));
      el.marketToggle.appendChild(btn);
    }

    el.chips.textContent = "";
    for (const tf of TIMEFRAME_ORDER) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.dataset.timeframe = tf;
      chip.textContent = TIMEFRAME_LABEL[tf];
      chip.setAttribute("aria-pressed", String(state.timeframes.has(tf)));
      chip.addEventListener("click", () => toggleTimeframe(tf));
      el.chips.appendChild(chip);
    }
    const all = document.createElement("button");
    all.type = "button";
    all.className = "chip chip-all";
    all.textContent = "すべて";
    all.addEventListener("click", () => {
      state.timeframes = new Set(TIMEFRAME_ORDER);
      syncControls();
      syncUrl();
      render();
    });
    el.chips.appendChild(all);
  }

  function syncControls() {
    for (const btn of el.marketToggle.querySelectorAll(".segment")) {
      btn.setAttribute("aria-pressed", String(btn.dataset.market === state.market));
    }
    for (const chip of el.chips.querySelectorAll(".chip[data-timeframe]")) {
      chip.setAttribute("aria-pressed", String(state.timeframes.has(chip.dataset.timeframe)));
    }
    const all = el.chips.querySelector(".chip-all");
    if (all) all.setAttribute("aria-pressed", String(state.timeframes.size === TIMEFRAME_ORDER.length));
  }

  function toggleTimeframe(tf) {
    if (state.timeframes.has(tf)) {
      if (state.timeframes.size === 1) return; // 最低1つは残す
      state.timeframes.delete(tf);
    } else {
      state.timeframes.add(tf);
    }
    syncControls();
    syncUrl();
    render();
  }

  async function switchMarket(market) {
    if (market === state.market) return;
    state.market = market;
    syncControls();
    syncUrl();
    await show(); // ページ遷移せず JSON を差し替えるだけ
  }

  /** 表示状態をURLのクエリに持たせ、そのまま共有できるようにする。 */
  function syncUrl() {
    const params = new URLSearchParams();
    params.set("market", state.market);
    if (state.timeframes.size !== TIMEFRAME_ORDER.length) {
      params.set("tf", activeTimeframes().join(","));
    }
    history.replaceState(null, "", `${location.pathname}?${params.toString()}`);
  }

  function readUrl() {
    const params = new URLSearchParams(location.search);
    const market = params.get("market");
    if (MARKETS.includes(market)) state.market = market;

    const tf = (params.get("tf") || "")
      .split(",")
      .map((s) => s.trim())
      .filter((s) => TIMEFRAME_ORDER.includes(s));
    if (tf.length) state.timeframes = new Set(tf);
  }

  // ------------------------------------------------------------ 読み込み
  function showStatus(nodes) {
    el.status.hidden = false;
    el.status.textContent = "";
    el.status.append(...nodes);
  }

  function statusBlock(title, lines, command) {
    const h2 = document.createElement("h2");
    h2.textContent = title;
    const out = [h2];
    for (const line of lines) {
      const p = document.createElement("p");
      p.textContent = line;
      out.push(p);
    }
    if (command) {
      const pre = document.createElement("pre");
      pre.textContent = command;
      out.push(pre);
    }
    return out;
  }

  async function loadMarket(market) {
    if (cache.has(market)) return cache.get(market);
    const res = await fetch(`data/${market}.json`, { cache: "no-cache" });
    if (!res.ok) throw new Error(`data/${market}.json の取得に失敗しました (HTTP ${res.status})`);
    const data = await res.json();
    cache.set(market, data);
    return data;
  }

  async function show() {
    try {
      el.status.hidden = true;
      el.board.setAttribute("aria-busy", "true");
      state.data = await loadMarket(state.market);
      renderMeta();
      render();
    } catch (err) {
      el.board.textContent = "";
      if (location.protocol === "file:") {
        showStatus(statusBlock(
          "ローカルサーバー経由で開いてください",
          [
            "ブラウザの制限（file:// では JSON を読み込めない）により、このページはファイルを直接開くと動作しません。",
            "リポジトリのルートで次を実行し、http://localhost:8940/ を開いてください。GitHub Pages 上では通常どおり動作します。",
          ],
          "python -m http.server 8940 --directory docs"
        ));
      } else {
        showStatus(statusBlock(
          "データを読み込めませんでした",
          [String(err.message || err), "データが未生成の場合は次を実行してください。"],
          "python scripts/build_data.py --market both"
        ));
      }
    } finally {
      el.board.removeAttribute("aria-busy");
    }
  }

  // ------------------------------------------------------------ 起動
  el.dialog.addEventListener("click", (e) => {
    // 背景クリックで閉じる
    if (e.target === el.dialog) el.dialog.close();
  });

  // Esc で閉じる（<dialog> の既定動作だが、環境によって効かないことがあるので明示する）
  el.dialog.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      el.dialog.close();
    }
  });

  readUrl();
  buildControls();
  syncControls();
  show();
})();
