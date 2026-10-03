/* Screenshot the dashboard through the DevTools protocol.

   `chrome --screenshot` with `--virtual-time-budget` cannot be used on this
   page. Virtual time advances the timer queue as fast as it can, so the
   `AbortSignal.timeout` every request is wrapped in fires immediately and
   aborts the whole first load: the capture is of a dashboard that never
   fetched anything, and it is indistinguishable from a capture of a dashboard
   whose backend is down.

   So this drives a real headless Chrome over the protocol instead, waits
   wall-clock for the tables to fill, prints what landed, and captures.

     node tools/shot.js <url> <out.png> [waitMs] [width] [height] [preJs|@file]

   `preJs` runs after the document is complete and before the wait, which is
   where a tab switch or a click on a card belongs: a setup that runs after the
   wait is photographed before its own request has come back. A setup longer
   than a shell argument can carry goes in a file and is passed as `@path`.
*/
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const CHROME = process.env.CHROME_PATH ||
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const url = process.argv[2] || "http://127.0.0.1:8787/";
const out = path.resolve(process.argv[3] || "shot.png");
const waitMs = parseInt(process.argv[4] || "9000", 10);
const width = parseInt(process.argv[5] || "1680", 10);
const height = parseInt(process.argv[6] || "1500", 10);
let pre = process.argv[7] || "";
if (pre.charAt(0) === "@") pre = fs.readFileSync(pre.slice(1), "utf8");

// A port of its own, and a profile directory of its own, so two runs never
// share a browser: a reused profile serves a new module against a cached one
// and the page dies on a name the cached copy does not have, which reads
// exactly like a broken backend.
//
// The profile lives in the system temp directory and not beside the output
// file, where it used to: a Chrome profile is a few thousand files of cache,
// cookies and crash dumps, and putting it next to the screenshots meant it was
// one careless `git add docs` away from being published.
//
// Every browser this harness starts carries this directory, and that is also
// how the leftovers are found and killed: see sweep().
const PORT = parseInt(process.env.SHOT_PORT || "9333", 10);
const PROFILE_TAG = "_chromeprof";
const profile = path.join(os.tmpdir(), PROFILE_TAG + "-" + PORT);
const sleep = ms => new Promise(r => setTimeout(r, ms));

// Chrome on Windows is not one process and is not `chrome.pid`: the launcher
// re-execs, the browser process is a stranger to node, and a signal to the
// original pid reaches nothing. That is how a run that reported a clean exit
// left nine browsers holding a profile directory, which then failed the next
// run's `rm -rf` with "device or resource busy" - a message about a process
// nobody can see. So the whole family is matched on the profile path in its
// command line, which nothing else on the machine uses, and killed by pid.
function sweep(){
  if (process.platform !== "win32") return;
  const { spawnSync } = require("child_process");
  spawnSync("powershell", ["-NoProfile", "-Command",
    "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | " +
    "Where-Object { $_.CommandLine -like '*" + PROFILE_TAG + "*' } | " +
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force " +
    "-ErrorAction SilentlyContinue }"], { stdio: "ignore" });
}

// Anything the previous run left behind, cleared before a new browser opens:
// it holds a port and it holds a profile directory.
sweep();

const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
  "--no-proxy-server", "--hide-scrollbars", "--force-device-scale-factor=1",
  "--user-data-dir=" + profile,
  "--remote-debugging-port=" + PORT,
  "--window-size=" + width + "," + height,
  "about:blank",
], { stdio: "ignore" });

// The browser socket, kept where done() can reach it: closing it before the
// sweep lets the browser come down on its own terms first, so the sweep is
// only ever cleaning up whatever refused to.
let sock = null;

// Every path out of here goes through this.
function done(code){
  try { if (sock && sock.readyState === 1) sock.close(); } catch (e) {}
  try { chrome.kill(); } catch (e) { /* already gone */ }
  sweep();
  process.exit(code);
}

async function main(){
  let ws = null;
  for (let i = 0; i < 60 && !ws; i++){
    await sleep(250);
    try {
      const r = await fetch("http://127.0.0.1:" + PORT + "/json/version");
      ws = (await r.json()).webSocketDebuggerUrl;
    } catch (e) { /* not up yet */ }
  }
  if (!ws) throw new Error("chrome never opened its debugging port");

  sock = new WebSocket(ws);
  let id = 0;
  const pending = new Map();
  const send = (method, params, sessionId) => new Promise((res, rej) => {
    const n = ++id;
    pending.set(n, { res, rej });
    sock.send(JSON.stringify({ id: n, method, params: params || {}, sessionId }));
  });

  const events = [];
  await new Promise(r => sock.addEventListener("open", r));
  sock.addEventListener("message", ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)){
      const p = pending.get(m.id);
      pending.delete(m.id);
      m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
    } else if (m.method) events.push(m);
  });

  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget",
                                   { targetId, flatten: true });
  const S = (method, params) => send(method, params, sessionId);

  await S("Page.enable");
  await S("Runtime.enable");
  await S("Network.enable");
  await S("Network.setCacheDisabled", { cacheDisabled: true });
  await S("Emulation.setDeviceMetricsOverride",
          { width, height, deviceScaleFactor: 1, mobile: false });
  await S("Page.navigate", { url });

  if (pre){
    for (let i = 0; i < 60; i++){
      const r = await S("Runtime.evaluate", {
        expression: 'document.readyState === "complete"', returnByValue: true });
      if (r.result.value) break;
      await sleep(250);
    }
    const r = await S("Runtime.evaluate", { expression: pre, awaitPromise: true,
                                            returnByValue: true });
    if (r.exceptionDetails) console.log("pre threw:",
      JSON.stringify(r.exceptionDetails).slice(0, 300));
    else console.log("pre ->", JSON.stringify(r.result.value).slice(0, 800));
  }

  await sleep(waitMs);

  // Anything the page threw while loading. Without this, a table that failed
  // to draw and a table with nothing in it look the same from here.
  const boom = events.filter(e => e.method === "Runtime.exceptionThrown" ||
    (e.method === "Runtime.consoleAPICalled" && e.params.type === "error"));
  for (const e of boom.slice(0, 6)){
    const d = e.params.exceptionDetails || {};
    console.log("PAGE ERROR:", (d.exception && d.exception.description) ||
      JSON.stringify(e.params.args || d).slice(0, 300));
  }

  // What actually landed, printed next to the file. A picture cannot tell "no
  // rows" from "no data", and this is the line that does.
  const probe = await S("Runtime.evaluate", {
    expression: `JSON.stringify({
      tab: (document.querySelector('.tab[aria-selected="true"]')||{}).textContent,
      rows: document.querySelectorAll('table tbody tr').length,
      tables: document.querySelectorAll('table').length,
      cards: document.querySelectorAll('.card').length,
      first: Array.from(document.querySelectorAll('table tbody tr'))
               .slice(0,2).map(function(r){ return r.cells.length; }),
      toast: (document.getElementById('toast')||{}).textContent,
      err: (document.querySelector('.err,.error,[data-err]')||{}).textContent
    })`, returnByValue: true });
  console.log("page state:", probe.result.value);

  const shot = await S("Page.captureScreenshot", { format: "png",
                                                   captureBeyondViewport: false });
  fs.writeFileSync(out, Buffer.from(shot.data, "base64"));
  console.log("wrote", out, fs.statSync(out).size, "bytes");
  done(0);
}

main().catch(e => { console.error("FAILED:", e.message); done(1); });
