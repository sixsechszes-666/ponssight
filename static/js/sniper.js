/* One sniper, opened.

   The Snipers tab ranks wallets and answers "who is doing this". This answers
   the question under it: which launches, launched by whom, claiming which
   handle - so that the criteria a sniper actually uses can be read off rather
   than guessed at. It is a tab rather than a panel under the leaderboard table
   or a modal because the list runs to thousands of rows and grows by scrolling,
   and because the coin card already owns the one overlay the page has.

   Two sets live here and they are not the same thing. A *snipe* is a launch
   this wallet was the first outside buyer into. A *lost race* is a launch it
   bought into and somebody beat it to. The table shows the snipes; the checkbox
   adds the lost ones in red. Every median above the table is computed over the
   snipes alone and the server says so by naming its denominator - a lag median
   taken across both would be measuring whoever won, not this wallet.

   Three states matter per row and none of them may render as a zero:
   a deployer nobody has read has no transaction count, a handle nobody has
   looked up has no follower count, and a launch with no known pair rate has no
   dollar figure. The row prints the state in words instead. */

// Offsets, like HF in handles.js: `off` is how much of the current order is on
// screen, `lim` is one ordinary page and `limAll` is the largest page the
// server will give. `gen` counts repaints from the top - a page that arrives
// from before the current one is about an order that is no longer on screen and
// is dropped - and `pending` is the repaint that could not start because a
// request was already in flight, remembered rather than dropped so the select
// never shows an order the table is not in.
const SP = {addr: "", off: 0, lim: 100, limAll: 500, total: 0, firsts: 0,
            all: false, sort: "", sort2: "", lost: false,
            busy: false, pending: false, gen: 0, io: null, timer: 0,
            now: 0, counted: false,
            xRun: null, xBusy: false, xTimer: 0,
            chainRun: null, chainBusy: false, chainTimer: 0};
let spData = null;

const SP_LOST_TITLE = "a launch he bought into that somebody else got to first " +
  "- counted, and left out of every median above";

// The other chains a deployer's transaction count is read on, as [chain_id,
// label]. This is the browser's copy of `SW_EXT_CHAINS` in config.py, and the
// order is the column order: the labels go in the header row, which is static
// markup, so a chain added on the server needs a column added here and an id
// added there. There is no way to derive one from the other without a request
// the page does not make, so the coupling is written down instead of hidden.
// The server is the authority on what it reads; this is the authority on where
// it is drawn, and a chain the server reads that this list does not name is
// simply not shown, which is a gap rather than a wrong number.
const SP_EXT_CHAINS = [[1, "ETH"], [42161, "ARB"]];

function spSay(text, isErr){
  const el = $("#sp-msg");
  if (!el) return;
  el.innerHTML = text
    ? '<div class="' + (isErr ? "err" : "hint") + '">' + text + "</div>" : "";
}

function spReset(){
  if (SP.io){ SP.io.disconnect(); SP.io = null; }
  if (SP.timer){ clearTimeout(SP.timer); SP.timer = 0; }
  if (SP.xTimer){ clearTimeout(SP.xTimer); SP.xTimer = 0; }
  if (SP.chainTimer){ clearTimeout(SP.chainTimer); SP.chainTimer = 0; }
  spData = null;
  SP.addr = ""; SP.off = 0; SP.total = 0; SP.firsts = 0; SP.all = false;
  SP.busy = false; SP.pending = false; SP.counted = false; SP.now = 0;
  SP.xRun = null; SP.xBusy = false;
  SP.chainRun = null; SP.chainBusy = false;
  // Not `gen`: it only ever counts up and is compared for equality, so leaving
  // it alone keeps an answer already in flight for the old wallet stale in the
  // new one too, which is what it is for.
  $("#sp-tb").innerHTML = "";
  $("#sp-wrap").hidden = true;
  $("#sp-sent").hidden = true;
  $("#sp-head").innerHTML = "";
  $("#sp-crit").innerHTML = "";
  $("#sp-who").textContent = "";
  $("#sp-note").textContent = "";
  $("#sp-chain").textContent = "";
  $("#sp-x").textContent = "";
  $("#sp-lostnote").hidden = true;
  $("#sp-prog").textContent = "-";
  $("#sp-xprog").hidden = true;
  $("#sp-xprog").textContent = "";
  $("#sp-cprog").hidden = true;
  $("#sp-cprog").textContent = "";
  spSay("");
  setEmpty("sp-empty", null);
  spGate();
}

// Sorting and show-all are off with nothing loaded, so neither can be pressed
// into an empty table and have the table look broken rather than empty.
function spGate(){
  const has = SP.total > 0;
  $("#sp-sort").disabled = !SP.addr;
  $("#sp-sort2").disabled = !SP.addr;
  $("#sp-lost").disabled = !SP.addr;
  const all = $("#sp-all");
  all.disabled = !has || SP.all || SP.off >= SP.total;
  all.textContent = SP.all ? "loading..." : "show all";
  const lbl = $("#sp-lost").closest("label");
  if (lbl) lbl.hidden = !SP.firsts || SP.firsts >= SP.total;
  const xb = $("#sp-xwalk");
  if (xb){
    xb.disabled = !SP.addr || SP.xBusy;
    xb.textContent = SP.xBusy ? "walking..." : "X profiles";
  }
  const cb = $("#sp-cwalk");
  if (cb){
    cb.disabled = !SP.addr || SP.chainBusy;
    cb.textContent = SP.chainBusy ? "reading..." : "Chain";
  }
}

function spAll(){
  if (SP.all || !SP.total || SP.off >= SP.total) return;
  SP.all = true;
  if (SP.timer){ clearTimeout(SP.timer); SP.timer = 0; }
  spGate();
  spPump();
}

// A new order is a new table. The rows on screen are a hundred of a thousand
// and cannot be reordered in place, so the table is emptied, the generator is
// bumped so anything in flight is dropped, and the order is asked for again
// from zero.
function spReSort(){
  SP.off = 0;
  $("#sp-tb").innerHTML = "";
  setEmpty("sp-empty", null);
  SP.gen++;
  spGate();
  spPump();
}

function spSort(){
  const s = $("#sp-sort").value || "", s2 = $("#sp-sort2").value || "";
  if (s === SP.sort && s2 === SP.sort2) return;
  SP.sort = s; SP.sort2 = s2;
  spReSort();
}

function spLost(){
  const on = !!$("#sp-lost").checked;
  if (on === SP.lost) return;
  SP.lost = on;
  spReSort();
}

function spProgress(){
  const el = $("#sp-prog");
  if (!SP.total){ el.textContent = "-"; return; }
  el.textContent = fmtInt(Math.min(SP.off, SP.total)) + " of " + fmtInt(SP.total);
}

function spXStart(){
  if (!SP.addr || SP.xBusy) return;
  SP.xBusy = true;
  spGate();
  const xp = $("#sp-xprog");
  xp.hidden = false;
  xp.textContent = "starting...";
  // `jpost`, not `api(..., "POST")`: `api` takes a path and an options object,
  // so a method handed to it as a third argument is dropped and the request
  // goes out as a GET. Every POST on this tab was written that way, which is
  // why the walks never started even once the `qs` throw was out of the way -
  // a GET on `/chain` is a real route, it just reads the run rather than
  // starting one, so the button looked like it worked and did nothing.
  jpost(qs("/api/snipers/" + encodeURIComponent(SP.addr) + "/x")).then(d => {
    if (SP.xBusy && d && d.run){
      SP.xRun = d.run;
      spXProgress(d.run);
      if (d.run.state === "running" || d.run.state === "idle")
        spXPoll();
    } else {
      spXStop();
    }
  }).catch(e => {
    spSay("X walk failed: " + errText(e), true);
    spXStop();
  });
}

function spXProgress(run){
  const xp = $("#sp-xprog");
  if (!run || !xp) return;
  xp.hidden = false;
  const total = fin(run.total) || 0;
  const walked = fin(run.walked) || 0;
  xp.textContent = "X: " + fmtInt(walked) + "/" + fmtInt(total);
  if (run.state === "running" || run.state === "idle"){
    xp.title = (run.error ? run.error + ". " : "") +
      "ETA ~" + fmtInt(run.eta_sec || 0) + "s at " + (run.per_sec || "?") + "/s";
  } else if (run.state === "ok"){
    xp.title = total ? "all " + fmtInt(total) + " handled" : "nothing to do";
    if (total) spSay("X profiles done: " + fmtInt(total) + " handles");
    else spSay("");
    spXStop();
    // Refresh the page once the cache has settled, so the new followers and
    // the new handles appear without the user having to reload.
    setTimeout(() => { if (SP.addr) spPump(); }, 400);
  } else {
    xp.title = run.error || ("state: " + run.state);
    spSay("X walk " + run.state + (run.error ? ": " + run.error : ""), true);
    spXStop();
  }
}

function spXPoll(){
  if (!SP.xRun || !SP.xBusy) return;
  const rid = SP.xRun.id;
  api(qs("/api/x/jobs/" + rid)).then(d => {
    if (!SP.xBusy) return;
    if (d && d.run){
      SP.xRun = d.run;
      spXProgress(d.run);
      if (d.run.state === "running" || d.run.state === "idle")
        SP.xTimer = setTimeout(spXPoll, 1500);
    } else {
      spXStop();
    }
  }).catch(e => {
    if (SP.xBusy) SP.xTimer = setTimeout(spXPoll, 3000);
  });
}

function spXStop(){
  SP.xBusy = false;
  if (SP.xTimer){ clearTimeout(SP.xTimer); SP.xTimer = 0; }
  if (SP.xRun){
    jpost("/api/x/jobs/" + encodeURIComponent(SP.xRun.id) + "/stop").catch(() => {});
    SP.xRun = null;
  }
  spGate();
}

function spChainStart(){
  if (!SP.addr || SP.chainBusy) return;
  SP.chainBusy = true;
  spGate();
  const cp = $("#sp-cprog");
  cp.hidden = false;
  cp.textContent = "starting...";
  jpost(qs("/api/snipers/" + encodeURIComponent(SP.addr) + "/chain")).then(d => {
    if (SP.chainBusy && d && d.run){
      SP.chainRun = d.run;
      spChainProgress(d.run);
      if (d.run.state === "running" || d.run.state === "idle")
        spChainPoll();
    } else {
      spChainStop();
    }
  }).catch(e => {
    spSay("Chain walk failed: " + errText(e), true);
    spChainStop();
  });
}

function spChainProgress(run){
  const cp = $("#sp-cprog");
  if (!run || !cp) return;
  cp.hidden = false;
  const total = fin(run.total) || 0;
  const done = fin(run.done) || 0;
  const read = fin(run.read) || 0;
  cp.textContent = "chain: " + fmtInt(read) + "/" + fmtInt(total);
  if (run.state === "running" || run.state === "idle"){
    cp.title = (run.error ? run.error + ". " : "") +
      "ETA ~" + fmtInt(run.eta_sec || 0) + "s at " + (run.per_sec || "?") + "/s";
  } else if (run.state === "ok"){
    cp.title = total ? "all " + fmtInt(total) + " read" : "nothing to do";
    if (total){
      // The external refusals are named separately and not folded into
      // `errors`. They are the same kind of fact - a call that did not come
      // back - but they are a different walk: a chain this indexer does not
      // read refusing every address leaves the local reading complete, and one
      // combined number would make a fully-covered Robinhood column look like
      // a failed walk.
      spSay("Chain read done: " + fmtInt(read) + " read, " +
            fmtInt(run.contracts) + " contracts, " +
            fmtInt(run.errors) + " errors" +
            (run.ext_errors
              ? ", " + fmtInt(run.ext_errors) + " refusals from the other chains"
              : ""));
    } else spSay("");
    spChainStop();
    setTimeout(() => { if (SP.addr) spPump(); }, 400);
  } else {
    cp.title = run.error || ("state: " + run.state);
    spSay("Chain walk " + run.state + (run.error ? ": " + run.error : ""), true);
    spChainStop();
  }
}

function spChainPoll(){
  if (!SP.chainRun || !SP.chainBusy) return;
  const addr = SP.chainRun.address;
  api(qs("/api/snipers/" + encodeURIComponent(addr) + "/chain")).then(d => {
    if (!SP.chainBusy) return;
    if (d && d.run){
      SP.chainRun = d.run;
      spChainProgress(d.run);
      if (d.run.state === "running" || d.run.state === "idle")
        SP.chainTimer = setTimeout(spChainPoll, 1500);
    } else {
      spChainStop();
    }
  }).catch(e => {
    if (SP.chainBusy) SP.chainTimer = setTimeout(spChainPoll, 3000);
  });
}

function spChainStop(){
  SP.chainBusy = false;
  if (SP.chainTimer){ clearTimeout(SP.chainTimer); SP.chainTimer = 0; }
  if (SP.chainRun){
    jpost("/api/snipers/" + encodeURIComponent(SP.chainRun.address) +
          "/chain/stop").catch(() => {});
    SP.chainRun = null;
  }
  spGate();
}

function spArgs(){
  return {offset: SP.off, limit: SP.all ? SP.limAll : SP.lim,
          sort: SP.sort, sort2: SP.sort2, include_lost: SP.lost ? 1 : 0};
}

// ------------------------------------------------------------------ summary
function spHead(d){
  const hits = fin(d.hits);
  const last = fin(d.last_ts);
  const age = (last != null && SP.now) ? Math.max(0, SP.now - last) : null;
  const cards = [
    ["snipes", fmtInt(d.criteria ? d.criteria.snipes : null),
     "launches this wallet was the first outside buyer into"],
    ["bought into", fmtInt(d.criteria ? d.criteria.contested.early_buys : null),
     "launches it bought into at all, won or lost"],
    ["won", d.criteria && fin(d.criteria.contested.win_rate) != null
      ? fmtPct(d.criteria.contested.win_rate * 100, 1) : "-",
     "share of those it was first into"],
    ["deployers", fmtInt(d.criteria ? d.criteria.deployers : null),
     "distinct wallets that launched them"],
    ["lifetime hits", fmtInt(hits),
     "the counter behind the Snipers leaderboard, which counts the same thing"],
    ["last seen", fmtAge(age), "time since its most recent snipe"]
  ];
  $("#sp-head").innerHTML = cards.map(c =>
    '<div class="stat"><div class="k" title="' + esc(c[2]) + '">' + esc(c[0]) +
    '</div><div class="v">' + esc(c[1]) + "</div></div>").join("");
  const who = [SP.addr];
  if (!d.counted)
    who.push("no leaderboard row: the counter has never credited this wallet, " +
             "so every launch below is one it lost");
  $("#sp-who").textContent = who.join(" - ");
}

function spCriteria(d){
  const c = d.criteria;
  if (!c) return;
  const med = v => (v == null ? "-" : fmtInt(v) + (v === 1 ? " block" : " blocks"));
  const hist = c.delta_hist || {};
  const parts = Object.keys(hist).filter(k => hist[k])
    .map(k => k + ":" + fmtInt(hist[k])).join("  ");
  // Not escaped here: the whole value is escaped once where it is written into
  // the card. Escaping a piece and then the line it sits in turns an `&` in a
  // symbol into `&amp;amp;`.
  const mix = (c.quote_mix || []).slice(0, 4).map(m =>
    fmtQuoteK(m.median, m.symbol) + " x" + fmtInt(m.n)).join("  ");
  // The fourth field is the card's width, and only the two that hold a list of
  // readings need it: "0.02218 ETH x1,009  60.73 USDG x84  ..." does not fit a
  // 145px cell, and a truncated middle is worse than no figure at all. The
  // whole line is on the value's own title either way, so a clipped one is
  // still readable.
  const cards = [
    ["median lag", med(c.median_delta),
     "blocks between the launch and this wallet's buy, over the " +
     fmtInt(c.snipes) + " launches it was first into. The denominator is named " +
     "because the same median over everything it bought into would be measuring " +
     "the wallets that beat it", 0],
    ["9th decile lag", med(c.p90_delta), "all but a tenth of its snipes are inside this", 0],
    ["lag spread", parts || "-", "how many snipes landed in each bucket of blocks", 1],
    ["fresh deployers", c.fresh_share == null ? "-" : fmtPct(c.fresh_share * 100, 1),
     "share of these launches that were their deployer's first, over the " +
     fmtInt(c.fresh_known) + " deployers that could be resolved", 0],
    ["graduated", c.graduated_share == null ? "-" : fmtPct(c.graduated_share * 100, 1),
     "share of its snipes that have since graduated", 0],
    ["median buy", mix || "-",
     "per quote asset, never pooled: ETH and USDG and NVDA do not add up to anything", 1],
    ["top deployer", c.top_deployer ? fmtPct(c.top_deployer.share * 100, 1) : "-",
     c.top_deployer ? "share of its snipes from " + c.top_deployer.address +
       " (" + fmtInt(c.top_deployer.snipes) + " launches)" : "", 0],
    ["top 10 deployers", c.top10_share == null ? "-" : fmtPct(c.top10_share * 100, 1),
     "share of its snipes from its ten most-used deployers", 0]
  ];
  $("#sp-crit").innerHTML = cards.map(x =>
    '<div class="stat' + (x[3] ? " wide" : "") + '"><div class="k" title="' +
    esc(x[2]) + '">' + esc(x[0]) + '</div><div class="v" title="' + esc(x[1]) +
    '">' + esc(x[1]) + "</div></div>").join("");
  $("#sp-note").textContent =
    "Every median here is over the " + fmtInt(c.snipes) + " launches this wallet " +
    "was first into - the denominator is " + esc(c.denominator) + ", not everything " +
    "it bought. It entered " + fmtInt(c.contested.early_buys) + " launches and lost " +
    fmtInt(c.contested.lost_races) + " of those races; the lost ones are in the table " +
    "when the box is ticked and in no median.";
}

function spChain(d){
  const c = d.chain;
  const el = $("#sp-chain");
  if (!c || !c.addresses){ el.textContent = ""; return; }
  const bits = [fmtInt(c.addresses) + " deployers behind these launches"];
  // The local chain first and named, because it is the only one whose block
  // number the walk carries and the only one whose reading says whether a
  // deployer is a contract. The others follow it in their own words.
  bits.push("Robinhood: " + (c.read
    ? fmtInt(c.read) + " read from chain, " + fmtInt(c.missing) + " never looked at"
    : "none read from chain yet"));
  // One chain at a time. A combined "read on 2 chains" figure would hide the
  // chain that is refusing, which is the one thing this line exists to show.
  //
  // "none answered" is its own phrase and not "never asked": a chain that
  // refused every address has a row for each of them, and calling that a chain
  // nobody has tried would report a node that is down as a node we have not
  // got to yet.
  for (const [cid, label] of SP_EXT_CHAINS){
    // JSON keys are strings whatever they were on the way out, so the chain id
    // is spelled as one here rather than left to index coercion.
    const e = (c.ext || {})[String(cid)];
    if (!e) continue;
    bits.push(label + ": " + (!fin(e.asked) ? "never asked"
      : !fin(e.read) ? "asked, none answered"
      : fmtInt(e.read) + " read, " + fmtInt(e.missing) + " never looked at"));
  }
  if (c.stale) bits.push(fmtInt(c.stale) + " older than " + fmtInt(c.ttl_sec / 60) + "m");
  if (c.contracts)
    bits.push(fmtInt(c.contracts) + " are contracts, which have no transaction count " +
              "in the sense a wallet does");
  if (c.median_nonce != null)
    bits.push("median transactions on Robinhood " + fmtInt(c.median_nonce) +
              " over the " + fmtInt(c.nonce_known) + " known");
  bits.push(c.calls
    ? "reading the rest costs about " + fmtInt(c.calls) + " calls, ~" +
      fmtInt(c.eta_sec) + "s at " + esc(String(c.per_sec)) + "/s through the same " +
      "node the indexer uses"
    : "nothing left to read");
  el.textContent = bits.join(". ") + ".";
}

function spX(d){
  const x = d.x;
  const el = $("#sp-x");
  if (!x || !x.claimed){ el.textContent = ""; return; }
  const bits = [fmtInt(x.claimed) + " of its snipes claim a handle, " +
                fmtInt(x.handles) + " distinct"];
  bits.push(x.known
    ? fmtInt(x.known) + " have a profile (" + fmtPct((x.coverage || 0) * 100, 1) + ")"
    : "none has a profile yet");
  if (x.missing) bits.push(fmtInt(x.missing) + " are accounts that do not exist");
  if (x.never) bits.push(fmtInt(x.never) + " never looked up");
  if (x.median_followers != null)
    bits.push("median followers " + fmtInt(x.median_followers) + " over the " +
              fmtInt(x.with_followers) + " rows that have one");
  bits.push("a handle here is what the token's own description claims, not a " +
            "verified owner");
  el.textContent = bits.join(". ") + ".";
}

// ---------------------------------------------------------------------- rows
function spXCell(r){
  const x = r.x || {};
  const h = x.handle;
  if (!h) return '<span class="hint">claims none</span>';
  const link = '<a class="soc" href="https://x.com/' + esc(h) +
    '" target="_blank" rel="noopener noreferrer" title="the handle this token claims">@' +
    esc(h) + "</a>";
  if (x.state === "never")
    return link + ' <span class="hint" title="we have not asked X about this handle">unchecked</span>';
  if (x.state === "missing" || x.state === "error")
    return link + ' <span class="dim2" title="' +
      esc(x.error || "X has no such account") + '">no such account</span>';
  return link;
}

function spFollowers(r){
  const x = r.x || {};
  if (x.state === "ok" && fin(x.followers) != null)
    return '<span class="mono" title="' + esc(fmtInt(x.followers) + " followers, " +
      fmtInt(x.following) + " following, " + fmtInt(x.statuses) + " posts, read " +
      (x.fetched_at ? fmtAge(Math.max(0, SP.now - x.fetched_at)) + " ago" : "at some point") +
      " - a snapshot, not a live figure") + '">' + esc(fmtInt(x.followers)) + "</span>";
  return '<span class="dim2" title="' +
    esc(x.state === "never" ? "we have not asked X about this handle"
        : "X has no such account") + '">' + DIMDASH + "</span>";
}

function spDeployerCell(r){
  const d = r.deployer_info || {};
  const a = r.deployer || "";
  if (!a) return DIMDASH;
  return '<span class="mono cp" data-copy="' + esc(a) + '" title="click to copy ' +
    esc(a) + '">' + esc(short(a)) + "</span>" +
    (d.is_contract ? ' <span class="lb bundled" title="this deployer has bytecode - ' +
      'it is a contract, not a wallet">contract</span>' : "");
}

function spNonceCell(r){
  const d = r.deployer_info || {};
  if (d.is_contract)
    return '<span class="dim2" title="a contract has no transaction count in the ' +
      'sense a wallet does">-</span>';
  if (d.state == null)
    return '<span class="dim2" title="nobody has read this deployer from chain yet">' +
      "unread</span>";
  if (d.state !== "ok" || fin(d.nonce) == null)
    return '<span class="dim2" title="' + esc("the read failed: " + (d.error || d.state)) +
      '">' + DIMDASH + "</span>";
  return '<span class="mono">' + esc(fmtInt(d.nonce)) + "</span>";
}

// A deployer's transaction count on a chain this indexer does not read. Three
// of these sit side by side and they disagree, which is the point: the same
// address has a different nonce on each chain, and a wallet with four hundred
// sends on Ethereum and none here is a different animal from one with none
// anywhere.
//
// "unread" is not zero and must never be drawn as one. The reading is a
// property of state rather than of a contract, so it cannot be batched into a
// multicall with everything else - it is one call per address per chain, made
// only when the walk is asked to make it, and until then the honest answer is
// that nobody has looked. The three states the walk can leave behind are
// distinct here for the same reason they are distinct in the table: a chain
// that refused the call is retried by the next walk, and a chain never asked
// about is a gap in the reading rather than a fact about the wallet.
function spExtNonceCell(r, chainId, label){
  const d = r.deployer_info || {};
  const ext = d.ext || {};
  const e = ext[chainId];
  if (d.is_contract)
    return '<span class="dim2" title="a contract has no transaction count in the ' +
      'sense a wallet does, here or on ' + esc(label) + '">-</span>';
  if (e == null)
    return '<span class="dim2" title="nobody has asked ' + esc(label) +
      " about this deployer yet\">unread</span>";
  if (e.state !== "ok" || fin(e.nonce) == null)
    return '<span class="dim2" title="' + esc(label + " did not answer for this " +
      "deployer - the next walk asks again") + '">' + DIMDASH + "</span>";
  return '<span class="mono">' + esc(fmtInt(e.nonce)) + "</span>";
}

// A deployer's balance, where the interesting reading is usually "nothing".
// These wallets are funded for one launch and swept, so the values that matter
// sit between zero and a hundredth of an ETH, and `fmtEth`'s four decimals turn
// every one of them into "0.0000 ETH" - which reads as empty and is not the
// same answer. Three cases, and the exact figure is always in the tooltip.
function spEth(v){
  const n = fin(v);
  if (n == null) return "-";
  if (n === 0) return "0 ETH";
  if (n < 0.0001) return "<0.0001 ETH";
  return fmtEth(n);
}

function spBalanceCell(r){
  const d = r.deployer_info || {};
  if (d.state == null)
    return '<span class="dim2" title="nobody has read this deployer from chain yet">unread</span>';
  if (fin(d.balance) == null)
    return '<span class="dim2" title="the read failed">' + DIMDASH + "</span>";
  return '<span class="mono" title="' + esc(fmtEth(d.balance) + " as of block " +
    fmtInt(d.block) + ", a slice of state rather than a standing fact") + '">' +
    esc(spEth(d.balance)) + "</span>";
}

// One row's spend, in the units a person reads. `early_buys.quote` is the
// amount in the quote token's own smallest unit - 2.34e16 for 0.023 ETH, and
// 45647150 for 45.6 USDG - so the decimals have to be divided out before it is
// printed. The row carries them; a token whose pair rate is unknown still
// carries the count, which is the part that does not need a price.
function spSpent(r){
  const n = fin(r.quote);
  if (n == null) return DIMDASH;
  const dec = fin(r.quote_decimals);
  const v = n / Math.pow(10, dec == null ? 18 : dec);
  const sym = r.quote_symbol || "";
  return '<span title="' + esc(fmtQuoteK(n, sym) + " raw, " + sym +
    " has " + (dec == null ? 18 : dec) + " decimals") + '">' +
    esc(fmtQuoteK(v, sym)) + "</span>";
}

function spRow(r, i){
  const age = r.launch_ts ? Math.max(0, SP.now - r.launch_ts) : null;
  const d = r.deployer_info || {};
  const lost = !r.first;
  const lag = fin(r.delta);
  return '<tr' + (lost ? ' class="lost"' : "") + ">" +
    '<td class="dim2 mono">' + (i + 1) + "</td>" +
    "<td>" + tokenCell(r, r.address
      ? "https://www.ponsfamily.com/launchpad/" + r.address : null) + "</td>" +
    "<td>" + ageCell(age) + "</td>" +
    '<td class="mono"' + (lost ? ' title="' + esc(SP_LOST_TITLE) + '"'
      : lag === 0 ? ' title="bought inside the launch block"' : "") + ">" +
      (lost ? '<span class="dim2">lost</span>'
        : esc(lag == null ? "-" : fmtInt(lag))) + "</td>" +
    '<td class="mono">' + spSpent(r) + "</td>" +
    "<td>" + tokenPnlCell(r, r.quote_symbol || "") + "</td>" +
    "<td>" + spDeployerCell(r) + "</td>" +
    '<td class="mono" title="launches this deployer has made">' +
      esc(fmtInt(d.launches)) + "</td>" +
    "<td>" + spNonceCell(r) + "</td>" +
    SP_EXT_CHAINS.map(([cid, label]) =>
      "<td>" + spExtNonceCell(r, cid, label) + "</td>").join("") +
    "<td>" + spBalanceCell(r) + "</td>" +
    "<td>" + spXCell(r) + "</td>" +
    "<td>" + spFollowers(r) + "</td></tr>";
}

// --------------------------------------------------------------------- load
// One page, appended. Never a repaint: a batch that arrives while somebody is
// reading must not move the rows already on screen, and appending at the end is
// the only thing that guarantees it.
async function spPump(){
  if (SP.busy){ SP.pending = true; return; }
  if (!SP.addr) return;
  if (SP.off >= SP.total && SP.total){
    SP.all = false;
    $("#sp-sent").hidden = true;
    spGate();
    spProgress();
    return;
  }
  SP.busy = true;
  const addr = SP.addr, from = SP.off, gen = SP.gen, args = spArgs();
  try {
    const d = await api(qs("/api/snipers/" + encodeURIComponent(addr), args));
    // The panel may have moved to another wallet while this was in flight;
    // those rows are somebody else's and must not land in this table.
    if (SP.addr !== addr) return;
    // And the order may have changed under it, so these rows would be appended
    // in an order the selects no longer say. The repaint that changed it is
    // queued and will ask again from zero.
    if (SP.gen !== gen) return;
    SP.now = fin(d.now) || SP.now;
    SP.total = fin(d.page.total) || 0;
    SP.firsts = fin(d.page.total_firsts) || 0;
    SP.counted = !!d.counted;
    if (from === 0){
      spData = d;
      spHead(d);
      spCriteria(d);
      spChain(d);
      spX(d);
      $("#sp-lostnote").hidden = !(SP.lost && SP.total > SP.firsts);
      if (SP.lost && SP.total > SP.firsts)
        $("#sp-lostnote").textContent =
          "showing all " + fmtInt(SP.total) + " launches it bought into, of which " +
          fmtInt(SP.firsts) + " it was first into. The rows marked lost are the " +
          fmtInt(SP.total - SP.firsts) + " races somebody else won, and no figure " +
          "above is computed from them.";
    }
    const rows = d.tokens || [];
    $("#sp-wrap").hidden = !rows.length && !SP.off;
    $("#sp-tb").insertAdjacentHTML("beforeend",
      rows.map((r, k) => spRow(r, from + k)).join(""));
    SP.off = from + rows.length;
    if (SP.off >= SP.total) SP.all = false;
    spProgress();
    $("#sp-sent").hidden = SP.off >= SP.total;
    spGate();
  } catch(e) {
    if (SP.addr === addr){
      SP.all = false;
      setEmpty("sp-empty", errText(e), true);
      spGate();
    }
  } finally {
    SP.busy = false;
    if (SP.timer === 0 && SP.addr === addr && SP.off < SP.total){
      if (SP.pending){ SP.pending = false; SP.timer = setTimeout(() => { SP.timer = 0; spPump(); }, 0); }
      else if (SP.all) SP.timer = setTimeout(() => { SP.timer = 0; spPump(); }, 0);
    }
  }
}

// Scroll to the bottom and the next hundred is asked for. The sentinel is only
// observed while there is more to fetch, so an exhausted list does not keep
// firing requests at a table that is already complete.
function spObserve(){
  const el = $("#sp-sent");
  if (!el) return;
  if (!SP.io){
    SP.io = new IntersectionObserver(es => {
      if (!es.some(e => e.isIntersecting)) return;
      if (SP.off >= SP.total) return;
      if (SP.timer){ clearTimeout(SP.timer); SP.timer = 0; }
      spPump();
    }, {rootMargin: "240px"});
  }
  SP.io.observe(el);
}

function mountSniper(arg){
  const a = String(arg || "").trim();
  if (!/^0x[0-9a-fA-F]{40}$/.test(a)){
    spReset();
    setEmpty("sp-empty", a
      ? "that is not a wallet address"
      : "open a sniper from the Snipers tab, or from a buyer on the Sniped tab");
    return;
  }
  spReset();
  SP.addr = a;
  SP.sort = $("#sp-sort").value || "";
  SP.sort2 = $("#sp-sort2").value || "";
  SP.lost = !!$("#sp-lost").checked;
  spGate();
  spObserve();
  spPump();
}
