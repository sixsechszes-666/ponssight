/* The wallet, in four parts.

   `provider` is whichever EIP-1193 object is currently in charge: an injected
   one chosen from the picker, a WalletConnect session, or window.ethereum as
   the fallback for a browser that announced nothing. Everything in this app
   reads provider(), so which wallet is connected is one variable rather than a
   decision repeated at each call site.

   `announced` is what EIP-6963 told us. A wallet that speaks the standard
   announces itself - name, icon, reverse-DNS id - repeatedly, for as long as
   the page is listening, and re-announces when asked. That is the difference
   between a picker that lists MetaMask and Rabby and one that offers a single
   button labelled "browser wallet" and hopes. */
const wallet = {
  address: null, chainId: null, available: false,
  provider: null,        // the EIP-1193 object in use
  label: null,           // what to call it on screen
  announced: [],         // EIP-6963 announcements, in arrival order
  wc: null,              // the WalletConnect provider, once one exists
  wcLoading: false,
  opener: null,          // what had the focus when the dialog opened
  backdrop: false,       // whether the press being handled started on the scrim
  cfg: { chain_id: CHAIN_ID, chain_name: CHAIN_NAME, rpc_url: RPC_URL,
         walletconnect_project_id: "" }
};
let viewAddr = null, watchBusy = false;

function setAddress(a){
  wallet.address = a || null;
  try {
    if (a) localStorage.setItem("pons.address", a);
    else localStorage.removeItem("pons.address");
  } catch(e){ /* private mode: the address just does not survive a refresh */ }
  if (a && !$("#f-recipient").value.trim()) $("#f-recipient").value = a;
  renderWalletChrome();
}
function forgetWallet(){
  setAddress(null);
  viewAddr = null;
  renderWalletChrome();
  clearWalletTables();
}
function clearWalletTables(){
  $("#w-summary").innerHTML = "";
  $("#w-launched").innerHTML = "";
  $("#w-early").innerHTML = "";
  setEmpty("w-launched-empty", null);
  setEmpty("w-early-empty", null);
}
function renderWalletChrome(){
  const a = viewAddr || wallet.address;
  const cid = wallet.chainId != null ? parseInt(wallet.chainId, 16) : null;
  const good = cid === CHAIN_ID;

  // The bar widget. Its dot carries the state, its label carries the address -
  // the same division the live badge makes. Amber rather than red for the wrong
  // chain: it is a step not taken yet, not a loss, and the money colours are
  // kept for money.
  const open = $("#w-open");
  if (open){
    $("#w-open-v").textContent = wallet.address ? short(a) : "connect";
    open.classList.toggle("on", !!wallet.address && good);
    open.classList.toggle("off", !!wallet.address && !good);
    open.title = wallet.address
      ? (wallet.label ? wallet.label + " - " : "") + a + " - open the wallet dialog"
      : "connect a wallet";
  }
  // the dialog reads from the same state this does, so it repaints with it
  if (walletOpen()) renderWalletModal();

  $("#w-noconn").hidden = !!wallet.address;
  $("#w-conn").hidden = !wallet.address;
  const el = $("#w-addr");
  if (wallet.address){
    el.textContent = wallet.address;
    el.dataset.copy = wallet.address;
    el.title = wallet.address + " - click to copy";
  } else {
    el.textContent = "-";
    el.dataset.copy = "";
  }
  const ch = $("#w-chain");
  ch.className = "badge" + (good ? " ok" : "");
  ch.innerHTML = "chain <b>" + esc(cid == null || isNaN(cid) ? "-" : String(cid)) + "</b>";
  $("#w-switch").hidden = !wallet.address || good;
  $("#w-view-clear").hidden = !viewAddr;
  $("#w-launched-h").textContent = viewAddr
    ? "deployed tokens - watching " + short(viewAddr) : "deployed tokens";
}

function mountWallet(){
  renderWalletChrome();
  refreshWallet();
  refreshWatch();
}

async function refreshWallet(fromPoll){
  const a = viewAddr || wallet.address;
  if (!a){
    $("#w-summary").innerHTML = '<div class="hint">connect a wallet, or press view on a watchlist ' +
      "entry, to read a summary</div>";
    setEmpty("w-launched-empty", null);
    setEmpty("w-early-empty", null);
    return;
  }
  // this one had no guard at all, so clicking a second watchlist entry
  // before the first resolved could paint one wallet's positions under
  // another wallet's header, with nothing on screen saying so
  const req = latest("wallet", fromPoll === true);
  msg("w-msg", "reading /api/wallet/" + short(a) + "...");
  try {
    const d = await api("/api/wallet/" + encodeURIComponent(a),
                        {signal: req.signal});
    if (req.stale()) return;
    const now = Math.floor(Date.now() / 1000);
    const cards = [
      ["eth balance", fin(d.eth_balance) != null ? fin(d.eth_balance).toFixed(4) : "-", "acc"],
      ["balance usd", fmtUsd(d.balance_usd), ""],
      ["eth price", fin(d.eth_usd) != null ? "$" + Math.round(fin(d.eth_usd)) : "-", ""],
      ["early buys", fmtInt(d.early_buy_count), numOr(d.early_buy_count, 0) > 0 ? "warn" : ""],
      // the table below is a capped page of these, and saying so is cheaper
      // than letting someone compare it against the Snipers tab and conclude
      // one of the two is broken
      ["listed below", fin(d.early_buys_listed) == null ? "-"
        : fmtInt(d.early_buys_listed) +
          (numOr(d.early_buys_listed, 0) < numOr(d.early_buy_count, 0)
            ? " of " + fmtInt(d.early_buy_count) : ""), ""]
    ];
    $("#w-summary").innerHTML = cards.map(c =>
      '<div class="stat"><div class="k">' + esc(c[0]) + '</div><div class="v ' + c[2] + '">' +
      esc(c[1]) + "</div></div>").join("");
    msg("w-msg", "");

    const launched = d.launched || [];
    $("#w-launched").innerHTML = launched.map(t => {
      const age = t.launch_ts ? Math.max(0, now - t.launch_ts) : null;
      return "<tr>" +
        "<td>" + tokenCell(t, t.address ? "https://www.ponsfamily.com/launchpad/" + t.address : null) + "</td>" +
        "<td>" + ageCell(age) + "</td>" +
        '<td class="mono">' + (t.graduated ? DIMDASH : esc(fmtUsd(t.mcap_usd))) + "</td>" +
        "<td>" + barCell(t.progress_pct, !!t.graduated) + "</td>" +
        "<td>" + snipePill(t.snipe_label ? { label: t.snipe_label } : null) + "</td>" +
        '<td class="mono">' + esc(fmtUsd(t.volume_usd)) + "</td>" +
        "<td>" + copyBtn(t.address) + "</td></tr>";
    }).join("");
    setEmpty("w-launched-empty", launched.length ? null : "no launches from this wallet");

    const early = d.early_buys || [];
    $("#w-early").innerHTML = early.map(t => {
      const age = t.launch_ts ? Math.max(0, now - t.launch_ts) : null;
      return "<tr>" +
        "<td>" + tokenCell(t, t.address ? "https://www.ponsfamily.com/launchpad/" + t.address : null) + "</td>" +
        "<td>" + ageCell(age) + "</td>" +
        '<td class="mono">' + esc(fmtQuoteK(t.first_buy_quote, t.quote_symbol || "")) + "</td>" +
        '<td class="mono">' + (fin(t.delta) != null ? esc(fmtInt(t.delta)) : DIMDASH) + "</td>" +
        "<td>" + snipePill(t.snipe_label ? { label: t.snipe_label } : null) + "</td>" +
        "<td>" + copyBtn(t.address) + "</td></tr>";
    }).join("");
    setEmpty("w-early-empty", early.length ? null : "no early buys indexed");
  } catch(e) {
    msg("w-msg", "wallet fetch failed: " + errText(e) +
      " (the backend has to answer /api/wallet/{address})", "err");
    clearWalletTables();
  }
}

async function refreshWatch(){
  if (watchBusy) return;
  watchBusy = true;
  const box = $("#wl-list");
  const now = Math.floor(Date.now() / 1000);
  try {
    const d = await api("/api/wallets");
    const ws = d.wallets || [];
    if (!ws.length){ box.innerHTML = '<div class="hint">watchlist is empty</div>'; return; }
    box.innerHTML = ws.map(w =>
      '<div class="prow">' +
      '<span class="mono cp" data-copy="' + esc(w.address) + '" title="' +
        esc(w.address + " - click to copy") + '">' + esc(short(w.address)) + "</span>" +
      '<span class="dim">' + esc(w.label || "-") + "</span>" +
      '<span class="hint">' + (fin(w.added_at) != null
        ? esc(fmtAge(Math.max(0, now - fin(w.added_at)))) + " ago" : "") + "</span>" +
      '<span class="grow"></span>' +
      '<button class="ghost" data-wlview="' + esc(w.address) + '" title="read this wallet summary">view</button>' +
      '<button class="ghost" data-wldel="' + esc(w.address) + '" title="remove from the watchlist">x</button>' +
      "</div>").join("");
  } catch(e) {
    box.innerHTML = '<div class="hint err">watchlist fetch failed: ' + esc(errText(e)) + "</div>";
  } finally {
    watchBusy = false;
  }
}
async function addWatch(){
  const address = $("#wl-addr").value.trim();
  const label = $("#wl-label").value.trim();
  if (!address){ msg("w-msg", "enter an address to watch", "err"); return; }
  try {
    await jpost("/api/wallets", { address: address, label: label });
    $("#wl-addr").value = ""; $("#wl-label").value = "";
    msg("w-msg", "added " + short(address) + " to the watchlist", "ok");
    refreshWatch();
  } catch(e) { msg("w-msg", "could not add it: " + errText(e), "err"); }
}
async function delWatch(address){
  try {
    await api("/api/wallets/" + encodeURIComponent(address), { method: "DELETE" });
    if (viewAddr && viewAddr.toLowerCase() === String(address).toLowerCase()) viewAddr = null;
    renderWalletChrome();
    refreshWatch();
    toast("removed from the watchlist", "ok");
  } catch(e) { msg("w-msg", "could not remove it: " + errText(e), "err"); }
}

/* ------------------------------------------------------ the wallet bridge */

// One accessor for "the wallet this page is talking to". Every request goes
// through it, so the copy tab cannot send a transaction through a wallet the
// rest of the page is not looking at.
function provider(){
  return wallet.provider || window.ethereum || null;
}

// Wallets disagree about how to report a chain they do not know: 4902 is the
// documented code, -32603 is what several of them actually send, and some send
// nothing but the message.
const chainUnknown = e => !!e &&
  (e.code === 4902 || e.code === -32603 ||
   /unrecognized chain|unknown chain|not added/i.test(e.message || ""));

// The wallet tab has its own message line, and the dialog sits over that tab,
// so a failure reported only there would be a failure reported nowhere.
function walletError(text){
  msg("w-msg", text, "err");
  if (walletOpen()) toast(text, "err");
}

/* --------------------------------------------------------------- discovery */
// EIP-6963: a wallet that speaks it announces itself, and answers a request for
// announcements, so the list is complete whether this page asked first or the
// wallet arrived late. A wallet that does not speak it is still reachable
// through window.ethereum - the standard adds names and icons to the picker, it
// does not gate the connection.
let wcListening = false;
function discoverWallets(){
  if (wcListening) return;
  wcListening = true;
  addEventListener("eip6963:announceProvider", e => {
    const d = e.detail || {};
    if (!d.info || !d.provider || !d.info.uuid) return;
    if (wallet.announced.some(w => w.info.uuid === d.info.uuid)) return;
    wallet.announced.push({ info: d.info, provider: d.provider });
    // A wallet can arrive while the dialog is open, and one that does should be
    // a row that is there now rather than after a reopen.
    if (walletOpen()) renderWalletModal();
  });
  dispatchEvent(new Event("eip6963:requestProvider"));
}

// An injected wallet old enough not to announce itself usually still admits to
// being one. Only documented flags are read here, and Rabby is checked before
// MetaMask because it sets both - a picker that calls Rabby MetaMask is worse
// than one that calls it "browser wallet".
function injectedName(){
  const p = window.ethereum || {};
  if (p.isRabby) return "Rabby";
  if (p.isMetaMask) return "MetaMask";
  if (p.isCoinbaseWallet) return "Coinbase Wallet";
  if (p.isBraveWallet) return "Brave Wallet";
  if (p.isTrust) return "Trust Wallet";
  if (p.isOKExWallet || p.isOkxWallet) return "OKX Wallet";
  return "browser wallet";
}

// What the picker shows. window.ethereum is offered only when nothing announced
// itself: every EIP-6963 wallet sets it as well, so listing both would show
// MetaMask twice, and the standard is explicit that announcements win.
function walletList(){
  const out = wallet.announced.map(w => ({
    uuid: w.info.uuid,
    name: w.info.name || "wallet",
    sub: w.info.rdns || "",
    icon: w.info.icon || "",
    provider: w.provider
  }));
  if (!out.length && window.ethereum)
    out.push({ uuid: "@injected", name: injectedName(), sub: "window.ethereum",
               icon: "", provider: window.ethereum });
  return out;
}

// The wallet supplied this, so it is data and not markup. It goes into an img,
// which cannot run anything, and it is gated to data:image/ so a wallet cannot
// point the page at a remote host just to learn that the dialog was opened. A
// wallet with no icon gets its own initial: that at least says which row is
// which, where a generic glyph would say "wallet" on every row at once.
function walletIcon(w){
  const src = String(w.icon || "");
  if (/^data:image\//i.test(src)) return '<img alt="" src="' + esc(src) + '">';
  const ch = String(w.name || "").trim().charAt(0).toUpperCase();
  return esc(ch || "?");
}

/* ------------------------------------------------------------ the picker */
function walletOpen(){ return !$("#wmodal").hidden; }

function openWalletModal(){
  if (walletOpen()) return;
  // whatever opened it takes the focus back when it closes
  wallet.opener = document.activeElement;
  $("#wmodal").hidden = false;
  document.body.style.overflow = "hidden";
  renderWalletModal();
  const card = $("#wmodal-card");
  if (card) card.focus();
}

function closeWalletModal(opts){
  opts = opts || {};
  if (!walletOpen()) return;
  const back = wallet.opener;
  wallet.opener = null;
  wallet.backdrop = false;
  $("#wmodal").hidden = true;
  $("#wmodal-body").innerHTML = "";
  document.body.style.overflow = "";
  if (opts.focus === false) return;
  // the widget may have been repainted by a poll since, so fall back to it
  if (back && document.contains(back) && back.focus){ try { back.focus(); } catch(e){} }
  else $("#w-open").focus();
}

// Rendered whole on every change, like the coin card. It is a handful of rows
// with no scroll position worth keeping, so a full repaint is cheaper than the
// bookkeeping that would preserve one.
function renderWalletModal(){
  const body = $("#wmodal-body");
  if (!body) return;
  const rows = walletList();
  const a = wallet.address;
  const now = provider();
  const cid = wallet.chainId != null ? parseInt(wallet.chainId, 16) : null;
  const good = cid === CHAIN_ID;
  const parts = [];

  if (a){
    // The address gets its own line. Forty-two monospace characters do not fit
    // beside an icon, a name and a state label in a dialog this wide, and the
    // alternative - truncating it - hides the one thing the block exists to
    // show. On its own line it fits whole at every width this dialog opens at.
    parts.push('<div class="wme">' +
      '<span class="wico">' +
        esc(String(wallet.label || "?").charAt(0).toUpperCase()) + "</span>" +
      '<span class="wmeta">' +
        '<span class="wname">' + esc(wallet.label || "connected") + "</span>" +
        '<span class="wsub">' + (cid == null || isNaN(cid)
          ? "chain unknown" : "chain " + cid) + "</span>" +
      "</span>" +
      '<span class="wstate' + (good ? " ok" : "") + '">' +
        (good ? "on chain" : "wrong chain") + "</span>" +
      "</div>" +
      '<div class="waddr mono cp" data-copy="' + esc(a) + '" title="' +
        esc(a + " - click to copy") + '">' + esc(a) + "</div>" +
      '<div class="wfoot">' +
        (good ? "" : '<button id="wm-switch">switch chain</button>') +
        '<span class="grow"></span>' +
        '<button class="ghost" id="wm-disconnect">disconnect</button>' +
      "</div>");
  }

  if (rows.length){
    parts.push('<div class="wlist">' + rows.map(w =>
      '<button class="wrow" data-wpick="' + esc(w.uuid) + '"' +
      (w.provider === now ? " disabled" : "") + ">" +
      '<span class="wico">' + walletIcon(w) + "</span>" +
      '<span class="wmeta">' +
        '<span class="wname">' + esc(w.name) + "</span>" +
        (w.sub ? '<span class="wsub">' + esc(w.sub) + "</span>" : "") +
      "</span>" +
      '<span class="wstate">' + (w.provider === now ? "connected" : "connect") +
      "</span></button>").join("") + "</div>");
  } else if (!a){
    parts.push('<div class="hint">no wallet announced itself and window.ethereum is ' +
      "not there. install an extension, or use the phone option below.</div>");
  }

  // WalletConnect last and never hidden. Whether it can run is a property of
  // the server configuration, and a row that quietly disappears makes that look
  // like a bug in the page.
  const pid = wallet.cfg.walletconnect_project_id;
  parts.push('<button class="wrow" data-wpick="@wc"' +
    (pid && !wallet.wcLoading ? "" : " disabled") + ">" +
    '<span class="wico">WC</span>' +
    '<span class="wmeta"><span class="wname">WalletConnect</span>' +
      '<span class="wsub">scan with a phone wallet</span></span>' +
    '<span class="wstate">' +
      (wallet.wcLoading ? "opening" : pid ? "connect" : "no id") +
    "</span></button>");

  // The footer comes last, under the note rather than between it and the row it
  // is about. It is the one other place a wallet is discussed, and it keeps the
  // route to the wallet tab that the status-bar badge used to carry.
  body.innerHTML = parts.join('<div class="wsep"></div>') +
    (pid ? "" : '<div class="hint wnote">WalletConnect needs a project id from ' +
      "cloud.reown.com. set PONS_WALLETCONNECT_ID and restart the server.</div>") +
    (a ? "" : '<div class="wfoot"><span class="grow"></span>' +
      '<button class="ghost" data-goto="wallet">wallet tab</button></div>');
}

/* ------------------------------------------------------------ connecting */

// The one place an EIP-1193 provider becomes this page wallet. Everything
// downstream reads provider(), so nothing else needs to know which kind it was.
async function connectWith(p, label){
  wallet.provider = p;
  wallet.label = label || null;
  try {
    const accs = await p.request({ method: "eth_requestAccounts" });
    if (!accs || !accs.length) throw new Error("the wallet returned no accounts");
    setAddress(accs[0]);
    viewAddr = null;
    initWalletEvents();
    await ensureChain();
    renderWalletChrome();
    refreshWallet();
    toast("connected " + short(accs[0]) + (label ? " with " + label : ""), "ok");
  } catch(e) {
    // A refused connection must not leave the page talking to a provider the
    // user did not choose.
    wallet.provider = null;
    wallet.label = null;
    renderWalletChrome();
    walletError("connect failed: " + errText(e));
  }
}

function pickWallet(uuid){
  if (uuid === "@wc") return connectWalletConnect();
  const w = walletList().filter(x => x.uuid === uuid)[0];
  if (!w) return undefined;
  return connectWith(w.provider, w.name);
}

// Disconnecting means two different things and both happen here. A
// WalletConnect session is a real session on a relay and is closed properly. An
// injected wallet cannot be disconnected by a page at all - the honest move is
// to forget the address locally and say so rather than pretend otherwise.
async function disconnectWallet(){
  const wasWc = wallet.provider && wallet.provider === wallet.wc;
  if (wasWc){
    try { await wallet.wc.disconnect(); } catch(e){ /* already gone */ }
    wallet.wc = null;
  }
  wallet.provider = null;
  wallet.label = null;
  forgetWallet();
  toast(wasWc ? "WalletConnect session closed"
              : "address forgotten locally, the wallet itself is untouched", "ok");
}

/* --------------------------------------------------------- walletconnect */
// The relay build, resolved and pinned, imported on the click rather than at
// boot: most sessions never need it and it is not small. The UMD bundle on the
// same CDN is not self-contained - it reaches for a dozen globals its own
// bundle never sets and dies on the first of them - so the ESM build is the one
// to load.
const WC_CDN = "https://cdn.jsdelivr.net/npm/@walletconnect/ethereum-provider@2.25.0/+esm";

async function connectWalletConnect(){
  const pid = wallet.cfg.walletconnect_project_id;
  if (!pid) return;
  // A session opened earlier in this page load is reused rather than joined by
  // a second one: the relay counts sessions and the phone already approved one.
  if (wallet.wc) return connectWith(wallet.wc, "WalletConnect");
  wallet.wcLoading = true;
  renderWalletModal();
  try {
    const mod = await import(WC_CDN);
    const EthereumProvider = mod.EthereumProvider || mod.default;
    if (!EthereumProvider)
      throw new Error("the relay module did not export a provider");
    const p = await EthereumProvider.init({
      projectId: pid,
      // Optional rather than required. A required namespace is a demand the
      // wallet has to meet before a session exists at all, and no phone wallet
      // has heard of chain 4663 - asked that way, every proposal is
      // unapprovable. Optional lets the session open on whatever chain the
      // wallet prefers, and ensureChain moves it afterwards, adding the chain
      // first if the wallet does not know it either.
      optionalChains: [CHAIN_ID],
      showQrModal: true,
      rpcMap: { [CHAIN_ID]: wallet.cfg.rpc_url },
      metadata: {
        name: "ponssight",
        description: "Robinhood Chain launchpad dashboard",
        url: location.origin,
        icons: []
      }
    });
    // The relay brings its own QR overlay. This dialog gets out of its way
    // rather than stacking a second scrim on top of it.
    p.on("display_uri", () => closeWalletModal({ focus: false }));
    wallet.wc = p;
    await connectWith(p, "WalletConnect");
  } catch(e) {
    walletError("WalletConnect could not start: " + errText(e));
  } finally {
    wallet.wcLoading = false;
    renderWalletModal();
  }
}

/* ----------------------------------------------------------------- chain */
// One implementation for any chain, because the two this app touches are the
// same three calls apart from their constants. `wallet.chainId` is updated
// either way: it is what the header reads, and a header that still says
// Robinhood while the wallet is signing on Arbitrum is a header lying.
async function ensureChainId(chainId, hex, name, rpc){
  const p = provider();
  if (!p || !p.request) return false;
  const params = [{
    chainId: hex,
    chainName: name,
    nativeCurrency: { name: "ETH", symbol: "ETH", decimals: 18 },
    rpcUrls: [rpc]
  }];
  try {
    const id = await p.request({ method: "eth_chainId" });
    wallet.chainId = id;
    if (String(id).toLowerCase() === String(hex).toLowerCase()){
      renderWalletChrome();
      return true;
    }
  } catch(e){ /* fall through to the switch attempt */ }
  try {
    await p.request({ method: "wallet_switchEthereumChain",
                      params: [{ chainId: hex }] });
    wallet.chainId = hex;
    renderWalletChrome();
    return true;
  } catch(e) {
    // 4902 means the wallet has never heard of this chain, so offer to add it.
    // Over WalletConnect that is the normal path rather than the rare one, and
    // for Arbitrum it is the rare one: almost nothing does not know 42161.
    if (chainUnknown(e)){
      try {
        await p.request({ method: "wallet_addEthereumChain", params: params });
        wallet.chainId = hex;
        renderWalletChrome();
        return true;
      } catch(e2) {
        walletError("could not add " + name + ": " + errText(e2));
        return false;
      }
    }
    walletError("chain switch refused: " + errText(e));
    return false;
  }
}

async function ensureChain(){
  return ensureChainId(wallet.cfg.chain_id, wallet.cfg.chain_hex,
                       wallet.cfg.chain_name, wallet.cfg.rpc_url);
}

async function switchChainNow(){
  const ok = await ensureChain();
  if (ok) toast("wallet on " + wallet.cfg.chain_name, "ok");
  renderWalletModal();
}

/* --------------------------------------------------------------- session */

// The server half of the configuration, read once. Failing is survivable:
// everything here has a working default in fmt.js except the project id, and
// the row that needs it says so when it is missing.
async function loadConfig(){
  try {
    const d = await api("/api/config");
    wallet.cfg = {
      chain_id: fin(d.chain_id) != null ? fin(d.chain_id) : CHAIN_ID,
      chain_hex: d.chain_hex || CHAIN_HEX,
      chain_name: d.chain_name || CHAIN_NAME,
      rpc_url: d.rpc_url || RPC_URL,
      // the middle hop of a transfer. Same defaults as fmt.js holds, so a
      // backend that does not answer these still leaves the tab working
      arb_chain_id: fin(d.arb_chain_id) != null ? fin(d.arb_chain_id) : ARB_CHAIN_ID,
      arb_chain_hex: d.arb_chain_hex || ARB_CHAIN_HEX,
      arb_chain_name: d.arb_chain_name || ARB_CHAIN_NAME,
      arb_rpc_url: d.arb_rpc_url || ARB_RPC_URL,
      walletconnect_project_id: d.walletconnect_project_id || ""
    };
    if (walletOpen()) renderWalletModal();
  } catch(e){ /* the constants in fmt.js are the same values */ }
}

function initWallet(){
  discoverWallets();
  let saved = null;
  try { saved = localStorage.getItem("pons.address"); } catch(e){}
  if (saved) wallet.address = saved;
  loadConfig();

  const p = window.ethereum;
  if (!p || !p.request){
    wallet.available = false;
    renderWalletChrome();
    return;
  }
  wallet.available = true;
  wallet.provider = p;
  wallet.label = injectedName();
  // The injected wallet is the default connection when there is one, and
  // eth_accounts is already permission gated: a site that was never approved
  // gets an empty list rather than a connection. A saved address the wallet no
  // longer holds is dropped instead of fetched - it would only produce reads
  // that fail for a reason the page cannot explain.
  p.request({ method: "eth_accounts" }).then(accs => {
    const list = accs || [];
    if (list.length && (!saved ||
        list.map(x => String(x).toLowerCase()).indexOf(String(saved).toLowerCase()) < 0))
      setAddress(list[0]);
    return p.request({ method: "eth_chainId" });
  }).then(id => {
    wallet.chainId = id;
    initWalletEvents();
    renderWalletChrome();
  }).catch(() => { renderWalletChrome(); });
}

// Subscriptions travel with the provider. A page that kept listening to
// window.ethereum after moving to a WalletConnect session would hear about the
// wrong wallet, and the account changes that matter would arrive on a provider
// nobody is listening to.
let walletBound = null;
function initWalletEvents(){
  const p = provider();
  if (!p || !p.on || p === walletBound) return;
  if (walletBound && walletBound.removeListener){
    try {
      walletBound.removeListener("accountsChanged", onAccountsChanged);
      walletBound.removeListener("chainChanged", onChainChanged);
      walletBound.removeListener("disconnect", onWalletGone);
    } catch(e){ /* a provider that has on() but not removeListener */ }
  }
  walletBound = p;
  p.on("accountsChanged", onAccountsChanged);
  p.on("chainChanged", onChainChanged);
  p.on("disconnect", onWalletGone);
}

function onAccountsChanged(accs){
  const list = accs || [];
  if (!list.length){
    forgetWallet();
    toast("wallet disconnected", "err");
    return;
  }
  if (!wallet.address || String(list[0]).toLowerCase() !== wallet.address.toLowerCase()){
    setAddress(list[0]);
    viewAddr = null;
    toast("account changed to " + short(list[0]));
  }
  renderWalletChrome();
  if (tab === "wallet") refreshWallet();
}

function onChainChanged(id){
  wallet.chainId = id;
  renderWalletChrome();
  const cid = parseInt(id, 16);
  toast(cid === CHAIN_ID ? "wallet on " + wallet.cfg.chain_name
                         : "wallet moved to chain " + (isNaN(cid) ? "?" : cid),
        cid === CHAIN_ID ? "ok" : "err");
}

// The session ended at the other end: the phone closed it, the relay timed it
// out, the extension was locked. Only the WalletConnect half ever fires this.
function onWalletGone(){
  if (wallet.provider && wallet.provider === wallet.wc) wallet.wc = null;
  wallet.provider = null;
  wallet.label = null;
  forgetWallet();
  toast("wallet disconnected", "err");
}

/* ------------------------------------------------------------ the dialog */
// Bound once on the container, so the rows a repaint replaces do not each need
// a listener of their own.
function walletModalClick(e){
  const t = e.target;
  if (!t || !t.closest || !walletOpen()) return;
  if (t.closest("#wmodal-x")){ closeWalletModal(); return; }
  const pick = t.closest("[data-wpick]");
  if (pick && !pick.disabled){ pickWallet(pick.dataset.wpick); return; }
  if (t.closest("#wm-switch")){ switchChainNow(); return; }
  if (t.closest("#wm-disconnect")){ disconnectWallet(); return; }
  // The wallet tab is behind this dialog, so the click that opens it closes
  // this first. The document-level handler does the switching itself, which is
  // why this branch only gets out of the way.
  if (t.closest("[data-goto]")){ closeWalletModal(); return; }
  // the scrim, and only when the press started on it, so a drag that began
  // inside the dialog and ended outside does not read as a dismissal
  if (wallet.backdrop && t === $("#wmodal")) closeWalletModal();
}

// The dialog is modal, so tab stays inside it and escape always gets out. The
// index shortcut handler returns early while it is open, so the two never both
// act on one key.
function walletKeydown(e){
  if (!walletOpen()) return;
  if (e.key === "Escape" || e.key === "Esc"){
    e.preventDefault();
    closeWalletModal();
    return;
  }
  if (e.key !== "Tab") return;
  const card = $("#wmodal-card");
  const f = $$("#wmodal-card button:not([disabled]),#wmodal-card a[href]," +
    "#wmodal-card [tabindex]:not([tabindex='-1'])")
    .filter(el => el.offsetParent !== null);
  if (!f.length){ e.preventDefault(); card.focus(); return; }
  const first = f[0], last = f[f.length - 1], here = document.activeElement;
  const inside = card.contains(here);
  if (e.shiftKey && (here === first || !inside)){ e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && (here === last || !inside)){
    e.preventDefault(); first.focus();
  }
}

/* ---------------------------------------------------------------- coin card */
// One card, opened by clicking a coin in any table. The verdict, the chart and
// the holder list all arrive in a single call, so opening the card is one
// request and the range selector refetches exactly that call.
