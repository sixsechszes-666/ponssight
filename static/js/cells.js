function logoSrc(t){
  if (!t || !t.logo) return null;
  if (String(t.logo).startsWith("data:")) return t.logo;
  return "/img?u=" + encodeURIComponent(t.logo);
}
// no inline handlers anywhere: image errors are caught in the capture phase and
// swapped for the symbol placeholder, so a hostile logo uri cannot inject js
function logoCell(t){
  const src = logoSrc(t);
  const ch = (t && t.symbol ? String(t.symbol) : "?")[0] || "?";
  if (!src) return '<div class="logo ph">' + esc(ch) + "</div>";
  return '<img class="logo" src="' + esc(src) + '" alt="" loading="lazy" ' +
         'decoding="async" data-ch="' + esc(ch) + '">';
}
document.addEventListener("error", e => {
  const img = e.target;
  if (!img || img.tagName !== "IMG" || !img.classList || !img.classList.contains("logo")) return;
  const d = document.createElement("div");
  d.className = "logo ph";
  d.textContent = img.dataset.ch || "?";
  img.replaceWith(d);
}, true);

function tokenCell(t, page){
  const addr = t.address || "";
  const name = esc(t.name || "?");
  // the name is the coin card's opener, so clicking a coin in any table opens
  // the card. The launchpad anchor the name used to hold lives in the card's
  // own links row now, and page stays as the fallback for a row that has a link
  // but no address to open a card for
  const nm = addr
    ? '<button type="button" class="nmbtn" data-coin="' + esc(addr) +
      '" title="open the coin card">' + name + "</button>"
    : (page
      ? '<a href="' + esc(page) + '" target="_blank" rel="noopener" title="open on ponsfamily">' + name + "</a>"
      : name);
  return '<div class="tok">' + logoCell(t) +
    "<div><div class=\"nm\">" + nm + "</div>" +
    '<div class="sym"><b>' + esc(t.symbol || "?") + "</b> " +
    '<span class="cp" data-copy="' + esc(addr) + '" title="click to copy">' +
    esc(short(addr)) + "</span></div></div></div>";
}
function ageCell(sec){
  const s = fin(sec);
  const hot = s != null && s < 60, fresh = s != null && s < 300;
  return '<span class="age ' + (hot ? "fresh" : fresh ? "hot" : "") + '">' +
         esc(fmtAge(s)) + "</span>";
}
function barCell(pct, grad){
  if (grad) return '<span class="pill g">graduated</span>';
  // numOr(null, 0) used to turn "no reserve row indexed yet" into a
  // confident 0.00%, which is indistinguishable from a curve nobody has
  // bought into. Everywhere else in this file an unknown is a dash.
  if (fin(pct) == null) return '<span class="mono dim">' + DIMDASH + "</span>";
  const p = Math.max(0, Math.min(100, numOr(pct, 0)));
  return '<div class="pct">' + esc(p.toFixed(2)) + '%</div>' +
         '<div class="bar"><i style="width:' + p.toFixed(2) + '%"></i></div>';
}
function pairPill(t){
  const s = t.quote_symbol || "?";
  return '<span class="pill ' + (t.quote_symbol === "ETH" ? "" : "r") + '">' + esc(s) + "</span>";
}
// extra goes inside the links row, so a caller with more room than a table
// cell (the coin card) can add a link the cell has no space for
// These strings come from the token contract, which means whoever launched
// the token chose them. esc() stops an attribute breakout but says nothing
// about the scheme, so a website of "javascript:..." became a link that runs
// in the origin holding window.ethereum. Only http and https get an href; the
// rest of the field, including the many that are prose rather than a url,
// renders as plain dim text instead of a link to nowhere.
function safeUrl(u){
  const s = String(u == null ? "" : u).trim();
  return /^https?:\/\//i.test(s) ? s : "";
}
function socialLink(u, label, title){
  const safe = safeUrl(u);
  if (!safe) return u ? '<span class="lnk-x" title="' + esc(title) +
    ' is not a link: ' + esc(String(u).slice(0, 80)) + '">' + label + "</span>" : "";
  return '<a class="soc" href="' + esc(safe) + '" target="_blank" ' +
    'rel="noopener noreferrer" title="' + esc(title) + '">' + label + "</a>";
}
// `dup` adds the one-click duplicate beside the copy chip. Only the launches
// table asks for it: that is the table a token gets picked off, and the card
// and the other five tables already have the copy chip to draft from.
function socialsCell(t, extra, dup){
  const l = [
    socialLink(t.twitter, "X", "X"),
    socialLink(t.telegram, "TG", "Telegram"),
    socialLink(t.website, "WEB", "Website"),
    socialLink(t.discord, "DC", "Discord"),
    socialLink(t.farcaster, "FC", "Farcaster"),
  ].filter(Boolean).join("");
  return '<div class="links">' + (l || DIMDASH) + copyBtn(t.address) +
         (dup ? blinkBtn(t.address) : "") + (extra || "") + "</div>";
}
function copyBtn(addr){
  if (!addr) return "";
  return '<button class="ghost" data-copytok="' + esc(addr) +
         '" title="draft a copy of this token on the Copy tab">copy</button>';
}
// BLINK COPY: the same duplicate the chip beside it drafts, with the form taken
// out of it. One click from a table row reads the source token, fills the
// launch fields from it and sends the transaction - the page does not move and
// the confirmation is the wallet's own.
//
// It signs on the first click, and that is deliberate rather than overlooked. A
// row in a table of two hundred is something the pointer crosses on the way to
// the next one, so a chip that spends the launch fee the moment it is touched
// is a chip nobody can keep in the table - but the fee is one launch fee, the
// wallet prints the fee, the value and the recipient before it signs, and a
// second click here would be a confirmation step this button exists to not
// have. The chip beside it is the one that opens the form.
function blinkBtn(addr){
  if (!addr) return "";
  return '<button class="ghost" data-blink="' + esc(addr) +
    '" title="duplicate this token now: the fields are filled from it and the ' +
    'transaction goes straight to your wallet">BLINK COPY</button>';
}
// What the token has earned the wallet that launched it, in the pair
// token's own units - so it is the same asset as the volume beside it and
// needs no dollar rate to be true. The tooltip carries the rate it was
// launched at, which is the difference between a token that has earned
// nothing and one that was never going to: a launch with no creator tax
// can trade all day and pay its author nothing at all.
function feesCell(t){
  const v = fin(t.creator_fees);
  if (v == null) return DIMDASH;
  const sym = t.quote_symbol || "";
  const bps = fin(t.creator_tax_bps);
  const title = "creator fees " + fmtFees(v, sym) +
    (bps == null ? "" : " at a " + bpsPct(bps) + " creator tax") +
    ". every sweep the curve has run, plus what it is holding for the " +
    "creator right now.";
  return '<span class="mono" title="' + esc(title) + '">' +
    esc(fmtFees(v, sym)) + "</span>";
}

/* ---------------------------------------------------------------- venues */
// DeBank files a chain under a slug and Axiom under a key, and both of these
// were read off the two sites rather than guessed. Neither is optional:
// without the slug DeBank opens the address on whichever network it defaults
// to, and Axiom reads an EVM address it was not told about as BNB Chain, then
// reports the market as unavailable - which looks like a dead token instead of
// a wrong link.
const DEBANK_CHAIN = "hood";       // debank.com/chain/list: "Robinhood"
const AXIOM_CHAIN = "robinhood";   // axiom.trade: ?chain=robinhood

function debankUrl(a){
  return a ? "https://debank.com/profile/" + a + "?chain=" + DEBANK_CHAIN : "";
}
function axiomUrl(a){
  return a ? "https://axiom.trade/token/" + a + "?chain=" + AXIOM_CHAIN : "";
}
// A way out of the page, drawn as a chip rather than as a link. The names to
// its left are facts about the token; these are actions that leave, and the
// eye should be able to tell the two apart without reading either of them.
function venueChip(href, label, title){
  if (!href) return "";
  return '<a class="venue" href="' + esc(href) + '" target="_blank" ' +
    'rel="noopener noreferrer" title="' + esc(title) + '">' + label + "</a>";
}
// The creator address is not always known - a token indexed out of a trade log
// with no launch record has none - so the DeBank chip is built from whatever
// the row carries and drops out on its own when there is nothing to point at.
function venuesCell(t, addr){
  t = t || {};
  const out = [
    venueChip(addr ? "https://www.ponsfamily.com/launchpad/" + addr : "",
              "pons", "this token on ponsfamily"),
    venueChip(debankUrl(t.deployer), "debank",
              "creator " + (t.deployer || "") + " on DeBank"),
    venueChip(axiomUrl(addr), "axiom", "trade this token on Axiom"),
  ].filter(Boolean);
  return out.length ? '<span class="venues">' + out.join("") + "</span>" : "";
}
function snipeTitle(s){
  const p = ["snipe " + (s.label || "unknown")];
  if (fin(s.score) != null) p.push("score " + fmtInt(s.score));
  if (fin(s.delta) != null) p.push("delta " + fmtInt(s.delta) + " blocks");
  if (s.bundled) p.push("atomic bundle, same tx as the launch");
  if (s.first_buyer) p.push("first buyer " + s.first_buyer);
  if (fin(s.first_buy_quote) != null) p.push("first buy " + fmtQuoteK(s.first_buy_quote, ""));
  if (fin(s.first_buy_share) != null) p.push("share of threshold " + fmtPct(s.first_buy_share, 1));
  if (fin(s.early_buyers) != null) p.push("early buyers " + fmtInt(s.early_buyers));
  if (fin(s.bot_hits) != null) p.push("bot hits " + fmtInt(s.bot_hits));
  if (s.bot) p.push("known repeat first buyer");
  return p.join(" | ");
}
function snipePill(s){
  if (!s) return DIMDASH;
  const lb = LABELS.concat(["unknown"]).indexOf(s.label) >= 0 ? s.label : "unknown";
  return '<span class="lb ' + esc(lb) + '" title="' + esc(snipeTitle(s)) + '">' + esc(lb) + "</span>";
}
// /api/volume and /api/wallet only carry the flat snipe_label and snipe_score,
// /api/tokens and /api/snipes carry the whole object
function snipeOf(t){
  if (!t) return null;
  if (t.snipe) return t.snipe;
  if (t.snipe_label) return { label: t.snipe_label, score: t.snipe_score };
  return null;
}
// rows that were taken in the launch block or right after it stay marked, so a
// scrolled list still shows which ones were sniped
// The label as an attribute rather than as part of the class. A row is
// tinted from this, because the class cannot say which label it is: "hi" is
// set for a bundled launch and for a snipe by a known repeat buyer alike, so
// keying the colour off it painted a red row under an amber "sniped" pill.
function snipeData(s){
  if (!s || !s.label) return "";
  const lb = LABELS.concat(["unknown"]).indexOf(s.label) >= 0
    ? s.label : "unknown";
  return ' data-lb="' + esc(lb) + '"';
}

function snipeRowClass(s){
  if (!s) return "";
  if (s.label === "bundled") return "snip hi";
  if (s.label === "sniped") return s.bot ? "snip hi" : "snip";
  return "";
}
// /api/tokens nests the trade numbers under vol, /api/volume and /api/snipes
// carry the same numbers at the top level of the row
function volOf(t){
  if (!t) return null;
  if (t.vol) return t.vol;
  if (t.volume_usd != null || t.volume_quote != null || t.buys != null)
    return { buys: t.buys, sells: t.sells, volume_quote: t.volume_quote,
             volume_usd: t.volume_usd, net_quote: t.net_quote, buyers: t.buyers,
             last_trade_ts: t.last_trade_ts };
  return null;
}
// Was the wallet that launched this token its only buyer?
//
// Exactly one buyer, and the first trade on the token was that same wallet's.
// `buyers` counts distinct buyers off trade_buyers, which is written from the
// buy logs and nothing else, so a 1 there is a fact about the chain and not
// an inference.
//
// It is deliberately `=== 1` and not `<= 1`. A zero means no buy was ever
// logged, and `deployer_first` is true for 69 of those: trades.first_buy_* is
// filled from the first logged trade of any side, so a deployer who only sold
// lands there looking like a deployer who bought. Those tokens are "nobody
// bought", which is not what this filter claims to hide.
function devOnly(t){
  const v = volOf(t), s = snipeOf(t);
  if (!v || !s) return false;
  return fin(v.buyers) === 1 && s.deployer_first === true;
}

function volCell(v, sym){
  if (!v) return DIMDASH;
  const main = fin(v.volume_usd) != null ? fmtUsd(v.volume_usd) : fmtQuoteK(v.volume_quote, sym);
  const title = "volume " + fmtQuoteK(v.volume_quote, sym) +
    " | buys " + fmtInt(v.buys) + " | sells " + fmtInt(v.sells) +
    " | net " + fmtQuoteK(v.net_quote, sym) + " | buyers " + fmtInt(v.buyers);
  return '<span class="mono" title="' + esc(title) + '">' + esc(main) + "</span>";
}
// the two things a per-token profit can be, in words. The tooltip is passed in
// because why a figure is a floor is not the same in every table.
const T_PNL_QUOTE = "realised plus unrealised, in this token's own quote asset";
const T_PNL_FLOOR = "a floor, not a total: the curve this position is priced " +
  "against is gone, so only the realised half of it is knowable";
// a profit has to read as profit or as loss at a glance, and a figure the
// backend could only half compute has to say so rather than look like a total.
// The sign, the colour and the unit live here alone, so a loss can never read
// as a profit and no two figures disagree about how to look. An empty sym means
// the figure is already dollars; otherwise it is that token's own quote asset.
function pnlSpan(v, sym, floor, title){
  const n = fin(v);
  if (n == null) return DIMDASH;
  const cls = n > 0 ? "pos" : n < 0 ? "neg" : "dim";
  return '<span class="mono ' + cls + '" title="' + esc(title == null ? "" : title) + '">' +
    (floor ? "&ge; " : "") +
    esc(sym ? (n > 0 ? "+" : "") + fmtQuoteK(n, sym) : fmtUsdSigned(n)) + "</span>";
}
function roiSpan(v){
  const n = fin(v);
  if (n == null) return DIMDASH;
  const cls = n > 0 ? "pos" : n < 0 ? "neg" : "dim";
  return '<span class="mono ' + cls + '">' +
    esc((n > 0 ? "+" : "") + fmtPct(n, 1)) + "</span>";
}
function lastAgeCell(t, now){
  const direct = fin(t.last_trade_age);
  const a = direct != null ? direct
    : (fin(t.last_trade_ts) != null && now ? now - fin(t.last_trade_ts) : null);
  return ageCell(a);
}

/* --------------------------------------------------------------- the shell */
