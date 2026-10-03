/* Keyboard shortcuts.

   A dashboard that stays open all day is worth driving from the keyboard, and
   these are deliberately only the things the pointer already does - switch a
   tab, jump to the search box, re-read the panel. Nothing here is a mode and
   nothing here changes state, so there is no way to get stuck in one.

   Escape is handled in coin.js for the card and here for a focused field; the
   two never both fire, because the card is modal and this returns early while
   it is open. */

// where "/" should put the cursor on each tab. Two tabs have no search box of
// their own, so the key does nothing there rather than focusing something
// unrelated.
const KEY_SEARCH = {launches: "#q", volume: "#v-q", sniped: "#sn-q",
                    handles: "#h-q", copy: "#c-q", snipers: null,
                    create: null, wallet: null, wallets: null, sniper: null};

function isTyping(e){
  const t = e.target;
  if (!t) return false;
  const tag = String(t.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" ||
         t.isContentEditable === true;
}

function keyShortcut(e){
  // A chord belongs to the browser or to the window manager. Intercepting
  // ctrl+w or cmd+1 here would be taking a shortcut away, not adding one.
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  // A modal owns every key while it is up. The coin panel is a modal only on a
  // window too narrow to hold it beside the list: docked, it is a column beside
  // a live table, and the digits, the slash and the refresh key all have to keep
  // working on that table.
  if ((coinOpen() && !coinDocked()) || walletOpen()) return;

  // Escape gets out of a field. It is the one key that has to work while
  // typing, so it is checked before the typing guard below.
  if (e.key === "Escape" || e.key === "Esc"){
    if (isTyping(e)){
      // blur rather than clearing: losing a half-typed search is worse than
      // having to press the key twice
      e.target.blur();
    }
    return;
  }

  // Everything else stays out of the way of a field being typed into, or a
  // symbol containing a 1 would change the tab under the cursor.
  if (isTyping(e)) return;

  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= TABS.length){
    e.preventDefault();
    setTab(TABS[n - 1]);
    return;
  }

  if (e.key === "/"){
    const sel = KEY_SEARCH[tab];
    if (!sel) return;
    const el = $(sel);
    if (!el) return;
    // Firefox opens its own quick-find on "/", which would fight this.
    e.preventDefault();
    el.focus();
    el.select();
    return;
  }

  if (e.key === "r"){
    const b = $("#refresh");
    if (!b || b.offsetParent === null) return;
    e.preventDefault();
    b.click();
  }
}
