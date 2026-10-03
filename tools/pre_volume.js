/* Setup for the Volume screenshot.

   The tab opens on the one hour window, which is the right default for someone
   watching the board and the wrong one for a picture of it: an hour of a
   seeded database is a handful of rows, and an hour of a live one can be none
   at all if the trade walk has not caught up with the tip yet. Either way the
   screenshot would be of a table that says "no trades in this window", which
   reads as a tab that does not work rather than as a quiet hour.

   So the window is a parameter: `?w=all` for the totals a live index always
   has. Without it the select is left alone and the tab is photographed on its
   own default.

   `window` is a name in this file's own scope and not a global, so the select
   is driven the way a person drives it - and refreshVolume() is called after,
   because a select that has been set programmatically is not obliged to have
   been listened to.

   Passed to shot.js as `@tools/pre_volume.js`. */
(() => {
  const want = new URLSearchParams(location.search).get("w");
  if (!want) return "left on the tab default";
  const sel = document.getElementById("v-window");
  sel.value = want;
  sel.dispatchEvent(new Event("change", { bubbles: true }));
  refreshVolume();
  return "window " + sel.value;
})()
