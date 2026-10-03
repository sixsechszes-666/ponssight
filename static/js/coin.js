const COIN_VB = {w: 1000, h: 280, l: 10, r: 10, t: 16, b: 18, vol: 54, gap: 8};
const COIN_RANGES = [[1, "1h"], [6, "6h"], [24, "24h"], [0, "all"]];
const COIN_HOLDERS = 50;
const coin = {addr: null, hours: 6, holders: COIN_HOLDERS, data: null,
              seq: 0, opener: null, geo: null, backdrop: false};

// The card is a view of the tab under it, never a tab of its own: it lives at
// #<tab>/coin/<address> and readHash hands that address back on its own, so
// every other reader of the tab and its argument is untouched by it.
const coinOpen = () => !$("#coin").hidden;
// The width at which the card stops being an overlay and becomes a column
// beside the table. The number is written in card.css and derived in shell.css,
// and it is repeated here because a media query cannot read a custom property:
// there is no constant the three places can share. What the comment can do is
// say what breaks if they disagree - the CSS moves the panel while this file
// still believes it is modal, which locks the page behind a panel that no
// longer covers it.
const coinDocked = () => matchMedia("(min-width:2124px)").matches;
// The page is locked only while the card is over it. Docked, the table beside
// the panel has to stay scrollable and clickable - that is the whole reason for
// docking it - so the lock follows the mode rather than the card being open,
// and the argument is "lock it", which is true exactly when the card is not
// docked. `aria-modal` follows the same switch: a panel that leaves the rest of
// the page reachable is not a modal, and saying it is would hide the table from
// a screen reader.
function coinLock(lock){
  document.body.style.overflow = lock ? "hidden" : "";
  const card = $("#coin-card");
  if (!card) return;
  if (lock) card.setAttribute("aria-modal", "true");
  else card.removeAttribute("aria-modal");
}
// The window can cross the threshold while the card is open, and the two
// directions need opposite things done: widening has to release the lock the
// modal took, narrowing has to take it again or the page scrolls behind an
// overlay.
matchMedia("(min-width:2124px)").addEventListener("change", () => {
  if (coinOpen()) coinLock(!coinDocked());
});
// hours 0 is the whole life of the token, which the api calls everything
function coinRangeLabel(h){
  const r = COIN_RANGES.filter(x => x[0] === numOr(h, -1))[0];
  return r ? r[1] : "all";
}
function coinFetch(addr, hours, holders){
  return api(qs("/api/token/" + encodeURIComponent(addr) + "/card",
                {hours: hours, holders: holders}));
}
function coinSkeleton(){
  return '<div class="cskel"><div class="sk h"></div><div class="sk s"></div>' +
    '<div class="sk c"></div><div class="sk t"></div></div>';
}

async function openCoinCard(addr, opts){
  opts = opts || {};
  if (!addr) return;
  const same = coinOpen() && coin.addr &&
    String(coin.addr).toLowerCase() === String(addr).toLowerCase();
  if (same && !opts.force) return;
  if (!coinOpen()){
    // whatever opened the card takes the focus back when it closes
    coin.opener = document.activeElement;
    $("#coin").hidden = false;
    coinLock(!coinDocked());
    // A docked panel sits beside the row that opened it, so taking the focus
    // into it would move the keyboard off the list the panel is about - and
    // the next row is one tab away.
    if (!coinDocked()){ try { $("#coin-card").focus(); } catch(e){} }
  }
  coin.addr = addr;
  coin.geo = null;
  if (opts.hash !== false) setHash("#" + tab + "/coin/" + addr, true);
  $("#coin-body").innerHTML = coinSkeleton();
  await loadCoin();
}

async function loadCoin(){
  const addr = coin.addr;
  if (!addr) return;
  const seq = ++coin.seq;
  try {
    const d = await coinFetch(addr, coin.hours, coin.holders);
    if (seq !== coin.seq) return;   // a newer open or range won the race
    coin.data = d;
    renderCoin(d);
  } catch(e) {
    if (seq !== coin.seq) return;
    coin.data = null;
    renderCoinError(errText(e));
  }
}

function renderCoin(d){
  $("#coin-body").innerHTML =
    coinHeader(d.token || {}, (d.token || {}).address || coin.addr || "") +
    '<div class="csec" id="coin-stats">' + coinStats(d) + "</div>" +
    coinSnipe(d) +
    '<div class="csec">' +
      '<div class="csec-hd"><h2>price</h2>' +
      '<span class="cread" id="coin-cx-read"></span>' +
      '<span class="grow"></span>' +
      '<div class="rng" id="coin-rng">' + COIN_RANGES.map(r =>
        '<button type="button" data-coinhours="' + r[0] + '"' +
        (numOr((d.chart || {}).hours, coin.hours) === r[0] ? ' class="on"' : "") +
        ">" + r[1] + "</button>").join("") + "</div></div>" +
      '<div id="coin-chartbox">' + coinChart(d) + "</div>" +
    "</div>" +
    '<div class="csec">' + coinHolders(d) + "</div>" +
    coinEarly(d);
  bindCoinChart();
  // The scrim is the scroller in the overlay and the card is the scroller in
  // the dock, so both are reset: clearing only the scrim left the panel opening
  // a second token at the first token's scroll offset.
  $("#coin").scrollTop = 0;
  $("#coin-card").scrollTop = 0;
}

// A failed read leaves nothing behind: a card that half renders reads as fact.
function renderCoinError(text){
  const addr = coin.addr || "";
  $("#coin-body").innerHTML =
    '<div class="chd"><div class="tok"><div class="logo ph">!</div>' +
      '<div><div class="nm">coin not read</div>' +
      '<div class="sym"><span class="mono cp" data-copy="' + esc(addr) +
        '" title="click to copy ' + esc(addr) + '">' + esc(shortHash(addr)) +
      "</span></div></div></div>" +
      '<span class="grow"></span>' +
      '<button class="cclose" data-coinclose="1" title="close (Esc)">close</button>' +
    "</div>" +
    '<div class="csec"><div class="dangerbox">could not read this coin: ' +
      esc(text) + "</div>" +
      '<div class="inline" style="margin-top:9px">' +
        '<button class="ghost" data-coinretry="1">try again</button>' +
        '<span class="hint">nothing else is drawn on purpose</span>' +
      "</div></div>";
}

function closeCoinCard(opts){
  opts = opts || {};
  if (!coinOpen()) return;
  const addr = coin.addr, back = coin.opener;
  coin.seq++;                       // a read still in flight must not paint
  coin.addr = null; coin.data = null; coin.geo = null; coin.opener = null;
  $("#coin").hidden = true;
  $("#coin-body").innerHTML = "";
  document.body.style.overflow = "";
  if (opts.hash !== false) setHash("#" + tab);
  if (opts.focus === false) return;
  // the row that opened the card may have been re-rendered by the tab's own
  // poll since, so fall back to the cell that names the same coin
  let to = back;
  if (!to || !document.contains(to) || to === document.body)
    to = /^0x[0-9a-fA-F]{40}$/.test(addr || "") ? $('[data-coin="' + addr + '"]') : null;
  if (to && to.focus){ try { to.focus(); } catch(e){} }
}

/* ------------------------------------------------------- the card's pieces */
function coinHeader(t, addr){
  return '<div class="chd">' +
    '<div class="tok">' + logoCell(t || {}) +
      '<div><div class="nm">' + esc((t && t.name) || "?") + "</div>" +
      '<div class="sym"><b>' + esc((t && t.symbol) || "?") + "</b></div></div></div>" +
    '<div class="cmeta">' +
      '<span class="mono cp" data-copy="' + esc(addr) + '" title="' +
        esc(addr + " - click to copy") + '">' + esc(shortHash(addr)) + "</span>" +
      pairPill(t || {}) +
    "</div>" +
    '<span class="grow"></span>' +
    // the same links the table cell carries, plus the three that leave this
    // page: the name opens this card instead of jumping straight out, and the
    // card is the only place with room for the venues worth leaving it for
    socialsCell(t || {}, venuesCell(t, addr)) +
    '<button class="cclose" data-coinclose="1" title="close (Esc)">close</button>' +
  "</div>";
}

function coinStats(d){
  const t = d.token || {}, ch = d.chart || {}, h = d.holders || {};
  const sym = t.quote_symbol || "";
  const grad = !!t.graduated;
  const win = coinRangeLabel(numOr(ch.hours, coin.hours));
  const noCurve = "graduated: the curve is gone, so there is no curve price " +
    "or market cap left to read";
  const px = fin(t.price_quote);
  // v is pre-escaped html, the way every other cell in this file is built
  const cards = [
    {k: "price", v: px == null ? DIMDASH : esc(fmtQuote(t.price_quote, sym)),
     cls: px == null ? "" : "acc",
     title: grad ? noCurve : "whole quote per whole token, read off the curve"},
    {k: "mcap", v: grad || fin(t.mcap_quote) == null ? DIMDASH
       : esc(fmtQuote(t.mcap_quote, sym)),
     title: grad ? noCurve : "curve price times the whole supply"},
    {k: "mcap usd", v: grad || fin(t.mcap_usd) == null ? DIMDASH
       : esc(fmtUsd(t.mcap_usd)),
     title: grad ? noCurve : "curve market cap at the latest eth rate"},
    {k: "graduation", v: barCell(t.progress_pct, grad), wide: 1,
     title: "how far the curve is towards its graduation threshold"},
    {k: "age", v: ageCell(t.age_seconds), title: "time since the launch block"},
    {k: "volume " + win, v: esc(fmtQuoteK(ch.volume_quote, sym)),
     title: "buys " + fmtInt(ch.buys) + ", sells " + fmtInt(ch.sells) +
       " - this chart window only"},
    {k: "holders", v: esc(fmtInt(h.count)),
     title: "wallets with a position left, counted from the trade history"},
    {k: "creator fees", v: feesCell(t),
     title: "what this token has paid the wallet that launched it, in " +
       (sym || "the pair token") + ": every sweep the curve has run, plus " +
       "what it is holding for the creator right now"}
  ];
  return '<div class="cstat">' + cards.map(c =>
    '<div class="stat' + (c.wide ? " wide" : "") + '">' +
    '<div class="k">' + esc(c.k) + "</div>" +
    '<div class="v ' + (c.cls || "") + '"' +
    (c.title ? ' title="' + esc(c.title) + '"' : "") + ">" + c.v + "</div></div>").join("") +
    "</div>";
}

function coinSnipe(d){
  const t = d.token || {};
  const s = t.snipe || {};
  const sym = t.quote_symbol || "";
  const pos = s.first_buyer_position || null;
  const pnl = pos ? fin(pos.pnl) : null;
  const floor = !!pos && pos.known === false && pnl != null;
  const rep = !!s.bot || numOr(s.bot_hits, 0) >= 5;
  // nothing to report is two different things: no outside buyer at all, or a
  // buyer the backend holds no trade history for
  const result = pos
    ? pnlSpan(pnl, sym, floor, floor ? T_PNL_FLOOR : T_PNL_QUOTE) +
      " " + roiSpan(pos.roi_pct)
    : DIMDASH + ' <span class="dim">' + (s.first_buyer
      ? "no trade history for this wallet"
      : "no outside buy in the launch window") + "</span>";
  const rows = [
    ["verdict", snipePill(s) + (fin(s.score) == null ? "" :
      ' <span class="mono dim">score ' + esc(fmtInt(s.score)) + "</span>")],
    ["first outside buy", fin(s.delta) == null
      ? DIMDASH + ' <span class="dim">nothing outside the launch transaction</span>'
      : '<span class="mono">' + esc(fmtInt(s.delta)) + "</span>" +
        ' <span class="dim">blocks after the launch</span>'],
    ["first buyer", s.first_buyer ? buyerCell(s.first_buyer, rep) : DIMDASH],
    ["size", fin(s.first_buy_quote) == null ? DIMDASH
      : '<span class="mono">' + esc(fmtQuoteK(s.first_buy_quote, sym)) + "</span>"],
    ["first buyer's result", result]
  ];
  // the same marker the tables put on a sniped coin, so it reads the same way
  return '<div class="csec ' + snipeRowClass(s) + '"' + snipeData(s) + '>' +
    '<div class="csec-hd"><h2>snipe</h2><span class="grow"></span>' +
    '<span class="hint">every buy inside the launch window is at the bottom of this card</span>' +
    "</div>" + kvHtml(rows) + "</div>";
}

function coinChart(d){
  const t = d.token || {}, ch = d.chart || {};
  const sym = t.quote_symbol || "";
  const V = COIN_VB;
  coin.geo = null;
  const pts = [];
  (ch.points || []).forEach(p => {
    if (!p) return;
    const price = fin(p.price);
    if (price == null) return;   // a bucket with no tokens traded has no price
    pts.push({t: numOr(p.t, 0), price: price,
              vol: Math.max(0, numOr(p.volume_quote, 0)),
              buys: numOr(p.buys, 0), sells: numOr(p.sells, 0)});
  });
  // Two points is the floor for a line. One point drawn flat would claim the
  // price never moved, which is the opposite of what a single trade says, and
  // an empty axis claims nothing at all.
  if (pts.length < 2){
    const trades = numOr(ch.buys, 0) + numOr(ch.sells, 0);
    return '<div class="cempty">' + esc(trades === 0
      ? "no trades in this window, so there is no price line to draw. try a wider range"
      : "only one bucket in this window carries a price, so there is no line to draw. " +
        "try a wider range") + "</div>";
  }

  const plotW = V.w - V.l - V.r;
  const priceTop = V.t;
  const priceBot = V.h - V.b - V.vol - V.gap;
  const volBot = V.h - V.b;

  let lo = Infinity, hi = -Infinity;
  pts.forEach(p => { if (p.price < lo) lo = p.price; if (p.price > hi) hi = p.price; });
  const lt = fin(ch.launch_ts), lp = fin(ch.launch_price_quote);
  // the launch never happened at the first trade, and that first jump is the
  // part everyone is looking at, so it is inside the price range too
  if (lp != null){ if (lp < lo) lo = lp; if (lp > hi) hi = lp; }
  let pad = (hi - lo) * 0.08;
  if (!(pad > 0)) pad = Math.abs(hi) * 0.05 || 1e-18;
  lo -= pad; hi += pad;
  if (!(hi > lo) || !isFinite(lo) || !isFinite(hi))
    return '<div class="cempty">this window has no usable price range to draw</div>';

  let t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  if (lt != null){ if (lt < t0) t0 = lt; if (lt > t1) t1 = lt; }
  const span = t1 - t0;
  const X = t => span > 0 ? V.l + (t - t0) / span * plotW : V.l + plotW / 2;
  const Y = p => priceBot - (p - lo) / (hi - lo) * (priceBot - priceTop);
  const xs = pts.map(p => X(p.t));
  const ys = pts.map(p => Y(p.price));
  let vmax = 0;
  pts.forEach(p => { if (p.vol > vmax) vmax = p.vol; });
  // the volume band is scaled to itself, never to the price
  const vh = v => vmax > 0 ? Math.max(0, Math.min(1, v / vmax)) * V.vol : 0;

  const line = pts.map((p, i) =>
    (i ? "L" : "M") + xs[i].toFixed(1) + " " + ys[i].toFixed(1)).join(" ");
  const area = line + "L" + xs[xs.length - 1].toFixed(1) + " " + priceBot +
    "L" + xs[0].toFixed(1) + " " + priceBot + "Z";
  const bw = Math.max(2, Math.min(26, plotW / pts.length * 0.68));
  const bars = pts.map((p, i) => {
    const h1 = vh(p.vol);
    if (h1 < 0.5) return "";
    const cls = p.buys > p.sells ? "vbar b" : (p.sells > p.buys ? "vbar s" : "vbar");
    return '<rect class="' + cls + '" x="' + (xs[i] - bw / 2).toFixed(1) + '" y="' +
      (volBot - h1).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' +
      h1.toFixed(1) + '"/>';
  }).join("");
  const grid = [0, 1, 2, 3].map(i => {
    const y = (priceTop + (priceBot - priceTop) * i / 3).toFixed(1);
    return '<line class="gline" x1="' + V.l + '" y1="' + y + '" x2="' +
      (V.w - V.r) + '" y2="' + y + '"/>';
  }).join("");

  let marker = "";
  if (lt != null && lp != null){
    const lx = X(lt).toFixed(1);
    marker = '<line class="lm" x1="' + lx + '" y1="' + priceTop + '" x2="' + lx +
      '" y2="' + volBot + '"/>' +
      '<circle class="lmdot" cx="' + lx + '" cy="' + Y(lp).toFixed(1) + '" r="3.5">' +
      "<title>launch at " + esc(fmtClock(lt)) + "</title></circle>";
  }

  coin.geo = {xs: xs, ys: ys, pts: pts, sym: sym};
  return '<svg class="chart-svg" id="coin-svg" viewBox="0 0 ' + V.w + " " + V.h + '" ' +
      'role="img" aria-label="' +
      esc((t.symbol || "this token") + " price and volume") + '">' +
    // The fill under the price line. Declared here so the two stops can be
    // styled from chart.css - an SVG stop-color cannot read a var() from an
    // attribute, only from a rule, so the class is what carries the colour.
    '<defs><linearGradient id="cgrad" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" class="gs0"/><stop offset="1" class="gs1"/>' +
      '</linearGradient></defs>' +
    grid +
    '<line class="gline" x1="' + V.l + '" y1="' + volBot + '" x2="' + (V.w - V.r) +
      '" y2="' + volBot + '"/>' +
    bars +
    '<path class="area" d="' + area + '"/>' +
    '<path class="line" d="' + line + '"/>' +
    marker +
    '<g class="cxg" id="coin-cx" style="display:none">' +
      '<line class="cx" id="coin-cx-l" x1="0" y1="' + priceTop + '" x2="0" y2="' +
        volBot + '"/>' +
      '<circle class="cxdot" id="coin-cx-d" cx="0" cy="0" r="3.6"/>' +
    "</g></svg>" +
    '<div class="clab">' +
      '<span><i class="k-price"></i>price</span>' +
      '<span><i class="k-buy"></i>buys</span>' +
      '<span><i class="k-sell"></i>sells</span>' +
      (marker ? '<span><i class="k-launch"></i>launch</span>' : "") +
      '<span class="grow"></span>' +
      '<span>' + esc(fmtQuote(ch.min_price_quote, sym)) + " low / " +
        esc(fmtQuote(ch.max_price_quote, sym)) + " high</span>" +
    "</div>";
}

// the crosshair reads the nearest bucket to the cursor: the points are sparse,
// so the nearest one is the only honest answer to "what happened around here"
function bindCoinChart(){
  const svg = $("#coin-svg");
  const g = $("#coin-cx"), ln = $("#coin-cx-l"), dot = $("#coin-cx-d");
  const read = $("#coin-cx-read");
  if (!svg || !coin.geo || !g || !ln || !dot) return;
  const geo = coin.geo;
  const off = () => { g.style.display = "none"; if (read) read.innerHTML = ""; };
  const move = e => {
    const r = svg.getBoundingClientRect();
    if (!(r.width > 0)) return;
    const ux = (e.clientX - r.left) / r.width * COIN_VB.w;
    let best = 0, bd = Infinity;
    for (let i = 0; i < geo.xs.length; i++){
      const dd = Math.abs(geo.xs[i] - ux);
      if (dd < bd){ bd = dd; best = i; }
    }
    const p = geo.pts[best];
    ln.setAttribute("x1", geo.xs[best].toFixed(1));
    ln.setAttribute("x2", geo.xs[best].toFixed(1));
    dot.setAttribute("cx", geo.xs[best].toFixed(1));
    dot.setAttribute("cy", geo.ys[best].toFixed(1));
    g.style.display = "";
    if (read) read.innerHTML = "<b>" + esc(fmtClock(p.t)) + "</b> " +
      esc(fmtQuote(p.price, geo.sym)) + " / vol " + esc(fmtQuoteK(p.vol, geo.sym)) +
      " / " + esc(fmtInt(p.buys)) + "b " + esc(fmtInt(p.sells)) + "s";
  };
  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerleave", off);
  svg.addEventListener("pointercancel", off);
}

function coinRangeState(){
  $$("#coin-rng button").forEach(b =>
    b.classList.toggle("on", numOr(b.dataset.coinhours, -1) === coin.hours));
}

// Only the stat strip and the chart answer to the range: the holders and the
// verdict are the same whichever window is asked for.
async function setCoinHours(h){
  h = numOr(h, 6);
  if (!coin.addr || h === coin.hours) return;
  const prev = coin.hours;
  const box = $("#coin-chartbox"), stats = $("#coin-stats");
  if (!box || !stats) return;      // the first read has not landed yet
  const addr = coin.addr, seq = ++coin.seq;
  coin.hours = h;
  coinRangeState();
  box.innerHTML = '<div class="cempty">reading the ' + esc(coinRangeLabel(h)) +
    " window...</div>";
  try {
    const d = await coinFetch(addr, h, coin.holders);
    if (seq !== coin.seq) return;
    coin.data = d;
    stats.innerHTML = coinStats(d);
    box.innerHTML = coinChart(d);
    bindCoinChart();
  } catch(e) {
    if (seq !== coin.seq) return;
    coin.hours = prev;             // the buttons keep matching what is drawn
    coinRangeState();
    box.innerHTML = '<div class="cempty err">could not read the ' +
      esc(coinRangeLabel(h)) + " window: " + esc(errText(e)) + "</div>";
  }
}

/* -------------------------------------------------------- holders and buys */
function coinHolders(d){
  const t = d.token || {}, h = d.holders || {};
  const sym = t.quote_symbol || "";
  const rows = h.rows || [];
  const count = numOr(h.count, rows.length);
  const head = '<div class="csec-hd"><h2>holders</h2><span class="grow"></span>' +
    '<span class="hint">' + esc(fmtInt(count)) + " holders, the " +
    esc(fmtInt(rows.length)) + " listed hold " + esc(fmtPct(h.held_pct, 1)) +
    " of the supply</span></div>";
  if (!rows.length) return head + '<div class="cempty">no wallet holds this token yet</div>';
  let html = head + '<div class="tw"><table class="ctab"><thead><tr>' +
    '<th style="width:32px">#</th><th>wallet</th>' +
    '<th style="width:104px">tokens</th>' +
    '<th style="width:72px">share</th>' +
    '<th style="width:104px">paid in</th>' +
    '<th style="width:116px">profit</th>' +
    '<th style="width:78px">roi</th>' +
    '<th style="width:158px">tags</th></tr></thead><tbody>' +
    rows.map((r, i) => coinHolderRow(r, i, sym)).join("") + "</tbody></table></div>";
  if (count > rows.length)
    html += '<div class="hint" style="margin-top:8px">showing ' +
      esc(fmtInt(rows.length)) + " of " + esc(fmtInt(count)) +
      " holders - the rest are cut off by the row cap this card asked for</div>";
  return html;
}

function coinHolderRow(r, i, sym){
  const tags = [];
  if (r.is_deployer)
    tags.push('<span class="pill s" title="the wallet that deployed this token">deployer</span>');
  if (r.is_first_buyer)
    tags.push('<span class="pill g" title="the first buy by someone other than the deployer">first buy</span>');
  if (r.bot)
    tags.push('<span class="pill h" title="first into ' + esc(fmtInt(r.bot_hits)) +
      ' launches, 5 or more counts as a repeat">bot</span>');
  return "<tr>" +
    '<td class="dim2 mono">' + (i + 1) + "</td>" +
    "<td>" + (r.wallet ? buyerCell(r.wallet, false) : DIMDASH) + "</td>" +
    '<td class="mono">' + esc(fmtBig(r.tokens)) + "</td>" +
    '<td class="mono">' + esc(fmtPct(r.share_pct, 2)) + "</td>" +
    '<td class="mono">' + esc(fmtQuoteK(r.spent_quote, sym)) + "</td>" +
    "<td>" + pnlSpan(r.pnl_quote, sym, r.pnl_known === false,
      r.pnl_known === false ? T_PNL_FLOOR : T_PNL_QUOTE) + "</td>" +
    "<td>" + roiSpan(r.roi_pct) + "</td>" +
    "<td>" + (tags.length ? tags.join(" ") : DIMDASH) + "</td></tr>";
}

function coinEarly(d){
  const t = d.token || {}, s = t.snipe || {};
  const sym = t.quote_symbol || "";
  const list = s.early_buys || [];
  const lb = fin(t.launch_block);
  const head = '<div class="csec-hd"><h2>first buys</h2><span class="grow"></span>' +
    '<span class="hint">every buy inside the launch window, the ones the verdict was made from</span>' +
    "</div>";
  if (!list.length)
    return head + '<div class="cempty">no buy landed inside this launch window</div>';
  return head + '<div class="tw"><table class="ctab"><thead><tr>' +
    '<th style="width:32px">#</th><th>wallet</th>' +
    '<th style="width:104px">after launch</th>' +
    '<th style="width:118px">size</th>' +
    '<th style="width:92px">kind</th>' +
    '<th style="width:92px">at</th>' +
    '<th style="width:64px"></th></tr></thead><tbody>' +
    list.map((e, i) => {
      const blk = fin(e.block);
      const delta = (blk != null && lb != null) ? Math.max(0, blk - lb) : null;
      // the buy that rode inside the launch transaction is the bundled one
      const bundled = !!(t.launch_tx && e.tx &&
        String(e.tx).toLowerCase() === String(t.launch_tx).toLowerCase());
      return "<tr>" +
        '<td class="dim2 mono">' + (i + 1) + "</td>" +
        "<td>" + (e.buyer ? buyerCell(e.buyer, false) : DIMDASH) + "</td>" +
        '<td class="mono" title="' + esc("block " + fmtInt(e.block) +
          ", the launch was block " + fmtInt(t.launch_block)) + '">' +
          (delta == null ? DIMDASH : esc(fmtInt(delta))) + "</td>" +
        '<td class="mono">' + esc(fmtQuoteK(e.quote, sym)) + "</td>" +
        "<td>" + (bundled
          ? '<span class="lb bundled" title="the buy rode inside the launch transaction">bundled</span>'
          : '<span class="lb early" title="bought after the launch transaction">race</span>') +
          "</td>" +
        '<td class="mono dim">' + esc(fmtClock(e.ts)) + "</td>" +
        "<td>" + (e.tx ? '<span class="mini cp" data-copy="' + esc(e.tx) + '" title="' +
          esc(e.tx) + ' - click to copy">tx</span>' : "") + "</td></tr>";
    }).join("") + "</tbody></table></div>";
}

/* ------------------------------------------------------------- the keyboard */
// The card is a modal, so tab stays inside it and escape always gets out.
// Docked it is not a modal: the table beside it is part of the same task, and
// trapping tab in the panel would put every row behind a click. Escape keeps
// its meaning in both, because a panel with no keyboard way out is a trap.
function coinKeydown(e){
  if (!coinOpen()) return;
  if (e.key === "Escape" || e.key === "Esc"){
    e.preventDefault();
    closeCoinCard();
    return;
  }
  if (e.key !== "Tab" || coinDocked()) return;
  const card = $("#coin-card");
  const f = $$("#coin-card a[href],#coin-card button,#coin-card input," +
    "#coin-card select,#coin-card textarea,#coin-card [tabindex]:not([tabindex='-1'])")
    .filter(el => !el.disabled && el.offsetParent !== null);
  if (!f.length){ e.preventDefault(); card.focus(); return; }
  const first = f[0], last = f[f.length - 1], a = document.activeElement;
  const inside = card.contains(a);
  if (e.shiftKey && (a === first || !inside)){ e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && (a === last || !inside)){ e.preventDefault(); first.focus(); }
}

/* ------------------------------------------------------------ interaction */
document.addEventListener("click", e => {
  const t = e.target;
  if (!t || !t.closest) return;

  // the coin card is modal, so while it is open its own controls are settled
  // first and a click on the backdrop behind it closes it
  if (coinOpen()){
    if (t.closest("[data-coinclose]")){ closeCoinCard(); return; }
    if (t.closest("[data-coinretry]")){
      $("#coin-body").innerHTML = coinSkeleton();
      loadCoin();
      return;
    }
    const ch = t.closest("[data-coinhours]");
    if (ch){ setCoinHours(numOr(ch.dataset.coinhours, 6)); return; }
    // Docked, everything outside the panel belongs to the page. There is no
    // backdrop to have clicked on, and the row that was just clicked is a
    // request to show that token - so falling through to the handlers below is
    // what makes the panel follow the table instead of swallowing the click.
    if (!coinDocked() && !t.closest("#coin-card")){
      // a drag that began inside the card and ended on the backdrop is not a
      // click on the backdrop
      if (coin.backdrop) closeCoinCard();
      return;
    }
    // anything else inside the card is one of the shared affordances below
  }

  const ck = t.closest("[data-coin]");
  if (ck && ck.dataset.coin){ openCoinCard(ck.dataset.coin); return; }

  // a wallet address opens the leaderboard, so it is checked before the copy
  // affordances: the copy chip beside it is a separate element, never nested
  // The wallet behind a snipe opens its own page, which is the whole list of
  // its launches and what is known about them - not the leaderboard row, whose
  // dozen-token peek is still reachable from the Snipers tab.
  const sw = t.closest("[data-sniperwallet], [data-snipepage]");
  if (sw){
    const a = sw.dataset.sniperwallet || sw.dataset.snipepage;
    if (a){ setTab("sniper", a); return; }
  }

  const exb = t.closest("[data-expand]");
  if (exb){ toggleSniper(exb.dataset.expand); return; }

  // the one-click duplicate sits beside the copy chip and shares none of its
  // markup, so it is matched before the pair below rather than inside it
  const bl = t.closest("[data-blink]");
  if (bl && bl.dataset.blink){ blinkCopy(bl.dataset.blink); return; }

  // one lookup for both copy affordances so the innermost element always wins,
  // whichever order the attributes happen to sit in
  const hit = t.closest("[data-copytok],[data-copy]");
  if (hit){
    if (hit.dataset.copytok){ openCopyFor(hit.dataset.copytok); return; }
    if (hit.dataset.copy){
      const value = hit.dataset.copy;
      if (value){
        // a second click inside the 700ms window used to capture "copied"
        // as the label to restore, so double-clicking a chip - a natural
        // "did that register" gesture - erased the address for good
        if (hit.dataset.copying) return;
        copyText(value).then(() => {
          const old = hit.innerHTML;
          hit.dataset.copying = "1";
          hit.textContent = "copied";
          setTimeout(() => {
            hit.innerHTML = old;
            delete hit.dataset.copying;
          }, 700);
        }).catch(() => toast("clipboard refused", "err"));
      }
      return;
    }
  }

  const tabBtn = t.closest("[data-tab]");
  if (tabBtn){ setTab(tabBtn.dataset.tab, null); return; }

  const go = t.closest("[data-goto]");
  if (go){ setTab(go.dataset.goto, null); return; }

  const plan = t.closest("[data-planload]");
  if (plan){ loadPlanById(plan.dataset.planload); return; }

  const pdel = t.closest("[data-plandel]");
  if (pdel){ deletePlanById(pdel.dataset.plandel); return; }

  const ex = t.closest("[data-exdel]");
  if (ex){ const r = ex.closest(".exrow"); if (r) r.remove(); return; }

  const wl = t.closest("[data-wlview]");
  if (wl){
    viewAddr = wl.dataset.wlview;
    renderWalletChrome();
    refreshWallet();
    return;
  }
  const wd = t.closest("[data-wldel]");
  if (wd){ delWatch(wd.dataset.wldel); return; }
});

