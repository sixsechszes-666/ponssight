/* Setup for the Wallet screenshot.

   The tab has no answer of its own until it is pointed at an address, and the
   address arrives by pressing view on a watchlist row - the tab has no
   `/<address>` route, on purpose: a summary exists for a wallet you are
   watching, not for one you can type.

   So there are two ways in, and which one is right depends on the database
   behind the page:

   - the seeded demo has a watchlist, and the picture is taken the way a person
     gets there, by waiting for it to paint and pressing view;
   - a live index belongs to whoever runs it, and its watchlist is theirs. The
     address comes in as `?addr=` instead, and the tab is pointed at it through
     the same `viewAddr` the key-wallet panel sets when it sends you here - the
     second way in that the app itself already has, rather than a new one
     invented for a screenshot.

   The wait is why the watchlist branch is a promise: shot.js runs the setup
   with awaitPromise and then sleeps, and a setup that finished before its own
   click had landed would photograph the empty tab.

   Passed to shot.js as `@tools/pre_wallet.js`. */
(async () => {
  const asked = new URLSearchParams(location.search).get("addr");
  if (asked) {
    viewAddr = asked;
    renderWalletChrome();
    refreshWallet();
    return "view " + viewAddr;
  }
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
