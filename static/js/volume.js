async function refreshVolume(fromPoll){
  const req = latest("volume", fromPoll === true);
  if (!req) return;
  try {
    const win = $("#v-window").value;
    const url = qs("/api/volume", {
      window: win,
      sort: $("#v-sort").value,
      limit: 200,
      q: $("#v-q").value.trim(),
      quote: $("#v-quote").value,
      graduated: $("#v-hidegrad").checked ? "false" : ""
    });
    const d = await api(url, {signal: req.signal});
    if (req.stale()) return;
    const rows = d.tokens || [];
    const now = fin(d.now) || Math.floor(Date.now() / 1000);
    $("#v-tb").innerHTML = rows.map((t, i) => volumeRow(t, i, now)).join("");
    setEmpty("v-empty", rows.length ? null : "no trades in this window");
    $("#v-count").textContent = rows.length + " rows";
    renderVolumeSummary(rows, d.window || win);
    live(true);
  } catch(e) {
    // an abort we caused ourselves by starting a newer request is
    // not news, and painting it would overwrite the newer result
    if (req.stale()) return;
    $("#v-count").textContent = "-";
    $("#v-summary").innerHTML = "";
    setEmpty("v-empty", "volume fetch failed: " + errText(e), true);
    live(false);
  }
}

function volumeRow(t, i, now){
  const grad = !!t.graduated;
  const sym = t.quote_symbol || "";
  const net = fin(t.net_quote);
  return '<tr class="' + snipeRowClass(snipeOf(t)) + '" data-addr="' + esc(t.address) + '">' +
    '<td class="dim2 mono">' + (i + 1) + "</td>" +
    "<td>" + tokenCell(t, "https://www.ponsfamily.com/launchpad/" + t.address) + "</td>" +
    "<td>" + ageCell(t.age_seconds) + "</td>" +
    "<td>" + pairPill(t) + "</td>" +
    '<td class="mono">' + (fin(t.volume_usd) != null ? esc(fmtUsd(t.volume_usd)) : DIMDASH) + "</td>" +
    '<td class="mono">' + esc(fmtQuoteK(t.volume_quote, sym)) + "</td>" +
    '<td class="mono">' + esc(fmtInt(t.buys)) + "</td>" +
    '<td class="mono">' + esc(fmtInt(t.sells)) + "</td>" +
    '<td class="mono ' + (net == null ? "" : net >= 0 ? "pos" : "neg") + '">' +
      esc(fmtQuoteK(t.net_quote, sym)) + "</td>" +
    '<td class="mono">' + esc(fmtInt(t.buyers)) + "</td>" +
    "<td>" + lastAgeCell(t, now) + "</td>" +
    '<td class="mono">' + (grad ? DIMDASH : esc(fmtUsd(t.mcap_usd))) + "</td>" +
    "<td>" + barCell(t.progress_pct, grad) + "</td>" +
    "<td>" + snipePill(snipeOf(t)) + "</td>" +
    "<td>" + copyBtn(t.address) + "</td></tr>";
}

function renderVolumeSummary(rows, win){
  let volUsd = 0, withUsd = 0, trades = 0;
  rows.forEach(t => {
    const u = fin(t.volume_usd), tr = fin(t.trades);
    if (u != null){ volUsd += u; withUsd++; }
    if (tr != null) trades += tr;
  });
  const top = rows.slice().sort((a, b) => numOr(b.volume_usd, -1) - numOr(a.volume_usd, -1))[0];
  const share = top && volUsd > 0 ? numOr(top.volume_usd, 0) / volUsd * 100 : null;
  const topTxt = top ? (top.symbol || "?") + (share == null ? "" : " " + fmtPct(share, 1)) : "-";
  const cards = [
    ["volume " + win, withUsd ? fmtUsd(volUsd) : "-", "acc"],
    ["trades", fmtInt(trades), ""],
    ["tokens traded", fmtInt(rows.length), ""],
    ["top by volume", topTxt, top && share != null && share >= 50 ? "warn" : "sm"]
  ];
  $("#v-summary").innerHTML = cards.map(c =>
    '<div class="stat"><div class="k">' + esc(c[0]) + '</div><div class="v ' + c[2] + '">' +
    esc(c[1]) + "</div></div>").join("") +
    '<div class="hint" style="flex:1 1 100%">totals cover the ' + rows.length +
    " loaded rows" + (withUsd < rows.length ? " (" + (rows.length - withUsd) + " without a usd rate)" : "") + "</div>";
}

function mountVolume(){ refreshVolume(); startTimer(refreshVolume); }

/* ----------------------------------------------------------------- sniped */
// the launches somebody got in ahead of the field on. the row shape is the same
// one /api/volume returns, so this table reuses the volume tab's cells
