/* Setup for the Wallets screenshot.

   The tab opens on a shut vault, and a shut vault is a passphrase field and two
   buttons - it is the state you are in before you can do anything, not the
   thing the tab does. Everything the tab is for is behind the passphrase:
   which keys are stored, where their balances are, what they launched, and the
   transfer controls underneath.

   The passphrase is the demo one, and it is printed in tools/seed_demo.py next
   to the keys it opens. That is the point of those two keys: they hold nothing
   on any chain, so the only thing anybody can do with them is unlock this demo
   and watch the tab work.

   Passed to shot.js as `@tools/pre_wallets.js`. */
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < 40; i++) {
    const b = document.getElementById("kv-unlock");
    if (b && !b.hidden) {
      const p = document.getElementById("kv-pass");
      p.value = "demo-passphrase";
      p.dispatchEvent(new Event("input", {bubbles: true}));
      b.click();
      // The answer lands in #kv-msg, and it is the honest probe: "vault open"
      // and a refusal are the same screen otherwise.
      for (let j = 0; j < 40; j++) {
        const m = document.getElementById("kv-msg");
        if (m && m.textContent && !/checking/.test(m.textContent)) {
          return "unlock -> " + m.textContent.slice(0, 90);
        }
        await sleep(250);
      }
      return "unlock pressed, no answer written";
    }
    await sleep(250);
  }
  return "no unlock button appeared";
})()
