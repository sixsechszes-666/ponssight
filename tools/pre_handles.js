/* Setup for the Handles screenshot.

   The tab is a lookup, not a feed: it renders nothing until something is
   asked of it, so the picture of it has to include the question. `hoodcrow` is
   the handle the demo dataset shares across four tokens from four deployers,
   which is the case the tab was built to answer.

   Passed to shot.js as `@tools/pre_handles.js`. */
(() => {
  const input = document.getElementById("h-q");
  input.value = "hoodcrow";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  document.getElementById("h-go").click();
  return "asked: hoodcrow";
})()
