/* Setup for the Copy screenshot.

   The tab opens on the source picker, and the picker is a list of every token
   in the index - a picture of a list, not of what the tab does. Pressing
   `start blank` is the documented way into the plan form without a source, and
   the form is the tab: what gets copied, how much is spent, the slippage, the
   entry point, and the cost the factory will charge for it. The saved drafts
   live under the picker, so the way to see both is the picker filtered down;
   the form is what the tab is for and it is what this shoots.

   Passed to shot.js as `@tools/pre_copy.js`. */
(async () => {
  for (let i = 0; i < 40; i++) {
    const b = document.getElementById("c-blank");
    if (b) {
      b.click();
      return "start blank -> " + (document.getElementById("c-src").hidden
        ? "form still hidden" : "form open");
    }
    await new Promise(r => setTimeout(r, 250));
  }
  return "no start-blank button appeared";
})()
