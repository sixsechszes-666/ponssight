/* Setup for the coin card in the Launches screenshot.

   The card opens on its six hour range, which is the right default for someone
   who has just watched a token launch and the wrong one for the card of a
   token that launched a week ago: the chart comes back with one point on it
   and the price panel says there is nothing to draw, which on a card that is
   otherwise full of numbers reads as the one broken panel.

   So the range is set to `all` - hours 0, the whole life of the token - by
   pressing the same button a person would. The card is fetched after the hash
   is read, so the buttons may not exist yet when this runs: it waits for them
   the way the watchlist branch of pre_wallet.js waits for its row.

   Passed to shot.js as `@tools/pre_coin.js`. */
(async () => {
  for (let i = 0; i < 80; i++) {
    const b = document.querySelector("[data-coinhours='0']");
    if (b) {
      if (!b.classList.contains("on")) b.click();
      return "range: all";
    }
    await new Promise(r => setTimeout(r, 250));
  }
  return "no range buttons appeared";
})()
