function bindControls(){
  // launches
  $("#q").addEventListener("input", debounce(refreshLaunches, 220));
  ["sort", "quote", "hidegrad", "fresh", "devonly"].forEach(id =>
    $("#" + id).addEventListener("change", refreshLaunches));
  // Debounced on input as well as change, because a floor is typed digit
  // by digit: "1" then "10" then "100" then "1000" each redraw, and the
  // last one is the only answer that matters.
  $("#minvol").addEventListener("change", refreshLaunches);
  $("#minvol").addEventListener("input", debounce(refreshLaunches, 400));
  $$("th[data-sort]").forEach(th => {
    th.addEventListener("click", () => {
      $("#sort").value = th.dataset.sort;
      $$("th").forEach(x => x.classList.remove("on"));
      th.classList.add("on");
      refreshLaunches();
    });
  });

  // volume
  ["v-window", "v-sort", "v-quote", "v-hidegrad"].forEach(id =>
    $("#" + id).addEventListener("change", refreshVolume));
  $("#v-q").addEventListener("input", debounce(refreshVolume, 220));

  // sniped: the label boxes have to keep at least one tick on
  ["sn-lb-bundled", "sn-lb-sniped", "sn-lb-early"].forEach(id =>
    $("#" + id).addEventListener("change", () => { keepOneLabel(); refreshSniped(); }));
  ["sn-sort", "sn-bots", "sn-quote"].forEach(id =>
    $("#" + id).addEventListener("change", refreshSniped));
  $("#sn-maxdelta").addEventListener("change", refreshSniped);
  $("#sn-maxdelta").addEventListener("input", debounce(refreshSniped, 400));
  $("#sn-q").addEventListener("input", debounce(refreshSniped, 220));

  // snipers (wallets)
  ["sw-sort", "sw-per"].forEach(id =>
    $("#" + id).addEventListener("change", refreshSnipers));
  $("#sw-minhits").addEventListener("change", refreshSnipers);
  $("#sw-minhits").addEventListener("input", debounce(refreshSnipers, 400));

  // handles
  $("#h-go").addEventListener("click", checkHandle);
  $("#h-q").addEventListener("keydown", e => {
    if (e.key === "Enter"){ e.preventDefault(); checkHandle(); }
  });
  $("#h-q").addEventListener("input", debounce(checkHandle, 350));
  $("#h-fgo").addEventListener("click", loadFollowings);
  $("#h-fstop").addEventListener("click", stopFollowings);
  // Both keys are read together on either change, because they are one order
  // rather than two: "launches, most" then "followers, most" is a different
  // question from either alone, and the server applies them in this order.
  $("#h-fsort").addEventListener("change", fSort);
  $("#h-fsort2").addEventListener("change", fSort);
  $("#h-fall").addEventListener("click", fAll);
  // One listener for every row and every name in the list, because the rows are
  // appended a hundred at a time and a listener per row would have to be added
  // again for each batch.
  $("#h-ftb").addEventListener("click", e => {
    // The row carries data-h, so a click anywhere in it means "check this
    // handle" - but two things inside the row mean something else: the X link,
    // which is the browser's own navigation, and the handle chip beside it,
    // which copies. The innermost element wins. This is an early return and not
    // stopPropagation on purpose: the copy is served by the delegated handler on
    // document, and the event has to reach it.
    if (e.target.closest("a, [data-copy]")) return;
    const b = e.target.closest("[data-h]");
    if (b) openHandleFor(b.dataset.h);
  });

  // one sniper, opened. Three controls and they are all one question - which
  // rows, in what order - so each of them empties the table and asks again from
  // the top rather than trying to rearrange rows that are a hundred of a
  // thousand.
  ["sp-sort", "sp-sort2"].forEach(id =>
    $("#" + id).addEventListener("change", spSort));
  $("#sp-lost").addEventListener("change", spLost);
  $("#sp-all").addEventListener("click", spAll);
  $("#sp-xwalk").addEventListener("click", spXStart);
  $("#sp-cwalk").addEventListener("click", spChainStart);

  // copy
  $("#c-search").addEventListener("click", pickerSearch);
  $("#c-q").addEventListener("input", debounce(pickerSearch, 250));
  $("#c-blank").addEventListener("click", () => {
    startBlank("copy");
    showForm();
    renderCopyChrome();
    setHash("#copy");
    loadCost();
  });
  $("#c-change").addEventListener("click", resetCopy);
  $("#c-extra-add").addEventListener("click", () => {
    $("#c-extra").insertAdjacentHTML("beforeend", exRow({ label: "", value: "" }));
    const rows = $$("#c-extra .exrow");
    const last = rows[rows.length - 1];
    if (last) last.querySelector(".ex-l").focus();
  });
  $("#c-save").addEventListener("click", savePlan);
  $("#c-json").addEventListener("click", copyJson);
  $("#c-delete").addEventListener("click", removePlan);
  $("#c-launch").addEventListener("click", startLaunch);
  $("#c-send").addEventListener("click", sendNow);
  $("#c-cancel").addEventListener("click", () => { hideConfirm(); msg("c-msg", "cancelled, nothing was sent"); });
  ["#f-config", "#f-pair"].forEach(sel => {
    const el = $(sel);
    if (el) el.addEventListener("change", loadCost);
  });
  $("#f-buy").addEventListener("change", loadCost);
  $("#f-buy").addEventListener("input", debounce(loadCost, 500));
  // The entry point decides whether those three fields can be used at all, so
  // it is bound before them and clears the buy on the way back to the factory.
  $$('input[name="f-entry"]').forEach(el =>
    el.addEventListener("change", () => { syncEntry(true); loadCost(); }));
  $("#f-slip").addEventListener("change", loadCost);
  ["#f-tax", "#f-slip"].forEach(sel =>
    $(sel).addEventListener("input", syncBps));
  // Who signs. The select and the radio in the Wallets table are two views of
  // one choice, so the select writes the same function the radio does rather
  // than a second state that would have to be kept in step with it.
  $("#f-signer").addEventListener("change", () => kwSetActive($("#f-signer").value || null));

  // create. The zone takes a click, a drop and a keypress, because all three
  // are things a person will try on something shaped like this.
  $("#cr-drop").addEventListener("click", () => $("#cr-file").click());
  $("#cr-drop").addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " "){ e.preventDefault(); $("#cr-file").click(); }
  });
  $("#cr-file").addEventListener("change", e =>
    crPick(e.target.files && e.target.files[0]));
  $("#cr-up").addEventListener("click", crUpload);
  $("#cr-clear").addEventListener("click", crClear);
  ["dragenter", "dragover"].forEach(ev => $("#cr-drop").addEventListener(ev, e => {
    e.preventDefault();
    $("#cr-drop").classList.add("on");
  }));
  ["dragleave", "drop"].forEach(ev => $("#cr-drop").addEventListener(ev, e => {
    e.preventDefault();
    $("#cr-drop").classList.remove("on");
  }));
  $("#cr-drop").addEventListener("drop", e => {
    const dt = e.dataTransfer;
    crPick(dt && dt.files && dt.files[0]);
  });

  // wallet. Both connect buttons open the picker rather than connecting
  // straight through window.ethereum: which wallet to use is now a question
  // with more than one answer.
  $("#w-open").addEventListener("click", openWalletModal);
  $("#w-connect").addEventListener("click", openWalletModal);
  $("#wmodal").addEventListener("click", walletModalClick);
  addEventListener("keydown", walletKeydown);
  // whether the press began on the scrim, so a drag out of the dialog is not
  // mistaken for a click on it
  $("#wmodal").addEventListener("pointerdown", e => {
    wallet.backdrop = (e.target === $("#wmodal"));
  });
  $("#w-switch").addEventListener("click", switchChainNow);
  $("#w-disconnect").addEventListener("click", disconnectWallet);
  $("#w-view-clear").addEventListener("click", () => {
    viewAddr = null;
    renderWalletChrome();
    refreshWallet();
  });
  $("#wl-add").addEventListener("click", addWatch);
  $("#wl-refresh").addEventListener("click", refreshWatch);

  // wallets. The table is redrawn whole on every change, so its buttons are
  // handled by delegation on the container rather than bound to nodes that do
  // not exist yet - the same reason the coin card does it this way.
  $("#ka-add").addEventListener("click", kwAdd);
  $("#ka-key").addEventListener("keydown", e => {
    if (e.key === "Enter"){ e.preventDefault(); kwAdd(); }
  });
  // the vault. Enter works in both fields because the second one only exists on
  // the first setup, where it is the field after the first - asking someone to
  // reach for the mouse in the middle of setting a passphrase is how a stray
  // keystroke ends up in the wrong box.
  $("#kv-unlock").addEventListener("click", kvUnlock);
  $("#kv-lock").addEventListener("click", kvLock);
  ["#kv-pass", "#kv-pass2"].forEach(sel => {
    $(sel).addEventListener("keydown", e => {
      if (e.key === "Enter"){ e.preventDefault(); kvUnlock(); }
    });
  });
  $("#kw-refresh").addEventListener("click", () => refreshKeyWallets(false));
  $("#kw-list").addEventListener("click", e => {
    // The radio that picks the signing key is not a button, so it is matched by
    // the attribute its own branch uses rather than being left to a second
    // listener - one row, one delegated handler, as everywhere else here.
    const b = e.target.closest("button, [data-kwuse]");
    if (!b) return;
    if (b.dataset.kwuse) kwSetActive(b.dataset.kwuse);
    else if (b.dataset.kwpick) kwPick(b.dataset.kwpick);
    else if (b.dataset.kwdel) kwDelete(b.dataset.kwdel);
    else if (b.dataset.kwshow) kwShow(b.dataset.kwshow);
    else if (b.dataset.kwcopy) kwCopyKey(b.dataset.kwcopy);
  });
  // the detail panel is painted by kwDet, so its controls are delegated too
  $("#kw-det").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.kwlabel) kwLabel(b.dataset.kwlabel);
    else if (b.dataset.kwback){
      kw.picked = null;
      kwRender();
    } else if (b.dataset.kwview){
      // the wallet tab's own view, reused rather than copied: viewAddr is what
      // it reads, and this is the second way to set it
      viewAddr = b.dataset.kwview;
      setTab("wallet");
    }
  });
  ["#br-src", "#br-dest"].forEach(sel =>
    $(sel).addEventListener("change", brRoute));
  $("#br-plan").addEventListener("click", brPlan);
  $("#br-start").addEventListener("click", brStart);

  // shared refresh controls: one timer for whichever tab is active
  $("#every").addEventListener("change", () => {
    const fn = REFRESH[tab];
    if (fn && POLLING[tab]) startTimer(fn);
    else stopTimer();
  });
  $("#refresh").addEventListener("click", () => {
    const fn = REFRESH[tab];
    if (fn) fn();
    maybeStats(true);
  });

  // the card is a modal, so escape always gets out of it and tab stays inside
  addEventListener("keydown", coinKeydown);
  // tab numbers, search focus and refresh. Also on the window, and silenced
  // while the card is open, so the two handlers cannot both act on one key.
  addEventListener("keydown", keyShortcut);
  // whether the pointer went down on the backdrop at all, so a drag that began
  // inside the card and ended outside it does not read as a backdrop click
  $("#coin").addEventListener("pointerdown", e => {
    coin.backdrop = (e.target === $("#coin"));
  });

  addEventListener("hashchange", () => {
    const h = readHash();
    // the card rides on top of whatever tab is showing, so opening one never
    // moves that tab and pressing back closes the card again instead of
    // breaking the router
    if (h.coin){
      if (h.name !== tab) setTab(h.name, null, { hash: false });
      if (!coinOpen() ||
          String(coin.addr || "").toLowerCase() !== h.coin.toLowerCase())
        openCoinCard(h.coin, { hash: false });
      return;
    }
    if (coinOpen()) closeCoinCard({ hash: false, focus: false });
    if (h.name !== tab){ setTab(h.name, h.arg, { hash: false }); return; }
    if (h.name === "copy" && h.arg && h.arg !== copy.sourceAddress) openSourceAddress(h.arg);
    // #snipers/0x.. is a shareable link to one wallet on the leaderboard, and
    // dropping the argument has to clear the focus again
    if (h.name === "snipers" && h.arg !== swFocus){
      swFocus = h.arg || null; swAutoDone = false; renderSnipers();
    }
    // #sniper/0x.. is one wallet's own page. Changing the address while that
    // page is already up is a move to a different wallet, and without this it
    // would leave the previous one on screen under the new route.
    if (h.name === "sniper" && h.arg !== SP.addr) mountSniper(h.arg);
  });
}

const REFRESH = { launches: refreshLaunches, volume: refreshVolume,
                  sniped: refreshSniped, snipers: refreshSnipers,
                  handles: checkHandle, wallet: refreshWallet,
                  sniper: () => mountSniper(SP.addr),
                  copy: null, create: null };

/* ------------------------------------------------------------------- boot */
initWallet();
bindControls();
initWalletEvents();
// The launch form's signer select is drawn here as well as from the Wallets
// tab, because `kw.active` is restored from storage at load and the form can be
// the first thing opened: a page that came back with a stored key selected and
// a select reading "browser wallet" would be describing a different launch than
// the one it would sign.
renderSigner();
const boot = readHash();
// a deep link into a card has to keep the card's own route, so the first setTab
// must not rewrite the hash out from under it
setTab(boot.name, boot.arg, boot.coin ? { hash: false } : undefined);
if (boot.coin) openCoinCard(boot.coin, { hash: false });
maybeStats(true);
refreshWatch();
