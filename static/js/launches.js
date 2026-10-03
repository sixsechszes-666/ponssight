const seen = new Set();
let firstLoad = true;

// The dollar floor as a number, or empty for no floor. A box holding
// something that is not a number is treated as empty rather than as zero,
// so a half-typed value does not blank the table on the way to a valid one.
function minVol(){
  const v = ($("#minvol").value || "").trim().replace(/[$,\s]/g, "");
  if (!v) return "";
  const n = Number(v);
  return isFinite(n) && n > 0 ? String(n) : "";
}

async function refreshLaunches(fromPoll){
  const req = latest("launches", fromPoll === true);
  if (!req) return;
  const t0 = performance.now();
  try {
    const url = qs("/api/tokens", {
      sort: $("#sort").value,
      limit: 200,
      q: $("#q").value.trim(),
      quote: $("#quote").value,
      graduated: $("#hidegrad").checked ? "false" : "",
      since_minutes: $("#fresh").checked ? "30" : "",
      // Empty means no floor at all, which is not the same as a floor of
      // zero: a token that has never traded has no volume to be above.
      min_volume_usd: minVol()
    });
    const d = await api(url, {signal: req.signal});
    if (req.stale()) return;
    const lat = Math.round(performance.now() - t0);
    const all = d.tokens || [];
    // The filter runs here rather than as a query parameter: the row it drops
    // is already in the response, so it costs one pass over the page and
    // nothing at all on the poll that fills it.
    const hiding = $("#devonly").checked;
    const rows = hiding ? all.filter(t => !devOnly(t)) : all;
    const dropped = all.length - rows.length;
    $("#b-lat").textContent = lat + " ms / " + all.length + " rows";
    $("#l-count").textContent = dropped
      ? rows.length + " of " + all.length + " rows"
      : rows.length + " rows";
    $("#tb").innerHTML = rows.map((t, i) => launchRow(t, i)).join("");
    // An empty table under a filter reads as a broken feed, so when the filter
    // is what emptied it the message says so instead of "no launches match".
    // A floor that emptied the table is a different message from a search
    // that matched nothing: the answer to the first is a lower number, and
    // saying so is the whole point of having typed one.
    const floor = minVol();
    setEmpty("l-empty", rows.length ? null
      : (hiding && all.length ? "every launch here was bought only by its deployer"
        : (floor ? "nothing has traded over $" + floor + " yet - lower the floor"
                 : "no launches match")));
    rows.forEach(t => seen.add(t.address));
    firstLoad = false;
    if (fin(d.eth_usd) != null) $("#b-eth").textContent = "$" + Math.round(fin(d.eth_usd));
    live(true);
  } catch(e) {
    // an abort we caused ourselves by starting a newer request is
    // not news, and painting it would overwrite the newer result
    if (req.stale()) return;
    $("#l-count").textContent = "-";
    setEmpty("l-empty", "launches fetch failed: " + errText(e), true);
    live(false);
  }
}

function launchRow(t, i){
  const grad = !!t.graduated;
  const isNew = !firstLoad && !seen.has(t.address);
  const page = "https://www.ponsfamily.com/launchpad/" + t.address;
  const sn = snipeOf(t);
  return '<tr class="' + (isNew ? "new " : "") + snipeRowClass(sn) + '"' +
    snipeData(sn) + ' data-addr="' + esc(t.address) + '">' +
    '<td class="dim2 mono">' + (i + 1) + "</td>" +
    "<td>" + tokenCell(t, page) + "</td>" +
    "<td>" + ageCell(t.age_seconds) + "</td>" +
    "<td>" + pairPill(t) + "</td>" +
    "<td>" + snipePill(sn) + "</td>" +
    "<td>" + volCell(t.vol, t.quote_symbol) + "</td>" +
    "<td>" + barCell(t.progress_pct, grad) + "</td>" +
    // a graduated token trades in a DEX pool we do not index yet, so a market
    // cap read off the curve there would be a lie
    '<td class="mono">' + (grad ? '<span class="pill s" title="graduated to a DEX pool - price tracking not indexed yet">DEX</span>'
      : esc(fmtQuote(t.mcap_quote, t.quote_symbol || ""))) + "</td>" +
    '<td class="mono">' + (grad ? DIMDASH : esc(fmtUsd(t.mcap_usd))) + "</td>" +
    "<td>" + feesCell(t) + "</td>" +
    "<td>" + socialsCell(t, "", true) + "</td>" +
    '<td class="mono dim cp" data-copy="' + esc(t.deployer || "") + '" title="' +
      esc(t.deployer || "") + '">' + esc(short(t.deployer)) + "</td></tr>";
}

function mountLaunches(){ refreshLaunches(); startTimer(refreshLaunches); }

/* ----------------------------------------------------------------- volume */
