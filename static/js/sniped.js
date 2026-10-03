// /api/snipes treats an omitted labels param as "all of them", so the filter
// always has to name a set: keeping the last box checked beats silently widening
// the query to slow and none
function snipeLabels(){
  // the order matches the contract example so the request url reads the same
  const on = [];
  if ($("#sn-lb-sniped").checked) on.push("sniped");
  if ($("#sn-lb-bundled").checked) on.push("bundled");
  if ($("#sn-lb-early").checked) on.push("early");
  return on.length ? on.join(",") : "sniped,bundled";
}
function keepOneLabel(){
  if ($("#sn-lb-bundled").checked || $("#sn-lb-sniped").checked || $("#sn-lb-early").checked) return;
  $("#sn-lb-sniped").checked = true;
  toast("one label has to stay on, back to sniped");
}

async function refreshSniped(fromPoll){
  const req = latest("sniped", fromPoll === true);
  if (!req) return;
  try {
    const labels = snipeLabels();
    const url = qs("/api/snipes", {
      labels: labels,
      sort: $("#sn-sort").value,
      limit: 300,
      only_bots: $("#sn-bots").checked ? "1" : "",
      max_delta: $("#sn-maxdelta").value.trim(),
      q: $("#sn-q").value.trim(),
      quote: $("#sn-quote").value
    });
    const d = await api(url, {signal: req.signal});
    if (req.stale()) return;
    const rows = d.tokens || [];
    const now = fin(d.now) || Math.floor(Date.now() / 1000);
    $("#sn-tb").innerHTML = rows.map((t, i) => snipeRow(t, i, now)).join("");
    setEmpty("sn-empty", rows.length ? null : "no launches carry these labels");
    // the api paginates, so say how much of the match is actually on screen
    $("#sn-count").textContent = rows.length + (d.total != null ? " of " + fmtInt(d.total) : "") +
      " rows / " + labels;
    renderSnipeStats(d.stats);
    live(true);
  } catch(e) {
    // an abort we caused ourselves by starting a newer request is
    // not news, and painting it would overwrite the newer result
    if (req.stale()) return;
    $("#sn-count").textContent = "-";
    $("#sn-stats").innerHTML = "";
    $("#sn-stack").innerHTML = '<i data-lb="unindexed" style="width:100%"></i>';
    $("#sn-legend").innerHTML = "";
    setEmpty("sn-empty", "snipes fetch failed: " + errText(e), true);
    live(false);
  }
}

// the racing wallet. the flat first_buyer now sits on the row itself, so it
// still works when the nested snipe object is absent. the address opens the
// wallet on the Snipers leaderboard, the chip beside it copies
function buyerCell(addr, rep){
  // Two things to click in one cell, so they need a gap between them:
  // written as two spans in a row the chip landed against the last digits
  // and the cell read as one broken string, 0x95...641ecopy.
  return '<span class="addr">' +
    '<span class="mono cp ' + (rep ? "rep" : "") + '" data-sniperwallet="' + esc(addr) +
    '" title="' + esc(addr + " - open on the Snipers leaderboard") + '">' +
    esc(short(addr)) + "</span>" +
    '<span class="mini cp" data-copy="' + esc(addr) + '" title="click to copy ' +
    esc(addr) + '">copy</span></span>';
}

function snipeRow(t, i, now){
  const s = snipeOf(t) || {};
  const buyer = t.first_buyer || s.first_buyer || "";
  const hits = fin(s.bot_hits);
  const rep = !!s.bot || (hits != null && hits >= 5);
  const age = t.age_seconds != null ? t.age_seconds
    : (t.launch_ts ? Math.max(0, now - t.launch_ts) : null);
  return '<tr class="' + snipeRowClass(s) + '"' + snipeData(s) +
    ' data-addr="' + esc(t.address) + '">' +
    '<td class="dim2 mono">' + (i + 1) + "</td>" +
    "<td>" + tokenCell(t, "https://www.ponsfamily.com/launchpad/" + t.address) + "</td>" +
    "<td>" + ageCell(age) + "</td>" +
    '<td class="mono">' + (fin(s.delta) != null ? esc(fmtInt(s.delta)) : DIMDASH) + "</td>" +
    "<td>" + (buyer ? buyerCell(buyer, rep) : DIMDASH) + "</td>" +
    // whole quote units, already scaled by the backend: no rescaling here
    '<td class="mono">' + esc(fmtQuoteK(s.first_buy_quote, t.quote_symbol || "")) + "</td>" +
    '<td class="mono">' + esc(fmtPct(s.first_buy_share, 1)) + "</td>" +
    '<td class="mono">' + esc(fmtInt(s.early_buyers)) + "</td>" +
    "<td>" + (rep
      ? '<span class="rep hi" title="this wallet was the first outside buyer of ' +
        esc(fmtInt(hits)) + ' launches">' + esc(fmtInt(hits)) + "</span>"
      : '<span class="dim mono">' + esc(fmtInt(hits)) + "</span>") + "</td>" +
    "<td>" + (s.bot ? '<span class="pill h">bot</span>' : DIMDASH) + "</td>" +
    "<td>" + volCell(volOf(t), t.quote_symbol) + "</td>" +
    "<td>" + copyBtn(t.address) + "</td></tr>";
}

// the stats cover every indexed launch, the table below only the filtered rows
function renderSnipeStats(s){
  s = s || {};
  const segs = [];
  let sum = 0;
  LABELS.forEach(l => {
    // a label the backend has not reported at all is not the same as zero of
    // them, so it stays out of the legend rather than claiming a count
    if (!Object.prototype.hasOwnProperty.call(s, l)) return;
    const n = numOr(s[l], 0);
    sum += n;
    segs.push([l, n]);
  });
  // whatever indexed does not account for is a launch the history walk has not
  // reached yet, which the contract names unknown and deliberately gives no chip
  const idx = fin(s.indexed);
  const unknown = idx != null ? Math.max(0, idx - sum) : 0;
  const total = sum + unknown;
  if (unknown > 0) segs.push(["unknown", unknown]);

  const cards = [
    ["indexed", fmtInt(idx), ""],
    ["bot wallets", fmtInt(s.bot_wallets), numOr(s.bot_wallets, 0) > 0 ? "hot" : ""],
    ["bundled", fmtInt(s.bundled), "hot"],
    ["sniped", fmtInt(s.sniped), "hot"],
    ["early", fmtInt(s.early), "warn"],
    ["slow", fmtInt(s.slow), ""],
    ["none", fmtInt(s.none), ""]
  ];
  $("#sn-stats").innerHTML = cards.map(c =>
    '<div class="stat"><div class="k">' + esc(c[0]) + '</div><div class="v ' + c[2] + '">' +
    esc(c[1]) + "</div></div>").join("");

  $("#sn-stack").innerHTML = total > 0
    ? segs.filter(g => g[1] > 0).map(g =>
        '<i data-lb="' + esc(g[0]) + '" style="width:' +
        (g[1] / total * 100).toFixed(3) + '%" title="' +
        esc(g[0] + " " + fmtInt(g[1])) + '"></i>').join("")
    : '<i data-lb="unindexed" style="width:100%"></i>';
  $("#sn-legend").innerHTML = segs.map(g =>
    '<span><i class="dot" data-lb="' + esc(g[0]) + '"></i>' + esc(g[0]) +
    " <b>" + esc(fmtInt(g[1])) + "</b>" +
    (total > 0 ? ' <span class="dim2">' + esc(fmtPct(g[1] / total * 100, 1)) + "</span>" : "") +
    "</span>").join("");
}

function mountSniped(){ refreshSniped(); startTimer(refreshSniped); }

/* ---------------------------------------------------------------- snipers */
// the wallets doing the sniping. hits is a lifetime count while the token list
// under a wallet is capped by per_wallet and by the index window, so the two
// never match and spent_usd only ever covers what is listed
