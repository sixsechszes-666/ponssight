/* Setup for the Copy screenshot.

   The tab takes a source token as its route argument, exactly like the coin
   card, so the URL already opens the plan form filled from that token: the
   name, the symbol, the logo URI, the socials, the tax, all of it read off the
   source. What the URL cannot do is wait, and this tab has two panels that
   arrive late - the source line fills when `/api/token/<addr>` answers, and the
   fee panel under the form fills when the factory has been read. Shot on the
   default wait the picture is a form with "no source token" over it and
   "reading launch config and preview..." where the cost belongs, which reads as
   a tab whose fee lookup is broken rather than as one still working.

   So this waits for both lines to settle and hands the frame over when they
   have. It is not a click: nothing here is hidden behind a button.

   Passed to shot.js as `@tools/pre_copy.js`. */
(async () => {
  // Both panels have to be there and neither may still say it is reading: an
  // empty `#c-cost` is a panel that has not started, not one that has finished.
  const settled = (v) => !!v && v.textContent.trim() !== "" && !/loading|reading/i.test(v.textContent);
  for (let i = 0; i < 160; i++) {
    const src = document.getElementById("c-src-v");
    const cost = document.getElementById("c-cost");
    if (settled(src) && settled(cost)) {
      return "source and fees settled: " + src.textContent.trim();
    }
    await new Promise(r => setTimeout(r, 250));
  }
  const cost = document.getElementById("c-cost");
  return "gave up waiting: " + (cost ? cost.textContent.slice(0, 60) : "no cost panel");
})()
