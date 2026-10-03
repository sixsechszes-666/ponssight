const FIELD_IDS = {
  name: "#f-name", symbol: "#f-symbol", description: "#f-description", logo: "#f-logo",
  twitter: "#f-twitter", telegram: "#f-telegram", discord: "#f-discord",
  website: "#f-website", farcaster: "#f-farcaster",
  creator_fee_recipient: "#f-recipient", creator_tax_bps: "#f-tax",
  launch_config_id: "#f-config", pair_token: "#f-pair",
  initial_buy_quote: "#f-buy", salt: "#f-salt"
};
const NUMERIC_FIELDS = {creator_tax_bps: 1, launch_config_id: 1, initial_buy_quote: 1};

const copy = {id: null, status: "draft", sourceAddress: null, calldata: null, blank: false};

// The creator tax a blink copy launches with, and it is deliberately not the
// form's DEFAULT_TAX_BPS. The two places mean different things by a default.
// The form puts the number on the screen before anything is signed, so a
// default there is a suggestion, read and changed or accepted on purpose. A
// blink copy has no form on it: whatever tax it carried would be a tax nobody
// saw and nobody chose, and the tax is the copier's own cut of the curve's fees
// (creator_fee_recipient is the connected wallet), so a non-zero one here
// quietly pays them out of a launch they never priced. Zero is the value that
// decides nothing on their behalf. The `copy` chip beside the button opens the
// form, where the tax is on screen, and that is the way to launch a copy that
// takes one.
const BLINK_TAX_BPS = 0;

function blankFields(){
  return {
    name: "", symbol: "", description: "", logo: "", twitter: "", telegram: "",
    discord: "", website: "", farcaster: "",
    // Filled from whoever signs, which is a stored key when one is picked and
    // the connected wallet otherwise. This is not cosmetic: the creator's share
    // of the curve's fees is paid to this address, and a launch signed by a
    // stored key whose recipient was the injected wallet would pay a wallet
    // that had nothing to do with the launch. The address is valid and the
    // transaction goes through, so nothing downstream would ever say so.
    creator_fee_recipient: signerAddress(),
    creator_tax_bps: DEFAULT_TAX_BPS, buyback_enabled: false,
    launch_config_id: 0,
    pair_token: NATIVE, initial_buy_quote: DEFAULT_BUY, salt: "",
    // The buy recipient opens on the signer for the same reason, because that
    // is who a person launching their own token means by "me". It is a field
    // rather than an implicit because the confirm dialog has to be able to show
    // where the tokens from the buy actually go.
    entry: "factory", buyer: signerAddress(),
    slippage_bps: DEFAULT_SLIPPAGE_BPS
  };
}
function fillFields(o){
  o = o || {};
  Object.keys(FIELD_IDS).forEach(k => {
    const el = $(FIELD_IDS[k]);
    if (!el) return;
    let v = o[k];
    if (v == null) v = NUMERIC_FIELDS[k] ? 0 : "";
    if (k === "pair_token" && !v) v = NATIVE;
    el.value = String(v);
  });
  $("#f-buyback").checked = !!o.buyback_enabled;
  setEntry(o.entry === "router" ? "router" : "factory");
  $("#f-buyer").value = String(o.buyer || "");
  $("#f-slip").value = String(o.slippage_bps == null
    ? DEFAULT_SLIPPAGE_BPS : o.slippage_bps);
  syncEntry();
}
function readFields(){
  const f = {};
  Object.keys(FIELD_IDS).forEach(k => { f[k] = $(FIELD_IDS[k]).value.trim(); });
  f.creator_tax_bps = intOr(f.creator_tax_bps, 0);
  f.launch_config_id = intOr(f.launch_config_id, 0);
  f.initial_buy_quote = numOr(f.initial_buy_quote, 0);
  f.buyback_enabled = $("#f-buyback").checked;
  f.entry = entryValue();
  f.buyer = $("#f-buyer").value.trim();
  f.slippage_bps = intOr($("#f-slip").value, 0);
  return normFields(f);
}
// What the payload has to say whoever built it. The form is one source of a
// set of launch fields and a blink copy is the other, and the two differ in
// where the fields came from and not in what the factory is handed.
function normFields(f){
  // Belt as well as braces. syncEntry clears the buy when the entry point goes
  // back to the factory, but the field is only the field: the payload is what
  // gets signed, and a buy travelling on a factory call is a revert.
  if (f.entry !== "router"){ f.initial_buy_quote = 0; f.slippage_bps = 0; }
  if (!f.pair_token) f.pair_token = NATIVE;
  return f;
}

/* ------------------------------------------------- the shared launch form */
/* Create launches from scratch and Copy launches from a token that already
   exists, and both need the same fields, the same confirm dialog and the same
   send button. Duplicating the form would duplicate every id in it, and this
   whole file addresses its fields by id - so there is one #c-form-wrap and
   Create moves it rather than copying it.

   The cost of one node is that the values travel with it, and a create draft
   that quietly turned into a copy draft - or lost the logo uri it had just
   spent an upload on - is a bad way to find that out. So whichever tab is
   holding the form parks its values here on the way out and gets them back on
   the way in. */
const formHold = {owner: null, saved: {}};

function formStash(){
  if (!formHold.owner) return;
  formHold.saved[formHold.owner] = {
    fields: readFields(), extra: readExtra(), notes: $("#f-notes").value,
    blank: copy.blank, sourceAddress: copy.sourceAddress,
    id: copy.id, status: copy.status
  };
}
// Take the form for `owner` and restore what that owner left in it. True means
// nothing was saved, so the caller decides what a fresh start looks like.
function formClaim(owner){
  formHold.owner = owner;
  const s = formHold.saved[owner];
  if (!s){
    // This tab has never held the form, so whatever the last one left in these
    // three belongs to it, not here. Without this, coming back from Create
    // found blank already set and opened the picker's replacement - a form
    // holding the other tab's draft.
    copy.blank = false;
    copy.sourceAddress = null;
    copy.id = null;
    copy.status = "draft";
    return true;
  }
  fillFields(s.fields);
  renderExtra(s.extra);
  $("#f-notes").value = s.notes;
  copy.blank = s.blank;
  copy.sourceAddress = s.sourceAddress;
  copy.id = s.id;
  copy.status = s.status;
  return false;
}
// An explicit load - a source token, a saved plan, the blank button - is not a
// tab switch, so it wins over the stash rather than being overwritten by it.
function formTake(owner){
  formHold.owner = owner;
  delete formHold.saved[owner];
}
// The blank state both tabs start from. `owner` decides which tab the draft
// belongs to, and copy.blank is what tells mountCopy whether to open the
// picker or the form the next time Copy is shown.
function startBlank(owner){
  formTake(owner);
  copy.blank = true;
  copy.sourceAddress = null; copy.id = null; copy.status = "draft";
  $("#c-confirm").hidden = true;
  $("#c-src-v").textContent = "no source token";
  fillFields(blankFields());
  renderExtra([]);
  $("#f-notes").value = "";
  msg("c-msg", "");
}

/* ------------------------------------------------------- the entry point */
/* The two entry points are a constraint, not a preference. The factory's
   launchToken takes the launch fee as its whole value - one wei more reverts
   the transaction at any balance - and the router's launchAndBuy takes the fee
   plus the buy. So the fields only one of them carries are disabled under the
   other rather than left editable and ignored: a buy that looks set and does
   not travel is how a launch gets signed that nobody meant to sign. */
function entryValue(){
  const el = $('input[name="f-entry"]:checked');
  return el ? el.value : "factory";
}
function setEntry(v){
  $$('input[name="f-entry"]').forEach(el => { el.checked = (el.value === v); });
}
function syncEntry(zeroBuy){
  const router = entryValue() === "router";
  if (!router && zeroBuy) $("#f-buy").value = "0";
  ["#f-buy", "#f-slip", "#f-buyer"].forEach(sel => {
    const el = $(sel);
    if (el) el.disabled = !router;
  });
  const hint = $("#f-buy-hint");
  if (hint) hint.textContent = router
    ? "the router's launchAndBuy carries the launch fee and the initial buy in one " +
      "transaction, and the value it takes is exactly the two added together. the " +
      "floor under the buy is the fill this model expects less the slippage, so at " +
      "5% the launch still lands if the buy fills up to 5% worse than quoted, and " +
      "reverts rather than overpaying if it is worse than that. the recipient " +
      "below is who the bought tokens go to, and it has to be an address."
    : "the factory's launchToken takes the launch fee as its whole value: one wei " +
      "more reverts the transaction, so an initial buy cannot travel this way. the " +
      "buy, its slippage and its recipient are switched off until the entry point " +
      "above is set to the router.";
  syncBps();
}
// A bps figure is hundredths of a percent, which is the unit the factory takes
// and not the unit anybody reads. The field keeps the number that goes on
// chain because that is what gets signed; the label carries the percentage
// beside it so 200 cannot be read as 200%.
function syncBps(){
  const t = $("#f-tax-pct"), s = $("#f-slip-pct");
  if (t) t.textContent = bpsPct($("#f-tax").value);
  if (s) s.textContent = bpsPct($("#f-slip").value);
}
function exRow(e){
  return '<div class="exrow">' +
    '<input class="ex-l" placeholder="label" autocomplete="off" value="' + esc(e.label || "") + '">' +
    '<input class="ex-v" placeholder="value" autocomplete="off" value="' + esc(e.value || "") + '">' +
    '<button class="ghost" data-exdel="1" title="remove this field">x</button></div>';
}
function renderExtra(list){
  $("#c-extra").innerHTML = (list || []).map(exRow).join("");
}
function readExtra(){
  return $$("#c-extra .exrow").map(r => ({
    label: r.querySelector(".ex-l").value.trim(),
    value: r.querySelector(".ex-v").value.trim()
  })).filter(x => x.label || x.value);
}
function planPayload(){
  return { fields: readFields(), extra: readExtra(), notes: $("#f-notes").value.trim() };
}

function mountCopy(arg){
  // The launch form is shared with the Create tab and may be parked over
  // there. Putting it back is the first thing that has to happen: everything
  // below reads fields that only exist inside it.
  $("#p-copy").appendChild($("#c-form-wrap"));
  if (arg && arg !== copy.sourceAddress) openSourceAddress(arg);
  else formClaim("copy");
  if (copy.sourceAddress || copy.id != null || copy.blank) showForm();
  else showPicker();
  renderCopyChrome();
  refreshPlans();
  // an empty picker is a dead end, so it starts on the newest launches
  if (!$("#c-picker").hidden && !copy.pickerLoaded){
    copy.pickerLoaded = true;
    pickerSearch();
  }
}
function showPicker(){
  $("#c-picker").hidden = false;
  $("#c-src").hidden = true;
  $("#c-form-wrap").hidden = true;
}
function showForm(){
  $("#c-picker").hidden = true;
  $("#c-src").hidden = false;
  $("#c-form-wrap").hidden = false;
}
// called from every table: jump to the copy tab with this token as the source
function openCopyFor(addr){
  if (!addr) return;
  copy.sourceAddress = null;   // force a reload even when the same token is open
  copy.id = null; copy.status = "draft"; copy.calldata = null; copy.blank = false;
  $("#c-confirm").hidden = true;
  msg("c-msg", "");
  if (tab !== "copy") setTab("copy", addr);
  else { setHash("#copy/" + addr); openSourceAddress(addr); }
}

/* ------------------------------------------------------------ blink copy */
/* The same duplicate as the chip beside it, with the form taken out of it and
   then the panel taken out of that. One click in the launches table reads the
   source token, fills the launch fields from it, builds the calldata and hands
   the transaction straight to the wallet. The only confirmation in the path is
   the wallet's own, and the tab the person is standing on does not change.

   Everything the source token does not carry comes from blankFields(), which
   is the state the Copy tab opens on: the connected wallet as the creator-fee
   recipient and as the buy recipient, the config, the native pair, the factory
   entry point. The creator tax is the one field this path takes from its own
   constant instead of from that draft - it launches at BLINK_TAX_BPS, zero,
   for the reason given there. A factory launch takes the launch fee as its
   whole value, so the initial buy is zeroed by normFields: an initial buy is a
   decision about this launch and not a property of the token being copied, and
   a tax is the same kind of decision, which is exactly why it cannot arrive
   here unread.

   Nothing in here asks whether the click was meant. A row in a table of two
   hundred is something the pointer crosses on the way to the next one and this
   button spends the launch fee, and that is the trade that was asked for: the
   fee is one launch fee and not a balance, the wallet shows the fee, the value
   and the recipient before it signs, and a chip that needed a second click to
   confirm itself would not be the one-click duplicate it is. What is guarded
   is the machine and not the person - one blink at a time, so a double click
   cannot open the wallet twice, and nothing reaches the wallet at all until
   the source token has been read and the calldata built from it. */
const blink = {busy: false};
async function blinkCopy(addr){
  if (!addr || blink.busy) return;
  if (!signerAddress()){
    toast("blink copy needs a signer - connect a wallet on the Wallet tab, or " +
          "pick a stored key there and it will sign from this machine", "err");
    return;
  }
  blink.busy = true;
  try {
    // Two round trips stand between the click and the wallet prompt, because
    // the calldata is built on the server - and the second one is the slow one,
    // seconds while the factory is read for the fee and the economics. This is
    // what says so while they run, and it stays up until one of the messages
    // below replaces it, because the wait is longer than a toast lives.
    toast("blink copy: reading the token and building the transaction", "", true);
    let t;
    try {
      t = await api("/api/token/" + encodeURIComponent(addr));
    } catch(e) {
      toast("blink copy: the source token could not be read - " + errText(e), "err");
      return;
    }
    // The newest few rows are indexed before their metadata is enriched, and a
    // launch cannot carry an empty name or symbol. There is no form on this
    // path to fill the gap in, so it stops with the reason - and the chip
    // beside it, which opens the form, is how the launch gets finished by hand.
    if (!t.name || !t.symbol){
      toast("that token has no name or symbol indexed yet, so it cannot be " +
            "launched as it stands - the copy chip beside this button opens it " +
            "in the form, where both can be filled in", "err");
      return;
    }
    const fields = normFields(fieldsFromToken(t, BLINK_TAX_BPS));
    let d;
    try {
      d = await jpost("/api/launch/calldata", fields);
    } catch(e) {
      toast("blink copy: the calldata could not be built - " + errText(e), "err");
      return;
    }
    // From here the transaction goes out, and how depends on who signs: the
    // wallet's own prompt, where the fee, the value and the recipient are read,
    // or straight to the node when a stored key signs. Both are irreversible,
    // so this is the last line this page writes before either happens.
    const sg = signerPick();
    toast(sg.kind === "key"
      ? "blink copy: " + t.name + " - signing with the stored key"
      : "blink copy: " + t.name + " - confirm in your wallet");
    await sendLaunch(d, { id: null, source_address: addr, fields: fields,
                          extra: [], notes: "", signer: sg }, toast);
  } finally {
    blink.busy = false;
  }
}
async function openSourceAddress(addr){
  formTake("copy");
  copy.sourceAddress = addr;
  copy.blank = false;
  $("#c-src-v").textContent = short(addr) + " (loading)";
  fillFields(blankFields());
  renderExtra([]);
  showForm();
  renderCopyChrome();
  try {
    const t = await api("/api/token/" + encodeURIComponent(addr));
    fillFields(fieldsFromToken(t));
    $("#c-src-v").textContent = (t.name || "?") + " (" + (t.symbol || "?") + ") " + short(addr);
    loadCost();
  } catch(e) {
    msg("c-msg", "could not read the source token: " + errText(e), "err");
    loadCost();
  }
}
// Everything the source token carries, on top of the blank draft. `taxBps` is
// passed by the blink path alone, which is the only caller with no form to show
// the number in; see BLINK_TAX_BPS. Every other caller gets the draft's
// DEFAULT_TAX_BPS, which is what the form opens on.
function fieldsFromToken(t, taxBps){
  const f = blankFields();
  if (taxBps != null) f.creator_tax_bps = taxBps;
  ["name", "symbol", "description", "logo", "twitter", "telegram",
   "discord", "website", "farcaster"].forEach(k => {
    f[k] = (t && t[k] != null) ? String(t[k]) : "";
  });
  return f;
}
function applyPlan(plan){
  if (!plan) return;
  if (plan.id != null) copy.id = plan.id;
  if (plan.status) copy.status = plan.status;
  if (plan.source_address) copy.sourceAddress = plan.source_address;
  if (plan.fields) fillFields(plan.fields);
  if (plan.extra) renderExtra(plan.extra);
  if (plan.notes != null) $("#f-notes").value = plan.notes;
  copy.blank = true;   // a plan without a source still counts as an open draft
  // The form now holds this plan, so the stash kept for whichever tab loaded
  // it is stale and would be restored over the top of it on the next switch.
  formTake(formHold.owner || "copy");
  renderCopyChrome();
}
function renderCopyChrome(){
  $("#c-id").innerHTML = copy.id == null ? "unsaved" : "#" + esc(copy.id);
  const st = $("#c-status");
  st.className = "badge" + (copy.status === "launched" ? " ok" : "");
  st.innerHTML = "<b>" + esc(copy.status || "draft") + "</b>";
  $("#c-delete").disabled = copy.id == null;
}
function resetCopy(){
  formTake("copy");
  copy.id = null; copy.status = "draft"; copy.sourceAddress = null;
  copy.calldata = null; copy.blank = false;
  $("#c-confirm").hidden = true;
  $("#c-form-wrap").hidden = true;
  $("#c-src").hidden = true;
  $("#c-picker").hidden = false;
  setHash("#copy");
  renderCopyChrome();
  refreshPlans();
}

async function pickerSearch(){
  const box = $("#c-results");
  box.innerHTML = '<div class="hint">searching...</div>';
  try {
    const d = await api(qs("/api/tokens", { q: $("#c-q").value.trim(), limit: 25, sort: "newest" }));
    const rows = d.tokens || [];
    if (!rows.length){ box.innerHTML = '<div class="hint">no token matches</div>'; return; }
    box.innerHTML = rows.map(t =>
      '<div class="prow">' + logoCell(t) +
      '<span class="mono"><b>' + esc(t.symbol || "?") + "</b></span>" +
      '<span class="dim">' + esc(t.name || "-") + "</span>" +
      '<span class="mono dim2 cp" data-copy="' + esc(t.address) + '" title="click to copy">' +
        esc(short(t.address)) + "</span>" +
      '<span class="hint">' + esc(fmtAge(t.age_seconds)) + " old</span>" +
      '<span class="grow"></span>' +
      '<button class="ghost" data-copytok="' + esc(t.address) +
        '" title="use this token as the source">use as source</button></div>').join("");
  } catch(e) {
    box.innerHTML = '<div class="hint err">search failed: ' + esc(errText(e)) + "</div>";
  }
}

async function refreshPlans(){
  const box = $("#c-plans");
  const now = Math.floor(Date.now() / 1000);
  try {
    const d = await api("/api/copy");
    const plans = d.plans || [];
    if (!plans.length){ box.innerHTML = '<div class="hint">no saved plans yet</div>'; return; }
    box.innerHTML = plans.map(p => {
      const f = p.fields || {};
      const age = fin(p.updated_at) != null ? fmtAge(Math.max(0, now - fin(p.updated_at))) : "-";
      return '<div class="prow' + (p.id === copy.id ? " on" : "") + '">' +
        '<span class="badge">#' + esc(p.id) + "</span>" +
        '<span class="mono"><b>' + esc(f.symbol || "?") + "</b></span>" +
        '<span class="dim">' + esc(f.name || "-") + "</span>" +
        '<span class="badge' + (p.status === "launched" ? " ok" : "") + '">' +
          "<b>" + esc(p.status || "draft") + "</b></span>" +
        (p.tx_hash ? '<span class="mono dim2 cp" data-copy="' + esc(p.tx_hash) +
          '" title="' + esc(p.tx_hash) + ' - click to copy">tx</span>' : "") +
        '<span class="hint">' + esc(age) + " ago</span>" +
        '<span class="grow"></span>' +
        '<button class="ghost" data-planload="' + esc(p.id) + '" title="load this plan into the form">edit</button>' +
        '<button class="ghost" data-plandel="' + esc(p.id) + '" title="delete this plan">x</button>' +
        "</div>";
    }).join("");
  } catch(e) {
    box.innerHTML = '<div class="hint err">could not list plans: ' + esc(errText(e)) + "</div>";
  }
}
async function loadPlanById(id){
  try {
    const d = await api("/api/copy");
    const p = (d.plans || []).filter(x => String(x.id) === String(id))[0];
    if (!p){ toast("plan #" + id + " is gone", "err"); refreshPlans(); return; }
    applyPlan(p);
    copy.sourceAddress = p.source_address || null;
    $("#c-src-v").textContent = p.source_address
      ? (p.source_symbol ? p.source_symbol + " " : "") + short(p.source_address)
      : "no source token";
    showForm();
    setHash("#copy" + (p.source_address ? "/" + p.source_address : ""));
    loadCost();
    msg("c-msg", "loaded plan #" + id, "ok");
    refreshPlans();
  } catch(e) { msg("c-msg", "could not load the plan: " + errText(e), "err"); }
}
async function deletePlanById(id){
  if (!confirm("delete plan #" + id + "?")) return;
  try {
    await api("/api/copy/" + encodeURIComponent(id), { method: "DELETE" });
    if (String(copy.id) === String(id)) resetCopy();
    else refreshPlans();
    toast("plan #" + id + " deleted", "ok");
  } catch(e) { msg("c-msg", "delete failed: " + errText(e), "err"); }
}

async function savePlan(){
  const btn = $("#c-save");
  btn.disabled = true;
  try {
    const body = planPayload();
    let plan = null;
    if (copy.id == null) {
      const d = await jpost("/api/copy", Object.assign({ source_address: copy.sourceAddress || undefined }, body));
      plan = d.plan || (d.id != null ? Object.assign({ id: d.id }, body) : null);
    } else {
      const d = await jpost("/api/copy/" + copy.id, body, "PUT");
      plan = d.plan || null;
    }
    if (plan) applyPlan(plan);
    copy.blank = true;
    msg("c-msg", "draft saved" + (copy.id != null ? " as #" + copy.id : ""), "ok");
    refreshPlans();
  } catch(e) {
    msg("c-msg", "save failed: " + errText(e), "err");
  } finally {
    btn.disabled = false;
  }
}
async function copyJson(){
  const p = Object.assign({ id: copy.id, source_address: copy.sourceAddress, status: copy.status },
                          planPayload());
  try {
    await copyText(JSON.stringify(p, null, 2));
    toast("plan JSON on the clipboard", "ok");
  } catch(e) { msg("c-msg", "clipboard refused: " + errText(e), "err"); }
}
function removePlan(){
  if (copy.id == null){ msg("c-msg", "this plan is not saved yet", "err"); return; }
  deletePlanById(copy.id);
}

/* ------------------------------------------------------- launch and cost */
let costSeq = 0;
async function loadCost(){
  const cfgId = intOr($("#f-config").value, 0);
  const pair = ($("#f-pair").value || NATIVE).trim();
  const buy = numOr($("#f-buy").value, 0);
  const seq = ++costSeq;
  const box = $("#c-cost");
  box.innerHTML = '<h2>launch cost and economics</h2><div class="hint">reading launch config and preview...</div>';
  let cfg = null, prev = null;
  const errs = [];
  try { cfg = await api("/api/launch/config?config_id=" + encodeURIComponent(cfgId)); }
  catch(e){ errs.push("config: " + errText(e)); }
  try {
    prev = await api("/api/launch/preview?config_id=" + encodeURIComponent(cfgId) +
                     "&pair_token=" + encodeURIComponent(pair));
  } catch(e){ errs.push("preview: " + errText(e)); }
  if (seq !== costSeq) return;   // a newer keystroke already asked for this

  let html = "<h2>launch cost and economics</h2>";
  const native = pair.toLowerCase() === NATIVE;
  if (!native) html += '<div class="warnbox">pair token is not the native zero address. ' +
    "only the native path is known to work, a tokenised equity pair may revert or price differently.</div>";
  if (errs.length) html += '<div class="dangerbox">' + esc(errs.join("\n")) + "</div>";
  if (cfg && cfg.launch_enabled === false)
    html += '<div class="dangerbox">launching is disabled on this config, the factory will reject the transaction.</div>';

  const fee = cfg ? fin(cfg.launch_fee) : null;
  const feeTxt = cfg ? fmtEth(fee)
    : (prev && prev.launch_fee_wei != null ? fmtEth(weiEth(prev.launch_fee_wei)) : "-");
  const rows = [
    ["config id", esc(cfgId)],
    ["launch fee", esc(feeTxt)],
    ["launch fee wei", esc(cfg && cfg.launch_fee_wei ? String(cfg.launch_fee_wei)
      : (prev && prev.launch_fee_wei ? String(prev.launch_fee_wei) : "-"))],
    ["entry point", esc(entryValue() === "router"
      ? "router launchAndBuy" : "factory launchToken")],
    ["initial buy", esc(fmtEth(buy))],
    // (fee || 0) printed a confident total with the fee silently missing
    // when /api/launch/config failed, which reads as the cheapest config
    ["estimated total", fee == null ? "fee unknown" : esc(fmtEth(fee + buy))],
    ["phantom quote", esc(cfg ? fmtBig(cfg.phantom_quote) : "-")],
    ["graduation threshold", esc(cfg ? fmtBig(cfg.graduation_threshold) : "-")],
    ["supply", esc(cfg ? fmtBig(cfg.supply) : "-")],
    ["curve fee", esc(cfg ? nz(cfg.curve_fee_bps) + " bps" : "-")],
    ["pool fee / tick", esc(cfg ? nz(cfg.pool_fee) + " / " + nz(cfg.tick_spacing) : "-")],
    ["config enabled", esc(cfg ? (cfg.enabled ? "yes" : "no") : "-")],
    ["launch enabled", esc(cfg ? (cfg.launch_enabled ? "yes" : "no") : "-")],
    ["pair token", '<span class="mono">' + esc(pair) + "</span>"],
    ["expected economics", prev && prev.expected_economics
      ? '<span class="mono" title="' + esc(prev.expected_economics) + '">' +
        esc(shortHash(prev.expected_economics)) + "</span>"
      : esc("-")]
  ];
  html += kvHtml(rows);
  html += '<div class="hint" style="margin-top:8px">the hash is read live from the factory and must match ' +
          "the one the launch transaction carries, the estimate above is the fee plus the initial buy</div>";
  box.innerHTML = html;
}
// values arrive already escaped, keys are escaped here
function kvHtml(rows){
  return '<div class="kv">' + rows.map(r =>
    '<div class="k">' + esc(r[0]) + '</div><div class="v">' + r[1] + "</div>").join("") + "</div>";
}
function hideConfirm(){ $("#c-confirm").hidden = true; copy.calldata = null; }

async function startLaunch(){
  hideConfirm();
  if (!signerAddress()){
    msg("c-msg", "no signer. connect a wallet on the Wallet tab, or pick a stored " +
      "key there and this machine signs instead.", "err");
    return;
  }
  const fields = readFields();
  if (!fields.name || !fields.symbol){
    msg("c-msg", "a name and a symbol are required before the calldata can be built", "err");
    return;
  }
  // The buy recipient is empty whenever the form was blanked with nothing to
  // sign with, and a router launch has nowhere to send the tokens it buys
  // without one. Filling it from the signer is not magic - the field is on the
  // screen and visibly changes - and it is what the person meant either way.
  if (fields.entry === "router" && !fields.buyer && signerAddress()){
    fields.buyer = signerAddress();
    $("#f-buyer").value = signerAddress();
  }
  if (fields.entry === "router" && !fields.buyer){
    msg("c-msg", "this launch buys tokens, so it has to say who receives them. " +
      "connect a wallet on the Wallet tab, pick a stored key there, or paste an " +
      "address into the buy recipient field.", "err");
    return;
  }
  const btn = $("#c-launch");
  btn.disabled = true;
  msg("c-msg", "building the calldata...");
  try {
    const d = await jpost("/api/launch/calldata", fields);
    copy.calldata = d;
    renderConfirm(d, fields);
    msg("c-msg", "");
  } catch(e) {
    msg("c-msg", "calldata failed: " + errText(e), "err");
  } finally {
    btn.disabled = false;
  }
}

function renderConfirm(d, fields){
  const total = fin(d.value_eth) != null ? fin(d.value_eth)
    : (fin(d.launch_fee_eth) || 0) + (fin(d.initial_buy_eth) || 0);
  const rows = [
    // Who signs, named here because this panel is the last thing read before
    // the fee leaves: with a stored key the transaction never reaches the
    // browser wallet, and a panel that printed the connected wallet over it
    // would be describing a different transaction than the one that is signed.
    ["from", '<span class="mono">' + esc(signerAddress() || "-") + "</span>"],
    ["to", '<span class="mono">' + esc(d.to || "-") + "</span>"],
    ["total cost", '<span class="mono">' + esc(fmtEth(total)) + "</span>"],
    ["launch fee", '<span class="mono">' + esc(fmtEth(d.launch_fee_eth)) + "</span>"],
    ["initial buy", '<span class="mono">' + esc(fmtEth(d.initial_buy_eth)) + "</span>"],
    ["entry point", esc(d.entry === "router"
      ? "router launchAndBuy" : "factory launchToken")],
    ["value (wei)", '<span class="mono">' + esc(d.value != null ? String(d.value) : "-") + "</span>"],
    // Only the router carries a buy, so these three are absent on a factory
    // launch and printing a row of dashes for them would read as a failure
    // rather than as a field that does not apply.
    ...(d.entry === "router" ? [
      ["buy recipient", '<span class="mono">' + esc(d.buyer || "-") + "</span>"],
      ["slippage", esc(bpsPct(d.slippage_bps))],
      ["tokens out (model)", '<span class="mono">' + esc(fmtBig(d.expected_tokens)) + "</span>"],
      ["tokens out (floor)", '<span class="mono">' + esc(fmtBig(d.min_tokens_out)) + "</span>"]
    ] : []),
    ["config id", esc(d.config_id != null ? d.config_id : fields.launch_config_id)],
    ["pair token", '<span class="mono">' + esc(d.pair_token || fields.pair_token) + "</span>"],
    ["salt", '<span class="mono">' + esc(d.salt || "-") + "</span>"],
    ["expected economics", d.expected_economics
      ? '<span class="mono" title="' + esc(d.expected_economics) + '">' +
        esc(shortHash(d.expected_economics)) + "</span>"
      : esc("-")],
    ["calldata", '<span class="mono">' + esc(String((d.data || "").length)) + " chars, starts " +
      esc(String(d.data || "-").slice(0, 12)) + "</span>"]
  ];
  let html = kvHtml(rows);
  if (d.warnings && d.warnings.length)
    html += '<div class="warnbox" style="margin-top:9px">' + esc(d.warnings.join("\n")) + "</div>";
  if (String(d.pair_token || fields.pair_token || "").toLowerCase() !== NATIVE)
    html += '<div class="warnbox">the pair token is not the native zero address</div>';
  $("#c-confirm-body").innerHTML = html;
  $("#c-confirm").hidden = false;
  $("#c-send").disabled = false;
  try { $("#c-confirm").scrollIntoView({ behavior: "smooth", block: "nearest" }); } catch(e){}
}

async function sendNow(){
  const d = copy.calldata;
  if (!d){ msg("c-msg", "no calldata waiting, press Launch from wallet again", "err"); return; }
  if (!signerAddress()){ msg("c-msg", "nothing is selected to sign with, nothing was sent", "err"); return; }
  const btn = $("#c-send");
  btn.disabled = true;
  const r = await sendLaunch(d, {
    id: copy.id, source_address: copy.sourceAddress,
    fields: readFields(), extra: readExtra(), notes: $("#f-notes").value.trim(),
    signer: signerPick()
  }, (text, kind) => msg("c-msg", text, kind));
  if (!r.ok){ btn.disabled = false; return; }
  hideConfirm();
  // The plan the hash went onto is the draft this form is holding, so the form
  // follows it - which is what marks it launched rather than a draft, and what
  // writes the hash back onto it.
  if (r.plan) applyPlan(r.plan);
  refreshPlans();
}

/* Who signs this launch. Answered once, here, and carried in the payload rather
   than asked again inside the send: the two callers already had to know the
   answer to decide whether they could run at all, and a second opinion taken
   halfway down a money path is a second opinion that can differ. */
function signerPick(){
  return kw.active ? {kind: "key", address: kw.active} : {kind: "wallet"};
}

/* ------------------------------------------------------- the send itself */
/* One launch, signed once. The Copy tab arrives here from its confirm panel
   and a blink copy straight from the launches table; the two differ in where
   the fields came from and in where the running commentary goes, and in
   nothing else. The value check, the chain check, the transaction and the plan
   the hash is written onto live here rather than in either caller, because a
   money path with two copies of itself is a money path that gets fixed in one
   of them.

   `say(text, kind)` is the commentary: the message box under the form for the
   Copy tab, the toast for a blink copy, which never leaves the table it was
   clicked in. Returns what happened, and the caller decides what its own
   screen does about it - the panel closes, the table does not.

   The transaction is the only irreversible step here, so it is the one step
   that is not allowed to fail quietly: everything after it is bookkeeping, and
   every way the bookkeeping can go wrong still ends in a message carrying the
   hash, which is the thing a person can still look up. */
async function sendLaunch(d, payload, say){
  const hex = d.value != null ? toHexWei(d.value) : null;
  if (d.value != null && !hex){
    say("the value returned by the backend is not a wei number, refusing to send", "err");
    return {ok: false};
  }
  // Whichever of the two signs, everything below the hash is the same: the plan
  // is written, the draft is marked launched, and the message carries the hash.
  // That is why the fork is here and not in either caller.
  const sg = payload.signer || {kind: "wallet"};
  let hash;
  if (sg.kind === "key"){
    // A stored key signs on the server, so there is no chain for this browser to
    // switch to and no wallet prompt to open - and asking the injected wallet to
    // move networks for a transaction it will never see is the one thing here
    // that could open a window for nothing.
    //
    // What goes out is what the confirm panel printed. The salt is drawn fresh
    // on every build and the token address is a function of it, so a rebuild on
    // the server would deploy a different token at a different address than the
    // one on the screen - which is why the fields are not sent, only the bytes.
    try {
      const r = await jpost("/api/launch/send",
        { from: sg.address, to: d.to, data: d.data, value: d.value });
      hash = r.tx_hash;
    } catch(e) {
      if (kvNeeds(e)){
        say("the vault is locked. unlock it on the Wallets tab and press again - "
          + "nothing was sent.", "err");
      } else if (e.status === 404){
        say("that key is no longer stored, so nothing was sent. pick another "
          + "signer on the Wallets tab.", "err");
      } else {
        say("the stored key could not sign this: " + errText(e), "err");
      }
      return {ok: false};
    }
  } else {
    const onChain = await ensureChain();
    if (!onChain){
      say("the wallet is not on chain " + CHAIN_ID + " and the switch was refused. " +
        "nothing was sent.", "err");
      return {ok: false};
    }
    try {
      const tx = { from: wallet.address, to: d.to, data: d.data };
      if (hex) tx.value = hex;
      hash = await provider().request({ method: "eth_sendTransaction", params: [tx] });
    } catch(e) {
      say("the wallet refused or the transaction failed: " + errText(e), "err");
      return {ok: false};
    }
  }
  let id = payload.id == null ? null : payload.id;
  let plan = null;
  if (id == null){
    try {
      const s = await jpost("/api/copy", Object.assign(
        { source_address: payload.source_address || undefined },
        { fields: payload.fields, extra: payload.extra, notes: payload.notes }));
      plan = s.plan || null;
      if (plan && plan.id != null) id = plan.id;
      else if (s.id != null) id = s.id;
    } catch(e) {
      // the transaction is already out, only the bookkeeping failed
    }
  }
  if (id != null){
    try {
      const r = await jpost("/api/copy/" + id + "/status", { status: "launched", tx_hash: hash });
      plan = r.plan || plan;
      say("sent, plan #" + id + " marked launched. hash " + hash, "ok");
    } catch(e) {
      say("sent with hash " + hash + " but marking the plan launched failed: " +
        errText(e), "err");
    }
  } else {
    say("sent with hash " + hash + " but the plan is unsaved so the status was " +
      "not recorded", "err");
  }
  return {ok: true, hash: hash, plan: plan};
}

/* ----------------------------------------------------------------- wallet */
