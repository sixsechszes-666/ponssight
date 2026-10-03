function copyText(txt){
  const t = String(txt == null ? "" : txt);
  if (navigator.clipboard && navigator.clipboard.writeText)
    return navigator.clipboard.writeText(t);
  // the clipboard api needs a secure context, keep a way out for plain http
  return new Promise((res, rej) => {
    try {
      const ta = document.createElement("textarea");
      ta.value = t; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      document.execCommand("copy"); ta.remove(); res();
    } catch(e){ rej(e); }
  });
}

let toastT = null;
// `sticky` keeps it up until something replaces it. A toast that hides itself
// after 2.8s is right for a result, which has been read by then, and wrong for
// a wait, which has not finished - a click that goes quiet halfway through
// reads exactly like a click that did nothing.
function toast(text, kind, sticky){
  const el = $("#toast");
  el.textContent = text;
  el.className = kind || "";
  el.style.display = "block";
  clearTimeout(toastT);
  if (!sticky) toastT = setTimeout(() => { el.style.display = "none"; }, 2800);
}
function msg(id, text, kind){
  const el = $("#" + id);
  if (!el) return;
  if (!text){ el.innerHTML = ""; return; }
  const cls = kind === "err" ? "dangerbox" : kind === "ok" ? "okbox" : "hint";
  el.innerHTML = '<div class="' + cls + '">' + esc(text) + "</div>";
}
function setEmpty(id, text, isErr){
  const el = $("#" + id);
  if (!el) return;
  if (!text){ el.hidden = true; el.textContent = ""; return; }
  el.hidden = false;
  el.className = "empty" + (isErr ? " err" : "");
  el.textContent = text;
}
function live(ok){
  const el = $("#b-live");
  el.textContent = ok ? "live" : "offline";
  // Toggled as classes rather than an inline colour: the badge's own modifier
  // is what controls.css styles, and an inline colour set from here would win
  // over it and then stay set after the feed came back.
  el.classList.toggle("ok", !!ok);
  el.classList.toggle("bad", !ok);
}
function note(selector, text){ const el = $(selector); if (el) el.textContent = text; }

// every request goes through here so a dead backend shows a line of text
// instead of an empty panel
// A request that never settles used to freeze a tab for good, because the
// busy flag it set only cleared in a finally that never ran. Fifteen seconds is
// far longer than any endpoint here needs and far shorter than forever.
const API_TIMEOUT_MS = 15000;
async function api(path, opts){
  const own = AbortSignal.timeout(API_TIMEOUT_MS);
  const theirs = opts && opts.signal;
  const signal = theirs && typeof AbortSignal.any === "function"
    ? AbortSignal.any([theirs, own]) : (theirs || own);
  const r = await fetch(path, Object.assign({}, opts, {signal: signal}));
  if (!r.ok){
    // The backend's own sentence is the useful half of a refusal - "that is not
    // a private key" says what to do and the status code does not - so it is
    // read off the body when there is one. It used to be dropped here, which
    // left every failed POST on the page reading as HTTP 400 and a path.
    let why = "";
    try {
      const body = await r.json();
      why = body && body.detail ? ": " + body.detail : "";
    } catch(e){ /* not a json body, or already consumed */ }
    const err = new Error("HTTP " + r.status + " " + String(path).split("?")[0] + why);
    // The code as well as the sentence, for the one caller that has to tell one
    // refusal from another: 423 means the vault is locked, and the answer to
    // that is a passphrase rather than a retry.
    err.status = r.status;
    throw err;
  }
  return r.json();
}

// Latest wins. The old guard was backwards: it dropped the newest request and
// let the stale one finish, so changing a filter while /api/snipes was in
// flight - nine seconds against a three second poll - simply did nothing, with
// no request sent and nothing on screen to say so. Each caller takes a ticket,
// the previous request is aborted, and only the holder of the current ticket
// may touch the DOM.
const apiGen = {};

// `poll` marks a call that came from the interval timer rather than from the
// user. Those are dropped while a request is still in flight; anything else
// supersedes it, which is what the guard is for.
function latest(key, poll){
  const g = apiGen[key] || (apiGen[key] = {n: 0, ac: null, at: 0});
  // The window is the request timeout, so this cannot wedge: a request that
  // died without settling is older than the timeout by definition and the
  // next tick starts a fresh one.
  if (poll && g.ac && Date.now() - g.at < API_TIMEOUT_MS) return null;
  g.n += 1;
  if (g.ac) g.ac.abort();
  g.ac = new AbortController();
  g.at = Date.now();
  const mine = g.n;
  return {signal: g.ac.signal, stale: function(){ return mine !== g.n; }};
}
const jpost = (path, body, method) => api(path, {
  method: method || "POST",
  headers: {"content-type": "application/json"},
  body: JSON.stringify(body || {})
});
// `params` defaults because "no query string" is the common case and the
// alternative is a caller that reads as if it passes one. Every walk button on
// the sniper tab was written as `api(qs(url), {}, "POST")` - where the `{}` and
// the method belong to `api`, not to `qs` - and all six of them threw
// `Object.keys(undefined)` inside their own click handler, before a request was
// ever made. The button flipped to "reading..." and stayed there, because the
// handler sets `busy` and disables itself before the throw, so the failure
// looked exactly like a walk that was running and finding nothing.
function qs(base, params = {}){
  const p = new URLSearchParams();
  Object.keys(params).forEach(k => {
    const v = params[k];
    if (v !== "" && v != null) p.set(k, v);
  });
  return base + "?" + p.toString();
}

/* ------------------------------------------------------------ shared cells */
