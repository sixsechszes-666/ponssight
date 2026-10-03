/* ------------------------------------------------------------------ utils */
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

const NATIVE = "0x0000000000000000000000000000000000000000";
const CHAIN_ID = 4663;
const CHAIN_HEX = "0x1237";
// The chain as a wallet has to be told about it: the name and the public RPC
// that go into the EIP-3085 add-chain parameters. They used to be typed inline
// in the one place that needed them; three places need them now - the switch,
// the add, and the WalletConnect session that has to be told which network it
// is being asked for.
const CHAIN_NAME = "Robinhood Chain";
const RPC_URL = "https://rpc.mainnet.chain.robinhood.com";
// The middle hop of a transfer-all, and the same four values in the same roles.
// The server hands these over in /api/config and these are the fallbacks for a
// backend that did not - a second literal here rather than a second source of
// truth, which is why they are only ever read when that call failed.
const ARB_CHAIN_ID = 42161;
const ARB_CHAIN_HEX = "0xa4b1";
const ARB_CHAIN_NAME = "Arbitrum One";
const ARB_RPC_URL = "https://arb1.arbitrum.io/rpc";
// 0, because that is the only initial buy this launch path can carry: the
// builder adds it to msg.value and the factory takes exactly the fee.
const DEFAULT_BUY = 0;
// 2%, which is the going rate on this launchpad. It is a starting point, not
// a rule - the field is editable and this is the value it opens on.
const DEFAULT_TAX_BPS = 200;
// 5%, which is what the launchpad's own router launches carry: their floor
// lands on 0.95 and 0.98 of the quoted fill, and 0.95 is the conservative end
// of that. Editable, and only what the field opens on.
const DEFAULT_SLIPPAGE_BPS = 500;
const DIMDASH = '<span class="dim2 mono">-</span>';
const LABELS = ["bundled", "sniped", "early", "slow", "none"];

// The colours for these labels live in tokens.css, as --lb-* , and reach the
// page through the data-lb attribute. They used to be a hex map here, which
// put the palette in two languages: the pills read the stylesheet and the
// legend read this, so the legend kept the old sheet's pink for a release
// after everything else had been repainted.

// every number that reaches the DOM goes through one of these: a raw float or a
// NaN in a table cell is worse than a dash
const fin = v => {
  if (typeof v === "number") return isFinite(v) ? v : null;
  if (v == null || v === "") return null;
  const n = parseFloat(v);
  return isFinite(n) ? n : null;
};
const fmtAge = s => {
  if (s == null) return "-";
  if (s < 0) return "0s";
  if (s < 60) return Math.floor(s) + "s";
  if (s < 3600) return Math.floor(s/60) + "m " + Math.floor(s%60) + "s";
  if (s < 86400) return Math.floor(s/3600) + "h " + Math.floor(s%3600/60) + "m";
  return Math.floor(s/86400) + "d " + Math.floor(s%86400/3600) + "h";
};
const fmtUsd = v => {
  const n = fin(v);
  if (n == null) return "-";
  const a = Math.abs(n);
  if (a >= 1e9) return "$" + (n/1e9).toFixed(2) + "B";
  if (a >= 1e6) return "$" + (n/1e6).toFixed(2) + "M";
  if (a >= 1e3) return "$" + (n/1e3).toFixed(1) + "k";
  if (a >= 1)   return "$" + n.toFixed(2);
  if (a === 0)  return "$0";
  return "$" + n.toPrecision(3);
};
const fmtQuote = (v, sym) => {
  const n = fin(v);
  if (n == null) return "-";
  const out = n >= 1000 ? n.toFixed(0) : n >= 1 ? n.toFixed(2)
            : n > 0 ? n.toPrecision(4) : "0";
  const s = (sym || "").trim();
  return s ? out + " " + s : out;
};
// volumes span many orders of magnitude, so they get the compact treatment
const fmtQuoteK = (v, sym) => {
  const n = fin(v);
  if (n == null) return "-";
  const s = (sym || "").trim();
  const a = Math.abs(n);
  let out;
  if (a >= 1e9) out = (n/1e9).toFixed(2) + "B";
  else if (a >= 1e6) out = (n/1e6).toFixed(2) + "M";
  else if (a >= 1e3) out = (n/1e3).toFixed(2) + "k";
  else if (a >= 1) out = n.toFixed(2);
  else if (a > 0) out = n.toPrecision(4);
  else out = "0";
  return s ? out + " " + s : out;
};
const fmtBig = v => {   // supplies and reserve counts run into 1e27
  const n = fin(v);
  if (n == null) return "-";
  const a = Math.abs(n);
  if (a >= 1e15) return n.toExponential(3).replace("e+", "e");
  if (a >= 1e9) return (n/1e9).toFixed(2) + "B";
  if (a >= 1e6) return (n/1e6).toFixed(2) + "M";
  if (a >= 1e3) return (n/1e3).toFixed(2) + "k";
  return n.toFixed(a < 1 ? 4 : 2);
};
// What a token has paid the wallet that launched it, in that token's own
// pair asset. Small next to a market cap and read at six places rather
// than two: a curve that has paid its creator a hundredth of an ETH is a
// different thing from one that has paid nothing, and at two places both
// of them say 0.01 and 0.00. Trailing zeros come off, so a round number
// does not wear four of them.
const fmtFees = (v, sym) => {
  const n = fin(v);
  if (n == null) return "-";
  const s = (sym || "").trim();
  const a = Math.abs(n);
  let out;
  if (n === 0) out = "0";
  else if (a >= 1e6) out = (n/1e6).toFixed(2) + "M";
  else if (a >= 1e3) out = n.toFixed(2);
  else if (a >= 1e-6)
    out = n.toFixed(6).replace(/0+$/, "").replace(/[.]$/, "");
  else out = n.toExponential(2).replace("e+", "e");
  return s ? out + " " + s : out;
};
const fmtEth = v => { const n = fin(v); return n == null ? "-" : n.toFixed(4) + " ETH"; };
const fmtInt = v => { const n = fin(v); return n == null ? "-" : Math.round(n).toLocaleString("en-US"); };
// a signed dollar figure puts its sign in front of the $, the way a person
// writes it, rather than leaving fmtUsd's "$-558.19" to be read twice
const fmtUsdSigned = v => {
  const n = fin(v);
  if (n == null) return "-";
  return (n > 0 ? "+" : n < 0 ? "-" : "") + fmtUsd(Math.abs(n));
};
const fmtPct = (v, d) => { const n = fin(v); return n == null ? "-" : n.toFixed(d == null ? 2 : d) + "%"; };
const nz = v => (v == null || v === "" ? "-" : v);
// Basis points are hundredths of a percent. 100 bps is the going curve fee and
// 200 is the going creator tax, so the two figures a person actually types
// into this page are 1% and 2% - a factor of a hundred away from what the
// fields read as without this.
const bpsPct = v => {
  const n = fin(v);
  if (n == null) return "-";
  return (n / 100).toFixed(n % 100 ? 2 : 0) + "%";
};
// A count, grouped. Not fmtInt: that rounds, which is right for a share count
// and wrong for a headline figure that has to read exactly as it was counted.
const fmtCount = v => {
  const n = fin(v);
  return n == null ? "-" : Math.round(n).toLocaleString("en-US");
};
const numOr = (v, d) => { const n = fin(v); return n == null ? d : n; };
const intOr = (v, d) => { const n = fin(v); return n == null ? d : Math.round(n); };
const short = a => a ? String(a).slice(0,4) + "…" + String(a).slice(-4) : "-";
const shortHash = h => { const s = String(h || ""); return s.length > 20 ? s.slice(0,12) + "…" + s.slice(-6) : (s || "-"); };
// the coin card's crosshair and its early-buy list name a clock time, which is
// not the same thing as the ages every table here shows
const fmtClock = ts => {
  const n = fin(ts);
  if (n == null) return "-";
  const d = new Date(n * 1000);
  if (!isFinite(d.getTime())) return "-";
  return d.toLocaleTimeString("en-GB", {hour12: false}) + " " +
    (d.getMonth() + 1) + "/" + d.getDate();
};
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const errText = e => (e && e.message) ? e.message : String(e);
const debounce = (fn, ms) => { let t; return function(){ clearTimeout(t); t = setTimeout(fn, ms); }; };
const weiEth = v => {
  if (v == null) return null;
  try { return Number(BigInt(v)) / 1e18; } catch(e){ return fin(v); }
};
const toHexWei = v => {
  try { return "0x" + BigInt(typeof v === "number" ? Math.round(v) : String(v).trim()).toString(16); }
  catch(e){ return null; }
};

