/* Setup for the Handles screenshot.

   The tab is a lookup, not a feed: it renders nothing until something is asked
   of it, so the picture of it has to include the question. `hoodcrow` is the
   handle the demo dataset shares across four tokens from four deployers, which
   is the case the tab was built to answer.

   A live database has no `hoodcrow` in it, so the question can come in on the
   URL as `?h=<handle>` - demo_shots.sh passes one when it is shooting a real
   index. The default stays the seeded handle, so a capture of the demo base
   still needs nothing but the hash.

   Passed to shot.js as `@tools/pre_handles.js`. */
(() => {
  const ask = new URLSearchParams(location.search).get("h") || "hoodcrow";
  const input = document.getElementById("h-q");
  input.value = ask;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  document.getElementById("h-go").click();
  return "asked: " + ask;
})()
