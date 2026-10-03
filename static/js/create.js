/* The Create tab.

   A launch from scratch: no source token, nothing inherited, and a logo of
   your own instead of one somebody else already used.

   The launch form is not duplicated here. #c-form-wrap is a single node shared
   with the Copy tab - it is moved into this panel while the tab is open and
   back into #p-copy when it is not - because the launch path in copy.js
   reaches every one of its fields by id, and a second copy of the form would
   be a second #f-name, a second #c-confirm and a second send button.

   So what this file owns is the one control the copy flow has no use for: the
   image. It is also the only thing on the page that leaves the machine before
   a transaction does, and it leaves for a public host with no account behind
   it. That is a real trade - it is how a logo gets a uri a token contract can
   carry - which is why the panel states it in full before the button rather
   than in a footnote after it. */
const create = {file: null, url: "", busy: false};

function crBytes(n){
  const v = fin(n);
  if (v == null) return "-";
  if (v >= 1048576) return (v / 1048576).toFixed(1) + " MB";
  if (v >= 1024) return Math.round(v / 1024) + " kB";
  return Math.round(v) + " bytes";
}

function crState(text, cls){
  const el = $("#cr-state");
  el.className = "badge" + (cls || "");
  el.textContent = text;
}

// The preview shows what will be uploaded, not the local file: once the host
// has the image the uri is the thing the token carries, and seeing what came
// back is the only way to know the two agree.
function crShot(src){
  const img = $("#cr-shot");
  // The instruction and the preview are the same slot: one says what to do
  // with an empty zone, the other shows what is in it, and having both on
  // screen at once reads as two things rather than one.
  $("#cr-drop-in").hidden = !!src;
  if (!src){
    img.hidden = true;
    img.removeAttribute("src");
    return;
  }
  img.src = src;
  img.hidden = false;
}

function crEnable(on){
  $("#cr-up").disabled = !on || create.busy;
  $("#cr-clear").disabled = !on && !create.url;
}

function crPick(file){
  if (!file) return;
  if (!/^image\//.test(file.type || "")){
    msg("cr-msg", "that is not an image file: " + (file.type || "no type") +
      ". an image is all this can send.", "err");
    return;
  }
  create.file = file;
  create.url = "";
  const r = new FileReader();
  r.onload = () => crShot(String(r.result));
  r.onerror = () => msg("cr-msg", "the browser could not read that file", "err");
  r.readAsDataURL(file);
  crEnable(true);
  crState(file.name.slice(0, 28) + " / " + crBytes(file.size), "");
  msg("cr-msg", "");
}

async function crUpload(){
  if (!create.file || create.busy) return;
  create.busy = true;
  crEnable(false);
  crState("uploading...", "");
  msg("cr-msg", "pinning the image...");
  try {
    // Read again rather than keeping the first result: a large file held as a
    // base64 string in memory for the whole session is a lot of memory for a
    // value that is only needed twice.
    const data = await new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(String(r.result));
      r.onerror = () => rej(new Error("the browser could not read the file"));
      r.readAsDataURL(create.file);
    });
    const d = await jpost("/api/upload", {data: data});
    create.url = d.url;
    $("#f-logo").value = d.url;
    crShot(d.url);
    crState(d.where === "ipfs" ? "pinned" : "uploaded", " ok");
    msg("cr-msg", "done. " + crBytes(d.original_bytes) + " in, " +
      crBytes(d.bytes) + " out" + (d.format === "png"
        ? ", re-encoded to " + d.width + "x" + d.height + " png"
        : ", passed through as " + d.format + " to keep it animating") +
      ". the token form now carries " + d.url + " - " +
      (d.where === "ipfs"
        ? "pinned to ipfs, so any gateway can serve it and no one host " +
          "has to stay up for the token's logo to keep loading."
        : "hosted on a public file host" + (d.note || "") +
          ", which is a single link that can go away.") +
      " either way it is public and cannot be taken back: it is the uri " +
      "the token will carry for the rest of its life.", "ok");
  } catch(e) {
    crState("upload failed", " bad");
    msg("cr-msg", "upload failed: " + errText(e), "err");
  } finally {
    create.busy = false;
    crEnable(!!create.file);
  }
}

function crClear(){
  create.file = null;
  create.url = "";
  $("#cr-file").value = "";
  $("#f-logo").value = "";
  crShot("");
  crEnable(false);
  crState("no image", "");
  msg("cr-msg", "");
}

function mountCreate(){
  const wrap = $("#c-form-wrap");
  if (wrap.parentNode !== $("#cr-form")) $("#cr-form").appendChild(wrap);
  if (formClaim("create")) startBlank("create");
  wrap.hidden = false;
  syncEntry();
  renderCopyChrome();
  loadCost();
}
