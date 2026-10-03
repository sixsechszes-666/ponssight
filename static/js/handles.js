/* The handle checker.

   One question, asked on its own tab: has anybody already launched a token
   under this X handle. It is not a filter over the launch feed - it is a
   lookup with a verdict - so it neither polls nor joins the shared refresh
   timer, and it is the only tab whose table is empty until something is
   asked of it.

   What the answer is allowed to say is the whole of the design. Three
   different situations reach the same empty table and they are not the same
   sentence:

     the input is not a handle   the column is full of prose and of urls that
                                 point at a path rather than an account, and
                                 "nothing found" would be a lie about a
                                 handle that was never asked about
     a real handle, nothing on it   true, but only back as far as the index
                                 reaches, so how far that is is printed next
                                 to it every time
     a handle with claims on it  and then the deployer count is the answer
                                 that matters: one wallet is the account's
                                 own launch, forty is a brand being farmed
*/
let hTok = null;

function hAgeText(ts, now){
  const s = fin(ts), n = fin(now);
  if (s == null || n == null) return "-";
  return fmtAge(Math.max(0, n - s));
}

// The coverage note is not a footnote. Indexed history has a depth, so every
// answer this tab gives is "in the last N", and a checker that printed a bare
// "no" would be making a claim about the chain that it cannot support.
function hCoverageText(cov, now){
  const n = fin(cov && cov.tokens);
  if (n == null) return "";
  const back = hAgeText(cov.oldest_ts, now);
  return "checked against " + fmtInt(n) + " indexed launches" +
    (back === "-" ? "" : ", back " + back);
}

function hCard(k, v, cls){
  return '<div class="stat"><div class="k">' + esc(k) + '</div><div class="v ' +
    (cls || "") + '">' + esc(v) + "</div></div>";
}

function renderHandleIdle(){
  $("#h-summary").innerHTML = "";
  $("#h-note").innerHTML = "";
  $("#h-tb").innerHTML = "";
  $("#h-count").textContent = "-";
  fReset();
  setEmpty("h-empty", "paste an x handle, an @handle or a profile url");
}

function renderHandle(d){
  const now = fin(d.now) || Math.floor(Date.now() / 1000);
  const cov = d.coverage || {};
  const s = d.summary || {};
  const rows = d.tokens || [];
  const head = [];

  if (!d.handle){
    // Nothing was checked, so nothing is claimed about the chain. The input
    // is echoed back because the usual cause is a paste of the wrong field.
    head.push(hCard("input", d.query || "empty", "dim"));
    head.push(hCard("verdict", "not a handle", "warn"));
    head.push(hCard("launches", "-", "dim"));
    head.push(hCard("deployers", "-", "dim"));
    const note = "that is not a handle or a link to one, so there was nothing " +
      "to look up. the twitter field holds a lot of prose and a lot of urls " +
      "that point at a path rather than an account, and neither is a handle";
    $("#h-summary").innerHTML = head.join("");
    $("#h-note").innerHTML = esc(note);
    $("#h-tb").innerHTML = "";
    $("#h-count").textContent = "-";
    setEmpty("h-empty", "nothing to check");
    return;
  }

  const handle = "@" + d.handle;
  head.push(hCard("handle", handle, ""));
  head.push(hCard("launches claiming it", fmtInt(s.launches), s.launches ? "" : "dim"));
  // One wallet is the account's own launch. More than one is the handle being
  // used by people who are not its owner, which is the thing worth seeing.
  head.push(hCard("distinct deployers", fmtInt(s.deployers),
                  s.deployers > 1 ? "warn" : ""));
  head.push(hCard("first claim", hAgeText(s.first_ts, now), ""));

  let note;
  if (d.reserved){
    note = "x.com/" + d.handle + " is a reserved x path, not an account - " +
      "these are launches whose link points at it, not launches of that handle";
  } else if (!s.launches){
    note = "no launch in the indexed history claims " + handle +
      " - which is not the same as never";
  } else if (s.deployers === 1){
    note = handle + " was launched by one wallet, so these are that account's own";
  } else {
    note = handle + " is claimed by " + fmtInt(s.deployers) + " different wallets " +
      "across " + fmtInt(s.launches) + " launches and " + fmtInt(s.symbols) +
      " symbols - a handle being farmed rather than an account launching";
  }
  $("#h-summary").innerHTML = head.join("");
  $("#h-note").innerHTML = esc(note) + ". " + esc(hCoverageText(cov, now));

  $("#h-tb").innerHTML = rows.map((t, i) => hRow(t, i, now)).join("");
  $("#h-count").textContent = s.launches ? fmtInt(s.launches) + " rows" : "-";
  setEmpty("h-empty", s.launches ? null
    : "no launch in the last " + hAgeText(cov.oldest_ts, now) + " claims " + handle);
}

// The stored claim, compacted for a cell. Everything in this column
// normalises to the same handle - that is why the rows are here at all - so
// what it is for is showing *how* each one was spelled. A profile link, an
// @handle and a link to a single tweet are three different claims and they
// all reduce to the same account.
function hClaim(v){
  const s = String(v || "").replace(/^https?:\/\//i, "").replace(/^www\./i, "");
  return s.length > 34 ? s.slice(0, 24) + "…" + s.slice(-8) : s;
}

function hRow(t, i, now){
  const grad = !!t.graduated;
  const sn = snipeOf(t);
  return '<tr class="' + snipeRowClass(sn) + '"' + snipeData(sn) +
    ' data-addr="' + esc(t.address) + '">' +
    '<td class="dim2 mono">' + (i + 1) + "</td>" +
    "<td>" + tokenCell(t, "https://www.ponsfamily.com/launchpad/" + t.address) + "</td>" +
    "<td>" + ageCell(t.age_seconds) + "</td>" +
    "<td>" + pairPill(t) + "</td>" +
    "<td>" + snipePill(sn) + "</td>" +
    "<td>" + volCell(t.vol, t.quote_symbol) + "</td>" +
    "<td>" + barCell(t.progress_pct, grad) + "</td>" +
    '<td class="mono">' + (grad ? '<span class="pill s" title="graduated to a DEX pool - price tracking not indexed yet">DEX</span>'
      : esc(fmtQuote(t.mcap_quote, t.quote_symbol || ""))) + "</td>" +
    '<td class="mono">' + (grad ? DIMDASH : esc(fmtUsd(t.mcap_usd))) + "</td>" +
    // The raw value, not the normalised one. The whole tab exists because
    // these strings disagree with each other, so hiding them would hide the
    // reason a row is in the list.
    '<td class="mono dim2" title="' + esc(t.twitter || "") + '">' +
      esc(hClaim(t.twitter)) + "</td>" +
    '<td class="mono dim cp" data-copy="' + esc(t.deployer || "") + '" title="' +
      esc(t.deployer || "") + '">' + esc(short(t.deployer)) + "</td></tr>";
}

async function checkHandle(){
  const q = $("#h-q").value.trim();
  if (!q){ hTok = null; renderHandleIdle(); return; }
  const req = latest("handles", false);
  if (!req) return;
  hTok = q;
  // The list on screen belongs to the handle that was in the box, not to the one
  // that is in it now. Cleared before the new answer arrives, because the two
  // requests are independent and the old rows must not be readable as the new
  // person's for the moment in between.
  fReset();
  loadProfile(q, latest("xprof", false));
  try {
    const d = await api(qs("/api/handle", {h: q}), {signal: req.signal});
    if (req.stale()) return;
    renderHandle(d);
    live(true);
  } catch(e) {
    if (req.stale()) return;
    $("#h-summary").innerHTML = "";
    $("#h-note").innerHTML = "";
    $("#h-tb").innerHTML = "";
    $("#h-count").textContent = "-";
    setEmpty("h-empty", "handle check failed: " + errText(e), true);
    live(false);
  }
}

/* ------------------------------------------------- who the handle belongs to

   A second request, against a second service. The verdict above it comes from
   the local index and is answered with no network at all; this comes from X.
   They are kept apart on purpose: X being down, rate limited or unconfigured
   must leave the local half working, so neither one's failure touches the
   other's elements.

   Nothing here is fetched twice for the same person. The profile is stored with
   the id X gives it, and that id is what the followings list is keyed by, so a
   second visit to the same profile costs no requests at all and the list is
   already there.
*/
let hProf = null;

// The followings list, as it is drawn. `off` is how many rows are on screen and
// is always where the next page is asked from: appending at an offset that is
// the number already drawn is what keeps a list that is still being written
// from repeating or skipping people.
//
// `sort` and `sort2` are the order the reader picked, `all` is the show-all
// button being worked through, and `reSort` records that the list grew after an
// order was applied to it and has to be rebuilt.
// `limAll` is the server's own ceiling on one page (C.MAX_LIMIT), which is what
// show-all asks for: the largest page it will give rather than a number invented
// here that would be quietly clamped anyway.
const HF = {off: 0, lim: 100, limAll: 500, parsed: 0, total: null, state: "none",
            run: "", timer: 0, busy: false, io: null, who: "", err: "",
            sort: "", sort2: "", all: false, reSort: false,
            // Two things a page has to survive being asked for twice at once.
            // `gen` counts repaints from the top: a response that arrives from
            // before the current one is about an order that is no longer on
            // screen and is dropped. `pending` is the repaint that could not
            // start because a request was already in flight - remembered, not
            // dropped, so the select never shows an order that is not the order
            // in the table.
            gen: 0, pending: false};

function fBg(url){
  const s = String(url || "");
  if (!/^https?:\/\//i.test(s)) return "";
  // encodeURIComponent leaves no quote and no backslash, so this cannot close
  // the string it is put in - which is why it is set through the style property
  // rather than written into an attribute.
  return "/img?u=" + encodeURIComponent(s) + "&w=1200";
}

function fAv(url, size){
  const s = String(url || "");
  if (!/^https?:\/\//i.test(s)) return '<div class="fav ph"></div>';
  return '<img class="fav" src="/img?u=' + encodeURIComponent(s) + "&w=" + size +
    '" alt="" loading="lazy" decoding="async">';
}

function fXNote(text){
  const el = $("#h-xnote");
  if (!el) return;
  el.hidden = !text;
  el.textContent = text || "";
}

function fHide(){
  $("#h-prof").hidden = true;
  $("#h-fhead").hidden = true;
  fXNote("");
}

function fReset(){
  if (HF.io){ HF.io.disconnect(); HF.io = null; }
  if (HF.timer){ clearTimeout(HF.timer); HF.timer = 0; }
  hProf = null;
  HF.off = 0; HF.parsed = 0; HF.total = null; HF.state = "none";
  HF.run = ""; HF.busy = false; HF.who = "";
  HF.all = false; HF.reSort = false;
  // Not `gen`: it only ever counts up and is compared for equality, so leaving
  // it alone keeps an answer that was already in flight for the old handle
  // stale in the new one too - which is what it is for.
  HF.pending = false;
  $("#h-ftb").innerHTML = "";
  $("#h-fwrap").hidden = true;
  $("#h-fsent").hidden = true;
  $("#h-fstop").hidden = true;
  $("#h-fgo").disabled = false;
  $("#h-fprog").textContent = "-";
  $("#h-fnote").hidden = true;
  setEmpty("h-fempty", null);
  fHide();
  fSortGate();
}

// What the two sort selects and the show-all button are allowed to do right now.
//
// Sorting is off while the walk is running, and that is not a nicety: the order
// is built over the whole list, so a list that is still growing would rank each
// person against a set that changed after they were drawn, and nothing on the
// screen would say so. The three states it reports are "you can sort", "wait for
// the walk" and "everything is already shown".
function fSortGate(){
  const running = HF.state === "running";
  $("#h-fsort").disabled = running;
  $("#h-fsort2").disabled = running;
  const all = $("#h-fall");
  all.disabled = HF.all || !HF.parsed || HF.off >= HF.parsed;
  all.textContent = HF.all ? "loading..." : "show all";
}

// The order actually asked for. Nothing while the walk is running: the rows come
// back in the order they were stored, which is the only order that does not
// shift under a list that is still being written. The chosen order is applied by
// the repaint that runs when the walk ends.
function fSortArgs(){
  if (HF.state === "running") return {sort: "", sort2: ""};
  return {sort: HF.sort, sort2: HF.sort2};
}

// Both keys are sent, first then second, and the server applies them in that
// order over every row this account follows - so the top of the table is the top
// of the list, not the top of the hundred rows that happened to be downloaded.
function fSort(){
  const s = $("#h-fsort").value || "", s2 = $("#h-fsort2").value || "";
  if (s === HF.sort && s2 === HF.sort2) return;
  HF.sort = s;
  HF.sort2 = s2;
  HF.reSort = false;
  // The rows on screen are in the old order and cannot be reordered in place:
  // they are a hundred of five thousand, so the table is emptied and the order
  // is asked for again from the top.
  HF.off = 0;
  $("#h-ftb").innerHTML = "";
  setEmpty("h-fempty", null);
  // A new order is a new table: anything still in flight was asked for in the
  // old one and is now stale.
  HF.gen++;
  fSortGate();
  pump();
}

// Not a second way of drawing the list: the same page loop, larger pages and no
// pause between them. `ord` is append-only, so this is also the one thing that
// stays usable while the walk is running.
function fAll(){
  if (HF.all || !HF.parsed || HF.off >= HF.parsed) return;
  HF.all = true;
  if (HF.timer){ clearTimeout(HF.timer); HF.timer = 0; }
  fSortGate();
  pump();
}

function fProgress(){
  const el = $("#h-fprog");
  if (HF.state === "running"){
    el.textContent = fmtInt(HF.parsed) + " of " +
      (HF.total == null ? "?" : fmtInt(HF.total));
    return;
  }
  if (HF.state === "ok" && HF.total != null && HF.parsed >= HF.total)
    el.textContent = fmtInt(HF.parsed) + " people";
  else if (!HF.parsed) el.textContent = "-";
  else el.textContent = fmtInt(HF.parsed) + " of " +
    (HF.total == null ? "?" : fmtInt(HF.total));
  fSortGate();
}

// Called by loadProfile once the profile is known: it is the profile that says
// whether this person's list has ever been parsed, and how long it is.
function renderProfile(d){
  const p = d.profile || {};
  hProf = p;
  $("#h-prof").hidden = false;
  $("#h-pname").textContent = p.name || p.handle || "";
  $("#h-phandle").textContent = p.handle ? "@" + p.handle : "";
  const ban = fBg(p.banner);
  const bn = $("#h-ban");
  bn.style.backgroundImage = ban ? 'url("' + ban + '")' : "";
  bn.hidden = !ban;
  $("#h-av").innerHTML = fAv(p.avatar, 96);
  $("#h-pbio").textContent = p.bio || "";
  $("#h-pbio").hidden = !p.bio;

  const nums = [["followers", p.followers], ["following", p.following],
                ["posts", p.statuses], ["launched", (d.summary || {}).launches],
                ["deployers", (d.summary || {}).deployers]];
  $("#h-pnums").innerHTML = nums.map(([k, v]) =>
    '<span class="xn"><i>' + esc(k) + "</i><b>" +
    (v == null ? "-" : fmtInt(v)) + "</b></span>").join("");

  const link = $("#h-plink");
  link.href = "https://x.com/" + encodeURIComponent(p.handle || "");
  link.textContent = "x.com/" + (p.handle || "");
  const site = $("#h-psite");
  const w = String(p.website || "");
  // Only a real link is shown as one. The column is free text and a bare word
  // is not a destination.
  if (/^https?:\/\//i.test(w)){
    site.href = w;
    site.textContent = w.replace(/^https?:\/\//i, "").replace(/\/$/, "").slice(0, 46);
    site.hidden = false;
  } else site.hidden = true;

  // Where the profile came from, always stated. It is cached for a day, and a
  // card that did not say so would be claiming to be current when it is not.
  const age = fin(d.age);
  const ageText = age == null ? "" : fmtAge(age);
  if (d.source === "x") fXNote("profile read from x just now");
  else if (d.source === "cache-stale")
    fXNote("x could not be reached (" + (d.note || "no answer") +
           "), so this is the stored copy" + (ageText ? ", " + ageText + " old" : ""));
  else fXNote("profile from the stored copy" + (ageText ? ", " + ageText + " old" : ""));

  HF.total = d.list ? d.list.total : p.following;
  HF.parsed = fin(d.parsed) || 0;
  HF.state = (d.list && d.list.state) || "none";
  HF.who = d.handle;
  $("#h-fhead").hidden = false;
  fProgress();

  if (HF.parsed) drawStoredList(d.handle);
  else {
    $("#h-fnote").hidden = false;
    $("#h-fnote").textContent = HF.total
      ? "follows " + fmtInt(HF.total) + " accounts - press load followings to parse them"
      : "press load followings to parse who this account follows";
    setEmpty("h-fempty", null);
  }
}

function renderProfileFail(e){
  fHide();
  const msg = String(e && e.message || e);
  // A missing token is a setup fact and the server says so in a sentence; that
  // sentence is more use here than anything this file could invent.
  fXNote("profile unavailable: " + msg);
}

async function loadProfile(q, req){
  if (!req) return;
  try {
    const d = await api(qs("/api/x/profile", {h: q}), {signal: req.signal});
    if (req.stale()) return;
    renderProfile(d);
  } catch(e) {
    if (req.stale()) return;
    renderProfileFail(e);
  }
}

// A list that is already in the database is drawn straight away, without asking
// X anything. This is the half that makes a second visit to a profile free.
function drawStoredList(who){
  HF.off = 0;
  $("#h-ftb").innerHTML = "";
  $("#h-fwrap").hidden = false;
  $("#h-fstop").hidden = HF.state !== "running";
  $("#h-fgo").disabled = HF.state === "running";
  if (HF.state === "running"){
    HF.who = who;
    fNote();
    fObserve();
    pump();
  } else {
    fDone();
    pump();
  }
}

function fNote(text){
  const el = $("#h-fnote");
  el.hidden = !text;
  el.textContent = text || "";
}

// The bottom line, and the only place the difference between a finished list
// and an interrupted one is stated. A walk that stopped at 300 of 5000 has to
// say so: 300 rows drawn as if they were the whole list is the one failure this
// table is built not to have.
function fDone(){
  const total = HF.total, got = HF.parsed;
  const shown = HF.off;
  const of = total == null ? "" : " of " + fmtInt(total);
  fSortGate();
  if (HF.state === "running"){
    fNote("parsing - " + fmtInt(got) + of + " so far, the list fills in as pages arrive" +
          (HF.sort || HF.sort2 ? " - sorting opens when the walk ends" : ""));
    $("#h-fstop").hidden = false;
    return;
  }
  $("#h-fstop").hidden = true;
  $("#h-fgo").disabled = false;
  if (HF.state === "ok" && (total == null || got >= total)){
    fNote("");
    setEmpty("h-fempty", fmtInt(shown) + " of " + fmtInt(got) +
      " accounts shown" + (total != null && got > total ? "" :
      " - the full list"));
    return;
  }
  const why = HF.state === "stopped" ? "stopped"
    : HF.state === "error" ? "interrupted" : "incomplete";
  setEmpty("h-fempty", "this list is " + why + " at " + fmtInt(got) + of +
    " - what is shown is what was parsed, not the whole list" +
    (HF.err ? " (" + HF.err + ")" : ""), HF.state === "error");
  fNote("");
}

// The pill counts launches that claim this handle; the line under it counts the
// wallets that made them. Both are on screen because both are sort keys, and a
// list ordered by a number nobody can see reads as arbitrary.
function fLaunched(r){
  const n = fin(r.launches) || 0;
  if (!n) return '<span class="dim2 mono">-</span>';
  const d = fin(r.deployers) || 0;
  const t = r.top || {};
  const title = n + (n === 1 ? " indexed launch claims" : " indexed launches claim") +
    " this handle, from " + fmtInt(d) + (d === 1 ? " wallet" : " wallets") +
    (t.symbol ? ", the largest being " + t.symbol : "");
  return '<span class="pill' + (n > 2 ? " r" : "") + '" title="' + esc(title) + '">' +
    fmtInt(n) + (n === 1 ? " launch" : " launches") + "</span>" +
    '<div class="dim2 mono">' + fmtInt(d) +
    (d === 1 ? " deployer" : " deployers") + "</div>";
}

function fTop(r){
  const t = r.top;
  if (!t || !t.symbol) return DIMDASH;
  return '<span class="nm">' + esc(t.symbol) + "</span>" +
    (fin(t.mcap_usd) == null ? "" :
     ' <span class="dim2 mono">' + esc(fmtUsd(t.mcap_usd)) + "</span>");
}

// The account's own page. socialLink is the one place in this project that
// writes an external anchor, so the target and the rel come from there rather
// than from a second copy of them here - and a handle goes into the path through
// encodeURIComponent, because a handle is data and a path segment is not where
// data gets to decide what the URL means.
function fX(r){
  const h = (r.handle || "").trim();
  if (!h) return "";
  return socialLink("https://x.com/" + encodeURIComponent(h), "X", "@" + h + " on X");
}
function fRow(r, i){
  const h = r.handle || "";
  const name = r.name || h || "?";
  return '<tr data-h="' + esc(h) + '">' +
    '<td class="dim2 mono">' + (i + 1) + "</td>" +
    '<td><div class="tok">' + fAv(r.avatar, 48) +
      '<div><div class="nm">' +
      (h ? '<button type="button" class="nmbtn" data-h="' + esc(h) +
        '" title="check this handle">' + esc(name) + "</button>" : esc(name)) +
      // The handle is a chip and the link beside it goes to the account, because
      // reaching for the handle to copy it used to start a lookup instead - the
      // row itself means "check this person" and everything in it inherited
      // that. What is copied is the bare handle: "@nick" works too, but the bare
      // one also works in this page's own search box and in x.com/<nick>, and
      // the @ is on screen beside it anyway.
      '</div><div class="sym">' + (h
        ? '<span class="mono cp" data-copy="' + esc(h) + '" title="copy @' + esc(h) +
          '">@' + esc(h) + "</span>"
        : "") + fX(r) +
      (r.blue ? ' <span class="lb">blue</span>' : "") +
      (r.protected ? ' <span class="lb">locked</span>' : "") +
      "</div></div></div></td>" +
    '<td class="mono">' + (fin(r.followers) == null ? DIMDASH : fmtInt(r.followers)) + "</td>" +
    '<td class="mono dim2">' + (fin(r.following) == null ? DIMDASH : fmtInt(r.following)) + "</td>" +
    "<td>" + fLaunched(r) + "</td>" +
    '<td class="mono">' + fTop(r) + "</td></tr>";
}

// One page, appended. Never a repaint: a batch that arrives while somebody is
// reading must not move the rows that are already on screen, and appending at
// the end is the only thing that guarantees it.
async function pump(){
  if (HF.busy){
    // A request is already in flight, so this repaint cannot start yet. It is
    // remembered rather than dropped: the caller has just cleared the table and
    // changed the order, and a dropped repaint would leave the select showing an
    // order that is not the one on screen.
    HF.pending = true;
    return;
  }
  if (!hTok || !HF.who) return;
  // Nothing left to draw and no walk writing more, so there is nothing to ask
  // for. A walk that IS running must not take this branch even when the table
  // holds every row committed so far, because `parsed` is how far the walk has
  // got and asking is the only way this page ever learns that it moved.
  // Returning here is a page waiting for a number nobody will tell it: on the
  // first walk of a handle the table holds 0 rows and the walk has committed 0,
  // which reads as caught up - so @laoyingkhq sat on "0 so far, waiting for the
  // next page" through a walk that fetched all 3,367 of them in nine seconds.
  if (HF.off >= HF.parsed && HF.state !== "running"){
    HF.all = false;
    $("#h-fsent").hidden = true;
    fDone();
    return;
  }
  HF.busy = true;
  const who = HF.who, from = HF.off, gen = HF.gen;
  const args = fSortArgs();
  try {
    const d = await api(qs("/api/x/followings",
                           {h: who, offset: from,
                            limit: HF.all ? HF.limAll : HF.lim,
                            sort: args.sort, sort2: args.sort2}));
    // The box may have moved to another handle while this was in flight; those
    // rows are somebody else's and must not land in this table.
    if (HF.who !== who) return;
    // And the order may have changed under it. These rows were asked for in the
    // old order, so appending them would put rows the server had already ranked
    // below others at the top of a table whose select now says otherwise. The
    // repaint that changed the order is queued and will ask again from zero.
    if (HF.gen !== gen) return;
    const rows = d.rows || [];
    HF.parsed = fin(d.parsed) || HF.parsed;
    HF.total = d.total == null ? HF.total : d.total;
    HF.state = d.state || HF.state;
    HF.err = d.error || "";
    // The walk has ended and the order on screen was built while the list was
    // smaller. Appending to it would freeze an order that no longer holds, so
    // the table is emptied and the order is asked for again over the whole set.
    if (HF.reSort && HF.state !== "running"){
      HF.reSort = false;
      HF.off = 0;
      $("#h-ftb").innerHTML = "";
      HF.timer = setTimeout(() => { HF.timer = 0; pump(); }, 0);
      return;
    }
    if (from === 0) $("#h-fwrap").hidden = !rows.length;
    $("#h-ftb").insertAdjacentHTML("beforeend",
      rows.map((r, i) => fRow(r, from + i)).join(""));
    HF.off = from + rows.length;
    // Caught up with everything stored, so show-all has done its job. While the
    // walk is running it is not caught up with anything: `parsed` is still moving.
    if (HF.off >= HF.parsed && HF.state !== "running") HF.all = false;
    fProgress();
    // The sentinel is the scroll target first and a status line second: blank
    // with only its height while show-all fetches, carrying the walk's count
    // while a walk runs. Written on every pass rather than only when the page
    // happens to be caught up, because the number it carries is the walk's and
    // it moves; and cleared on the way out, so a line that says "parsing" cannot
    // outlive the walk it was describing.
    if (HF.state === "running"){
      $("#h-fsent").hidden = false;
      $("#h-fsent").textContent = "parsing - " + fmtInt(HF.parsed) +
        " so far, waiting for the next page";
    } else {
      $("#h-fsent").hidden = HF.off >= HF.parsed;
      $("#h-fsent").textContent = "";
    }
    fDone();
  } catch(e) {
    if (HF.who === who){
      HF.state = "error";
      HF.err = errText(e);
      HF.all = false;
      fDone();
    }
  } finally {
    HF.busy = false;
    // One loop for all three: a repaint somebody asked for while this request
    // was in flight, then the 1.2 s poll while the walk is being written, then
    // the immediate next page when show-all still has rows left to fetch. The
    // first is not guarded by `who`: it is a standing request for a repaint, and
    // the box may well have moved to another handle while this was in flight -
    // which is a repaint too, and the one most likely to have been dropped.
    if (HF.timer === 0){
      if (HF.pending && HF.who){
        HF.pending = false;
        HF.timer = setTimeout(() => { HF.timer = 0; pump(); }, 0);
      } else if (HF.who === who && HF.state === "running")
        HF.timer = setTimeout(() => { HF.timer = 0; pump(); }, 1200);
      else if (HF.who === who && HF.all && HF.off < HF.parsed)
        HF.timer = setTimeout(() => { HF.timer = 0; pump(); }, 0);
    }
    fSortGate();
  }
}

// Scroll to the bottom and the next hundred is asked for. If the walk has not
// committed that far yet, the sentinel stays and the poll above brings it in.
function fObserve(){
  const el = $("#h-fsent");
  if (!el) return;
  if (!HF.io){
    HF.io = new IntersectionObserver(es => {
      if (es.some(e => e.isIntersecting)){
        if (HF.timer){ clearTimeout(HF.timer); HF.timer = 0; }
        pump();
      }
    }, {rootMargin: "240px"});
  }
  HF.io.observe(el);
}

async function loadFollowings(){
  const who = hTok;
  if (!who) return;
  $("#h-fgo").disabled = true;
  setEmpty("h-fempty", null);
  try {
    const run = await jpost("/api/x/followings", {h: who});
    if (hTok !== who) return;
    HF.run = run.id;
    // An order is in force and the list is about to grow under it: remember that
    // it has to be rebuilt once the walk ends, because nothing appended between
    // now and then can be trusted to sit in the right place.
    if (HF.sort || HF.sort2) HF.reSort = true;
    HF.state = "running";
    HF.who = run.handle || HF.who;
    HF.total = run.total == null ? HF.total : run.total;
    $("#h-fwrap").hidden = false;
    $("#h-fstop").hidden = false;
    $("#h-fnote").hidden = false;
    fNote("parsing " + (run.total ? "up to " + fmtInt(run.total) + " accounts" : "followings") +
          " - the list appears as pages arrive");
    fProgress();
    fObserve();
    if (HF.timer){ clearTimeout(HF.timer); HF.timer = 0; }
    HF.timer = setTimeout(() => { HF.timer = 0; pump(); }, 700);
  } catch(e) {
    if (hTok !== who) return;
    $("#h-fgo").disabled = false;
    setEmpty("h-fempty", "could not start the walk: " + errText(e), true);
  }
}

async function stopFollowings(){
  if (!HF.run) return;
  $("#h-fstop").disabled = true;
  try { await jpost("/api/x/followings/" + HF.run + "/stop", {}); }
  catch(e){ /* the walk will end on its own; the poll picks up whichever way */ }
  $("#h-fstop").disabled = false;
}

// Checking somebody from the list is the same action as typing them in, so it
// goes through the same door: the box, the check, and the hash.
function openHandleFor(who){
  if (!who) return;
  $("#h-q").value = who;
  hTok = who;
  setHash("#handles/" + encodeURIComponent(who), true);
  checkHandle();
}

// Opening or reloading #handles/x puts that handle in the box and checks it,
// so a handle is a link the way a snipers wallet is.
function mountHandles(arg){
  if (arg && arg !== hTok){
    $("#h-q").value = decodeURIComponent(arg);
    hTok = arg;
  }
  if ($("#h-q").value.trim()) checkHandle();
  else renderHandleIdle();
}
