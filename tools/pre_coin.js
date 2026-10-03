/* Setup for the coin-card screenshot.

   A card is a route of its own (`#<tab>/coin/<address>`), reached by clicking
   a row. Navigating the hash is what the click does, so it is also the way to
   photograph the card without hunting for the row in a table that is
   re-rendered under the pointer on every poll. Long Weekend is at 49% of the
   curve with trades on both sides, which is a card that has a chart in it.

   Passed to shot.js as `@tools/pre_coin.js`. */
(() => {
  location.hash = "#launches/coin/0x7000000000000000000000000000000000000010";
  return location.hash;
})()
