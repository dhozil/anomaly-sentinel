# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
Anomaly Sentinel
==================

A reusable GenLayer Intelligent Contract primitive that tracks a numeric
metric over time and classifies each new observation as normal, watch, or
anomalous relative to the metric's own recorded history - not against a fixed
threshold set in advance, but against a statistical baseline the contract
builds up on-chain as data arrives.

Consensus design
----------------
For every ``submit_observation`` call:
  1. A non-deterministic block fetches ``source_url`` and asks the LLM to
     extract the current numeric value of the tracked metric.
  2. A custom leader/validator pair (``gl.vm.run_nondet_unsafe``) reaches
     consensus on the raw numeric value. Validators independently re-fetch and
     re-extract, and only accept a reading that is EXACTLY equal to the
     leader's. The accepted value is bound to the exact normalized observation
     (the integer ``value_millionths`` = value * 1_000_000): no tolerance is
     applied at consensus time.
     Exact binding is the key reliability property. Any tolerated difference
     between agreeing readings can prove the same classification NOW while
     leaving different running ``sum``/``sum_sq`` variance baselines, which
     yields different mean/variance - and therefore different tiers - for every
     LATER observation. Requiring exact equality means every agreeing node
     would record the identical value, so the accepted observation preserves
     both the currently recorded classification AND every future
     classification (the post-update count/sum/sum_sq state is identical no
     matter which agreeing node is used).
  3. Once the value is agreed, the contract deterministically computes the
     mean and standard deviation of all PRIOR recorded values, derives a
     z-score, and assigns a tier from fixed z-score bands. This step involves
     no further consensus round because it is pure arithmetic over state
     everyone already agrees on.
  4. The value, z-score, and tier are appended to an append-only history.
"""

from dataclasses import dataclass

from genlayer import *
import json

MIN_HISTORY_FOR_STATS = 5  # minimum prior points before z-scores are meaningful
WATCH_Z_THRESHOLD = 1.5
ANOMALY_Z_THRESHOLD = 3.0

MAX_VALUE = 10 ** 12  # sanity cap on extracted magnitudes (avoid float/scale overflow)
MAX_PAGE_CHARS = 4000
MAX_NOTES_CHARS = 200
MAX_ID_CHARS = 100
MAX_DESC_CHARS = 400
MAX_URL_CHARS = 500
MAX_INSTRUCTION_CHARS = 500


# ---------------------------------------------------------------------
# Storage schema
# ---------------------------------------------------------------------

@allow_storage
@dataclass
class Observation:
    epoch: u256
    value_millionths: u256  # value * 1_000_000, integer-scaled for storage
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
    """Pure-Python square root (Newton's method), avoiding a dependency on the
    ``math`` module's availability/behavior inside the GenVM sandbox."""
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


def _classify(
    value: float,
    prior_count: int,
    prior_sum: float,
    prior_sum_sq: float,
):
    """Deterministic classification of ``value`` against prior history.

    Returns ``(tier, abs_z, is_negative)``. This is pure arithmetic over the
    sentinel's already-agreed state, so anyone can re-derive it by hand.
    """
    if prior_count < MIN_HISTORY_FOR_STATS:
        return "insufficient_history", 0.0, False

    mean = prior_sum / prior_count
    variance = (prior_sum_sq / prior_count) - (mean * mean)
    if variance < 0:
        variance = 0.0  # guard against float rounding error
    stddev = _sqrt(variance)

    if stddev == 0:
        z_score = 0.0 if value == mean else 999_999.0
    else:
        z_score = (value - mean) / stddev
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
    return tier, abs_z, z_score < 0


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
        """Register a numeric metric to monitor over time."""
        sentinel_id = sentinel_id.strip()
        if len(sentinel_id) == 0:
            raise gl.vm.UserError("sentinel_id cannot be empty")
        if len(sentinel_id) > MAX_ID_CHARS:
            raise gl.vm.UserError("sentinel_id too long")
        if sentinel_id in self.sentinels:
            raise gl.vm.UserError("sentinel_id already exists")

        if len(metric_description.strip()) == 0:
            raise gl.vm.UserError("metric_description cannot be empty")
        if len(metric_description) > MAX_DESC_CHARS:
            raise gl.vm.UserError("metric_description too long")

        source_url = source_url.strip()
        if len(source_url) == 0:
            raise gl.vm.UserError("source_url cannot be empty")
        if not (source_url.startswith("http://") or source_url.startswith("https://")):
            raise gl.vm.UserError("source_url must start with http(s)://")
        if len(source_url) > MAX_URL_CHARS:
            raise gl.vm.UserError("source_url too long")

        if len(extraction_instruction.strip()) == 0:
            raise gl.vm.UserError("extraction_instruction cannot be empty")
        if len(extraction_instruction) > MAX_INSTRUCTION_CHARS:
            raise gl.vm.UserError("extraction_instruction too long")

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
        """Fetch the current metric value, reach validator consensus on the
        exact normalized observation, then deterministically classify it
        against this sentinel's own recorded history."""
        if sentinel_id not in self.sentinels:
            raise gl.vm.UserError("unknown sentinel_id")

        sentinel = self.sentinels[sentinel_id]
        source_url = sentinel.source_url
        extraction_instruction = sentinel.extraction_instruction
        metric_description = sentinel.metric_description

        prior_count = int(sentinel.observation_count)
        prior_sum = _unscale(int(sentinel.sum_millionths))
        prior_sum_sq = _unscale(int(sentinel.sum_sq_scaled))

        def extract_value() -> dict:
            page_text = gl.nondet.web.render(source_url, mode="text")[:MAX_PAGE_CHARS]
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
            notes = str(result.get("notes", ""))[:MAX_NOTES_CHARS]
            try:
                value = float(result.get("value", 0))
            except Exception:
                value = None
            # Return the value as a scaled integer (value * 1_000_000) so the
            # leader result is calldata-encodable (floats are not). -1 marks an
            # unparseable/out-of-range extraction, which validators reject.
            if value is None or value < 0 or value > MAX_VALUE:
                return {"value_millionths": -1, "notes": notes}
            return {"value_millionths": _scale_to_uint(value), "notes": notes}

        def validator_fn(leader_result) -> bool:
            """The accepted value is bound to the EXACT normalized observation.

            This validator approves a leader reading only if it independently
            reproduces that exact normalized value (``value_millionths``). No
            tolerance is applied: two agreeing readings that differ at all
            would record different values, which can produce the same tier now
            but different running variance baselines - and therefore different
            classifications later. Exact equality guarantees the accepted value
            preserves both the current classification and every future
            classification, because the post-update statistical state
            (count, sum, sum_sq) is identical no matter which agreeing node is
            used.
            """
            if not isinstance(leader_result, gl.vm.Return):
                return False
            ld = leader_result.calldata
            if not isinstance(ld, dict):
                return False
            lm = ld.get("value_millionths")
            if not isinstance(lm, int) or isinstance(lm, bool) or lm < 0:
                return False

            # Independent re-extraction.
            my = extract_value()
            mm = my.get("value_millionths")
            if not isinstance(mm, int) or isinstance(mm, bool) or mm < 0:
                return False

            # Bind the exact normalized observation. ``value_millionths`` is an
            # integer (value * 1_000_000), so equality is exact arithmetic and
            # the stored value is the very reading this validator reproduced.
            return lm == mm

        result = gl.vm.run_nondet_unsafe(extract_value, validator_fn)

        lm = result.get("value_millionths")
        if not isinstance(lm, int) or isinstance(lm, bool) or lm < 0:
            raise gl.vm.UserError("Could not extract a numeric value from the source")
        new_value = lm / 1_000_000.0
        if new_value < 0:
            raise gl.vm.UserError(
                "Extracted value is negative; this contract assumes non-negative "
                "metrics (prices, gas fees, latency, etc.)"
            )
        if new_value > MAX_VALUE:
            raise gl.vm.UserError("Extracted value is too large")

        tier, z_abs, z_neg = _classify(new_value, prior_count, prior_sum, prior_sum_sq)

        next_epoch = u256(prior_count + 1)
        sentinel.history.append(
            Observation(
                epoch=next_epoch,
                value_millionths=u256(_scale_to_uint(new_value)),
                z_score_abs_millionths=u256(_scale_to_uint(z_abs)),
                z_score_negative=z_neg,
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
            raise gl.vm.UserError("unknown sentinel_id")
        return {
            "tier": s.last_tier,
            "observation_count": int(s.observation_count),
        }

    @gl.public.view
    def get_history(self, sentinel_id: str) -> list:
        """Append-only history. Numeric fields are returned integer-scaled
        (``value_millionths`` = value * 1_000_000, ``z_score_abs_millionths`` =
        abs(z-score) * 1_000_000) because floats are not calldata-encodable;
        the sign of the z-score is carried in ``z_score_negative``."""
        s = self.sentinels.get(sentinel_id, None)
        if s is None:
            raise gl.vm.UserError("unknown sentinel_id")
        result = []
        for o in s.history:
            result.append(
                {
                    "epoch": int(o.epoch),
                    "value_millionths": int(o.value_millionths),
                    "z_score_abs_millionths": int(o.z_score_abs_millionths),
                    "z_score_negative": o.z_score_negative,
                    "tier": o.tier,
                }
            )
        return result

    @gl.public.view
    def get_anomaly_count(self, sentinel_id: str) -> int:
        """Total number of recorded observations classified as 'anomaly'."""
        s = self.sentinels.get(sentinel_id, None)
        if s is None:
            raise gl.vm.UserError("unknown sentinel_id")
        count = 0
        for o in s.history:
            if o.tier == "anomaly":
                count += 1
        return count

    @gl.public.view
    def list_sentinel_ids(self) -> list:
        return list(self.sentinels.keys())
