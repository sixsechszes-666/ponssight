let swRows = [], swNow = 0, swFocus = null, swAutoDone = false;
const swOpen = {};

async function refreshSnipers(fromPoll){
  const req = latest("snipers", fromPoll === true);
  if (!req) return;
  try {
    const url = qs("/api/snipers", {
      sort: $("#sw-sort").value,
      limit: 100,
      min_hits: $("#sw-minhits").value.trim(),
      per_wallet: $("#sw-per").value
    });
    const d = await api(url, {signal: req.signal});
    if (req.stale()) return;
    swRows = d.snipers || [];
    swNow = fin(d.now) || Math.floor(Date.now() / 1000);
    renderSnipers();
    const tracked = fin((d.stats || {}).wallets);
    $("#sw-count").textContent = swRows.length +
      (tracked != null ? " of " + fmtInt(tracked) : "") + " wallets";
    renderSniperStats(d.stats, swRows);
    live(true);
  } catch(e) {
    // an abort we caused ourselves by starting a newer request is
    // not news, and painting it would overwrite the newer result
    if (req.stale()) return;
    $("#sw-count").textContent = "-";
    $("#sw-stats").innerHTML = "";
    $("#sw-tb").innerHTML = "";
    swRows = [];
    msg("sw-msg", "");
    setEmpty("sw-empty", "snipers fetch failed: " + errText(e), true);
    live(false);
  }
}

// the wallet total is dollars now, each launch converted at its own pair's
// rate, so it can be read against another wallet or against the per-launch
// figures below it. Launches left out for either reason are named: a total that
// silently dropped some is a floor and must not read as a complete one.
function sniperProfitCell(w){
  const unknown = numOr(w.pnl_unknown, 0);   // the backend could not price these
  const noRate = numOr(w.pnl_no_rate, 0);    // these had no known pair rate
  const bits = [];
  if (unknown > 0) bits.push(fmtInt(unknown) + " unpriced");
  if (noRate > 0) bits.push(fmtInt(noRate) + " with no known rate");
  const moved = unknown + noRate;
  const floor = !!w.pnl_partial || noRate > 0;
  const title = floor
    ? "a floor, not a total: " + fmtInt(moved) + " of this wallet's " +
      fmtInt(w.hits) + " first buys are left out of it (" + bits.join(", ") +
      "), so the real figure is higher. Realised plus unrealised over the " +
      "launches that could be converted, at each pair's own rate."
    : "realised plus unrealised over every launch this wallet was first into, " +
      "converted to dollars at each pair's own rate";
  return pnlSpan(w.pnl_usd, "", floor, title) + (moved > 0
    ? '<span class="cellnote dim2 mono" title="' + esc(bits.join(", ")) + '">' +
      esc(fmtInt(moved)) + " missing</span>" : "");
}
// a per-launch figure is in one asset, so it keeps that launch's own symbol and
// stays honest; the dollar twin goes in the tooltip, where it costs the 126px
// column no width and can still be read against the wallet total above
function tokenPnlCell(tk, sym){
  const p = tk.position;
  if (!p) return DIMDASH;
  const usd = fin(p.pnl_usd);
  const rate = fin(tk.quote_usd);
  const title = (p.known === false ? T_PNL_FLOOR : T_PNL_QUOTE) + (usd == null
    ? "; this launch has no known pair rate, so there is no dollar figure for it"
    : "; " + fmtUsdSigned(usd) + " at the rate the backend used" +
      (rate == null ? "" : ", " + fmtUsd(rate) + " per " + (sym || "token")));
  return pnlSpan(p.pnl, sym, p.known === false, title) + " " + roiSpan(p.roi_pct);
}
function walletCell(addr){
  return '<span class="mono cp" data-copy="' + esc(addr) + '" title="click to copy ' +
    esc(addr) + '">' + esc(short(addr)) + "</span>";
}

// the buys this wallet was first into, one per launch. a list shorter than hits
// is normal and the header says so rather than pretending otherwise
function sniperDetRow(w, open){
  const toks = w.tokens || [];
  const fsym = w.quote_symbol || null;
  let inner;
  if (!toks.length){
    inner = '<div class="hint">' + (numOr(w.hits, 0) > 0
      ? "no single buy to list: every launch this wallet was first into has aged out of the index window, or falls outside the per-wallet cap"
      : "no early buy indexed for this wallet yet") + "</div>";
  } else {
    // the nested table scrolls inside itself, the way every other table here
    // does, so the extra column cannot push the page sideways on a phone
    inner = '<div class="tw"><table><thead><tr><th>Token</th>' +
      '<th style="width:70px">Delta</th>' +
      '<th style="width:118px">Buy</th>' +
      '<th style="width:126px">P&amp;L</th>' +
      '<th style="width:74px">Share</th>' +
      '<th style="width:82px">Kind</th>' +
      '<th style="width:78px">Launched</th>' +
      '<th style="width:64px"></th></tr></thead><tbody>' +
      toks.map(tk => {
        const age = tk.launch_ts ? Math.max(0, swNow - tk.launch_ts) : null;
        return "<tr>" +
          "<td>" + tokenCell(tk, tk.address
            ? "https://www.ponsfamily.com/launchpad/" + tk.address : null) + "</td>" +
          '<td class="mono" title="blocks between the launch and this first buy">' +
            (fin(tk.delta) != null ? esc(fmtInt(tk.delta)) : DIMDASH) + "</td>" +
          // whole quote units, already scaled, same formatter as everything else
          '<td class="mono">' + esc(fmtQuote(tk.quote, tk.quote_symbol || fsym || "")) + "</td>" +
          "<td>" + tokenPnlCell(tk, tk.quote_symbol || fsym || "") + "</td>" +
          '<td class="mono" title="percent of the graduation threshold">' +
            esc(fmtPct(tk.share, 2)) + "</td>" +
          "<td>" + (tk.bundled
            ? '<span class="lb bundled" title="the buy rode inside the launch transaction">bundled</span>'
            : '<span class="lb early" title="bought after the launch transaction">race</span>') +
            "</td>" +
          "<td>" + ageCell(age) + "</td>" +
          "<td>" + copyBtn(tk.address) + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }
  if (toks.length && numOr(w.hits, 0) > toks.length){
    inner += '<div class="hint" style="margin-top:8px">showing ' + esc(fmtInt(toks.length)) +
      " of " + esc(fmtInt(w.hits)) +
      " lifetime first buys - the rest are capped per wallet or aged out of the index window</div>";
  }
  return '<tr class="det"' + (open ? "" : " hidden") + '><td colspan="8">' +
    '<div class="detin">' + inner + "</div></td></tr>";
}

function sniperRow(w, i){
  const addr = w.address || "";
  const toks = w.tokens || [];
  const hits = fin(w.hits);
  const rep = !!w.bot || (hits != null && hits >= 5);
  const last = fin(w.last_ts);
  const lastAge = last != null && swNow ? Math.max(0, swNow - last) : null;
  const open = !!swOpen[addr];
  const focused = swFocus && String(swFocus).toLowerCase() === addr.toLowerCase();
  return '<tr class="wrow' + (rep ? " rep" : "") + (open ? " open" : "") +
    (focused ? " on" : "") + '" data-wrow="' + esc(addr) + '"' +
    (focused ? ' data-focus="1"' : "") + ">" +
    '<td class="dim2 mono">' + (i + 1) + "</td>" +
    "<td>" + walletCell(addr) + "</td>" +
    "<td>" + (rep ? '<span class="pill h" title="' +
      esc(fmtInt(hits) + " lifetime first buys, 5 or more counts as a repeat") +
      '">bot</span>' : DIMDASH) + "</td>" +
    '<td class="mono ' + (rep ? "rep" : "") + '">' + esc(fmtInt(hits)) + "</td>" +
    "<td>" + ageCell(lastAge) + "</td>" +
    // dollars here too: the launches listed below do not share one quote asset,
    // so a single symbol printed over their sum was never a true label
    '<td class="mono" title="' + esc("every launch this wallet was first into" +
      (fin(w.pnl_no_rate) && w.pnl_no_rate > 0
        ? ", except " + fmtInt(w.pnl_no_rate) + " with no known pair rate" : "") +
      " - a lifetime total, so it is normally larger than the " + toks.length +
      " launch" + (toks.length === 1 ? "" : "es") + " listed below add up to. " +
      "Converted at each pair's own rate.") +
      '">' + (fin(w.spent_usd) == null ? DIMDASH : esc(fmtUsd(w.spent_usd))) + "</td>" +
    "<td>" + sniperProfitCell(w) + "</td>" +
    "<td>" + (toks.length
      ? '<button class="rowbtn" data-expand="' + esc(addr) + '">' +
        (open ? "hide" : "show") + " " + esc(fmtInt(toks.length)) + "</button>"
      : '<span class="hint" title="' + esc("hits " + fmtInt(hits) +
        " is a lifetime count, but none of those launches still resolve in the index window") +
        '">' + (numOr(hits, 0) > 0 ? "aged out" : "none") + "</span>") +
      // The dozen here is a peek. The profile is the same wallet without the cap,
      // with what is known about each deployer and each claimed handle beside it,
      // and it costs a tab rather than an expansion - which is why both exist.
      '<button class="rowbtn" data-snipepage="' + esc(addr) +
      '" title="open this wallet on its own tab: every launch, every deployer, ' +
      'every claimed handle">open</button>' + "</td></tr>" +
    sniperDetRow(w, open);
}

function renderSnipers(){
  let matched = null;
  if (swFocus){
    const k = String(swFocus).toLowerCase();
    matched = swRows.filter(w => String(w.address || "").toLowerCase() === k)[0] || null;
  }
  // open the focused wallet once, not on every poll, so closing it sticks
  if (matched && !swAutoDone) swOpen[matched.address] = 1;
  $("#sw-tb").innerHTML = swRows.map((w, i) => sniperRow(w, i)).join("");
  setEmpty("sw-empty", swRows.length ? null : "no wallets match");
  if (swFocus && !matched){
    msg("sw-msg", "wallet " + swFocus + " is not among the " + swRows.length +
      " wallets loaded here (the top 100 by hits with min hits " +
      ($("#sw-minhits").value.trim() || "1") + "). Lower min hits to widen the page.", "err");
  } else {
    msg("sw-msg", "");
  }
  if (matched && !swAutoDone){
    swAutoDone = true;
    const tr = $("#sw-tb").querySelector("[data-focus]");
    if (tr && tr.scrollIntoView) tr.scrollIntoView({block: "center"});
  }
}

function renderSniperStats(s, rows){
  s = s || {};
  let listed = 0;
  rows.forEach(w => { listed += (w.tokens || []).length; });
  const cards = [
    ["wallets tracked", fmtInt(s.wallets), ""],
    ["repeat first buyers", fmtInt(s.repeat), numOr(s.repeat, 0) > 0 ? "hot" : ""],
    ["launches indexed", fmtInt(s.launches), ""],
    ["shown here", fmtInt(rows.length), ""],
    ["buys listed", fmtInt(listed), ""]
  ];
  $("#sw-stats").innerHTML = cards.map(c =>
    '<div class="stat"><div class="k">' + esc(c[0]) + '</div><div class="v ' + c[2] + '">' +
    esc(c[1]) + "</div></div>").join("");
  $("#sw-note").textContent =
    "hits counts every launch a wallet was the first outside buyer into, over the whole index. " +
    "The list under a wallet is capped per wallet and covers only launches still in the window, " +
    "so it is normally shorter than hits. Spent and Profit are not subtotals of that list: " +
    "they cover every launch the wallet was first into, which is why they are usually larger " +
    "than the rows on screen add up to. " +
    "A wallet showing hits with no list was first into launches that have since aged out. " +
    "Spent and Profit are in dollars: a wallet's launches do not share a quote asset, so " +
    "each one is converted at its own pair's rate instead of being labelled with a symbol " +
    "that would have been false over the sum. A figure marked as a floor had launches left " +
    "out of it - either ones the backend could not price at all or ones with no known pair " +
    "rate - and the count beside it is how many, so the real total is higher. Inside an " +
    "expanded wallet each P&L is still in that launch's own quote asset, which the row " +
    "names, and its tooltip carries that launch's dollar figure and the rate behind it.";
}

function toggleSniper(addr){
  if (!addr) return;
  if (swOpen[addr]) delete swOpen[addr]; else swOpen[addr] = 1;
  renderSnipers();
}

function mountSnipers(arg){
  if (arg !== undefined){ swFocus = arg || null; swAutoDone = false; }
  refreshSnipers();
  startTimer(refreshSnipers);
}

/* ------------------------------------------------------------------- copy */
