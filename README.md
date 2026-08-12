<div align="center">

# Anomaly Sentinel

### Statistical Anomaly Detection on GenLayer

[![GenLayer](https://img.shields.io/badge/GenLayer-Intelligent%20Contract-6b5bff)](https://docs.genlayer.com)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-12%20passed-22c55e)](https://docs.genlayer.com/developers/intelligent-contracts/tooling-setup)

A reusable primitive that tracks a numeric metric over time and classifies each
new observation as **normal**, **watch**, or **anomaly** — not against a fixed
threshold set in advance, but against a statistical baseline (mean/standard
deviation) the contract builds up on-chain as data arrives.

</div>

---

## Why this is a genuine primitive

Every "is this number unusual?" problem — gas prices, service latency, treasury
or market metrics — needs a baseline that reflects *current* conditions, not a
stale hardcoded cutoff. This contract provides it as a composable on-chain
building block:

1. **Consensus only on the raw number.** Validators reach consensus on the one
   genuinely non-deterministic step: reading a real-world numeric value off an
   external source. The anomaly classification itself is never asked of an LLM —
   it is pure arithmetic (a z-score against the recorded history), exactly
   reproducible by anyone reading the contract's history.
2. **Consensus-stable classifications.** A naive "validators must agree within
   5%" lets a value near a tier boundary silently flip `normal`↔`watch`↔`anomaly`
   between agreeing nodes. Here the validator also requires that classifying the
   leader's reading and its own reading against the **same history** yields the
   **same tier**, and that appending either value keeps the future running
   baseline statistically equivalent. Whatever value is accepted, the recorded
   classification is the one every agreeing node derived.
3. **Append-only, deterministic history.** Tiers are recomputed from agreed
   values + agreed state only — no subjective severity judgement is ever encoded.

---

## How it works

### The lifecycle

```mermaid
flowchart LR
    A[create_sentinel] --> B[no_data]
    B -->|submit_observation| C[insufficient_history]
    C -->|5+ prior points| D[normal / watch / anomaly]
```

### Consensus design

`submit_observation` runs a leader/validator pair (`gl.vm.run_nondet_unsafe`):

1. **Extraction** — the leader fetches `source_url`, asks the LLM
   (`gl.nondet.exec_prompt`, JSON) to extract the metric's current numeric value,
   and returns it as an integer scaled by 1,000,000 (`value_millionths`).
2. **Validation** — every validator independently re-fetches and re-extracts, and
   only accepts a reading when all of the following hold:
   - **(a) Same value** — within the value tolerance (5%) of the leader's reading.
   - **(b) Same tier** — classifying the leader's and its own reading against the
     same recorded history yields the same `normal`/`watch`/`anomaly` tier. This
     is the property that answers "can validators agree on a number but record
     different classifications?" — no: a boundary-crossing value inside the
     tolerance is rejected outright.
   - **(c) Same future statistics** — appending either reading keeps the running
     mean statistically equivalent (within the same tolerance), so the baseline
     every future observation is judged against does not depend on which agreeing
     node's reading won.
3. **Classification** — once the value is agreed, the contract deterministically
   computes the mean and standard deviation of all **prior** recorded values,
   derives a z-score, and assigns a tier from fixed bands. No further consensus
   round is needed because this is pure arithmetic over already-agreed state.

| Tier | Condition |
|---|---|
| `insufficient_history` | Fewer than 5 prior observations recorded |
| `normal` | `\|z-score\| < 1.5` |
| `watch` | `1.5 <= \|z-score\| < 3.0` |
| `anomaly` | `\|z-score\| >= 3.0` |

The `stddev == 0` edge case (all prior values identical) is handled explicitly:
an equal new value gets `z_score = 0` (`normal`); any different value is treated
as maximally anomalous instead of dividing by zero.

### Security & threat model

- **No hallucinated classification** — the LLM only extracts a number; the tier is
  deterministic arithmetic anyone can re-derive by hand from `get_history`.
- **No boundary-flip smuggling** — the validator's tier-equivalence check (b)
  guarantees every accepted reading maps to the same recorded tier across all
  agreeing nodes.
- **Bounded inputs** — URL/id/instruction length caps, page fetch cap (4,000
  chars), `MAX_VALUE` sanity cap on extracted magnitudes, and explicit rejection
  of negative values (metrics are assumed non-negative: prices, fees, latency).

---

## Public API

| Kind | Methods |
|---|---|
| Write (2) | `create_sentinel` · `submit_observation` |
| View (4) | `get_current_status` · `get_history` · `get_anomaly_count` · `list_sentinel_ids` |

## Quick start

```python
contract.create_sentinel(
    "btc_price1",
    "Bitcoin price in USD",
    "https://api.github.com/users/genlayerlabs",   # any stable numeric source
    "Extract the value of the 'followers' field from the JSON",
)

contract.submit_observation("btc_price1")   # fetch → validator consensus → classify
contract.get_current_status("btc_price1")   # {"tier": ..., "observation_count": n}
contract.get_history("btc_price1")          # append-only history
contract.get_anomaly_count("btc_price1")    # number of anomaly-tagged entries
```

---

## Verification

**12 deterministic unit tests** cover: sentinel registration and validation,
the `insufficient_history` → `normal`/`watch`/`anomaly` lifecycle, the `stddev ==
0` constant-series edge case, unknown-sentinel errors, the validator logic
(agree / beyond tolerance / tier-flip rejected / same-tier-within-tolerance
accepted / non-numeric and out-of-range rejections), and all view methods.

**Deployed & tested on GenLayer Studio** (real consensus, web and LLM) via
`scripts/onchain_test.py`:

- **Reference deployment:**
  [`0x0dD4870705Adbf1a12f6C8ccE87a75d6D9e3AEeF`](https://explorer-studio.genlayer.com/address/0x0dD4870705Adbf1a12f6C8ccE87a75d6D9e3AEeF)
- `create_sentinel` then **6 consecutive `submit_observation` rounds all reached
  consensus** on the real GitHub follower count (`327.0`), proving the leader/
  validator pair works on-chain with live web fetches and LLM extraction.
- `get_history` shows exactly the expected lifecycle: five
  `insufficient_history` entries, then the 6th classified `normal` once 5 prior
  points activate the z-score bands. `get_anomaly_count` correctly returns 0.
- The test source was chosen to stay inside the contract's 4,000-char page
  truncation: a fuller GitHub response puts `stargazers_count` past the cutoff
  and the LLM then extracts a default `0` — a data-source concern, not a
  contract bug, and it is why the script uses the compact `users` endpoint.

```bash
genvm-lint check contracts/anomaly_sentinel.py
pytest tests/test_anomaly_sentinel.py
```

---

## Demo (live on studionet)

The deployed contract is a working demo you can inspect and replay:

- **Contract:** [`0x0dD4870705Adbf1a12f6C8ccE87a75d6D9e3AEeF`](https://explorer-studio.genlayer.com/address/0x0dD4870705Adbf1a12f6C8ccE87a75d6D9e3AEeF)

The reference run — sentinel tracking the genlayerlabs GitHub follower count:

| Step | Transaction | Value recorded | Tier |
|---|---|---|---|
| Create sentinel | [`0x68eacca7…e967b`](https://explorer-studio.genlayer.com/tx/0x68eacca7d1c062abf8694ed0989e4fb1320bd9be3b6e417ec7ef17acaf3e967b) | — | `no_data` |
| Submit 1 | [`0xda928824…8c96`](https://explorer-studio.genlayer.com/tx/0xda928824b8415af564639c5f3f2498efa670b0f8164030f279161d4b99a08c96) | 327.0 | `insufficient_history` |
| Submit 2 | [`0x8a1534bd…72b6d`](https://explorer-studio.genlayer.com/tx/0x8a1534bd54546b99b065e65d941a5bea9036acae280d30e9e2c57557a9072b6d) | 327.0 | `insufficient_history` |
| Submit 3 | [`0xa597f4d9…b611`](https://explorer-studio.genlayer.com/tx/0xa597f4d9d4d4cb74b887f0a512cc027efc4b01fc2ba15293c1705dfd8669b611) | 327.0 | `insufficient_history` |
| Submit 4 | [`0x931f73fb…e281e`](https://explorer-studio.genlayer.com/tx/0x931f73fb1334d7755e953464a29a3a2a2667b82e938b4c18162139c5f5ce281e) | 327.0 | `insufficient_history` |
| Submit 5 | [`0x957e16d3…a4c5b`](https://explorer-studio.genlayer.com/tx/0x957e16d3e28e35f063ed1ad1411152324a8d4a837df0af8bc3539dd54c6a4c5b) | 327.0 | `insufficient_history` |
| Submit 6 | [`0x7e126e4c…7390`](https://explorer-studio.genlayer.com/tx/0x7e126e4cffdaf5096c5d764eb685fc10f7a316e50176d42a3bc4d2aa3d8c7390) | 327.0 | `normal` |

Every hash above is a real, finalized transaction you can open in the explorer.

Replay the demo yourself against the live contract:

```bash
python scripts/onchain_test.py --address 0x0dD4870705Adbf1a12f6C8ccE87a75d6D9e3AEeF
```

---

## Implementation notes

- **Floats are not calldata-encodable** in this SDK, so no `float` ever crosses a
  consensus or view boundary. `extract_value` returns the reading as an integer
  scaled by 1,000,000 (`value_millionths`), and storage uses `u256` scaled
  integers throughout (`Observation` carries `value_millionths`,
  `z_score_abs_millionths`, and a separate `z_score_negative` bool).
- A live-testing bug found and fixed: the original leader result returned a
  plain `float` (`{"value": 63653.0}`), which crashed with `TypeError: not
  calldata encodable ... float`. Scaling to an integer in the leader/validator
  return fixed it. The same constraint applies to view returns — `get_history`
  therefore exposes the scaled integers plus the z-score sign, so clients
  unscale (divide by 1,000,000) rather than the contract returning floats.
- Running totals (`sum_millionths`, `sum_sq_scaled`) are maintained
  incrementally on each submit, so `submit_observation` never reads back the
  history array (O(1) per call).
- The square root uses a small Newton's-method helper (`_sqrt`) rather than the
  `math` module, avoiding an unverified standard-library dependency inside the
  sandbox.
- Known limitation: percentage-based tolerance behaves oddly very close to zero
  (a metric legitimately near 0 makes any percentage tolerance very tight in
  absolute terms). This primitive is best suited to metrics that stay
  meaningfully away from zero.
- Known limitation: monitored values are assumed non-negative (prices, gas fees,
  latency); `submit_observation` rejects a negative extracted value explicitly.

---

## Development

Run from the repo root:

```bash
pip install -r requirements.txt
genvm-lint check contracts/anomaly_sentinel.py   # lint + SDK validation (6 methods)
pytest tests/test_anomaly_sentinel.py  # 12 tests
```

To deploy and exercise it on GenLayer Studio (free fees), use
`scripts/onchain_test.py`. The contract constructor takes no arguments; there is
no privileged owner role.

---

## References

- [GenLayer docs — Equivalence Principle](https://docs.genlayer.com/developers/intelligent-contracts/equivalence-principle)
- [GenLayer docs — Crafting Prompts](https://docs.genlayer.com/developers/intelligent-contracts/crafting-prompts)
- [GenLayer docs — Non-deterministic features](https://docs.genlayer.com/developers/intelligent-contracts/features/nondeterministic-functions)
