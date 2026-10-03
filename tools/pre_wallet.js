/* Setup for the Wallet screenshot.

   The tab has no answer of its own until it is pointed at an address, and the
   address arrives by pressing view on a watchlist row - the tab has no
   `/<address>` route, on purpose: a summary exists for a wallet you are
   watching, not for one you can type. So the picture is taken the way a person
   gets there, by waiting for the watchlist to paint and pressing the button.

   The wait is why this is a promise: shot.js runs the setup with awaitPromise
   and then sleeps, and a setup that finished before its own click had landed
   would photograph the empty tab.

   Passed to shot.js as `@tools/pre_wallet.js`. */
(async () => {
  for (let i = 0; i < 40; i++) {
    const b = document.querySelector("button[data-wlview]");
    if (b) {
      b.click();
      return "view " + b.dataset.wlview;
    }
    await new Promise(r => setTimeout(r, 250));
  }
  return "no watchlist row appeared";
})()
