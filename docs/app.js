/* セクター別トレンド銘柄ダッシュボード
   読むのは自分の docs/data/{jp,us}.json だけ。外部APIは一切叩かない。 */
(() => {
  "use strict";

  const TIMEFRAME_ORDER = ["long", "mid", "short"];
  const TIMEFRAME_NOTE = { long: "6〜12ヶ月", mid: "1〜3ヶ月", short: "1〜4週" };
  const RETURN_LABEL = { long: "12ヶ月", mid: "3ヶ月", short: "20日" };
  // スコアがこの絶対値で色の濃さが最大になる（ユニバース全体のz-score合成なので概ね±2.5に収まる）
  const TINT_SCALE = 2.0;

  const el = {
    board: document.getElementById("board"),
    status: document.getElementById("status"),
    meta: document.getElementById("meta"),
    subtitle: document.getElementById("subtitle"),
  };

  const cache = new Map();

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

  function formatPct(ratio, digits = 1) {
    if (ratio == null) return "—";
    const v = ratio * 100;
    return (v >= 0 ? "+" : "") + nf(digits).format(v) + "%";
  }

  function formatSigned(value, digits = 2) {
    if (value == null) return "—";
    return (value >= 0 ? "+" : "") + nf(digits).format(value);
  }

  function formatDateTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  /** スコアを色（青=強い / オレンジ=弱い）と濃さに変換する。色は補助で、数値は必ず併記する。 */
  function tintFor(score) {
    const s = score == null ? 0 : score;
    const t = Math.min(Math.abs(s) / TINT_SCALE, 1);
    return {
      rgb: s >= 0 ? "var(--pos)" : "var(--neg)",
      tintAlpha: (0.04 + 0.9 * t * 0.2).toFixed(3),
      barAlpha: (0.25 + 0.75 * t).toFixed(3),
    };
  }

  // ------------------------------------------------------------ スパークライン
  function sparkline(values, positive) {
    const svgns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgns, "svg");
    svg.setAttribute("class", "spark");
    svg.setAttribute("viewBox", "0 0 100 26");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");

    const pts = (values || []).filter((v) => typeof v === "number");
    if (pts.length < 2) return svg;

    const min = Math.min(...pts);
    const max = Math.max(...pts);
    const span = max - min || 1;
    const x = (i) => (i / (pts.length - 1)) * 100;
    const y = (v) => 24 - ((v - min) / span) * 22;

    const path = document.createElementNS(svgns, "polyline");
    path.setAttribute("points", pts.map((v, i) => `${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(" "));
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", `rgb(${positive ? "var(--pos)" : "var(--neg)"})`);
    path.setAttribute("stroke-width", "1.4");
    path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("vector-effect", "non-scaling-stroke");
    svg.appendChild(path);

    const dot = document.createElementNS(svgns, "circle");
    dot.setAttribute("cx", "100");
    dot.setAttribute("cy", y(pts[pts.length - 1]).toFixed(2));
    dot.setAttribute("r", "1.6");
    dot.setAttribute("fill", `rgb(${positive ? "var(--pos)" : "var(--neg)"})`);
    dot.setAttribute("vector-effect", "non-scaling-stroke");
    svg.appendChild(dot);

    return svg;
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

    const chgClass = stock.chg_pct > 0 ? "up" : stock.chg_pct < 0 ? "down" : "flat";
    const chgText =
      stock.chg_pct == null ? "—" : (stock.chg_pct >= 0 ? "+" : "") + nf(2).format(stock.chg_pct) + "%";

    const row1 = document.createElement("div");
    row1.className = "tile-row1";
    row1.innerHTML =
      `<span class="ticker"></span><span class="chg ${chgClass}"></span>`;
    row1.querySelector(".ticker").textContent = stock.ticker;
    row1.querySelector(".chg").textContent = chgText;

    const name = document.createElement("div");
    name.className = "name";
    name.textContent = stock.name;
    name.title = stock.name;

    const row2 = document.createElement("div");
    row2.className = "tile-row2";
    const price = document.createElement("span");
    price.className = "price";
    price.textContent = formatPrice(stock.price, data.currency);
    const ret = document.createElement("span");
    ret.className = "ret";
    ret.innerHTML = `${RETURN_LABEL[timeframe]} <b></b>`;
    ret.querySelector("b").textContent = formatPct(sc.return);
    row2.append(price, ret);

    const spark = sparkline(stock.spark, (sc.return ?? 0) >= 0);

    const row3 = document.createElement("div");
    row3.className = "tile-row3";
    const rank = document.createElement("span");
    rank.textContent = sc.rank ? `市場全体 ${sc.rank}位 / ${data.counts.scored}` : "";
    const score = document.createElement("span");
    score.className = "score";
    score.textContent = `スコア ${formatSigned(sc.score)}`;
    row3.append(rank, score);

    tile.append(row1, name, row2, spark, row3);
    tile.setAttribute(
      "aria-label",
      `${stock.name} ${stock.ticker} スコア${formatSigned(sc.score)} ${RETURN_LABEL[timeframe]}リターン${formatPct(sc.return)}`
    );
    return tile;
  }

  // ------------------------------------------------------------ 盤面描画
  function render(data) {
    el.board.textContent = "";

    for (const sector of data.sectors) {
      const block = document.createElement("section");
      block.className = "sector";
      block.dataset.sector = sector.key;

      const head = document.createElement("div");
      head.className = "sector-head";
      const nameEl = document.createElement("span");
      nameEl.className = "sector-name";
      nameEl.textContent = sector.ja;
      const countEl = document.createElement("span");
      countEl.className = "sector-count";
      countEl.textContent = `${sector.count}銘柄`;
      head.append(nameEl, countEl);

      const cols = document.createElement("div");
      cols.className = "timeframes";

      for (const tf of TIMEFRAME_ORDER) {
        const col = document.createElement("div");
        col.className = "tf-column";
        col.dataset.timeframe = tf;

        const colHead = document.createElement("div");
        colHead.className = "tf-head";
        const label = document.createElement("span");
        label.className = "tf-label";
        label.textContent = data.timeframe_labels?.[tf] ?? tf;
        const note = document.createElement("span");
        note.className = "tf-note";
        note.textContent = TIMEFRAME_NOTE[tf];
        colHead.append(label, note);

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
            short.textContent = `該当は${tickers.length}銘柄（このセクターの対象は${sector.count}銘柄）`;
            list.appendChild(short);
          }
        }

        col.append(colHead, list);
        cols.appendChild(col);
      }

      block.append(head, cols);
      el.board.appendChild(block);
    }
  }

  function renderMeta(data) {
    el.subtitle.textContent =
      `${data.label} / GICS 11セクター × 長期・中期・短期 の上位${data.top_n}銘柄`;
    const failed = data.counts.failed
      ? ` <span title="${data.failed.join(", ")}">取得失敗 ${data.counts.failed}件</span>`
      : "";
    el.meta.innerHTML =
      `株価: <b>${data.price_date ?? "—"}</b> 時点 ／ 対象 <b>${data.counts.scored}</b> 銘柄${failed}<br>` +
      `最終更新: <b>${formatDateTime(data.generated_at)}</b>`;
  }

  // ------------------------------------------------------------ 読み込み
  function showStatus(html) {
    el.status.hidden = false;
    el.status.innerHTML = html;
  }

  async function loadMarket(market) {
    if (cache.has(market)) return cache.get(market);
    const res = await fetch(`data/${market}.json`, { cache: "no-cache" });
    if (!res.ok) throw new Error(`data/${market}.json の取得に失敗しました (HTTP ${res.status})`);
    const data = await res.json();
    cache.set(market, data);
    return data;
  }

  async function show(market) {
    try {
      el.status.hidden = true;
      const data = await loadMarket(market);
      renderMeta(data);
      render(data);
    } catch (err) {
      el.board.textContent = "";
      if (location.protocol === "file:") {
        showStatus(
          "<h2>ローカルサーバー経由で開いてください</h2>" +
          "<p>ブラウザの制限（file:// では JSON を読み込めない）により、このページはファイルを直接開くと動作しません。" +
          "リポジトリのルートで次を実行し、表示されたURLを開いてください。</p>" +
          "<pre>python -m http.server 8080 --directory docs</pre>" +
          "<p><code>http://localhost:8080/</code> で表示されます。GitHub Pages 上では通常どおり動作します。</p>"
        );
      } else {
        showStatus(
          "<h2>データを読み込めませんでした</h2>" +
          `<p>${String(err.message || err)}</p>` +
          "<p>データ未生成の場合は次を実行してください。</p>" +
          "<pre>python scripts/build_data.py --market both</pre>"
        );
      }
    }
  }

  const params = new URLSearchParams(location.search);
  const market = params.get("market") === "us" ? "us" : "jp";
  show(market);
})();
