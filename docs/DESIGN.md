# Design contract - Pons Sight

Every stylesheet in `static/css/` is written against this file. It exists so
that ten people working at once produce one interface instead of ten. If a
decision is not covered here, follow the intent in section 1 and say what you
chose in a comment.

Read `_recon/old.css` for what the classes used to look like and
`_recon/classes.txt` for the full list that has to stay covered. Read
`static/index.html` for the markup you are styling. Do not edit either.

---

## 1. The intent

Near-black canvas, monochrome interface, dense rows inside panels, and small
wide-tracked labels above figures that are read in columns rather than gaped
at. The reference point is a trading terminal - Axiom Trade is the one that was
named - and the thing being copied from it is the shape of the screen: a rail
down the left that says which list you are on, a panel around each list, a
strip of metrics over it, and the token panel beside the table rather than
over it.

**What could not be copied, and what stands in for it.** `axiom.trade` answers
403 to a fetch and puts a Cloudflare challenge in front of a headless browser.
That challenge was not worked around, so not one colour, size or typeface here
comes from the reference: the language below was written from the public
description of the screen and then approved against before-and-after
screenshots. Where this file says "like Axiom", it means the layout, and the
palette is still the one this project already had.

Three rules carry most of the look.

**The interface is monochrome. Colour only ever means money or state.** Every
border, label, icon, button, active tab and hover state is white, grey or
black. Green means profit, red means loss, amber means warning, blue means
information. That is the whole palette. An interface that spends green on a
button has nothing left to say profit with, and on this dashboard profit is
the point.

**A group of things is a panel.** The earlier version of this rule said the
opposite - separate with space and a hairline, never a box - and it was right
for the page it was written for, where a row was 56px tall and a section had
room to announce itself with air. At 46px a row is a texture, a hairline
between two densities of text stops reading as a boundary, and the frame is
what says where a list begins and ends. So `--bg-1`, a `--line-2` ring and
`--r-lg` around every list, every metric strip and every filter bar, and the
canvas between them. A panel inside a panel gives up its frame: two frames
around one table is a border drawn twice.

What survives from the old rule is the reason it existed. A box is not
decoration, and it is never used to group two things that air would group just
as well - the sections inside the coin card are still a heading with a hairline
under it, and still not a stack of frames.

**Numbers are the content, so keep them legible.** Tabular figures, tight
tracking, and one size for a figure wherever it appears on a page: `--t-xl`
(20px) with a 10px mono uppercase label above it. The keynote-slide version of
this rule asked for 28 to 56px, and the strip it produced competed with the
table it was introducing - the figures a reader actually compares are in the
table, not above it. The one step down, `--t-lg`, is the coin card's own stat
grid, because those figures are read one at a time inside a panel that already
has the page's full attention.

---

## 2. Tokens

Defined in `static/css/tokens.css`. **Never write a raw colour, radius,
duration or shadow in any other file.** If you need a value that does not
exist, it is almost always because an existing one is right; if it genuinely
is not, add it to `tokens.css` in your own file's pull and say so.

```
surfaces    --bg  --bg-1  --bg-2  --bg-3
rules       --line  --line-2  --line-3  --edge
text        --txt  --txt-2  --dim  --dim-2
money       --pos  --neg  --warn  --info
tints       --pos-bg --neg-bg --warn-bg --info-bg  (8% washes for row states)
type        --t-micro --t-xs --t-sm --t-md --t-lg --t-xl --t-2xl --t-3xl --t-4xl
family      --sans  --mono
space       --s1 4  --s2 8  --s3 12  --s4 16  --s5 24  --s6 32  --s7 48  --s8 64
radius      --r-sm 4  --r-md 8  --r-lg 14  --r-full 999
motion      --dur-1 120ms  --dur-2 180ms  --dur-3 260ms  --ease
elevation   --shadow-pop  (the only drop shadow; overlays and the toast only)
layout      --page-max 1400  --rail-w  --dock-w 480  --bar-h 48  --tab-h 36
density     --row-pad-y 7  --ctl-h 28
chrome      --chrome-h  (how tall the sticky bands are at this width)
```

`--edge` is not a rule. It is the one-pixel highlight along the top of a panel,
6% white, and it is what keeps a `--bg-1` surface from reading as a hole in the
`--bg` canvas. `--row-pad-y` and `--ctl-h` exist because a row's height and a
control's height were literals in two sheets and drifted three pixels apart.
`--chrome-h` is the height everything sticky measures from, and it is one bar
tall or two depending on the width of the window; section 6b has the rest.

There is no light theme. This is a dark-only instrument and pretending
otherwise would double every decision for nobody.

### Type scale

| token | px | used for |
|---|---|---|
| `--t-micro` | 10 | mono uppercase labels, `letter-spacing:.14em` |
| `--t-xs` | 11 | pills, badges, legends |
| `--t-sm` | 12 | mono values in table cells |
| `--t-md` | 13 | body, the default |
| `--t-lg` | 15 | a token's name, a section heading |
| `--t-xl` | 20 | a card's title |
| `--t-2xl` | 28 | a secondary hero figure |
| `--t-3xl` | 40 | a hero figure |
| `--t-4xl` | 56 | the one figure a tab is about |

Anything `--t-xl` and up is a display size: `font-weight:600`,
`letter-spacing:-.025em`, `font-variant-numeric:tabular-nums`. Never
`font-weight:700` or above - the system faces go clumsy there.

Body copy is `--sans`. Anything a person compares digit by digit - an address,
a price, a count, a percentage, a timestamp - is `--mono` with
`font-variant-numeric:tabular-nums`, so columns of figures line up and a
changing value does not shift its neighbours.

---

## 3. Rules that are not negotiable

**Radius.** Tables and the rows inside them: 0. A panel: `--r-lg`, which is
also what the coin overlay and the toast get - a panel and an overlay are the
same shape at two elevations. Inputs, buttons, pills, badges: `--r-sm` or
`--r-md`. Nothing is rounder than `--r-lg`, and nothing in a table is rounded
at all, including the panel's own corners against the rows beneath them.

**Borders.** 1px, `--line` by default. `--line-2` when a thing needs to read
as interactive, `--line-3` on hover. A border is never coloured except to
carry a money or state meaning.

**A panel's edge is a ring, not a border.** `box-shadow:0 0 0 1px
var(--line-2)`, on `.tw` and on the coin card's docked column. This is
arithmetic rather than taste: a border is 2px of the box, and the widest table
on the page fits its box by exactly the 1px `--page-max` leaves it. Spending
that on an edge is what puts a horizontal scrollbar under the Launches table at
a 1440 window - measured, 1354 needed against 1350 available. A spread shadow
follows the same radius, is painted outside the box, and costs the content
nothing. Everywhere else a border is fine, because nothing else is that tight.

**Shadows.** `--shadow-pop`, on the coin overlay and the toast. Nowhere else.
No drop shadow on a panel, a button, a row or an input. The ring above is a
shadow by property and not by intent: it draws an edge, it does not lift
anything off the page.

**Gradients.** Two, both functional: the graduation progress bar, and the
skeleton shimmer. No decorative gradient, no gradient text, no glow.

**Motion.** `--dur-1` for a colour change, `--dur-2` for a size or opacity
change, `--dur-3` for the overlay. Always `--ease`. Transition named
properties, never `all`. Hover changes colour and border, never position or
size - a table row that moves under the cursor is a table row you cannot
click. Every file that animates must end with:

```css
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms !important;
    animation-iteration-count:1 !important;transition-duration:.01ms !important}
}
```

**Focus.** Every interactive element needs a visible `:focus-visible`: 1px
solid `--txt`, `outline-offset:2px`. Never `outline:none` without a
replacement. This is the one place the interface is allowed to be loud.

**Hit targets.** Anything clickable is at least 28px tall, and at least 32px
on a touch width. A 2px icon button is not a button.

---

## 4. Colour semantics

| meaning | token | where |
|---|---|---|
| profit, alive, confirmed | `--pos` | P&L figures, the live dot, an ok box |
| loss, danger, sniped | `--neg` | P&L figures, the delete button, the bundled label |
| caution, slow, pending | `--warn` | the early label, a warning box, a stale age |
| information, graduated | `--info` | the graduated bar, the slow label |
| everything else | `--txt` `--txt-2` `--dim` `--dim-2` | all of it |

An active tab, a primary button, a selected row: **white**, not green. The
primary button is white text on `--bg-3` with a `--line-3` border, or on a
true-white fill if it is the one action on the screen.

Row state tints use the `-bg` tokens at the given opacity and are additive
with hover. A sniped row keeps a 2px inset marker on its first cell, the way
it does now - that pattern works and the redesign keeps it.

---

## 5. Density

This is an instrument, not a landing page, and a trader scanning for a launch
needs rows close together. Resolve the tension by direction:

- **Table rows stay tight.** `--row-pad-y` (7px) above and below, 8px of
  horizontal gutter, which is a 46px row. Do not grow them. The measured
  effect is 17 rows in a 1680x1000 window against 13 before.
- **Everything around the tables gets air.** `--s5` between a metric strip and
  a table, `--s4` between a control bar and what it filters, `--s6` at the
  top of a panel.
- **The page has a spine.** `max-width:var(--page-max)`, centred, `--s4` side
  gutters. Tables may be wider than the text column and scroll inside `.tw`;
  the page body must never scroll sideways. That 1400 is a measured number and
  not a round one - the widest table needs 1351 of the 1352 the gutters leave
  it - and the rail and the dock both derive their breakpoints from it. Section
  6b has that arithmetic.
- **Controls are one height.** `--ctl-h` (28px), so a filter bar and the table
  under it agree without either sheet naming a pixel.

---

## 6. Responsive

Must work at 400px wide. The page keeps a side gutter of at least 16px at
every width, set once on the page wrapper. Control bars wrap; the metric strip
goes from one row to two under 700px, which is the same media query that
tightens the cell's own padding. Tables keep their widths and scroll inside
`.tw` - that is already how they work and it is the right answer, because a
squeezed numeric column is worse than a scrollbar. The coin panel goes
full-bleed under 700px: no side padding, no radius, full height.

The four widths that matter, and what each one is derived from rather than
picked at:

| width | what changes | where the number comes from |
|---|---|---|
| 2124 | the coin panel docks beside the table instead of covering it | `--page-max + --dock-w + --s5` = 1904 of content, so the window has to be 1904 + `--rail-w` + 2 x `--s5` |
| 1572 | the tab strip becomes a rail down the left edge | the 1399 a table needs (1351 of content plus the two 24px gutters) plus `--rail-w` = 1571 |
| 900 | the table's outer gutters go from 16px to 12px | at 400px a 16px gutter is 4% of the table |
| 700 | the metric strip wraps to two rows, the coin panel goes full-bleed | measured legibility floor for a 150px track and for the card's own padding |

Both of the wide ones are thresholds below which something else gets *worse*
rather than merely smaller, which is why they are derived and not chosen. 6b
states the failure at each in numbers.

---

## 6b. The rail, the dock, and the two thresholds

Two rules in this interface exist only above a width, and both widths are
arithmetic rather than taste. The common fact underneath them is that
`--page-max` is measured: the widest table on the page - the sniper profile,
sixteen columns - needs 1351px of content, and with the page's two 24px gutters
that is a 1399px page. Everything below is that number, rearranged.

### The rail, from 1572px

Above 1572 the tab strip leaves the top bar and becomes a fixed column down the
left edge, 172px wide, and `body` is padded left by exactly that. The bar keeps
the brand and the status corner, and `.bwrap` and `.page` go on centring
themselves on what is left.

The rail costs the table nothing only when the width left for the page is still
1399 or more: 1399 + 172 = 1571. Below that it takes width the table was using.
Measured at 1440 with the rail on, the page drops to 1268 and the table's box to
1218 against the 1331 the row needs, so the Launches table grows a horizontal
scrollbar in the default window of a 1440 laptop. That is the whole reason the
threshold is 1572 and not something rounder.

Below it, the strip is a second sticky band under the bar and the page is
byte-for-byte what it was before the redesign had a rail at all, which is what
makes the change free at every narrower width. Two consequences are written
into the tokens rather than into the sheets: `--chrome-h` becomes two bands
tall, because the anchor offset, the strip, the filter bar and the dock all
measure from the chrome and any two of them disagreeing parks one behind
another; and `--rail-w` becomes 0, which is what lets `#toast` centre itself on
the page with one declaration instead of a second breakpoint.

The rail needs a `z-index` above the sticky table header and below the bar, and
it has one, not because the two overlap but because "they do not overlap at any
width" is a claim that stops being true the first time a column is added.

### The dock, from 2124px

Above 2124 the coin panel stops being an overlay and becomes a column beside
the table. The table stays live: the second click test in `_recon/_ui_fit.js`
clicks another row and reads which token the panel is showing, which is the
difference between a dock and an overlay dressed as one.

The arithmetic is the page plus the panel plus one gutter, and the window has
to hold all of it without either side losing anything: 1400 + 480 + 24 = 1904
of content, plus the rail and two 24px gutters, is 2124. While the panel is
open the spine's `max-width` is raised to that same 1904 and its right padding
becomes `--dock-w + --s5`, so the dock is the last column of the spine rather
than something beside it - and the top bar's own spine, `.bwrap`, is raised to
match, or the brand would stop lining up with the table under it. The
alternative was an offset that grows with the window, which is what the dock's
right edge is already doing: `max(--s5, (100vw - rail - page - dock - s5) / 2)`.

At 3440 - the width it was built and measured at - the block is 1904 wide with
682px of empty canvas either side of it, symmetric, and the table's box grows
from 1352 to 1376 because the spine it sits in grew.

Three things in the script have to know they are docked, and they are the
complete list of what the redesign changed in `static/js`: `body`'s overflow is
only locked when the panel is an overlay, Tab is only trapped inside it when it
is an overlay, and a click on another row swaps the panel's token instead of
being swallowed as a click outside a modal. The threshold is written twice -
once in `card.css` as a media query and once in `coin.js` as a `matchMedia` -
because a media query cannot read a custom property, and 6b is the place that
records they must agree.

### What deliberately did not come across from the reference

- **A buy button in each row.** This application has no swap. A button that
  buys nothing is a lie told in the interface.
- **A global search in the bar.** There is no `/api/search`; every tab has its
  own box because every tab searches a different table.
- **Icons in the rail.** The project has no icon system at all - `.mark` is a
  square and the sort arrows are drawn with borders. A rail item gets a small
  `::before` mark instead, hollow at rest and filled when active.
- **A table with its own height and its own scrollbar.** The infinite lists on
  Handles and Sniper ride viewport-rooted sentinels. A height-bounded scroller
  would stop those sentinels intersecting the window and the next page would
  never be asked for - silently, with no error.

---

## 7. Hard constraints

- **No external resources.** No web font, no CDN, no icon library, no image
  host. `--sans` and `--mono` are system stacks. The only remote URLs on the
  page are the ones already there (the RPC endpoint and token logos through
  the app's own image proxy). An offline dashboard has to render.
- **No build step.** Plain CSS, plain classic scripts. No preprocessor, no
  postcss, no `@import` of another local sheet (each sheet is linked
  separately so one failing does not take the rest down).
- **The markup and the JavaScript are a contract, and the redesign spent
  exactly one withdrawal from it.** Class names and ids belong to
  `static/js/*.js`: if a class you need does not exist, style what does exist;
  if that is genuinely impossible, say so in your report and leave it. Two
  edits are on the record as already made, and nothing else may join them.
  1. `static/index.html`: `<nav class="tabs">` moved out of
     `header.topbar > div.bwrap` to between the header and `main.page`. Not
     cosmetic - `.topbar` carries a `backdrop-filter`, which makes it the
     containing block for `position:fixed` descendants, so a rail left inside
     it would position against the bar instead of the window. No id, class or
     attribute changed, and no module in `static/js` reads `.tabs`, `.bwrap`,
     `.brand` or `.mark`, so nothing observes the move.
  2. `static/js/coin.js` (eight places) and `static/js/keys.js` (one line).
     Each is a case where the panel being a column rather than an overlay
     changes the right answer, and every one of them asks the same question -
     `coinDocked()`, the 2124 threshold the sheets use. In `coin.js`:
     `coinDocked()` itself; `coinLock()`, because the page is only locked and
     `aria-modal` only true while the panel is over it; the `matchMedia`
     listener on that threshold, because a window can cross it with the panel
     open and the two directions need opposite things done; the call to
     `coinLock` on open; the `focus()` on the card, which must not move the
     keyboard off the list the panel is beside; the `#coin-card` scroll reset,
     because docked the card is the scroller; the Tab trap in `coinKeydown`,
     which would otherwise hold every row behind a click; and the
     click-outside-closes branch, where falling through is what makes the panel
     follow the table instead of swallowing the row that was just clicked. In
     `keys.js`, the guard that mutes the number keys, `/` and `r` while a panel
     is open now only applies to the overlay.
- **Plain hyphens only.** No em dash, no en dash, anywhere - including in CSS
  comments. This is a standing rule on this project.
- **No `!important`,** except the one `[hidden]` rule that already needs it.
- Keep selectors flat. One class is the target. Avoid `>` chains deeper than
  two and never use an id as a style hook where a class exists.

---

## 8. File ownership

One file per person. Do not write outside your own file.

| file | owns |
|---|---|
| `css/tokens.css` | the tokens. Locked - read it, do not edit it |
| `css/base.css` | reset, `html`/`body`, type defaults, links, `.mono` `.dim` `.dim2` `.pos` `.neg` `.grow` `.inline` `.row`, `.empty` `.err` `.hint`, the reduced-motion block |
| `css/shell.css` | the top bar, brand, `.tabs`/`.tab`, `#poll-ctl`, `.page`, `#foot`, `#toast` |
| `css/controls.css` | `input` `select` `button` `textarea`, `.ctl`, `label.chk` `.lblchk`, `.ghost` `.b-p` `.b-d` `.lg`, `.rng`, `.rowbtn` `.mini`, `.badge` (`.ok` `.go` `.bad`) |
| `css/table.css` | `table` `th` `td` `.tw`, row states (`.new` `tr.snip` `tr.hi` `.wrow` `.det` `.detin` `.ctab`), sort affordances |
| `css/cells.css` | `.tok` `.logo` `.nm` `.sym`, `.age` (`.fresh` `.hot`), `.bar` `.pct`, `.pill` (`.g` `.r`), `.lb` + the label family, `.links` `.lnk-x` `.soc`, `.addr`, `.cp`, `.rep.hi`, `.nmbtn` |
| `css/stats.css` | `.hero`, `.stat` `.k` `.v`, `.cstat` (the card's stat grid, since it is the same component at a smaller size), `.stack` `.legend` `.dot` |
| `css/card.css` | `.cwrap` `.ccard` `.chd` `.cmeta` `.csec` `.cstat` `.cempty`, `.cskel` `.sk`, `.cclose` |
| `css/modal.css` | `.wwrap` `.wcard` `.whd` `.wclose` `.wbody` `.wfoot`, `.wme` `.wname` `.waddr` `.wico` `.wstate` (`.ok`) `.wlist` `.wrow` `.wsep` `.wnote` (`.err`) `.wsub` `.wmeta` |
| `css/chart.css` | `.chart-svg` and every class inside it, `.cread` `.clab`, `.gs0` `.gs1` (the gradient stops), `.k-price` `.k-buy` `.k-sell` `.k-launch` (the legend swatches) |
| `css/forms.css` | `.box` `.grid` `.f` `.kv` `.exrow` `.prow`, `.warnbox` `.dangerbox` `.okbox`, `.lblchk` |

`modal.css` had no owner in this table for as long as it existed, which is why
the wallet dialog drifted: it is the one overlay that is still a centred card
rather than a docked column, and the difference is deliberate - a dialog is
something you answer, and an answer should be in front of you. It takes the
panel surface, the panel radius and the same control height as everything else.

Load order in `index.html` is tokens, base, shell, controls, table, cells,
stats, card, modal, chart, forms. Later files may rely on earlier ones for
defaults but must not restyle another file's classes.

---

## 9. Before you report done

Run these and put the output in your report:

```sh
# 1. your file parses and every token you used exists
python _recon/check_css.py

# 2. no raw colours, no em dashes, no !important outside the allowed one
grep -nE "#[0-9a-fA-F]{3,8}|rgba?\(" static/css/YOURFILE.css   # expect none
grep -nP "\x{2014}|\x{2013}" static/css/YOURFILE.css  # expect none
grep -n "!important" static/css/YOURFILE.css                   # expect none

# 3. anything you claim about layout, measured rather than asserted
node _recon/shot.js 'http://127.0.0.1:8787/#launches' _recon/x.png 3000 1680 1000 @_recon/_ui_fit.js
node _recon/shot.js 'http://127.0.0.1:8787/#launches' _recon/x.png 3000 3440 1440 @_recon/_ui_fit.js
```

Step 3 is not ceremony. Three of the defects this interface was built through
were invisible to every check above it and visible in one number: the rail
costing the table 112px at 1440, the table's box being 4px short after a 1px
border, and a metric strip wrapping to a row that did not fill its last cell.
Two of those are in 6b, and the third is in `stats.css`. A stylesheet that
parses is not a layout that fits.

Then say, in three sentences, what the thing you styled now looks like and
what you deliberately did not do. If you left a class unstyled, name it.

---

## 8b. Classes the rewritten markup adds

The body was rewritten before the sheets were written, so a few names in
`_recon/classes.txt` are not in the table above. They belong to these files:

| file | also owns |
|---|---|
| `css/base.css` | `.skip` (the skip link), `.mt1` `.mt2` `.mt3` `.mt4` (the only spacing utilities, 4/8/12/16px top margin), `.num` (a right-aligned numeric column), `.pos` `.neg` |
| `css/shell.css` | `.topbar` `.bwrap` (the sticky bar and its centred inner spine), `.mark` (the 8px brand square), `.status` (the right-hand group of badges) |
| `css/controls.css` | `.ok` and `.go` (badge modifiers: alive, clickable), `.w-num` `.w-addr` `.w-label` `.w-key` (fixed input widths, 72 / 340 / 180 / 470px; the key field is wider because a private key is 66 characters and wraps badly) |
| `css/stats.css` | `.stack .ph` (the pre-first-render placeholder bar) |
| `css/cells.css` | `.logo.ph` (the letter shown when a logo is missing or fails) |
| `css/card.css` | `.csec-hd` (a section heading row inside the card), `.xprof` `.xban` `.xhd` `.xav` `.xwho` `.xnm` `.xhd2` `.xstats` `.xn` `.xbio` `.xlinks` (the X profile card) and `.fhead` `.fsent` `.fav` (the followings list), see 8g |
| `css/chart.css` | `.cx` `.cxdot` `.cxg` (the crosshair, its dot, its group), `.gline` (a gridline), `.line` `.area` (the price path and its fill), `.hi` (the highlight), `.lm` `.lmdot` (the launch marker) |
| `css/forms.css` | `.kstep` `.kn` `.kd` (one line of a transfer plan on the Wallets tab: the leg, what is happening to it, and the figure it is about; the state is the `data-st` attribute, not a class, because the same line is redrawn as its leg moves) |

`.on` is set by two files on three different parents: `.tab.on` in `shell.css`
(the active tab), and `th.on` (the sorted column) with `tr.on` (the row whose
detail panel is open, on the Wallets tab) in `table.css`. None of them may write
a bare `.on` rule, and neither file may write the other's parent.

`.ph` is likewise the one name two files touch, and only ever through a different
parent: `.logo.ph` in `cells.css`, `.stack .ph` in `stats.css`. Do not write a
bare `.ph` rule in either.

---

## 8c. What the implementation changed, and why

Three things in the tables above turned out to be wrong once the code was read
properly, and the sheets were written against what the code does rather than
what the table assumed:

- **`.hi` is not a chart highlight.** `snipeRowClass` in `cells.js` returns
  `"snip hi"` for a bundled launch or a known repeat buyer, and `snipers.js`
  puts it on a `.rep` span. Nothing in the chart uses it. It is now a row state
  in `table.css` (`tr.hi`) and a cell state in `cells.css` (`.rep.hi`), and
  neither file writes a bare `.hi` - the same rule `.on` and `.ph` already
  followed.
- **`.cstat` is the stat grid, not a card component.** It is `.stat` at a
  smaller size, so it lives in `stats.css` beside the class it wraps.
- **`.k` and `.v` are used twice.** Once inside `.stat` (a big number and its
  label) and once inside `.kv` (a cost row). They are only ever written with
  their parent, `.stat .k` / `.kv .k`, so the two cannot collide. A bare `.k`
  rule would silently restyle both.

`js/keys.js` was added after the sheets were written: the numbered tab keys,
`/` to focus the active tab's search box, and `r` to re-read the panel. It owns
no CSS and no markup beyond the `aria-keyshortcuts` on the tabs.

The label colours moved out of `fmt.js` and into `tokens.css` as `--lb-*`,
reached through a `data-lb` attribute. They used to be a hex map in JS, which
meant the pills read the stylesheet and the legend read the script, and the
legend kept the old palette's pink for a release after everything else had been
repainted. There are now no colour literals anywhere in `static/js`.

---

## 8d. Marking a row

A `bundled` or `sniped` row is the one thing in the tables that has to be found
without being read, so it is the one place the palette raises its voice. The
marking is three things at once, and only one of them is a colour:

1. a tinted wash across the row,
2. a saturated bar on the leading edge,
3. a filled pill in the label column.

A greyscale screenshot keeps the bar and the pill, and a reader who cannot
separate red from amber keeps them too. That is why the wash is only about a
tenth of the hue: it sits under eleven columns of figures, and a fill strong
enough to see from across the room is also strong enough to stop the eye
comparing a column downwards.

**The wash is painted on the `tr`, not on the `td`.** A per-cell background
restarts at every column edge, so eleven of them in a line read as stripes
rather than as one marked row. It runs strong at the leading edge and falls
away to nothing by four fifths across, which leaves the figures on the right
on the plain canvas.

**The hue comes from the label; the intensity comes from severity.** The row
carries `data-lb` - the same attribute the legend and the stacked bar already
use - and `hi` only makes the tint heavier. Keying the colour off the class
alone was wrong: `snipeRowClass` returns `snip hi` both for a bundled launch
and for a snipe by a wallet already known to do it, so a red row sat under an
amber pill that said `sniped`. A row that lies about what it is costs more
than an unmarked row does.

## 8e. A stylesheet can lose a block without looking wrong

`tokens.css` had seventeen tokens written after the `html` rule with no `:root`
around them. Declarations outside a block are not a block the browser can use,
so every one was dropped on parse: the label palette, the row washes, the
sticky bar's backdrop, the scrim and the chart gradient were all absent, and
the file still read as correct. The symptom was a table of colourless labels
that looked like a design choice.

`_recon/check_css.py` missed it because it looked for the text `--lb-sniped:`
anywhere in the file and called the token defined. It now splits a sheet into
runs at every brace and only counts a declaration found at depth one or deeper,
reporting anything at depth zero as dropped. Any change to `tokens.css` should
be read against that check, not against the file looking plausible.

## 8f. The links row is filled

Section 4 gives the white fill to a primary button and the active tab. The link
row at the end of a table row now has it too, at the person's request, and it is
the same argument: `X` `TG` `WEB` `copy` and `BLINK COPY` are the only things in
that table that are not a figure, and every figure around them is white-on-black
text to be compared down a column. A wash behind them was enough to give the rank
a shape but not enough to say which column is the one you press.

Two boundaries hold it in place. The rule is written as `.links a.soc` and
`.links .ghost`, never as `.ghost` - that class is every per-row action on the
page, including the `copy` chips in Sniped, Volume, Snipers, Wallet and Wallets,
and those are not this row. And `.lnk-x`, the span a social field that is prose
rather than a URL renders as, is deliberately left dim: it is not a link and
must not be filled like one. A filled chip is a promise that pressing it does
something, and that is the one chip in the row that pressing does nothing to.

The ink is `--pill-ink`, the same black the filled snipe pills use, so a filled
chip and a filled pill are inked alike. The hover resets `border-color` to
transparent because `controls.css` raises it to `--line-2`, which draws a dark
ring around a white fill and reads as the chip changing size.

---

## 8g. The X card and the followings list

The Handles tab gained a profile card and a list under the table. Both are
`card.css`, and neither introduces a colour: the card is built from the same
surfaces as the coin card and the list is a `table.tw` with the same `th`/`td`
and row states as every other table on the page. The names that are genuinely new
are the card's frame and its parts, plus three for the list.

`.xprof` is the frame: `--bg-1`, a `--line` border, `--r-md`, and
`overflow:hidden` for one reason - the banner is a full-bleed band and the corner
radius has to clip it. `.xban` is that band, 96px painted as a `background` with
`center/cover` so a wide banner crops instead of letterboxing. It is hidden
outright when the account has none, because an empty strip reads as a picture
that failed to load.

The banner url is set through `element.style.backgroundImage` and not written
into a `style` attribute, and that is a shape decision rather than a shortcut: a
url that came out of a profile field can contain a quote, and a url interpolated
into an attribute closes the string and turns the rest into attributes. Set as a
property it never becomes markup. The same reasoning is why the url goes through
`encodeURIComponent` before it reaches `/img`.

`.xav` is the avatar's frame: 64px, `--r-full`, a `--line-2` border, and
`margin-top:-34px`, which pulls it up over the band. The border is not decoration
- with no banner there is nothing to overlap, and the picture would otherwise sit
on the card's own surface with no edge to separate it from it.

`.fav` is the avatar itself, and it is deliberately one class at two sizes: it is
the `<img>` inside `.xav` in the card (sized to fill the frame) and it is the
whole avatar in a list row at 24px. Roundness is `--r-full` in both, which makes
an avatar the only round thing on this page - a face in a square reads as a logo.
`.fav.ph` is the placeholder for an account with no avatar or one whose image
failed, and it is written as `.fav.ph`, never as a bare `.ph`, by the rule in 8b
that `.ph` is the one name two files already touch.

`.fhead` is the block's own header, holding the two buttons and the progress
badge, and `.fsent` is the sentinel the scroll observer watches. It is the last
thing in the block, after the table and before the empty state, and it has a
`min-height` on purpose: an empty div with no box never intersects, so a sentinel
with no height would simply never fire.

The verdict column is not a new component. A handle with launches renders through
the `.pill` family the snipe labels use, taking `.pill.r` above two, and a handle
with none gets the dim `-` that a zero gets everywhere else on the page. Nothing
in that column is filled, because nothing in it is pressed: the row's click
target is the handle, which is a link. This section adds no exception to the `.on`
and `.ph` rules in 8b.

The list is unbounded in length and bounded in memory. Rows are appended to the
end of the table a hundred at a time as they arrive, and nothing already rendered
is redrawn. That is a layout constraint as much as a data one: re-rendering the
table on each batch would re-run the image lookup for every avatar above it and
reflow the page under the reader's scroll position, which is the exact experience
the batching exists to avoid.

Two sort selects and a `show all` button sit in `.fhead`'s `.ctl`, and they are
ordinary `select` and `button` elements taking the page's existing control
styles - the toolbar is a row that wraps, and three more controls in it are a
layout the page already has everywhere else. What is worth stating is the one
case where the table IS rebuilt: when a walk ends while a sort is selected. The
rows on screen were ordered against a list that has since grown, so appending to
them would freeze an order that no longer holds, and the honest repair is to
empty the table and ask for the order again. The same reset happens when the
reader picks a different order. Both are the reader's own action landing, not a
background batch arriving, which is why they do not break the rule above.

The `Launched` cell gained a second line: the launch pill on top and the distinct
wallet count under it in the dim mono that a secondary number gets everywhere
else. Two numbers in one cell rather than a new column, because only one of them
is a count of tokens - the other is a count of wallets - and a column heading has
to name the thing it counts. The column is wider for it (150px) and still not a
new component: `.pill`, `.dim2` and `.mono` are all existing names.

The page loop can now be asked for a repaint while a page is already in flight,
because changing the order and pressing show-all are both one click and neither
waits for the last request. Two rules came out of that, and both are about the
same lie: a table drawn in one order under a select that says another.

- A response that comes back from before an order change is **dropped**, not
  appended. It was asked for in the previous order, so its rows are ranked
  against a list that is no longer the one on screen. `HF.gen` counts repaints
  from the top and the response carries the count it was sent under; this is the
  same shape as the existing `HF.who !== who` check, which drops a page that
  belongs to a handle the box has since left.
- A repaint that cannot start yet is **remembered**, not dropped. `pump()` is
  guarded by `HF.busy` and returns early when a request is in flight, so without
  this the click would be lost and the select would sit on an order nobody
  applied. `HF.pending` is a standing request for a repaint, so the re-arm in
  `finally` checks it before the walk poll and before the show-all continue, and
  does not guard it by handle: the box moving to another account is a repaint
  too, and the likeliest one to have been dropped.
