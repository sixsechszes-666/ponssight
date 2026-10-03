/* The Wallets tab: keys kept on this machine, and the transfer that spends them.

   Two things live here and they are deliberately in that order on the page. The
   list is a store - a private key goes in, an address comes out, and the key is
   read back only when the show button is pressed, and only while the vault above
   it is open. The runner below it is the only part of this app that moves money
   without asking a wallet first, and it asks before it moves any: a plan is
   quoted and printed, and nothing is signed until start is pressed.

   The vault is a passphrase the server holds in memory. A key is encrypted with
   it before it is stored and decrypted with it when something needs it, so a
   copy of data/pons.db is not a copy of the keys. Three of the calls below can
   come back 423, which is the server saying the vault is shut, and that is
   handled as a thing to fix rather than an error to report.

   Who signs is decided by which wallet pays, not by a mode switch:

     connected -> a stored key     the browser wallet signs both legs, and the
                                   server never sees a signature
     a stored key -> anywhere else the server signs with the key it holds, so
                                   this is the direction that runs on its own
     an address -> itself          one leg, out of Arbitrum: this is the only
                                   way to collect a balance already stranded
                                   there, and relay is the only bridge involved

   The route is two relay deposits rather than one transfer because a direct
   transfer is the thing being avoided: a native send from a burner to the main
   wallet writes the link between them into the chain for good. Through relay
   the far end is paid by a solver, and the two ends are only joined off-chain. */

const kw = {
  wallets: [],     // the last list the server sent, which is what both tables draw
  locked: true,    // the vault's state, as the server last reported it
  picked: null,    // address whose detail panel is open
  secrets: {},     // address -> the key itself, and only once show was pressed
  shown: {},       // address -> whether that key is on screen
  det: null,       // the last /api/wallet/{address} answer, for the detail panel
  plan: null,      // the quoted plan waiting for start
  route: null,     // which of the three shapes the two selects make
  running: false,
  sweep: null,     // the id of a run in flight, if this page started one
  chain: null,     // the server's account of the balances: ages, refusals, due
  chainAt: 0,      // when that account arrived, so an age can be ticked up locally
  chainTimer: null,
  err: "",         // why the list could not be read, if it could not
  active: null     // the stored key that signs launches, or null for the wallet
};

// Which key signs is the one choice on this page that has to outlive a reload:
// a launch built while looking at one signer is signed by that signer, and
// asking again on every visit would be asking a question with no new answer.
// Read at load, the same way wallet.js reads its remembered address, and inside
// a try because a browser with storage switched off throws on the access rather
// than handing back nothing.
try { kw.active = localStorage.getItem("pons.sendkey") || null; } catch(e){ kw.active = null; }

/* ------------------------------------------------------------------ format */
// Wei, because that is the unit the arithmetic happens in and the only unit in
// which "nothing left" is a statement rather than a rounding. The ether figure
// beside it is for reading; the wei figure is for checking.
const kwWei = v => {
  const n = fin(v);
  if (n == null) return "-";
  return Math.round(n).toLocaleString("en-US");
};
const kwEth = v => {
  const e = weiEth(v);
  return e == null ? "-" : e.toFixed(8);
};
// A balance that is zero is the point of this tab, so it is drawn as a zero and
// not as a dash: a dash is this page's word for "not known".
const kwBal = v => {
  const n = fin(v);
  if (n == null) return '<span class="dim2 mono">-</span>';
  if (n === 0) return '<span class="mono">0</span>';
  return '<span class="mono">' + esc(kwEth(n)) + "</span>";
};

/* ----------------------------------------------------------------- the vault */
// A passphrase the server holds in memory and uses to encrypt a key as it is
// stored. Everything that touches a key needs it: adding one, reading one back,
// sweeping with one. The server says which refusals are about the vault with a
// 423, so those are handled here as a next step rather than as a failure.
function kvFirst(){
  // With nothing stored there is nothing to check a passphrase against, so it
  // is being set rather than entered - and that is the one moment a typo cannot
  // be caught later, which is what the second field is for.
  //
  // A list that could not be read is not a list that is empty. Without the
  // second half of this the page offers to *set* a passphrase on a vault that
  // already has one - it is not destructive (`keywallet_blobs()` is not empty,
  // so the server checks instead of setting, and answers 401) but the badge
  // said "not set" and the button said "set it", and neither was true.
  return !kw.wallets.length && !kw.err;
}
function kvRender(){
  const locked = kw.locked, first = kvFirst();
  const st = $("#kv-state");
  if (!st) return;
  st.textContent = kw.err ? "unknown"
    : (locked ? (first ? "not set" : "locked") : "unlocked");
  st.className = "badge" + (kw.err ? "" : (locked ? " bad" : " ok"));
  $("#kv-pass").hidden = !locked;
  $("#kv-pass").placeholder = first ? "choose a passphrase" : "passphrase";
  $("#kv-pass2").hidden = !(locked && first);
  $("#kv-setup").hidden = !(locked && first);
  $("#kv-unlock").hidden = !locked;
  $("#kv-unlock").textContent = first ? "set it" : "unlock";
  $("#kv-lock").hidden = locked;
  $("#ka-add").disabled = locked || kw.running;
}
function kvFocus(){
  const f = $("#kv-pass");
  if (f){ f.hidden = false; f.focus(); }
}
// The page's answer to every "not without the passphrase": say so under the
// field, put the cursor in it, and drop the row of the tab's own message that
// the refused call had already written - "deriving the address..." from a call
// that has just been refused is stale, and left up it reads as a request still
// in flight rather than as a refusal.
function kvLocked(text){
  kw.locked = true;
  kvRender();
  msg("ka-msg", "");
  msg("kv-msg", text, "err");
  kvFocus();
}
// True when the refusal was "the vault is locked", which is answered by typing
// a passphrase rather than by trying again.
function kvNeeds(e){
  if (!e || e.status !== 423) return false;
  kvLocked("the vault is locked. type the passphrase above and press unlock, then "
    + "do that again - nothing was lost.");
  return true;
}
async function kvUnlock(){
  const pass = $("#kv-pass").value;
  const first = kvFirst();
  if (!pass){ msg("kv-msg", "type the passphrase first", "err"); kvFocus(); return; }
  if (first && pass !== $("#kv-pass2").value){
    msg("kv-msg", "the two do not match. the passphrase is not stored anywhere, so a typo "
      + "here is the one thing that cannot be undone later.", "err");
    $("#kv-pass2").focus();
    return;
  }
  msg("kv-msg", first ? "setting the passphrase..." : "checking it against a stored key...");
  try {
    const d = await jpost("/api/keywallets/unlock", {passphrase: pass});
    kw.wallets = d.wallets || [];
    kw.locked = !!d.locked;
    // Out of the field as soon as it is not needed: it is the one secret this
    // page asks a person to type, and a browser can remember a filled input.
    $("#kv-pass").value = "";
    $("#kv-pass2").value = "";
    kvRender();
    kwRender();
    brFill();
    msg("kv-msg", d.fresh
      ? "the vault is open and the passphrase is set. it is not stored on this machine or "
        + "anywhere else, so if it is lost the keys stored under it cannot be read back."
      : "unlocked for this session: until you press lock, the server restarts, or it sits "
        + "idle. " + (d.repaired
          ? d.repaired + " row(s) from before the vault existed were encrypted in place."
          : ""), "ok");
    toast("vault open", "ok");
  } catch(e) {
    msg("kv-msg", errText(e), "err");
    kvFocus();
  }
}
async function kvLock(){
  try {
    const d = await jpost("/api/keywallets/lock", {});
    kw.wallets = d.wallets || [];
    kw.locked = !!d.locked;
    kvRender();
    kwRender();
    msg("kv-msg", "locked, and the passphrase is out of the server's memory. the keys are "
      + "still there and nothing was deleted.", "ok");
  } catch(e) {
    msg("kv-msg", "could not lock: " + errText(e), "err");
  }
}

/* --------------------------------------------------------------- the store */
function mountWallets(){
  // The tick is stopped before anything else, so leaving the tab and coming
  // back cannot leave two of them running over the same table.
  kwChainStop();
  kvRender();
  // The launch form is in another tab and its select lists the keys stored
  // here, so it is drawn on the way in as well as on every answer - a key added
  // while the form is open is a key the form should be able to sign with.
  renderSigner();
  brFill();
  refreshKeyWallets();
}
function kwBusy(on){
  kw.running = on;
  const b = $("#br-start");
  if (b) b.disabled = on || !kw.plan;
  $("#br-plan").disabled = on;
  $("#ka-add").disabled = on || kw.locked;
}
/* ------------------------------------------------------------ the chain read */
// The rows are drawn from the database and the two balances beside them are a
// snapshot the server keeps fresh on the side. This is the side: the page says
// when it last asked and what came back, and the server decides whether asking
// again is worth it.
//
// The decision is the server's and not the page's, and that is deliberate: a
// refresh pressed twice in a second is one local request that starts nothing
// and comes back with a time, and no arithmetic about ages happens in a browser
// whose clock may be a minute out.
const KW_CHAIN_MS = 2000;

function kwChainStop(){
  if (kw.chainTimer){ clearInterval(kw.chainTimer); kw.chainTimer = null; }
}
function kwChainKick(){
  kwChainStop();
  kw.chainTimer = setInterval(kwChainTick, KW_CHAIN_MS);
}
function kwChainTick(){
  // This tab is in no POLLING list because it must not read anything on its own
  // - it moves money and prices keys - so its timer is its own, and it puts
  // itself away the moment the tab is left rather than polling from behind
  // another panel.
  if (tab !== "wallets"){ kwChainStop(); return; }
  const c = kw.chain;
  if (!c) return;
  if (c.running) kwChainGet();       // a read is in flight: watch for it landing
  else if (c.due) kwChainAsk();
}
// The same endpoint the table draws from, so one answer carries both the rows
// and the numbers and a read that lands cannot be applied to half the table.
async function kwChainGet(){
  await refreshKeyWallets(true);
}
async function kwChainAsk(){
  try {
    const d = await jpost("/api/keywallets/chain", {});
    kw.chain = d.chain || null;
    kw.chainAt = Date.now();
  } catch(e) {
    // Asking failed, so stop asking. The tick reads `due` out of an answer it
    // never got, and a page that kept posting every two seconds at a server
    // that is refusing would turn one failure into a minute of them. The next
    // successful read of the list sets this going again.
    kw.chain = null;
  }
  kwChainDraw();
}
function kwChainDraw(){
  const c = kw.chain;
  // One writer for this element. `note` is not used here because it sets
  // textContent and would fight msg() for the same box.
  if (kw.err || !c){ msg("kw-chain", ""); return; }
  // Ages are ticked up from the server's own numbers rather than recomputed
  // from a timestamp: fmtAge takes a duration, and the duration this page knows
  // is the server's age plus the time that has passed since the answer arrived.
  // Neither half of that is a comparison of two clocks.
  const drift = kw.chainAt ? Math.max(0, (Date.now() - kw.chainAt) / 1000) : 0;
  const at = a => (a == null ? null : a + drift);
  const rh = at(c.rh.age), arb = at(c.arb.age);
  // The two halves fail on their own, so the sentence names the one that failed
  // rather than saying "the chain": a Robinhood node that is refusing says
  // nothing about the Arbitrum number standing beside it, and a caption that
  // blamed both would be wrong about one of them.
  const bad = [];
  if (c.rh.error) bad.push("Robinhood: " + c.rh.error);
  if (c.arb.error) bad.push("Arbitrum: " + c.arb.error);
  const one = (name, v) => name + (v == null ? " not read yet" : " read " + fmtAge(v) + " ago");
  const split = rh == null || arb == null || Math.abs(rh - arb) > 5;
  let text;
  if (c.running){
    text = c.age == null
      ? "reading the chain for the first time - no balances yet"
      : "reading the chain; the numbers below are " + fmtAge(at(c.age)) + " old";
  } else if (c.age == null){
    text = "no balances read yet";
  } else {
    // Printed one half at a time when they disagree: a single "read 4s ago" over
    // a column whose other half is three days old is the exact lie this tab is
    // being cured of.
    text = split ? one("Robinhood", rh) + ", " + one("Arbitrum", arb)
                 : "balances read " + fmtAge(at(c.age)) + " ago";
  }
  msg("kw-chain", text + (bad.length ? " - " + bad.join("; ") : ""),
      bad.length ? "err" : "");
}

/* ------------------------------------------------------- who signs a launch */
// One variable, two controls. A radio in this table and a select in the launch
// form are two views of one choice, so both are drawn from `kw.active` and both
// write it through kwSetActive - two independent states would be two opinions
// about who signs, and the one that was wrong would be the one that signed.
//
// The launch form lives in a tab this one is not on, so nothing in here draws
// blindly: the select exists whether or not it is visible, and the fee
// recipient may be a field somebody typed into.
function signerAddress(){
  return kw.active || wallet.address || "";
}
function renderSigner(){
  const sel = $("#f-signer");
  if (!sel) return;
  const ws = (kw.wallets || []).slice();
  // The active key is offered even when the list that would name it has not
  // been read yet. The form is mounted from a tab that does not load the wallet
  // list, so without this a page that came back with a stored key selected
  // would show "browser wallet" over a launch that key is about to sign - and
  // an unknown label for one read is a smaller lie than that. The next list
  // read either fills the label in or clears the choice, which is the rule
  // refreshKeyWallets applies.
  if (kw.active && !ws.some(w => w.address === kw.active))
    ws.unshift({address: kw.active, label: null, known: false});
  sel.innerHTML = '<option value="">browser wallet (connect on the Wallet tab)</option>' +
    ws.map(w => '<option value="' + esc(w.address) + '"' +
      (w.known === false ? ' title="remembered from the last visit"' : "") + ">" +
      esc((w.label ? w.label + " - " : "") + short(w.address)) + "</option>").join("");
  sel.value = kw.active || "";
}
function kwSetActive(addr){
  const next = addr || null;
  if (kw.active === next) return;
  const was = signerAddress();
  kw.active = next;
  try {
    if (next) localStorage.setItem("pons.sendkey", next);
    else localStorage.removeItem("pons.sendkey");
  } catch(e){ /* private mode: the choice simply does not survive a refresh */ }
  // The two fields that mean "me" - the creator fee recipient and the buy
  // recipient - open on whoever signs, so a change of signer has to take them
  // along. Same rule as wallet.js applies to an empty field, plus the one case
  // that is this page's own: a value that was itself the old signer's address
  // is an autofill and not a decision. Anything else was typed, and is left.
  const low = v => (v || "").trim().toLowerCase();
  ["#f-recipient", "#f-buyer"].forEach(id => {
    const el = $(id);
    if (!el) return;
    if (!low(el.value) || low(el.value) === low(was)) el.value = signerAddress();
  });
  kwRender();
  renderSigner();
}

async function refreshKeyWallets(poll){
  const req = latest("keywallets", poll === true);
  if (!req) return;
  try {
    const d = await api("/api/keywallets", {signal: req.signal});
    if (req.stale()) return;
    kw.wallets = d.wallets || [];
    // A key that is no longer stored cannot be the one that signs. The page
    // would be remembering a choice the server does not have, and the next
    // launch would come back "that wallet is not in the stored list" instead of
    // asking - so the list, which is the truth, wins here.
    if (kw.active && !kw.wallets.some(w => w.address === kw.active)){
      kw.active = null;
      try { localStorage.removeItem("pons.sendkey"); } catch(e){}
    }
    // The server is the one that knows: a restart locks the vault, and this is
    // how the page finds out rather than by trying something and being refused.
    kw.locked = !!d.locked;
    kw.chain = d.chain || null;
    kw.chainAt = Date.now();
    kw.err = "";
    kvRender();
    kwRender();
    renderSigner();
    brFill();
    msg("ka-msg", "");
  } catch(e) {
    // The list is not cleared and never will be here. Emptying it is what the
    // tab used to do on a slow answer, and it printed "no wallets saved yet"
    // over wallets that were in the database the whole time - the fault this
    // whole path exists to fix. The rows stay and the page says what happened.
    kw.err = errText(e);
    kvRender();
    kwRender();
    msg("ka-msg", "could not read the stored wallets: " + kw.err, "err");
  }
  kwChainDraw();
  // A tick right here, not in two seconds: a person who pressed refresh is owed
  // the read starting now if it is due, and the tick is the one place that
  // decides. It is a no-op when nothing is due.
  kwChainKick();
  kwChainTick();
}

function kwRender(){
  const box = $("#kw-list");
  const ws = kw.wallets;
  // Three states, not two. "Nothing is stored" and "the list could not be read"
  // are different facts, and drawing the first over the second is the bug this
  // tab had: a request that timed out printed "no wallets saved yet" over a
  // wallet that was in the database, which reads as an empty store and not as
  // a failed read.
  setEmpty("kw-empty",
    kw.err ? "the stored wallets could not be read: " + kw.err
           : (ws.length ? null : "no wallets saved yet"),
    !!kw.err);
  box.innerHTML = ws.map(w => {
    const shown = !!kw.shown[w.address];
    const secret = kw.secrets[w.address];
    const label = w.label
      ? '<span class="mono"><b>' + esc(w.label) + "</b></span>"
      : '<span class="dim">no label</span>';
    // The key cell. Masked until show is pressed, and the masked half is the
    // server's own mask rather than one this file builds, so the page never
    // holds the key in order to draw it.
    const keyCell = '<span class="mono">' +
      esc(shown && secret ? secret : w.secret_mask) + "</span>" +
      '<button class="ghost" data-kwshow="' + esc(w.address) + '">' +
      (shown ? "hide" : "show") + "</button>" +
      '<button class="ghost" data-kwcopy="' + esc(w.address) + '" title="copy the key">cp</button>';
    return "<tr" + (kw.picked === w.address ? ' class="on"' : "") + ">" +
      "<td>" + label + '<div><span class="mono cp" data-copy="' + esc(w.address) +
        '" title="' + esc(w.address + " - click to copy") + '">' + esc(short(w.address)) +
        "</span></div></td>" +
      // Which key signs. A radio and not a button, because one of them is
      // always the signer and a button would leave the page in a state where
      // nothing is - the browser wallet is the other option, and it is the
      // absence of a selected row rather than a row of its own.
      '<td><input type="radio" name="kw-send" data-kwuse="' + esc(w.address) + '"' +
        (kw.active === w.address ? " checked" : "") +
        ' title="sign launches with this key"></td>' +
      '<td class="num">' + kwBal(w.rh_wei) + "</td>" +
      '<td class="num">' + kwBal(w.arb_wei) + "</td>" +
      '<td class="num mono">' + esc(fmtInt(w.launched)) + "</td>" +
      '<td class="num mono">' + esc(fmtInt(w.early_buys)) + "</td>" +
      "<td>" + keyCell + "</td>" +
      "<td>" +
        '<button class="ghost" data-kwpick="' + esc(w.address) + '" title="what this wallet did">open</button> ' +
        '<button class="ghost" data-kwdel="' + esc(w.address) + '" title="forget this key">x</button>' +
      "</td></tr>";
  }).join("");
  kwDet();
}

async function kwAdd(){
  const el = $("#ka-key");
  const key = el.value.trim();
  if (!key){ msg("ka-msg", "paste a private key first", "err"); return; }
  const label = $("#ka-label").value.trim();
  msg("ka-msg", "deriving the address...");
  try {
    const d = await jpost("/api/keywallets", {key, label});
    // Cleared the moment it is stored: the field is the one place on this page
    // a key is typed, and leaving it there puts it back on screen at the next
    // tab switch, in a browser that may well remember the field.
    el.value = "";
    $("#ka-label").value = "";
    kw.wallets = d.wallets || [];
    kwRender();
    brFill();
    msg("ka-msg", "saved. the address was derived from the key, and the key is stored "
      + "encrypted under the vault passphrase.", "ok");
    toast("wallet added", "ok");
  } catch(e) {
    // The field keeps the key it holds on a refusal, so a locked vault costs a
    // passphrase rather than the paste.
    if (kvNeeds(e)) return;
    msg("ka-msg", "not saved: " + errText(e), "err");
  }
}

async function kwShow(address){
  if (kw.shown[address]){
    kw.shown[address] = false;
    kwRender();
    return;
  }
  if (!kw.secrets[address]){
    try {
      const d = await api("/api/keywallets/" + encodeURIComponent(address) + "/secret");
      kw.secrets[address] = d.secret;
    } catch(e) {
      if (kvNeeds(e)) return;
      msg("ka-msg", "could not read the key back: " + errText(e), "err");
      return;
    }
  }
  kw.shown[address] = true;
  kwRender();
}

async function kwCopyKey(address){
  if (!kw.secrets[address]){
    try {
      const d = await api("/api/keywallets/" + encodeURIComponent(address) + "/secret");
      kw.secrets[address] = d.secret;
    } catch(e) {
      if (kvNeeds(e)) return;
      msg("ka-msg", "could not read the key back: " + errText(e), "err");
      return;
    }
  }
  try {
    await copyText(kw.secrets[address]);
    toast("key copied", "ok");
  } catch(e) {
    msg("ka-msg", "the browser refused the clipboard: " + errText(e), "err");
  }
}

async function kwLabel(address){
  const w = kw.wallets.filter(x => x.address === address)[0];
  const next = prompt("name this wallet", w && w.label ? w.label : "");
  if (next == null) return;
  try {
    const d = await jpost("/api/keywallets/" + encodeURIComponent(address),
                          {label: next.trim()}, "PATCH");
    kw.wallets = d.wallets || [];
    kwRender();
    brFill();
    toast("renamed", "ok");
  } catch(e) {
    msg("ka-msg", "could not rename: " + errText(e), "err");
  }
}

async function kwDelete(address){
  const w = kw.wallets.filter(x => x.address === address)[0];
  const name = w && w.label ? w.label : address;
  if (!confirm("forget " + name + "?\n\nthe key is deleted from this machine. the wallet "
      + "itself is untouched, and anything already sent stays where it is.")) return;
  try {
    const d = await jpost("/api/keywallets/" + encodeURIComponent(address),
                          null, "DELETE");
    kw.wallets = d.wallets || [];
    delete kw.secrets[address];
    delete kw.shown[address];
    if (kw.picked === address) kw.picked = null;
    // Forgotten here means forgotten everywhere: a signer whose key is gone
    // would fail at the next launch with a 404 from the server, which is a
    // worse way to find out than the page falling back to the wallet. This also
    // takes the address out of the two fields that were filled with it.
    if (kw.active === address) kwSetActive(null);
    kwRender();
    brFill();
    toast("forgotten", "ok");
  } catch(e) {
    msg("ka-msg", "could not delete: " + errText(e), "err");
  }
}

/* --------------------------------------------------------- the detail panel */
// Same tables as the Wallet tab, drawn from the same endpoint and the same cell
// helpers, so a launch looks the same wherever it is looked at. The one thing
// this panel adds is the count the list already carried: which launches came
// from this wallet is the question the tab exists to answer.
function kwDet(){
  const box = $("#kw-det-box");
  const a = kw.picked;
  if (!a){ box.hidden = true; return; }
  box.hidden = false;
  const w = kw.wallets.filter(x => x.address === a)[0];
  $("#kw-det-h").textContent = (w && w.label ? w.label + " - " : "") + a;
  const d = kw.det;
  if (!d){
    $("#kw-det").innerHTML = '<div class="hint">reading ' + esc(short(a)) + "...</div>";
    return;
  }
  const now = Math.floor(Date.now() / 1000);
  const launched = d.launched || [];
  const early = d.early_buys || [];
  // The list's number wins over the detail panel's own live one, deliberately,
  // so that one wallet cannot show two different balances in two places on the
  // same tab. What that means since the rows stopped going to the chain is that
  // this is the cached reading - the caption under the table is where its age
  // is stated, and that is the one place it belongs.
  let html = '<div class="inline">' +
    '<span class="mono cp" data-copy="' + esc(a) + '" title="click to copy">' + esc(a) + "</span>" +
    '<span class="badge">robinhood <b>' + esc(kwEth(w ? w.rh_wei : d.eth_balance)) + " ETH</b></span>" +
    '<span class="badge">arbitrum <b>' + esc(kwEth(w ? w.arb_wei : null)) + " ETH</b></span>" +
    '<span class="grow"></span>' +
    '<button class="ghost" data-kwlabel="' + esc(a) + '">rename</button>' +
    '<button class="ghost" data-kwview="' + esc(a) + '" title="open this wallet on the Wallet tab">' +
      "view on the wallet tab</button>" +
    '<button class="ghost" data-kwback="1">close</button>' +
    "</div>";

  html += '<h2 class="mt4">launches from this wallet - ' + esc(fmtInt(launched.length)) + "</h2>";
  html += launched.length
    ? '<div class="tw"><table><thead><tr><th>Token</th><th style="width:74px">Age</th>' +
      '<th style="width:98px">MCap USD</th><th style="width:118px">Graduation</th>' +
      '<th style="width:78px">Snipe</th><th style="width:60px"></th></tr></thead><tbody>' +
      launched.map(t => {
        const age = t.launch_ts ? Math.max(0, now - t.launch_ts) : null;
        return "<tr><td>" + tokenCell(t, t.address
            ? "https://www.ponsfamily.com/launchpad/" + t.address : null) + "</td>" +
          "<td>" + ageCell(age) + "</td>" +
          '<td class="mono">' + (t.graduated ? DIMDASH : esc(fmtUsd(t.mcap_usd))) + "</td>" +
          "<td>" + barCell(t.progress_pct, !!t.graduated) + "</td>" +
          "<td>" + snipePill(t.snipe_label ? {label: t.snipe_label} : null) + "</td>" +
          "<td>" + copyBtn(t.address) + "</td></tr>";
      }).join("") + "</tbody></table></div>"
    : '<div class="empty">no launches from this wallet</div>';

  html += '<h2 class="mt4">early buys - ' + esc(fmtInt(early.length)) + "</h2>";
  html += early.length
    ? '<div class="tw"><table><thead><tr><th>Token</th><th style="width:74px">Age</th>' +
      '<th style="width:110px">First buy</th><th style="width:80px">Delta</th>' +
      '<th style="width:88px">Snipe</th><th style="width:60px"></th></tr></thead><tbody>' +
      early.map(t => {
        const age = t.launch_ts ? Math.max(0, now - t.launch_ts) : null;
        return "<tr><td>" + tokenCell(t, t.address
            ? "https://www.ponsfamily.com/launchpad/" + t.address : null) + "</td>" +
          "<td>" + ageCell(age) + "</td>" +
          '<td class="mono">' + esc(fmtQuoteK(t.first_buy_quote, t.quote_symbol || "")) + "</td>" +
          '<td class="mono">' + (fin(t.delta) != null ? esc(fmtInt(t.delta)) : DIMDASH) + "</td>" +
          "<td>" + snipePill(t.snipe_label ? {label: t.snipe_label} : null) + "</td>" +
          "<td>" + copyBtn(t.address) + "</td></tr>";
      }).join("") + "</tbody></table></div>"
    : '<div class="empty">no early buys indexed</div>';
  $("#kw-det").innerHTML = html;
}

async function kwPick(address){
  kw.picked = address;
  kw.det = null;
  kwRender();
  try {
    kw.det = await api("/api/wallet/" + encodeURIComponent(address));
  } catch(e) {
    kw.det = {launched: [], early_buys: []};
    msg("ka-msg", "could not read that wallet's launches: " + errText(e), "err");
  }
  kwDet();
}

/* --------------------------------------------------------------- the runner */
// The two selects are the whole interface. Every pair of them means exactly one
// route, and which one is printed under them in words before anything is
// quoted, so the button is never the first place the plan appears.
function brFill(){
  const me = wallet.address;
  const opts = [];
  if (me) opts.push({v: me, t: "my connected wallet " + short(me)});
  kw.wallets.forEach(w => opts.push({
    v: w.address,
    t: (w.label ? w.label + " - " : "key wallet ") + short(w.address)
  }));
  const html = opts.map(o => '<option value="' + esc(o.v) + '">' + esc(o.t) + "</option>").join("");
  const src = $("#br-src"), dst = $("#br-dest");
  const keepSrc = src.value, keepDst = dst.value;
  src.innerHTML = html;
  // The same list on both sides, including the source's own address: an address
  // paying itself is a real route here - it is how a balance stranded in
  // Arbitrum comes home - so it is a choice rather than something hidden.
  dst.innerHTML = html;
  if (keepSrc && opts.some(o => o.v === keepSrc)) src.value = keepSrc;
  if (keepDst && opts.some(o => o.v === keepDst)) dst.value = keepDst;
  if (!opts.length) src.innerHTML = dst.innerHTML = '<option value="">no wallets</option>';
  brRoute();
}

function brRoute(){
  const src = $("#br-src").value, dst = $("#br-dest").value;
  const me = wallet.address;
  kw.plan = null;
  kw.route = null;
  if (!src || !dst){
    $("#br-route").textContent = "add a key wallet, or connect a browser wallet, "
      + "and the route appears here.";
    $("#br-start").disabled = true;
    return;
  }
  const same = src.toLowerCase() === dst.toLowerCase();
  const mine = !!me && src.toLowerCase() === me.toLowerCase();
  if (same && mine){
    kw.route = {kind: "return", src, dst};
    $("#br-route").textContent = "one leg: everything this wallet holds on "
      + ARB_CHAIN_NAME + " is bridged back to the same address on Robinhood Chain. "
      + "this is the way to collect a balance already stranded in Arbitrum. your browser "
      + "wallet signs it.";
  } else if (same){
    kw.route = {kind: "self", src, dst};
    $("#br-route").textContent = "a key wallet cannot bridge to its own address, and it "
      + "does not need to: sending it to any other wallet sweeps its Arbitrum balance "
      + "along with the rest, because the second leg takes whatever is in Arbitrum.";
  } else if (mine){
    kw.route = {kind: "out", src, dst};
    $("#br-route").textContent = "two legs: everything in the connected wallet leaves "
      + "Robinhood Chain, lands in Arbitrum at the same address, and is bridged on to the "
      + "wallet you picked - which leaves the connected wallet with nothing but the "
      + "unused-gas refund. the key of the receiving wallet is not used; your browser "
      + "wallet signs both legs.";
  } else {
    kw.route = {kind: "back", src, dst};
    $("#br-route").textContent = "two legs, signed on this machine with the stored key: "
      + "everything leaves the key wallet on Robinhood Chain, lands in Arbitrum at the "
      + "same address, and is bridged on to "
      + (dst.toLowerCase() === (me || "").toLowerCase() ? "the connected wallet" : short(dst))
      + ". this one runs without a wallet prompt, so read the plan before pressing start.";
  }
  $("#br-plan").disabled = kw.route.kind === "self";
  $("#br-start").disabled = true;
  $("#br-badge").textContent = "not planned";
  $("#br-steps").innerHTML = "";
  msg("br-msg", "");
}

// One line per step, replaced in place as the step moves: the log reads as the
// plan being carried out rather than as a growing list of attempts.
function brLine(key, text, detail, st){
  const box = $("#br-steps");
  let el = box.querySelector('[data-k="' + key + '"]');
  if (!el){
    el = document.createElement("div");
    el.className = "kstep";
    el.dataset.k = key;
    el.innerHTML = '<span class="kn mono"></span><span class="grow"></span>' +
      '<span class="kd dim mono"></span>';
    box.appendChild(el);
  }
  el.dataset.st = st || "";
  el.children[0].textContent = key;
  el.children[1].textContent = text;
  el.children[2].textContent = detail || "";
}
function brBadge(text, kind){
  const b = $("#br-badge");
  b.textContent = text;
  b.className = "badge" + (kind ? " " + kind : "");
}

const brQuote = (direction, frm, to, leg) =>
  jpost("/api/bridge/plan", {direction, from: frm, to: to, leg});

// What a leg looks like before it is run: the amount, the gas reserved for it,
// and what is expected to arrive at the other end. In wei as well, because the
// question this tab answers is whether the wallet ends at zero.
function brPlanLine(key, n, leg){
  brLine(key, "leg " + n + " plan",
    kwEth(leg.amount) + " ETH out / gas " + kwEth(leg.gas_cost)
    + " / arrives ~" + kwEth(leg.out_estimate),
    "run");
}

async function brPlan(){
  const r = kw.route;
  if (!r || r.kind === "self") return;
  $("#br-steps").innerHTML = "";
  msg("br-msg", "asking relay for a route...");
  $("#br-plan").disabled = true;
  try {
    // Only the first leg is quoted. The second one's amount is whatever the
    // first one delivers, so a quote for it now would be a quote against a
    // balance that does not exist yet - and relay refuses to price a transfer
    // out of an empty wallet rather than guessing.
    const dir = r.kind === "back" ? "back" : r.kind;
    const a = await brQuote(dir, r.src, r.dst, 1);
    brPlanLine("leg1", 1, a.leg);
    brLine("leg2", "leg 2 plan",
      "quoted once leg 1 lands, on the " + kwEth(a.leg.out_estimate)
      + " ETH it brings to " + ARB_CHAIN_NAME, "wait");
    kw.plan = {kind: r.kind, legs: [a.leg]};
    brBadge("planned", "ok");
    msg("br-msg", "nothing has been signed. the amounts above are relay's own quote for "
      + "the whole balance of the paying wallet, and the second leg is quoted fresh "
      + "against the balance that actually arrives.", "ok");
    $("#br-start").disabled = false;
  } catch(e) {
    brBadge("plan failed", "bad");
    msg("br-msg", "relay would not quote this: " + errText(e), "err");
  } finally {
    $("#br-plan").disabled = false;
  }
}

// relay's own status for one deposit, polled until it is filled. A failure is
// relay's word - failure or refund - and is reported as itself rather than
// turned into a generic error, because a refund means the money is back.
async function brWaitFill(requestId, key){
  const deadline = Date.now() + 180000;
  let last = "";
  while (Date.now() < deadline){
    let st = "unknown";
    try {
      const d = await api("/api/bridge/status?requestId=" + encodeURIComponent(requestId));
      st = String(d.status || "unknown");
    } catch(e) {
      st = "unknown";
    }
    if (st !== last){
      brLine(key, "waiting for relay", st, st === "success" ? "ok" : "run");
      last = st;
    }
    if (st === "success") return true;
    if (st === "failure" || st === "refund")
      throw new Error("relay reported " + st);
    await brSleep(3000);
  }
  // Still in flight rather than lost: the deposit is in and the solver has not
  // finished. The page says so and stops, because the next leg needs a balance
  // that has not arrived.
  brLine(key, "still in flight", "relay has not filled it yet", "run");
  return false;
}

const brSleep = ms => new Promise(r => setTimeout(r, ms));

async function brBalances(address){
  return api("/api/bridge/balances?address=" + encodeURIComponent(address));
}

// Waits for the middle hop to appear. The solver pays the recipient, so this is
// a node catching up rather than the bridge working, and the balance is the
// only thing a second leg can be quoted against.
async function brWaitArb(address, key){
  const deadline = Date.now() + 120000;
  let seen = 0;
  while (Date.now() < deadline){
    const b = await brBalances(address);
    seen = numOr(b.arb_wei, 0);
    if (seen > 0){
      brLine(key, "middle hop arrived", kwEth(seen) + " ETH on " + ARB_CHAIN_NAME, "ok");
      return seen;
    }
    brLine(key, "waiting for the middle hop", "arbitrum balance is still zero", "run");
    await brSleep(4000);
  }
  return 0;
}

// The browser-signed leg: the wallet is put on the right chain, the transaction
// relay quoted is handed over, and the fee cap is passed with it. The cap is
// not optional - the amount was solved as the balance minus gas at that exact
// cap, so a wallet that picks its own higher fee is spending money the balance
// was not told to reserve, and the node rejects it.
async function brSendBrowser(leg, chain, key){
  const ok = await ensureChainId(chain.id, chain.hex, chain.name, chain.rpc);
  if (!ok){
    brLine(key, "wrong chain", "the wallet is not on " + chain.name
      + " and the switch was refused", "bad");
    throw new Error("the wallet is not on " + chain.name);
  }
  const tx = {
    from: leg.user,
    to: leg.to,
    data: leg.data,
    value: toHexWei(leg.value),
    gas: toHexWei(leg.gas),
    maxFeePerGas: toHexWei(leg.fee_cap)
  };
  if (!tx.value || !tx.gas || !tx.maxFeePerGas)
    throw new Error("the backend returned a number that is not a wei amount");
  brLine(key, "sending from " + chain.name,
    kwEth(leg.amount) + " ETH, " + short(leg.user), "run");
  try {
    const h = await provider().request({method: "eth_sendTransaction", params: [tx]});
    brLine(key, "sent from " + chain.name, shortHash(h), "run");
    return h;
  } catch(e) {
    brLine(key, "the wallet refused", errText(e), "bad");
    throw e;
  }
}

async function brRunOut(r){
  const chain = {id: wallet.cfg.chain_id, hex: wallet.cfg.chain_hex,
                 name: wallet.cfg.chain_name, rpc: wallet.cfg.rpc_url};
  const arb = {id: wallet.cfg.arb_chain_id, hex: wallet.cfg.arb_chain_hex,
               name: wallet.cfg.arb_chain_name, rpc: wallet.cfg.arb_rpc_url};
  const l1 = await brQuote("out", r.src, r.dst, 1);
  await brSendBrowser(l1.leg, chain, "leg1");
  if (!await brWaitFill(l1.leg.request_id, "leg1")) return "pending";
  if (!await brWaitArb(r.src, "leg1")){
    msg("br-msg", "the first leg reported filled but nothing has arrived in "
      + ARB_CHAIN_NAME + " yet. press start again in a minute: the second leg reads the "
      + "balance fresh, so nothing is lost by waiting.", "err");
    return "pending";
  }
  // Quoted now rather than reusing the preview: the amount is the balance that
  // actually landed, and the fee has moved since the plan was drawn.
  const l2 = await brQuote("out", r.src, r.dst, 2);
  brPlanLine("leg2", 2, l2.leg);
  await brSendBrowser(l2.leg, arb, "leg2");
  if (!await brWaitFill(l2.leg.request_id, "leg2")) return "pending";
  await brFinish(r.src, r.dst);
  return "done";
}

async function brRunReturn(r){
  const arb = {id: wallet.cfg.arb_chain_id, hex: wallet.cfg.arb_chain_hex,
               name: wallet.cfg.arb_chain_name, rpc: wallet.cfg.arb_rpc_url};
  const l1 = await brQuote("return", r.src, r.dst, 1);
  await brSendBrowser(l1.leg, arb, "leg1");
  if (!await brWaitFill(l1.leg.request_id, "leg1")) return "pending";
  await brFinish(r.src, r.dst);
  return "done";
}

// The server-signed direction. The plan above was this page's estimate; what
// actually runs is the sweep on the server, and its own steps are what gets
// drawn - the page is watching a run, not driving one.
async function brRunBack(r){
  const run = await jpost("/api/bridge/sweep", {from: r.src, to: r.dst});
  kw.sweep = run.id;
  brBadge("running on the server");
  const deadline = Date.now() + 900000;
  let seen = null;
  while (Date.now() < deadline){
    await brSleep(2000);
    let cur;
    try {
      cur = await api("/api/bridge/sweep/" + encodeURIComponent(kw.sweep));
    } catch(e) {
      brLine("leg1", "lost contact with the run", errText(e), "bad");
      throw e;
    }
    (cur.steps || []).forEach(s => {
      const key = "leg" + s.leg;
      brLine(key, "leg " + s.leg + " " + s.state, s.detail || "", s.state === "filled"
        ? "ok" : (s.state === "pending" ? "run" : "run"));
    });
    if (cur.final && fin(cur.final.rh_wei) != null) seen = cur;
    if (cur.state !== "running"){
      if (cur.state === "failed"){
        brBadge("failed", "bad");
        msg("br-msg", "the run stopped: " + (cur.error || "no reason given")
          + ". whatever is already bridged is on the chain; press start again to continue "
          + "from wherever it stopped.", "err");
        return "failed";
      }
      await brFinish(r.src, r.dst, seen);
      return cur.state;
    }
  }
  brBadge("still running");
  msg("br-msg", "the run is taking longer than this page waited. it is still going on the "
    + "server; press refresh to see where it got to.", "err");
  return "pending";
}

// The final readout. Two numbers matter and they are both in wei: what is left
// on the wallet that paid, and what arrived at the one that was paid.
async function brFinish(frm, to, run){
  let f = run && run.final ? run.final : null;
  if (!f){
    try {
      const [a, b] = [await brBalances(frm), await brBalances(to)];
      f = {rh_wei: a.rh_wei, arb_wei: a.arb_wei, to_rh_wei: b.rh_wei};
    } catch(e) {
      msg("br-msg", "the transfers are through, but the closing balances could not be read: "
        + errText(e), "err");
      return;
    }
  }
  const left = numOr(f.rh_wei, 0) + numOr(f.arb_wei, 0);
  brLine("end", "left on the paying wallet",
    kwWei(f.rh_wei) + " wei on Robinhood / " + kwWei(f.arb_wei) + " wei on "
    + ARB_CHAIN_NAME, left === 0 ? "ok" : "run");
  brLine("done", "balance of " + short(to) + " now",
    kwWei(f.to_rh_wei) + " wei on Robinhood Chain", "ok");
  brBadge("done", "ok");
  msg("br-msg", "done. " + kwWei(left) + " wei is left on the paying wallet: the gas the "
    + "transaction did not use comes back by the rules of the chain, so this is the refund "
    + "and never a remainder that was missed. the wallet is on " + ARB_CHAIN_NAME
    + " now - the header switches it back.", left === 0 ? "ok" : "");
  refreshKeyWallets();
}

async function brStart(){
  const r = kw.route;
  if (!r || r.kind === "self" || !kw.plan) return;
  // The one direction that is signed here rather than by the browser wallet is
  // the one that needs the vault, and saying so before the confirm dialog beats
  // saying it after the run has started and been refused.
  if (r.kind === "back" && kw.locked){
    kvLocked("the vault is locked, and this direction is signed on this machine with "
      + "the stored key. unlock it above and press start again.");
    return;
  }
  if (!confirm("run the transfer for real?\n\n"
      + (r.kind === "back"
        ? "this one is signed on this machine with the stored key. it does not ask the "
          + "wallet first."
        : "your browser wallet will ask for each leg.")))
    return;
  kwBusy(true);
  brBadge("running");
  try {
    if (r.kind === "out") await brRunOut(r);
    else if (r.kind === "return") await brRunReturn(r);
    else await brRunBack(r);
  } catch(e) {
    brBadge("stopped", "bad");
    msg("br-msg", "stopped: " + errText(e) + ". nothing further was sent; press plan to "
      + "quote the rest again.", "err");
  } finally {
    kwBusy(false);
  }
}
