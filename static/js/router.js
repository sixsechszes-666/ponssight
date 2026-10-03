const TABS = ["launches", "volume", "sniped", "snipers", "handles",
              "copy", "create", "wallet", "wallets", "sniper"];
// copy, create and wallet mount their own controls, so they never poll on a
// timer. handles is a lookup rather than a feed, so it has nothing to re-read.
// wallets only moves money when a button is pressed, for the same reason.
// sniper is one wallet's own launches: they do not change while the page is
// open, and re-reading a thousand rows every two seconds to be told so is the
// one thing this tab must not do.
const POLLING = {launches: 1, volume: 1, sniped: 1, snipers: 1};

let tab = "launches";
let timer = null;
let lastStatsAt = 0;

function readHash(){
  const parts = String(location.hash || "").replace(/^#/, "").split("/");
  // #<tab>/coin/<address> is the coin card sitting on top of that tab, never a
  // tab of its own, so the coin part is taken off before the tab's own argument
  // is read and everything else keeps working exactly as it did
  const isCoin = parts[1] === "coin" && !!parts[2];
  return {
    name: TABS.indexOf(parts[0]) >= 0 ? parts[0] : "launches",
    arg: (isCoin ? [] : parts.slice(1)).join("/") || null,
    coin: isCoin ? parts[2] : null
  };
}
// A tab or a filter is a view of the same page, so it replaces the entry and
// leaves the back button pointing wherever the person came from. Opening a coin
// card is the one move that reads as going somewhere, so it pushes - which is
// what the hashchange handler downstream already assumed, and without it back
// left the app entirely instead of closing the card.
function setHash(h, push){
  if (location.hash === h) return;
  try {
    if (push) history.pushState(null, "", h);
    else history.replaceState(null, "", h);
  } catch(e){ location.hash = h; }
}
function stopTimer(){ if (timer){ clearInterval(timer); timer = null; } }
function startTimer(fn){
  stopTimer();
  const ms = numOr($("#every").value, 0);
  // The flag tells the refresh that this tick is a poll, so it stands down
  // rather than cancelling a request it would only have to repeat.
  if (ms > 0) timer = setInterval(() => { fn(true); maybeStats(false); }, ms);
}
function maybeStats(force){
  const now = Date.now();
  if (!force && now - lastStatsAt < 4500) return;
  lastStatsAt = now;
  loadStats();
}

function setTab(name, arg, opts){
  opts = opts || {};
  if (TABS.indexOf(name) < 0) name = "launches";
  // moving to a tab is leaving the card, and the hash below replaces the card's
  // own route. A redraw that passes hash:false is not a move
  if (opts.hash !== false && coinOpen()) closeCoinCard({ hash: false, focus: false });
  tab = name;
  $$("section[data-panel]").forEach(s => { s.hidden = s.dataset.panel !== name; });
  // aria-selected is what a screen reader reads off a role="tab"; the class
  // is only what the eye reads, and the two were allowed to disagree
  $$(".tab").forEach(b => {
    const on = b.dataset.tab === name;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
  });
  $("#poll-ctl").hidden = !POLLING[name];
  if (opts.hash !== false) setHash("#" + name + (arg ? "/" + arg : ""));
  stopTimer();
  // Copy and Create share one form node, so whichever of them is holding it
  // has to park its values before the next tab is mounted.
  formStash();
  if (name === "launches") mountLaunches();
  else if (name === "volume") mountVolume();
  else if (name === "sniped") mountSniped();
  else if (name === "snipers") mountSnipers(arg);
  else if (name === "handles") mountHandles(arg);
  else if (name === "copy") mountCopy(arg);
  else if (name === "create") mountCreate();
  else if (name === "wallet") mountWallet();
  else if (name === "wallets") mountWallets();
  else if (name === "sniper") mountSniper(arg);
}

async function loadStats(){
  try {
    const s = await api("/api/stats");
    $("#b-block").textContent = s.latest_block ? Number(s.latest_block).toLocaleString() : "-";
    // Grouped, like the block height on the line above. These are counts in
    // the tens of thousands and they read as one long digit otherwise.
    $("#b-1h").textContent = fmtCount(s.last_1h);
    $("#b-24h").textContent = fmtCount(s.last_24h);
    $("#b-total").textContent = fmtCount(s.tokens);
    if (fin(s.eth_usd) != null) $("#b-eth").textContent = "$" + Math.round(fin(s.eth_usd));
    $("#foot").textContent = nz(s.tokens) + " indexed / " + nz(s.enriched) + " enriched / " +
      nz(s.pending) + " pending / " + nz(s.graduated) + " graduated / chain " +
      nz(s.chain_id) + " / " + nz(s.factory);
  } catch(e) {
    // header badges are decoration, the panels carry their own error lines
  }
}

/* --------------------------------------------------------------- launches */
