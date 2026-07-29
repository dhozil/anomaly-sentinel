# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
Anomaly Sentinel
==================

A reusable GenLayer Intelligent Contract primitive that tracks a
numeric metric over time and classifies each new observation as
normal, watch, or anomalous relative to the metric's own recorded
history - not against a fixed threshold set in advance, but against a
statistical baseline the contract builds up on-chain as data arrives.

Why this is a different consensus shape
------------------------------------------
The other primitives in this submission set that involve numeric
judgement (Graduated Confidence Consensus) reach validator consensus
directly on a categorical tier produced by the LLM. Anomaly Sentinel
splits the problem differently: validators only need to reach
consensus on one thing - the raw numeric value extracted from an
external source for this round - and the anomaly classification itself
is then computed deterministically, in plain Python, from that agreed
value together with the contract's own already-agreed historical data.
No LLM is ever asked "is this an anomaly" and no validator has to
agree on a subjective judgement call about severity; the severity tier
is arithmetic (a z-score against the recorded mean and standard
deviation), so it is exactly reproducible by anyone reading the
contract's history, not just trusted because validators said so.

This is a meaningfully different reliability property: the consensus
surface is minimized to the one genuinely non-deterministic step
(reading a real-world number off an external source), and everything
downstream of that is fully auditable, deterministic math anyone can
re-run by hand from the stored history.

This primitive is deliberately single-source per sentinel - it solves
the temporal/statistical half of "is this real-world number sane"
rather than the source-diversity half. It composes naturally with
Cross-Source Fact Quorum (also in this submission set): a builder who
needs both multi-source agreement AND historical anomaly detection for
the same metric can feed Cross-Source Fact Quorum's agreed value into
this contract's `submit_observation` as the trusted source, or run
them side by side.

Consensus mechanism
--------------------
For every `submit_observation` call:
  1. A non-deterministic block fetches `source_url` and asks the LLM
     to extract the current numeric value of the tracked metric,
     following `extraction_instruction`.
  2. `gl.eq_principle.prompt_comparative` requires validators to agree
     the extracted value is equivalent - a percentage-based tolerance
     is stated explicitly in the principle text (values within a
     configurable percent of each other are treated as the same
     underlying reading, accounting for normal source volatility
     between validator fetches) - while free-text notes may vary
     freely.
  3. Once the value is agreed, the contract deterministically computes
     the mean and standard deviation of all PRIOR recorded values for
     this sentinel, derives a z-score for the new value, and assigns a
     tier (normal / watch / anomaly) from fixed, documented z-score
     bands. This step involves no further consensus round because it
     is pure arithmetic over state everyone already agrees on.
  4. The value, z-score, and tier are appended to an append-only
     history for the sentinel.
"""

from genlayer import *
from dataclasses import dataclass
import json

MIN_HISTORY_FOR_STATS = 5  # minimum prior points before z-scores are meaningful
WATCH_Z_THRESHOLD = 1.5
ANOMALY_Z_THRESHOLD = 3.0
VALUE_TOLERANCE_PERCENT = 5  # validators' extracted values may differ by this many percent


# ---------------------------------------------------------------------
# Storage schema
# ---------------------------------------------------------------------

@allow_storage
@dataclass
class Observation:
    epoch: u256
    value_millionths: u256  # value * 1_000_000, integer-scaled for storage
                            # (monitored metrics are assumed non-negative,
                            # e.g. prices, gas fees, latency - see README)
    z_score_abs_millionths: u256  # abs(z-score) * 1_000_000
    z_score_negative: bool  # sign of the z-score, stored separately since
                            # storage here avoids signed integer types
    tier: str


@allow_storage
@dataclass
class Sentinel:
    metric_description: str
    source_url: str
    extraction_instruction: str
    owner: Address
    observation_count: u256
    last_tier: str
    sum_millionths: u256  # running sum of all recorded values (scaled)
    sum_sq_scaled: u256  # running sum of (value^2), scaled by 1_000_000
    history: DynArray[Observation]


def _sqrt(value: float) -> float:
    """Pure-Python square root (Newton's method), avoiding a dependency
    on the `math` module's availability/behavior inside the GenVM
    sandbox - this contract only needs sqrt for one computation, so a
    small, self-contained implementation removes an unverified
    dependency rather than assuming stdlib `math` works identically to
    CPython inside this environment."""
    if value <= 0:
        return 0.0
    guess = value
    for _ in range(50):
        guess = (guess + value / guess) / 2
    return guess


def _scale_to_uint(value: float) -> int:
    return int(round(value * 1_000_000))


def _unscale(value_millionths: int) -> float:
    return value_millionths / 1_000_000


class AnomalySentinel(gl.Contract):
    sentinels: TreeMap[str, Sentinel]

    def __init__(self):
        self.sentinels = TreeMap()

    # -------------------------------------------------------------
    # Write methods
    # -------------------------------------------------------------

    @gl.public.write
    def create_sentinel(
        self,
        sentinel_id: str,
        metric_description: str,
        source_url: str,
        extraction_instruction: str,
    ) -> None:
        """Register a numeric metric to monitor over time.
        `extraction_instruction` should tell the model precisely which
        number to read off the page (e.g. "the current average gas
        price in gwei shown on this page")."""
        if sentinel_id in self.sentinels:
            raise Exception("sentinel_id already exists")
        if len(metric_description.strip()) == 0:
            raise Exception("metric_description cannot be empty")
        if len(source_url.strip()) == 0:
            raise Exception("source_url cannot be empty")
        if len(extraction_instruction.strip()) == 0:
            raise Exception("extraction_instruction cannot be empty")

        self.sentinels[sentinel_id] = Sentinel(
            metric_description=metric_description,
            source_url=source_url,
            extraction_instruction=extraction_instruction,
            owner=gl.message.sender_address,
            observation_count=u256(0),
            last_tier="no_data",
            sum_millionths=u256(0),
            sum_sq_scaled=u256(0),
            history=gl.storage.inmem_allocate(DynArray[Observation], []),
        )

    @gl.public.write
    def submit_observation(self, sentinel_id: str) -> None:
        """Fetch the current metric value, reach validator consensus on
        it, then deterministically classify it against this sentinel's
        own recorded history."""
        if sentinel_id not in self.sentinels:
            raise Exception("unknown sentinel_id")

        sentinel = self.sentinels[sentinel_id]
        source_url = sentinel.source_url
        extraction_instruction = sentinel.extraction_instruction
        metric_description = sentinel.metric_description

        def extract_value() -> str:
            page_text = gl.nondet.web.render(source_url, mode="text")[:4000]

            prompt = f"""
Metric being tracked: {metric_description}
Extraction instruction: {extraction_instruction}

Page content:
{page_text}

Extract the current numeric value of this metric from the page.
Respond with ONLY a compact JSON object, no markdown, no commentary:
{{"value": <number, integer or decimal, no units or symbols>, "notes": "<one short sentence on where in the page this value was found>"}}
"""
            result = gl.nondet.exec_prompt(prompt, response_format="json")
            value = float(result.get("value", 0))
            notes = str(result.get("notes", ""))[:200]

            return json.dumps({"notes": notes, "value": value}, sort_keys=True)

        principle = f"""
Two answers are EQUIVALENT if the "value" field in both answers is
within {VALUE_TOLERANCE_PERCENT} percent of each other (relative to the
larger of the two values), accounting for normal source volatility
between independent fetches. If the values differ by more than
{VALUE_TOLERANCE_PERCENT} percent, the answers are NOT equivalent. The
"notes" field may differ freely and does not affect equivalence.
"""

        consensus_result = gl.eq_principle.prompt_comparative(extract_value, principle)
        parsed = json.loads(consensus_result)
        new_value = float(parsed["value"])
        if new_value < 0:
            raise Exception(
                "extracted value is negative; this contract assumes "
                "non-negative metrics (prices, gas fees, latency, etc.) - "
                "see README for this known limitation"
            )

        # Deterministic statistics using running sums already stored on
        # the sentinel (updated incrementally on each prior call) -
        # this avoids ever iterating the stored `history` array inside
        # a write method, unlike an earlier version of this contract
        # that computed mean/variance via a list comprehension over
        # `sentinel.history` directly. See README for why that was
        # changed.
        prior_count = int(sentinel.observation_count)
        prior_sum = _unscale(int(sentinel.sum_millionths))
        prior_sum_sq = _unscale(int(sentinel.sum_sq_scaled))

        if prior_count >= MIN_HISTORY_FOR_STATS:
            mean = prior_sum / prior_count
            variance = (prior_sum_sq / prior_count) - (mean * mean)
            if variance < 0:
                variance = 0.0  # guard against float rounding error
            stddev = _sqrt(variance)

            if stddev == 0:
                z_score = 0.0 if new_value == mean else 999_999.0
            else:
                z_score = (new_value - mean) / stddev
                # Cap to a large finite bound instead of allowing runaway
                # magnitudes to cause scaling/overflow issues downstream.
                if z_score > 999_999.0:
                    z_score = 999_999.0
                elif z_score < -999_999.0:
                    z_score = -999_999.0

            abs_z = abs(z_score)
            if abs_z >= ANOMALY_Z_THRESHOLD:
                tier = "anomaly"
            elif abs_z >= WATCH_Z_THRESHOLD:
                tier = "watch"
            else:
                tier = "normal"

            stored_z_negative = z_score < 0
            stored_z_abs = abs(z_score)
        else:
            tier = "insufficient_history"
            stored_z_negative = False
            stored_z_abs = 0.0

        next_epoch = u256(int(sentinel.observation_count) + 1)

        sentinel.history.append(
            Observation(
                epoch=next_epoch,
                value_millionths=u256(_scale_to_uint(new_value)),
                z_score_abs_millionths=u256(_scale_to_uint(stored_z_abs)),
                z_score_negative=stored_z_negative,
                tier=tier,
            )
        )
        sentinel.observation_count = next_epoch
        sentinel.last_tier = tier
        sentinel.sum_millionths = u256(_scale_to_uint(prior_sum + new_value))
        sentinel.sum_sq_scaled = u256(_scale_to_uint(prior_sum_sq + (new_value * new_value)))

    # -------------------------------------------------------------
    # Read methods
    # -------------------------------------------------------------

    @gl.public.view
    def get_current_status(self, sentinel_id: str) -> dict:
        s = self.sentinels.get(sentinel_id, None)
        if s is None:
            raise Exception("unknown sentinel_id")
        return {
            "tier": s.last_tier,
            "observation_count": int(s.observation_count),
        }

    @gl.public.view
    def get_history(self, sentinel_id: str) -> list:
        s = self.sentinels.get(sentinel_id, None)
        if s is None:
            raise Exception("unknown sentinel_id")
        result = []
        for o in s.history:
            z_score = _unscale(int(o.z_score_abs_millionths))
            if o.z_score_negative:
                z_score = -z_score
            result.append(
                {
                    "epoch": int(o.epoch),
                    "value": _unscale(int(o.value_millionths)),
                    "z_score": z_score,
                    "tier": o.tier,
                }
            )
        return result

    @gl.public.view
    def get_anomaly_count(self, sentinel_id: str) -> int:
        """Total number of recorded observations classified as
        'anomaly' - useful for downstream contracts that want to gate
        an action on a sentinel having accumulated repeated anomalies,
        rather than acting on a single flagged reading."""
        s = self.sentinels.get(sentinel_id, None)
        if s is None:
            raise Exception("unknown sentinel_id")
        count = 0
        for o in s.history:
            if o.tier == "anomaly":
                count += 1
        return count

    @gl.public.view
    def list_sentinel_ids(self) -> list:
        return list(self.sentinels.keys())
