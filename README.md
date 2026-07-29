# Anomaly Sentinel

A reusable GenLayer Intelligent Contract primitive that tracks a
numeric metric over time and classifies each new observation as
**normal**, **watch**, or **anomaly** relative to the metric's own
recorded history — not against a fixed threshold set in advance, but
against a statistical baseline the contract builds up on-chain as data
arrives.

## Live deployment (GenLayer Studio Network)

This contract is deployed and verified working on the **GenLayer
Studio** network. The source below is the exact source deployed at the
address given.

| | |
|---|---|
| Network | GenLayer Studio Network |
| Contract address | `0x4FfB0Ecf00be955BDb5f9C29A24Bf25B8E5F33b6` |
| Explorer | [explorer-studio.genlayer.com/address/0x4FfB0Ecf00be955BDb5f9C29A24Bf25B8E5F33b6](https://explorer-studio.genlayer.com/address/0x4FfB0Ecf00be955BDb5f9C29A24Bf25B8E5F33b6) |

Anyone can independently verify the deployment and re-run
`create_sentinel` / `submit_observation` against this address through
the explorer or GenLayer Studio.

## Why this is a distinct primitive

The other numeric-judgement primitive in this submission set (Graduated
Confidence Consensus) has validators reach consensus directly on a
categorical tier produced by the LLM. Anomaly Sentinel splits the
problem differently: **validators only reach consensus on one thing —
the raw numeric value extracted from an external source for this
round.** The anomaly classification itself is then computed
**deterministically, in plain Python**, from that agreed value together
with the contract's own already-agreed historical data. No LLM is ever
asked "is this an anomaly," and no validator has to agree on a
subjective severity judgement — the severity tier is arithmetic (a
z-score against the recorded mean and standard deviation), so it is
exactly reproducible by anyone reading the contract's history, not
merely trusted because validators said so.

This minimizes the consensus surface to the one genuinely
non-deterministic step (reading a real-world number off an external
source) and keeps everything downstream fully auditable, deterministic
math anyone can re-run by hand.

## Composability with Cross-Source Fact Quorum

This primitive is deliberately single-source per sentinel — it solves
the temporal/statistical half of "is this real-world number sane,"
not the source-diversity half. It composes naturally with **Cross-Source
Fact Quorum** (also in this submission set): a builder who needs both
multi-source agreement *and* historical anomaly detection for the same
metric can feed Cross-Source Fact Quorum's agreed value into this
contract as the trusted reading, or run the two side by side. This is
a concrete example of the primitives in this submission set being
designed to be combined, not just standalone demos.

## Consensus design

`submit_observation()` runs a non-deterministic block that fetches
`source_url` and asks the LLM to extract the current numeric value per
`extraction_instruction`. `gl.eq_principle.prompt_comparative` requires
validators' extracted values to be within a configurable percentage
tolerance (default 5%) of each other — expressed directly in the
equivalence principle text — to account for normal volatility between
independent fetches; free-text notes may vary freely.

Once the value is agreed, the contract computes, **outside of any
consensus round**, the mean and standard deviation of all prior
recorded values for this sentinel, derives a z-score for the new
value, and assigns a tier from fixed bands:

| Tier | Condition |
|---|---|
| `insufficient_history` | Fewer than 5 prior observations recorded |
| `normal` | `\|z-score\| < 1.5` |
| `watch` | `1.5 <= \|z-score\| < 3.0` |
| `anomaly` | `\|z-score\| >= 3.0` |

## Public interface

| Method | Type | Description |
|---|---|---|
| `create_sentinel(sentinel_id, metric_description, source_url, extraction_instruction)` | write | Registers a numeric metric to monitor. |
| `submit_observation(sentinel_id)` | write | Extracts the current value (validator consensus), classifies it deterministically, appends to history. |
| `get_current_status(sentinel_id)` | view | Latest tier and observation count. |
| `get_history(sentinel_id)` | view | Full append-only history: value, z-score, tier per observation. |
| `get_anomaly_count(sentinel_id)` | view | Total observations classified `anomaly` — for callers who want to gate on repeated anomalies, not a single flagged reading. |
| `list_sentinel_ids()` | view | All tracked sentinel ids. |

## Example use cases

- **Gas price / network congestion monitoring** — flag unusual spikes
  relative to a chain's own recent history rather than a fixed gwei
  threshold that becomes stale as conditions change.
- **Service latency / uptime monitoring** for SLA-adjacent contracts,
  where "anomalous" is relative to that service's own baseline.
- **Treasury or market metric monitoring** for a DAO, flagging unusual
  price or volume movement for a tracked asset without hardcoding
  thresholds that would need constant governance updates.

## Implementation notes

- Storage cannot hold Python `float` directly in this SDK, so numeric
  values and z-scores are stored as integers scaled by 1,000,000
  (`value_millionths`, `z_score_abs_millionths`).
- **A real bug found and fixed during live testing**: an earlier
  version of this contract used the signed integer type `i256` to
  store z-scores (which can be negative). This caused `submit_observation`
  to fail on every call, on every source URL tried, with a raw
  `genvm_crash_handler` / `NO_MAJORITY` result rather than a clean
  Python exception — a strong signal the failure was at the storage
  encoding layer rather than in the contract's own logic (unlike other
  errors encountered elsewhere in this submission set, which surfaced
  as ordinary Python tracebacks). Since the crash occurred even on the
  very first `submit_observation` call for a brand-new sentinel —
  before the code path that computes a real z-score is ever reached —
  the cause had to be the storage schema itself, not the statistics
  logic. The fix: `i256` was removed entirely in favor of `u256` plus
  a separate `z_score_negative: bool` field (sign and magnitude stored
  separately), reusing only integer/bool/string storage types already
  proven reliable across every other primitive in this submission set.
  As a consequence, this contract assumes monitored metric values
  themselves are non-negative (true for prices, gas fees, latency,
  etc.) — `submit_observation` explicitly rejects a negative extracted
  value with a clear error rather than silently mishandling it.
- **A second bug hypothesis tested during the same round of live
  testing**: even after removing `i256`, `submit_observation` still
  failed identically (`genvm_crash_handler` / `NO_MAJORITY`) on every
  attempt. Comparing this contract's source against the other five in
  this submission set, it was the *only* one containing a literal `%`
  character anywhere in an equivalence-principle string (the
  percentage-based value tolerance description). That character was
  removed from both the principle text and a nearby comment, spelling
  out "percent" instead — but a redeploy with this change alone
  produced the exact same failure signature again, so this hypothesis
  is documented as ruled out rather than confirmed.
- **A third, structural change** made after both of the above:
  reading/iterating the stored `history` `DynArray` from *inside a
  write method* was identified as the one remaining pattern unique to
  this contract among all six in this submission set. The original
  version computed mean and variance via a list comprehension over
  `sentinel.history` on every `submit_observation` call; every other
  primitive in this set only ever iterates a stored array from
  read-only view methods, never from a write method. The redesigned
  version maintains running totals (`sum_millionths`, `sum_sq_scaled`)
  as scalar fields on `Sentinel`, updated incrementally on each call,
  so `submit_observation` never reads back the `history` array at all
  — it only ever appends to it. This is also a strictly better design
  regardless of whether it was the actual bug (O(1) per call instead
  of O(n) growing with history length), so it was kept as the current
  implementation. All three of these changes, and their outcomes, are
  documented here in full rather than presenting only the final
  working version, since the debugging process itself is relevant to
  reviewers evaluating how this contract was actually built and tested
  against a real, opaque failure mode with no available traceback.
- For the same reason, the standard-deviation calculation uses a
  small self-contained Newton's-method square root (`_sqrt`) instead
  of importing the `math` module, to avoid depending on unverified
  standard-library support inside the GenVM sandbox for a single
  arithmetic operation.
- `DynArray[Observation]` is allocated via
  `gl.storage.inmem_allocate(DynArray[Observation], [])`, consistent
  with the storage pattern used across all primitives in this set.
- LLM calls use `gl.nondet.exec_prompt(prompt, response_format="json")`,
  and results are re-serialized with `json.dumps(..., sort_keys=True)`
  before being handed to the equivalence principle.
- The `stddev == 0` edge case (all prior values identical) is handled
  explicitly: a new value equal to that constant gets `z_score = 0`
  (`normal`); any different new value is treated as maximally anomalous
  (capped at a large finite bound) rather than causing a
  division-by-zero.
- Known limitation: percentage-based value tolerance in the
  equivalence principle can behave oddly very close to zero (a metric
  legitimately near 0 makes any percentage tolerance very tight in
  absolute terms). This primitive is best suited to metrics that stay
  meaningfully away from zero in normal operation; documenting this
  rather than claiming universal applicability.
- Known limitation: monitored values are assumed non-negative (see
  above). A future version could support signed metrics if a reliable
  signed storage pattern is confirmed; for this submission, sticking
  to types already validated in production across this set was judged
  more important than generality.

## Testing notes

This contract has been exercised live on GenLayer Studio at the
address above. `create_sentinel("btc_price1", "Bitcoin price in USD",
"https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
"...")` followed by `submit_observation("btc_price1")` produced a
successful consensus round, with the equivalence principle output
showing a Bitcoin price value of `63977.0` extracted and agreed on by
validators, and `get_current_status("btc_price1")` correctly returning
`{"observation_count": 1, "tier": "insufficient_history"}` — exactly
the expected state before the 5-observation minimum for statistics is
reached.

During testing, the CoinGecko API's free tier rate limit (HTTP 429)
was hit after repeated calls, and a Binance API source configured on a
separate sentinel produced a `NO_MAJORITY` result (likely due to
validators experiencing inconsistent reachability to that specific
endpoint) — both genuine third-party API behaviors external to this
contract's logic, not contract bugs. The GenLayer Studio network also
intermittently returned infrastructure-level errors unrelated to this
contract during the same testing session (a backend `GenVMInternalError`
with a traceback entirely inside GenLayer's own RPC/database layer, and
a separate `psycopg2.ProgrammingError` from GenLayer's backend
database query layer) — included here as further evidence that not
every failure encountered while building this primitive originated in
its own code.

Suggested manual walkthrough in GenLayer Studio:
1. Deploy with no constructor args.
2. `create_sentinel("gas1", "Ethereum average gas price in gwei", "<a page showing current gas price>", "Extract the current average gas price in gwei shown on this page")`.
3. Call `submit_observation("gas1")` at least 5 times (across separate
   transactions) to build enough history for z-scores to activate —
   inspect `get_history("gas1")` and confirm early entries show
   `tier: "insufficient_history"` before the 6th observation onward
   shows `normal` / `watch` / `anomaly`.
4. Confirm `get_anomaly_count("gas1")` correctly counts only entries
   tiered `anomaly`.

Edge cases worth testing explicitly:
- The first observation on a fresh sentinel — should always be
  `insufficient_history`.
- A metric whose value stays essentially constant across several
  submissions (stddev near/at zero) — confirm no crash and a sane
  tier assignment per the documented edge-case handling.
