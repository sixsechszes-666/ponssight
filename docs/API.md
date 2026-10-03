# ponssight API contract

Frozen interface between the backend and `static/index.html`. Any change here
must be made on both sides in the same commit.

Amounts in `quote` units are raw integers divided by `10**quote_decimals`,
read off the token row - the trades table carries no decimals of its own, and
taking them from there silently defaulted to 18, which is wrong by 10^12 for
every six-decimal pair.
already converted to human floats. `*_quote` is denominated in the token's pair
asset (ETH or a tokenised equity); `*_usd` is that times the quote's USD rate,
and is `null` when no rate is known. Timestamps are unix seconds.

## POST /api/upload

Body `{"data": "<data url or bare base64>", "name": "<optional>"}`. Returns
`{"url": "ipfs://<cid>", "where": "ipfs", "note": "", "bytes": n, "format":
"png", "width": w, "height": h, "original_bytes": n}`.

The image is opened and re-encoded to png on this machine first, so what is
published is pixels and nothing else; a gif is passed through so it keeps
animating. It is then pinned to ipfs and the uri that comes back is a cid, not
a gateway url - a gateway url names one server, and the uri a token carries
cannot be changed after launch.

`where` is `"ipfs"` when it was pinned and `"host"` when the anonymous file
host was used instead, which happens when no key is configured or the pin
failed; `note` carries the reason in that case. Both are public and permanent.
The pinning key lives only on the server and is never sent to the page.

## GET /api/tokens

Existing launch list, unchanged, plus two new optional objects per row: `snipe`
and `vol`. All existing query params keep working (`sort`, `limit`, `offset`,
`q`, `quote`, `min_progress`, `graduated`, `since_minutes`, `min_volume_usd`).
New sort values: `volume`, `snipe`.

`min_volume_usd` is a floor on the row's own `vol.volume_usd` - dollars, not
quote units, and the same number the Volume column shows. It is applied in the
query rather than to the returned page, so the limit still means "the top N of
the table above the floor" instead of "the ones above the floor out of the
newest N". A token that has never traded has no volume and is below every
floor; omit the parameter for no floor at all.

```json
{
  "now": 1789366491,
  "count": 200,
  "eth_usd": 2521.5,
  "tokens": [ { "...existing fields...": "...",
    "snipe": {
      "label": "sniped",
      "score": 87,
      "delta": 1,
      "bundled": false,
      "first_buy_block": 62587001,
      "first_buy_ts": 1789366400,
      "first_buyer": "0xabc...",
      "first_buy_quote": 0.42,
      "first_buy_share": 10.0,
      "early_buyers": 3,
      "bot_hits": 41,
      "bot": true
    },
    "vol": {
      "buys": 128, "sells": 44,
      "volume_quote": 3.91, "volume_usd": 9858.2,
      "net_quote": 2.10,
      "buyers": 61,
      "last_trade_ts": 1789366480
    }
  } ]
}
```

Every row also carries what the token has paid the wallet that launched it:

```json
{ "creator_fees": 0.082696, "creator_fees_paid": "82696000000000000",
  "creator_tax_balance": "0", "creator_tax_bps": 200 }
```

`creator_fees` is the figure the page shows: everything the curve has swept
out to the creator over the token's life, plus what it is holding for them
right now, in whole units of the pair token - the same units as `*_quote`,
not the raw base units `curve_config` uses. `null` means no curve state has
been read for this token yet, which is not the same as a token that has paid
nothing: that one is `0`.

`creator_fees_paid` and `creator_tax_balance` are the two halves in raw base
units, as strings, because they run past what a double holds exactly.
`creator_tax_bps` is the creator tax the launch was made with, in basis
points, so `200` is 2%.

`snipe` is always an object: a token the trades indexer has not reached yet
gets `label: "unknown"` and a `null` score rather than a `null` verdict, so
"not looked at yet" can never be mistaken for "not sniped". `vol` is `null`
for a token with no indexed trades at all.

`first_buy_quote` is in whole quote units, like every other `*_quote` field.
`first_buy_share` is that spend as a percentage of the graduation threshold,
so it is a ratio of two raw values and needs no scaling. `bot_hits` is how
many launches that wallet was the first *outside* buyer of, inside the early
window. `bot` is true when `bot_hits >= 5`.

`first_buyer` and `first_buy_quote` describe the wallet that raced in, which
is the first early buy by someone other than the deployer. When nobody else
bought inside the early window they fall back to whoever bought first at all,
which is why `slow` rows can carry a buy thousands of blocks after the
launch.

### Snipe labels

| label | meaning |
|---|---|
| `bundled` | the launch transaction itself carried a buy, and the launcher's own wallet got the tokens (a dev buy) |
| `sniped` | someone else bought atomically with the launch, or within `SNIPE_BLOCKS` (2) blocks of it |
| `early` | first outside buy within `EARLY_BLOCKS` (10) blocks |
| `slow` | first buy later than that |
| `none` | nobody ever bought (fully indexed, no trades at all) |
| `unknown` | the history walk has not reached this launch yet |

Only the launcher can put a buy inside the launch transaction, so a bundle is
never a third party unless the tokens were sent to a different wallet, which
is the `sniped` case. A buy in the launch transaction is therefore never
counted as a race, and the deployer is excluded from the race too.

`score` combines block delta, the first buyer's share of the graduation
threshold, how few distinct wallets bought early, and whether the buyer is a
known repeat first-buyer.

## GET /api/volume

Ranked by traded volume over a window.

Params: `window` = `5m` | `1h` | `6h` | `24h` | `all` (default `1h`),
`sort` = `volume` | `buys` | `sells` | `net` | `newest` (default `volume`),
`limit` (default 100, max 500), `offset`, `q`, `quote`, `graduated`.

```json
{ "now": 1789366491, "window": "1h", "count": 100, "tokens": [ {
  "address": "0x...", "symbol": "ROAM", "name": "Roam", "logo": "ipfs://...",
  "quote_symbol": "ETH", "quote_decimals": 18, "usd_price": 2521.5,
  "volume_quote": 12.4, "volume_usd": 31274.0, "buys": 210, "sells": 88,
  "net_quote": 7.1, "buyers": 96, "trades": 298,
  "last_trade_ts": 1789366480, "last_trade_age": 11,
  "age_seconds": 900, "launch_ts": 1789365591,
  "price_quote": 0.0000301, "mcap_usd": 41000.0, "progress_pct": 31.4,
  "graduated": false, "snipe_label": "sniped", "snipe_score": 87
} ] }
```

## GET /api/snipes

Params: `limit` (100, max 500), `offset`, `sort` = `score` | `delta` |
`volume` | `newest` (default `score`), `only_bots` (0/1), `max_delta`,
`since_minutes`, `q`, `quote`, `labels` (comma-separated, any of `bundled`,
`sniped`, `early`, `slow`; omitted means all of them).

This is the feed behind the Sniped tab. `labels=sniped` is the set someone
else actually got in ahead of the field on; `labels=sniped,bundled` adds the
launches where only the deployer bought atomically.

```json
{ "now": 1789366491, "count": 60, "total": 4098, "stats": {
    "bundled": 6767, "sniped": 4098, "early": 3728, "slow": 1157,
    "indexed": 16326, "bot_wallets": 107 },
  "tokens": [ { "...same row shape as /api/volume...": "...",
                "first_buyer": "0xabc...",
                "snipe": { "...as in /api/tokens...": "..." } } ] }
```

`total` is the number of rows the filters matched, before `limit`/`offset`;
`count` is how many are in this page.

### The chips add up

Every filter that removes rows - `since_minutes`, `q`, `quote`, `only_bots`,
`max_delta` - runs *before* `stats` is counted, so the four label counts always
sum to `total` when no `labels` is given, and the named ones sum to `total`
when one is. A chip whose number disagrees with the table it opens is worse
than no chip, so that ordering is a contract rather than an implementation
detail. `labels` is the only filter applied after counting, because a chip has
to keep reporting its own size so the chips next to it still mean something.

`indexed` and `bot_wallets` are the exceptions: they describe the whole table
rather than the filtered set, and are copied through untouched. `indexed` is
how far the history walk has reached, which is why `total` can be smaller than
it: a token the walk has not reached yet has no verdict to show, so it is
counted in `indexed` and appears in no chip. That state is `unknown` and it is
deliberately not a chip, since a filter that opens an empty table would be a
small lie. `none` - a token nobody ever bought - cannot occur here at all,
because this feed joins on the trades table.

The dev buy being *inside* the launch transaction is the norm on this chain
rather than an anomaly - the launch router performs it as part of the launch
call - which is why `bundled` is the largest bucket and why the deployer is
excluded from the race rather than counted as the fastest sniper. See
[Snipe labels](#snipe-labels) for what each label means; that table is the
single definition every endpoint shares.

## GET /api/snipers

The wallets doing the sniping, one row per wallet. Params: `limit` (100, max
500), `offset`, `sort` = `hits` | `recent` | `address` (default `hits`),
`min_hits` (1), `per_wallet` (12, max 50, 0 means none).

```json
{ "now": 1789368300, "count": 6,
  "stats": { "wallets": 550, "repeat": 59, "launches": 1599 },
  "snipers": [
    { "address": "0xFEFE...CC1b", "hits": 421, "last_ts": 1789368180,
      "bot": true,
      "spent_quote": 363.68, "pnl_quote": 530.22, "quote_symbol": "AAPL",
      "spent_usd": 26209.18, "pnl_usd": 530.22, "pnl_no_rate": 0,
      "pnl_partial": false, "pnl_unknown": 0,
      "tokens": [
        { "address": "0x..", "symbol": "Openfang", "name": "Openfang",
          "logo": "https://..", "launch_ts": 1789367000,
          "launch_block": 62601000, "block": 62601003, "ts": 1789367001,
          "delta": 3, "quote": 0.0222604, "quote_symbol": "ETH",
          "share": 1.23, "bundled": false,
          "buy_tx": "0x..", "launch_tx": "0x..",
          "quote_usd": 2520.1,
          "position": { "net_tokens": 0.0, "spent": 0.0025,
                        "received": 0.002037, "realized": -0.000462,
                        "unrealized": null, "pnl": -0.000462,
                        "roi_pct": -18.49, "known": true,
                        "spent_usd": 6.30, "received_usd": 5.14,
                        "realized_usd": -1.16, "unrealized_usd": null,
                        "pnl_usd": -1.16 } } ] } ] }
```

`hits` is a lifetime count, so it is normally larger than `tokens.length`:
the list is capped by `per_wallet`. `share` is the buy as a percentage of that
launch's graduation threshold and is `null` when the threshold is unknown.
`repeat` counts wallets at or above `BOT_HITS_MIN` (5).

### The wallet's profit, and why it comes in two units

`pnl_quote`, `spent_quote` and `quote_symbol` cover **every** launch the wallet
was first into, not the page of them a table renders, or the busiest wallets
would look like the smallest ones. They sum to the per-token `position.pnl`
figures only when the whole list is expanded, which is what `per_wallet=0` is
for.

`pnl_quote` is a sum in no single unit, and `quote_symbol` is a lie beside it.
A wallet's races are not all in the same pair: of the launches raced in a
recent window, 17,536 were quoted in ETH, 4,034 in USDG, 860 in NVDA, 605 in
SPCX and the rest across equities and cbBTC. Adding ETH to USDG to NVDA gives
a number with no denominator, and `quote_symbol` can only name the first row's
- which is why it reads `AAPL` above while most of the sum is not AAPL at all.
It is kept because it is the raw on-chain figure, but **`pnl_usd` is the one
to show**. A wallet's launches are unlike each other in a way its dollars are
not.

`pnl_usd` and `spent_usd` are converted per token, through the quote asset's
current USD rate. That is the rate as of now applied to a figure built over
hours, the same approximation `mcap_usd` already makes. A launch whose pair
has no known rate is left out of the USD totals and counted in
`pnl_no_rate`, so a missing rate can never read as a zero profit.

`pnl_partial` is true when some of the wallet's launches could not be valued
at all - a graduated token has no curve left to price against - and
`pnl_unknown` says how many. When it is true the USD total is a floor, not a
total.

Each token row carries `quote_usd`, that row's pair rate, and a `position`
object holding the wallet's history in that one token: `spent`, `received`,
`realized`, `unrealized`, `pnl` and `roi_pct`, each with a `_usd` twin, plus
`net_tokens` and `known`. The quote figures are what the wallet actually paid
and received on chain; the USD twins are what let one row be compared with the
next. `pnl` is `null` when nothing is known about that position, and `known`
says which. For a graduated token `unrealized` is `null` and `pnl` is the
realised half alone, a true lower bound. See
[the card endpoint](#get-apitokenaddresscard) for the same object described in
full.

A wallet qualifies by being the first buy by someone other than the deployer
in the launch window, which is the same rule the `sniped` label uses, so the
tokens listed here and the tokens in `/api/snipes?labels=sniped` are the same
set viewed two ways: by coin and by wallet.

## GET /api/token/{address}

Single token, full detail. Everything in `/api/tokens` plus:

```json
{ "...": "...",
  "curve": "0x...", "deployer": "0x...", "launch_block": 62243885,
  "launch_tx": "0x...",
  "curve_config": { "phantom_quote": 1.68e18, "graduation_threshold": 4.2e18,
                    "real_quote_reserve": 1.0,
                    "sellable_tokens": "714285714285714285714285715",
                    "reserved_tokens": "285714285714285714285714285",
                    "total_supply": "1000000000000000000000000000",
                    "graduated": false },
  "snipe": { "label": "bundled", "score": 1, "delta": 0,
             "first_buyer": "0x9134...4b9e", "first_buy_quote": 0.005,
             "first_buy_share": 0.119,
             "early_buys": [ { "block": 62243885, "buyer": "0x...",
                               "quote": 0.005, "tokens": 2.64e24,
                               "ts": 1789331660, "tx": "0x..." } ] },
  "vol": { "...": "..." }
}
```

This is the call that answers "was this one sniped" for a single coin, so the
verdict comes with the early buys it was made from rather than asking you to
take the label on faith.

**Units.** Two different conventions live on this response, and the split is
deliberate:

- anything named `*_quote` is a trade amount in **whole quote units**, divided
  by `10**quote_decimals` (`first_buy_quote: 0.005`, `vol.volume_quote`), and
  `first_buy_share` is a percentage;
- `curve_config` holds **raw base units**, the same as the flat
  `phantom_quote` / `graduation_threshold` / `real_quote_reserve` /
  `total_supply` fields beside it. A reserve is not a trade amount, and the
  browser scales these with a big-number formatter. The supply fields stay
  strings because they run past what a double holds exactly.

## GET /api/token/{address}/card

Everything the coin card draws, in one request. Params: `hours` = `0` | `1` |
`6` | `24` (default `6`, `0` means the token's whole life), `holders`
(default 50, max 500).

The card is opened by a click and its range selector refetches the same call,
so the token, its verdict, the chart and the holder list come back together
rather than in three round trips. They are built from different tables and do
not agree to the second anyway - the chart is bucketed, the holders are read
live - so splitting them would buy nothing but latency.

```json
{ "now": 1789372000,
  "token": { "...exactly what /api/token/{address} returns...": "...",
             "snipe": { "...as in /api/tokens...": "...",
                        "first_buyer_position": {
                          "net_tokens": 0.0, "spent": 0.0025,
                          "received": 0.002037, "realized": -0.000462,
                          "unrealized": null, "pnl": -0.000462,
                          "roi_pct": -18.49, "known": true } } },
  "chart": {
    "bucket_sec": 60, "hours": 6,
    "points": [ { "t": 1789371060, "price": 1.832e-09,
                  "volume_quote": 0.0956, "buys": 5, "sells": 1 } ],
    "launch_ts": 1789370400, "launch_price_quote": 1.68e-09,
    "price_quote": 1.681e-09, "min_price_quote": 1.642e-09,
    "max_price_quote": 1.832e-09, "last_price_quote": 1.832e-09,
    "volume_quote": 0.182, "buys": 6, "sells": 5, "graduated": false },
  "holders": {
    "count": 1, "shown": 1, "supply": 1000000000.0,
    "held": 351572.78, "held_pct": 0.0351,
    "rows": [ {
      "wallet": "0x3207...309b", "tokens": 351572.78, "share_pct": 0.0351,
      "spent_quote": 0.000606, "received_quote": 0.0,
      "realized_quote": -0.000606, "unrealized_quote": 0.000591,
      "pnl_quote": -1.49e-05, "roi_pct": -2.47, "pnl_known": true,
      "first_ts": 1789371472, "last_ts": 1789371472,
      "is_deployer": false, "is_first_buyer": false,
      "bot_hits": 0, "bot": false } ] } }
```

**The chart.** One point per `CANDLE_BUCKET_SEC` (60 s) bucket that saw a
trade, so a quiet token has gaps rather than a flat line drawn over nothing.
`price` is the bucket's `quote / tokens`, which is a VWAP rather than a close:
the bucket stores sums, and the ratio of the sums is exactly the volume
weighted average, where a close would need every trade kept individually. It
is `null` for a bucket whose token amount rounds to zero, and every price is
in whole quote units per whole token, the same as `token.price_quote`.

A token's first trade is rarely at its launch, and a chart starting there
hides the jump the card exists to show, so the launch is prepended as a
synthetic point when the first real bucket is later. It is not a guess:
nothing is sold at launch, so virtual tokens is the whole supply and the ratio
is the phantom reserve over it. `min_price_quote` / `max_price_quote` /
`last_price_quote` are read from the points (launch point included);
`volume_quote`, `buys` and `sells` are the window's totals. A graduated token
has no curve price, so `token.price_quote` is `null` while the chart, which is
built from trades, still has its history.

**The holder list.** One row per wallet that still holds, largest first.
`count` is how many exist and can exceed `shown`, which is how many rows came
back; `held` and `held_pct` cover the returned rows only, so when `count`
exceeds `shown` the percentage is of a partial list and is labelled as such in
the UI. `tokens` is the wallet's **net** position - bought minus sold - so a
wallet that sold everything is left out of the list entirely rather than
appearing with zero.

`spent_quote` and `received_quote` are lifetime totals in and out;
`realized_quote` is the difference; `unrealized_quote` values the net position
against the current curve reserves; `pnl_quote` is the sum and `roi_pct` is
that over `spent_quote`. All in whole quote units.

`pnl_known` is the field that says whether the number means anything. For a
graduated token the curve is gone, so the unrealised half cannot be computed
from anything in the database and `unrealized_quote` is `null` with
`pnl_quote` reporting the realised half alone, which is a true lower bound
rather than the whole story. `pnl_known: false` marks those rows. For a token
whose history the position backfill has not reached there is no row at all,
and `pnl_quote` is `null` - reporting zeros would read as broke even, which is
a claim, where the truth is that nothing is known.

`is_deployer` and `is_first_buyer` name the token's launcher and the wallet
that won the race. `bot_hits` is that wallet's lifetime count of launches it
was first into, and `bot` is `bot_hits >= BOT_HITS_MIN` (5).

`token.snipe.first_buyer_position` is the same shape without the identity
fields, and is present only when the race's winner has trade history. It is
looked up by address rather than read out of the holder list, because a sniper
that took its profit and left is no longer a holder and that is exactly the
case worth showing. Like every other position it is in whole quote units, and
its `pnl` is `null` when nothing is known.

## GET /api/launch/config

Params: `config_id` (default 0).

`supply`, `phantom_quote` and `graduation_threshold` are raw base units, as
above; `launch_fee` is whole ETH with `launch_fee_wei` beside it.

```json
{ "config_id": 0, "supply": 1000000000000000000000000000, "curve_fee_bps": 100,
  "phantom_quote": 1680000000000000000,
  "graduation_threshold": 4200000000000000000,
  "pool_fee": 0, "tick_spacing": 200, "enabled": true,
  "launch_fee": 0.0005, "launch_fee_wei": "500000000000000",
  "native_quote": "0x0000000000000000000000000000000000000000",
  "launch_enabled": true }
```

## GET /api/launch/preview

Params: `config_id`, `pair_token`. Returns the `expectedEconomics` bytes32 that
`launchToken` requires, read live from the factory.

```json
{ "config_id": 0, "pair_token": "0x000...0",
  "expected_economics": "0xa9fc75d420333ffe660e8fa32c74c3aa41c1fda4bf23d3a39b6bc22a1f8b1ca7",
  "launch_fee_wei": "500000000000000" }
```

## Copy plans

A copy plan is a draft of a token to launch, pre-filled from a source token and
fully editable, with free-form extra fields.

`GET /api/copy` -> `{ "plans": [ plan, ... ] }`

```json
{ "id": 7, "created_at": 1789366491, "updated_at": 1789366491,
  "status": "draft",
  "source_address": "0x...", "source_symbol": "ROAM",
  "tx_hash": null, "error": null,
  "fields": {
    "name": "Roam", "symbol": "ROAM", "description": "...",
    "logo": "ipfs://...", "twitter": "", "telegram": "", "discord": "",
    "website": "", "farcaster": "",
    "creator_fee_recipient": "0x...", "creator_tax_bps": 0,
    "buyback_enabled": false, "launch_config_id": 0,
    "pair_token": "0x0000000000000000000000000000000000000000",
    "initial_buy_quote": 0.05,
    "salt": ""
  },
  "extra": [ { "label": "website mirror", "value": "https://..." } ],
  "notes": "free text" }
```

`status` is `draft` | `launched` | `archived`. `initial_buy_quote` is the
amount of the pair asset bought inside the launch transaction. A blank `salt`
is generated server side.

`POST /api/copy` body `{ source_address?, fields?, extra?, notes? }` ->
`{ "id": 7, "plan": { ... } }`

`PUT /api/copy/{id}` body `{ fields?, extra?, notes?, status? }` ->
`{ "plan": { ... } }`

`DELETE /api/copy/{id}` -> `{ "ok": true }`

`POST /api/copy/{id}/status` body `{ "status": "...", "tx_hash": "0x...", "error": "..." }`
-> `{ "plan": { ... } }`

Missing `fields` keys are filled from the source token server side.

## POST /api/launch/calldata

Body: a copy plan's `fields` (same keys). Returns an **unsigned** transaction
for the browser wallet to sign. The backend never holds a key.

```json
{ "to": "0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e",
  "data": "0xf35abbcf000...", "value": "500500000000000000",
  "value_eth": 0.5005, "launch_fee_eth": 0.0005,
  "initial_buy_eth": 0.5, "config_id": 0,
  "pair_token": "0x000...0", "salt": "0x...",
  "expected_economics": "0x...",
  "warnings": ["a token with this symbol already exists"] }
```

## POST /api/launch/send

The same launch as above, signed by a **stored** key instead of the browser
wallet. `{ "from", "to", "data", "value" }`, where `data` and `to` are exactly
what `/api/launch/calldata` returned and `value` is its `value` string.

**It signs the bytes it is given and never rebuilds them.** The salt is random
per build, so a launch assembled here would deploy a different token at a
different address than the confirm panel on the page just showed. This endpoint
exists to sign what was shown, which is why it takes no `fields`.

```json
{ "tx_hash": "0x...", "nonce": 7, "gas": 300000,
  "from": "0x...", "to": "0x...", "value_wei": "500500000000000000" }
```

Refusals, in the order they are checked:

- `423` - the vault is locked; unlock it on the Wallets tab and press again;
- `404` - `from` is not in the stored key list;
- `400` - `to` is neither the factory nor the router, `data` is not hex
  calldata, the selector in `data` does not belong to that `to` (the pair is
  checked together: the router with the factory's selector is an error, not a
  coincidence), `value` is not a whole number of wei, or `value` is above
  `2 * launch fee + PONS_MAX_INITIAL_BUY`;
- `502` - the gas estimate failed, or the node refused the broadcast.

Gas is estimated and signed with `PONS_GAS_HEADROOM` over the estimate; the fee
cap is `bridge.fee_cap(base + priority)`. The balance is deliberately not read -
an overspend is refused by the node, as it is on the wallet path. The nonce
comes from `pending`, remembered per process so that two presses inside one
block take two numbers instead of replacing each other. Neither the key, the
raw transaction, nor the calldata is logged or returned: the reply is the hash
and the numbers above.

## Wallet

`GET /api/wallet/{address}` -> read-only summary. The backend only reads.

```json
{ "address": "0x...", "eth_balance": 0.42, "eth_usd": 2521.5,
  "balance_usd": 1059.0, "early_buy_count": 12,
  "launched": [ { "address": "0x...", "symbol": "X", "name": "X",
                  "mcap_usd": 41000, "progress_pct": 31.4,
                  "graduated": false, "launch_ts": 1789365591,
                  "snipe_label": "bundled", "volume_usd": 9858.2 } ],
  "early_buys": [ { "address": "0x...", "symbol": "Y",
                    "first_buy_quote": 0.42, "delta": 1,
                    "launch_ts": 1789365591, "snipe_label": "sniped" } ] }
```

`GET /api/wallets` -> `{ "wallets": [ { "address", "label", "added_at" } ] }`

`POST /api/wallets` body `{ "address", "label"? }` -> saves to the watchlist

`DELETE /api/wallets/{address}` -> `{ "ok": true }`

## Key wallets

The Wallets tab's own store, and a different thing from the `wallets`
watchlist above: these carry a private key, and one endpoint here signs with
it. Addresses are checksummed and derived from the key on the way in, so no
endpoint takes a key in a path or a query.

**The key is stored encrypted**, in `key_wallets.secret` in `data/pons.db`, as a
Web3 Secret Storage v3 keystore under a passphrase the server holds in memory
only. A copy of the database without the passphrase contains no keys. See "The
vault" below and the README for what that does and does not protect against.
Every listing below returns a mask and never the key; the one reveal endpoint
is the only thing on the whole API that returns it.

`GET /api/keywallets` -> the list, which is what the tab polls.

```json
{ "locked": true,
  "wallets": [ {
  "address": "0x1234...abcd", "label": "burner 1",
  "secret_mask": "0x1234...cdef", "added_at": 1789366491,
  "rh_wei": "4200000000000000", "arb_wei": "0",
  "launched": 3, "early_buys": 12 } ],
  "chain": {
  "ttl": 60, "now": 1789366500, "running": false, "due": false,
  "next_at": 1789366552, "at": 1789366492, "age": 8,
  "rh":  { "at": 1789366492, "age": 8,  "error": "" },
  "arb": { "at": 1789366480, "age": 20, "error": "" } } }
```

`rh_wei` and `arb_wei` are decimal strings in wei, not floats in ether: this
tab exists to spend a balance down to the last wei, and a double with eighteen
decimals cannot say that. `arb_wei` is `null` when the Arbitrum node did not
answer, which is not the same as zero. `launched` and `early_buys` are counts
from the local index, so they cover what this machine has indexed and nothing
older.

`chain` is what the two balances are: a snapshot the server keeps on the side,
not something this request goes and reads. The rows above come out of SQLite
and are always there; these numbers arrive when a read has been done, and the
block is the page's account of when that was. `ttl` is `KEY_CHAIN_TTL` - how
long a reading is shown before the tab offers to take a new one - and `next_at`
is when that is, so `due` is the page's question and the server's arithmetic
rather than a comparison of two clocks. `running` is true while a read is in
flight, and `age` is how long ago the whole snapshot was taken.

`rh` and `arb` are separate because the two networks fail separately: each
carries its own `at`, its own `age`, and its own `error`, which is empty when
that half was read. Neither one's failure touches the other's number, so a
Robinhood node that is refusing does not erase a good Arbitrum balance and does
not make it look fresh either. This is why `rh_wei` can be `null` on a wallet
that does hold money: it means that node was asked and did not answer, and the
alternative - writing a zero - would read as an empty wallet rather than an
unanswered question. The number is a reading, and a reading that did not happen
is missing.

`POST /api/keywallets/chain` -> `{ "started": true, "chain": { ... } }`. Asks
for a fresh reading if one is due; `started` says whether this call began one.
The vault does not have to be unlocked and this is deliberate: it reads public
balances for addresses, opens no keystore, and the table it feeds is drawn
while the vault is locked. A call inside the TTL starts nothing and returns the
snapshot as it stands, which is what makes pressing refresh twice cost one
local request instead of two reads of the node. The read itself runs in the
background: both networks at once, because they are two hosts and reading them
in turn is the sum of two waits rather than the larger of them.

`locked` is the vault's state. It is true after every server restart, and the
page uses it to decide between showing the passphrase field and showing the
list's own controls.

`POST /api/keywallets` body `{ "key": "0x...", "label"? }` -> the whole list,
as above. The address is derived from the key; adding an address that is
already stored updates its label instead of duplicating it. A key that is not
a key - wrong length, wrong alphabet, no `0x` - is refused with 400 and one
message for every way of being wrong, so the response never says which part of
the key was wrong. **423 while the vault is locked**: a key cannot be written
without the passphrase that would encrypt it.

`PATCH /api/keywallets/{address}` body `{ "label": "..." }` -> the whole list.
An empty label clears the name. 404 when the address is not stored. Works while
the vault is locked.

`DELETE /api/keywallets/{address}` -> the whole list. 404 when not stored. This
removes the key from this machine and is not recoverable from anywhere else.
Works while the vault is locked: forgetting a key is always allowed to be
easier than keeping one.

`GET /api/keywallets/{address}/secret` -> `{ "address": "...", "secret":
"0x..." }`. The full key, for the reveal button and for nothing else. 404 when
the address is not stored, 423 while the vault is locked, and 409 when the
stored value does not open - which is what a corrupted blob or a passphrase
that was never the one looks like. Decrypting one key costs about 0.8 s of
scrypt, which is the point of it.

## The vault

The passphrase lives in the server process and nowhere else: not on disk, not
in a log, not in any response, and deliberately not in an environment variable
(a variable means the passphrase in a launcher script on disk, which is the
thing being avoided). Consequences worth knowing before calling these:

- **every restart locks it.** That is the feature, not an inconvenience.
- **it auto-locks after `PONS_KEY_IDLE_SEC`** (default 1800 s, `0` disables) of
  no key-touching call. The check runs before each such call rather than on a
  timer, so the state a caller sees is never stale.
- **the passphrase cannot be recovered.** Nothing here can open a key stored
  under a passphrase that was mistyped at setup: not this server, not MetaMask.

`POST /api/keywallets/unlock` body `{ "passphrase": "..." }` -> `{ "unlocked":
true, "fresh": false, "repaired": 0, "locked": false, "wallets": [...] }`.

`fresh` is true when the table was empty, which means the passphrase is being
**set** rather than checked - there is nothing to check it against, so the page
asks for it twice before calling this. `repaired` counts rows found in the
clear (a database restored from a backup made before the vault existed) that
were encrypted in place by this call.

- 400 when no passphrase is given at all.
- 401 with a sentence when the passphrase does not open a stored key. A wrong
  passphrase leaves the vault **shut**, so a failed unlock does not have to be
  undone.

`POST /api/keywallets/lock` -> the list, with `locked: true`. The passphrase is
dropped from memory; every key is still there and nothing is deleted.

**423** is returned by everything that touches a key while the vault is shut -
adding a key, revealing one, and `POST /api/bridge/sweep`, which is signed on
this machine with a stored key rather than by a browser wallet. The detail is
always `the vault is locked - unlock it first`, so a client can tell this apart
from a key that is genuinely missing.

## Transfer plan

The plan is built here and signed by the browser; the alternative below is
built and signed here. Both directions are relay deposits rather than direct
transfers, so a wallet that is being emptied is never sent to directly by the
wallet that is being paid.

`POST /api/bridge/plan` body `{ "direction", "from", "to", "leg"? }` ->
`{ "direction", "signer", "leg": { ... } }`.

| `direction` | `leg` | what it routes | who signs |
|---|---|---|---|
| `out` | 1 | `from` on Robinhood to `from` on Arbitrum | browser (`signer: "browser"`) |
| `out` | 2 | `from` on Arbitrum to `to` on Robinhood | browser |
| `back` | 1, 2 | the same two legs, paid by a stored key wallet | server (`signer: "server"`) |
| `return` | 1 | `from` on Arbitrum to `to` on Robinhood, one leg, no middle hop | browser |

Leg one always pays the address it started from; where the trip ends is leg
two. `return` is the single leg for money already sitting in Arbitrum, and it
is the one shape the other two directions refuse.

Refusals, all before any quote is asked for: 400 when `to` is `from` itself
(the fees are the only thing that would move), 404 when `back` names an address
that is not a stored key wallet, 400 for any other direction or leg number.
relay's own errors come back as 400 with relay's sentence in `detail`, which is
what `api()` in `static/js/ui.js` lifts into the page.

```json
{ "direction": "out", "signer": "browser", "leg": {
  "origin_chain_id": 4663, "dest_chain_id": 42161,
  "user": "0x...", "recipient": "0x...",
  "amount": "9995983351305760", "amount_eth": 0.00999598335130576,
  "value": "9995983351305760",
  "to": "0x4cd00e387622c35bddb9b4c962c136462338bc31",
  "data": "0x49290c1c...",
  "gas": 32713, "max_fee_per_gas": 101819200, "fee_cap": 122183040,
  "gas_cost": "3997430261", "gas_cost_eth": 3.997430261e-09,
  "balance_before": "10000000000000000",
  "balance_before_eth": 0.01,
  "left_estimate": "0",
  "relayer_fee": "0", "relayer_fee_eth": 0.0,
  "out_estimate": "9896023517792702", "out_estimate_eth": 0.009896023517792702,
  "out_min_eth": 0.0098, "seconds": 30,
  "request_id": "0x...", "settled": true } }
```

**`amount` is the whole balance minus the gas of this very transfer**, which is
the only way to leave nothing behind: the EVM refunds unused gas, so a literal
zero is unreachable and the remainder is that refund rather than a rounding
error. `settled` says whether the loop that solves for it converged - the
amount and the fee depend on each other, and the fee moves between two quotes.
`left_estimate` is what is expected to be left, and it is `0` on a settled leg.

`fee_cap` is relay's `max_fee_per_gas` plus a margin, and it is the fee the
transaction **must** be signed with: the amount was solved from that exact
number, so a wallet choosing its own higher fee produces a transaction the node
rejects for spending past the balance. `max_fee_per_gas` is kept beside it as
relay's own quote, for the record. `value` is the amount to send and is carried
in the transaction's value, not in its calldata.

`GET /api/bridge/status?requestId=` -> relay's own status for a deposit, passed
through so the page needs neither CORS nor relay's hostname. 400 with no
`requestId`, 502 when relay could not be reached.

`GET /api/bridge/balances?address=` -> `{ "address", "rh_wei", "arb_wei" }`,
both chains for one address in one request. This is what the runner polls while
it waits for a leg to land. Either side is `null` when that node did not
answer.

## Sweep

`POST /api/bridge/sweep` body `{ "from", "to" }` -> the run, as below. This is
the one endpoint in the app that spends with no wallet prompt: `from` must be a
stored key wallet and the leg is signed here with the key it was added with.
Refusals: 400 when `to` is `from`, 404 when `from` is not stored, 409 while
another sweep is running or when the stored value does not open, and 423 while
the vault is locked - this endpoint decrypts the key to sign, so it needs the
passphrase like the reveal button does.

```json
{ "id": "9f3c1a2b7d40", "state": "running",
  "from": "0x...", "to": "0x...",
  "steps": [ { "leg": 1, "state": "sent", "detail": "tx 0x...",
               "tx": "0x..." } ],
  "error": null, "final": {},
  "started_at": 1789366491, "updated_at": 1789366491 }
```

`state` is `running` | `done` | `pending` | `failed`. A step's `state` is
`quoting` | `signing` | `sent` | `bridging` | `filled` | `pending`.
`pending` is not a failure: the deposit is in and the solver has not filled it
yet, so the second leg is **not** started on a balance that has not landed and
the page shows the run as still in flight. Pressing start again is the retry.

`final` carries the balances read once the run stops - `rh_wei` and `arb_wei`
for the paying wallet, `to_rh_wei` for the receiving one - and is what the page
prints as the exact wei left behind.

`GET /api/bridge/sweep/{run_id}` -> the same object, 404 for an unknown id.
Runs live in memory and are not a table: the chain holds the outcome, every
step is retryable, and a run does not outlive the process pretending to be
state the database could resume from. The last 40 are kept.

## GET /api/handle

The launch verdict for a twitter handle, by the handle alone. `h` is anything a
person might paste: `poly_enjoyer`, `@poly_enjoyer`, `https://x.com/poly_enjoyer`,
`twitter.com/poly_enjoyer?ref=1`. `handles.norm` strips the scheme, the host and
the path, and `reserved` says whether what is left is a path rather than an
account (`x.com/i/search` resolves to `i`, which is reserved, and the answer is
`reserved: true` with nothing else).

```json
{ "query": "poly_enjoyer", "handle": "poly_enjoyer", "reserved": false,
  "summary": { "launches": 0, "deployers": 0, "symbols": 0,
               "first_ts": null, "last_ts": null },
  "coverage": { "tokens": 510818, "oldest_ts": 1785786647,
                "depth_blocks": 37475253 },
  "now": 1789561110, "tokens": [] }
```

This endpoint reads `token_handles` and never touches the network, deliberately:
it has to keep answering when X is down, unconfigured or rate limited. That is
why the profile and the followings list are separate endpoints rather than extra
fields here.

`launches` counts tokens whose `twitter` field claims this handle, which makes it
a claim and not an attribution: the field is free text typed by whoever launched.
`coverage` is the honest bound and is the same block the Handles tab prints.
All three fields are read from the rows rather than from the configuration, so
they describe how deep the index actually goes: `tokens` is what is held,
`oldest_ts` is the oldest launch in it and `depth_blocks` is that launch's
distance from the indexed tip. The floor is the block the factory was deployed
in, so once the history walk has finished `depth_blocks` is the launchpad's
whole life and "no launches" means "none since the launchpad began" rather than
"none lately" - but it is still a claim about this launchpad and about a free
text field, never about the chain or about a person.

## GET /api/x/profile

The account's profile plus the same launch verdict. `h` as above, `refresh=1` to
skip the cache. Served from `x_users` when the stored row is younger than
`PONS_X_PROFILE_TTL` (86400 s), otherwise fetched from X and stored.

```json
{ "query": "poly_enjoyer", "handle": "poly_enjoyer", "age": 4215,
  "source": "cache",
  "profile": { "id": "1540806109744840704", "handle": "poly_enjoyer",
               "name": "OMEGA", "bio": "| building in AI/web3 | ...",
               "followers": 1943, "following": 815, "statuses": 5604,
               "listed": null, "location": "EVM",
               "website": "https://polymarket.com?via=omega",
               "avatar": "https://pbs.twimg.com/profile_images/..",
               "banner": "https://pbs.twimg.com/profile_banners/..",
               "verified": 0, "blue": 0, "protected": 0,
               "created_ts": 1656191780, "fetched_at": 1789556389 },
  "summary": { "launches": 0, "deployers": 0, "symbols": 0,
               "first_ts": null, "last_ts": null },
  "coverage": { "tokens": 510819, "oldest_ts": 1785786647,
                "depth_blocks": 37475399 },
  "tokens": [],
  "list": { "owner_id": "1540806109744840704", "handle": "poly_enjoyer",
            "state": "ok", "cursor": "0", "pages": 5, "users": 815,
            "total": 815, "started_at": 1789555848, "done_at": 1789555854,
            "error": "" },
  "parsed": 815, "now": 1789561122 }
```

`source` is `x`, `cache`, or `cache-stale` when X could not be reached and the
stored row was served anyway. A failed refresh never hides the local verdict:
the launch half is computed from this machine's own index and arrives whatever
X says. `age` is the stored row's age in seconds. `list` and `parsed` are
present only when this account's followings have been walked at least once.

The profile is parsed by `xprofile.py`, not by the bot's `parsers.parse_user`,
because the current `UserByScreenName` answer no longer carries `legacy` and that
parser reads every counter out of it: reused here it would return `null` for
followers, following, bio, location, protected and verified. The ids, avatars and
banners come from `core` / `avatar` / `banner` / `profile_bio` /
`relationship_counts` / `verification`. `avatar` is stored at the size X gave it
(`_normal`); the card asks `/img` for a bigger one.

## POST /api/x/followings

Starts a walk of one account's followings. Body `{h, refresh?, max_pages?}`
(`max_pages` default `PONS_X_MAX_PAGES`, 25, clamped to 500). Returns the run:

```json
{ "id": "031cde48469f", "owner_id": "1540806109744840704",
  "handle": "poly_enjoyer", "state": "running", "pages": 0, "walked": 0,
  "parsed": 0, "cursor": "", "total": 815, "error": "",
  "started_at": 1789555848, "updated_at": 1789555848 }
```

Behind a POST and a button rather than triggered by the search box, because a
walk is twenty-five requests against somebody else's rate limit and a typo in a
handle should not spend them. One walk at a time: X's limits are per account and
the client is a single shared session, so a second walk would serialise behind
the first at page granularity while both claimed to be running. A second POST
while one is live is `409 already walking @X - stop it first`.

`total` comes from the profile, and it is the number the walk is measured
against: a walk that stops early leaves `parsed < total`, which is the only thing
that distinguishes a half-read list from a small one.

## GET /api/x/followings/{run_id}

Progress, not the list. Same object as above, plus `finished_at` once it is over.
`state` is `running`, `ok`, `stopped` or `error`. `ok` with a non-empty `error`
of `page limit reached` means the page budget ran out before X ran out of
followings. A stop that has been asked for but not yet honoured is still
`running`, because the page should keep polling through it rather than draw a
stopped list.

## GET /api/x/followings

One page of the stored list, and the endpoint the page actually polls. Params:
`h`, `offset` (0), `limit` (100, max 500), `sort`, `sort2` (empty).

```json
{ "handle": "poly_enjoyer", "owner_id": "1540806109744840704",
  "rows": [
    { "ord": 0, "target_id": "2093138700477640704", "seen_at": 1789555848,
      "handle": "yieldfields_rh", "name": "Yield Fields", "bio": "..",
      "followers": 22973, "following": 1, "avatar": "https://..",
      "banner": "https://..", "blue": 1, "verified": 0, "protected": 0,
      "website": "https://yieldfields.fun/", "location": "Rolling in the yield",
      "launches": 220, "deployers": 34,
      "top": { "address": "0x965993..C8AE", "symbol": "YIELD",
               "mcap_usd": 4256.44 } } ],
  "offset": 0, "limit": 100, "sort": "", "sort2": "",
  "parsed": 815, "total": 815, "state": "ok",
  "done_at": 1789555854, "error": "", "running": null, "now": 1789556532 }
```

Rows come back in `ord` order, which is the order X gave them. That ordering is
the reason the table is clustered on `(owner_id, ord)`: this list is read while
it is still being written, and appending a page must not move the rows already on
screen. `launches`, `deployers` and `top` are computed per batch (`handle_stats`
and `handles_for`, one indexed query per hundred handles), so a row carries its
verdict as soon as it appears rather than after the walk. `parsed` is what is in
the table right now, `total` what the profile said, `state` whether a walk is in
flight (`running` is the run id when one is). A list read with `parsed < total`
is a list that is not finished, and the page says so instead of presenting it as
complete.

### sort, sort2

Two keys, applied in that order, both from `store.FOLLOW_SORTS`:

`followers_desc` `followers_asc` `launches_desc` `launches_asc`
`deployers_desc` `deployers_asc`

The order is built over **every row the owner has** and only then cut into pages.
Sorting the hundred rows already downloaded would put somebody first who is not
first, which is why this is a server parameter rather than something the table
does in the browser.

An unknown name is dropped rather than refused - the same way an unknown volume
sort falls back to volume. `sort="nonsense"` is `ord` order;
`sort="nonsense"` with `sort2="followers_desc"` is `followers_desc`, because the
key that was understood is still applied. `sort` and `sort2` in the answer are
the keys that were actually used, so a caller can tell an order it asked for from
one it did not get. A row whose handle has no launches counts as zero, and a row
whose handle has no profile at all still comes back.

`launches` and `deployers` in a row come from the same `handle_stats` call that
built the order, so the number drawn next to a person is the number they were
sorted by rather than a second opinion about it. `launches` counts launches that
*claim* the handle - the twitter column is free text - and `deployers` counts the
distinct wallets that made them. `followers` is whatever X said when the account
was last read, so it is a snapshot, not a live figure.

Ties are broken by `ord` ascending, always. Without that the order would not be
total: the hundredth row of one request would not be the hundredth of the next,
and scrolling would repeat some people and skip others.

Reading this endpoint never calls X. That is what makes the second visit instant.

## POST /api/x/followings/{run_id}/stop

Asks a walk to stop at the next page boundary. Honoured between pages, not
mid-page, so the page being written when the button was pressed still lands.
Answers with the run, `state` still `running` until the walk notices. The result
is honest by construction: `state="stopped"` and `parsed < total`, which the page
renders as a stopped list reading "N of M", not as a complete one.

## GET /api/stats

The header counters: total indexed, enriched, queued, graduated, launches in
the last hour and day, last block, ETH/USD, chain id, factory.

The answer is held for `PONS_STATS_TTL` seconds (2 by default) and re-sent
unchanged inside that window. The page asks for it every five seconds and it
used to cost more than the interval: 601 ms of that was `COUNT(*) WHERE
graduated=1`, which had no index and scanned every token, plus a second of the
same shape elsewhere. With `idx_graduated` the count is 0-1 ms and the whole
answer is about 7 ms cold, so the cache is no longer load-bearing - it is
there so that a page and a tab open at once do not both pay for it. A cached
response is byte-identical to a fresh one; there is no staleness marker,
because two seconds is shorter than the poll.

## GET /img

Proxies and caches a token logo. `u` is `ipfs://`, `http(s)://` or a bare CID;
`w` is an optional width hint and is part of the cache key, so a resized
request is a separate entry rather than a wrong one.

The cache key is `sha1(u|w)` and the file lives in a subfolder named after the
first two characters of that hash. Entries written before the cache was
sharded are still in the top folder and are moved into their shard the first
time anything asks for them - served from where they are if the move fails,
because answering correctly beats answering tidily.

Served with `Cache-Control: public, max-age=86400`. Gateways are tried in
order, each with `PONS_IMAGE_TIMEOUT` (12 s), and the walk stops at the first
one that answers with something image-shaped. Answers: `400` for a uri that
resolves nowhere, `413` above `PONS_IMAGE_MAX_MB`, `502` when no gateway
answered. Pinned uploads from the Create tab (`data/pins`) are checked before
the cache and before any gateway, so showing your own logo never depends on
somebody else's rate limit.

The same module (`imgcache.py`) is what the background `logos` loop calls, so
the two share one definition of where an image lives and how to ask for it.
A logo the loop already fetched is a disk read by the time the browser asks.

## Static

`GET /` serves `static/index.html`. Tabs are client side and drive the
endpoints above. Launching a token is `window.ethereum` only: no library, no
key, no signing on the backend - the backend builds calldata and the wallet
signs it. Two things break that rule, both deliberately: the Wallets tab stores
private keys on this machine - encrypted under a passphrase the process holds in
memory, see "The vault" - and shows them, and `POST /api/bridge/sweep` signs
a leg with one of them instead of asking a wallet. Chain is 4663, with
`wallet_addEthereumChain` offered when the wallet does not know it; Arbitrum
(42161) is offered the same way for the transfers.
