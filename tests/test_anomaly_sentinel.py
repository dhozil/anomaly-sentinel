"""Direct-mode tests for the AnomalySentinel contract.

Covers: sentinel creation + validation, the observation lifecycle
(insufficient_history -> normal/watch/anomaly), deterministic classification,
and the validator guarantee that the accepted value is bound to the EXACT
normalized observation every agreeing validator independently reproduces - so
it preserves both the current classification and every future classification
(the steward-requested fix).
"""

import json

from gltest.direct import create_address

SRC_URL = "https://source.example.com/metric"


def _setup(vm, value, notes="found on the page"):
    vm.mock_web(r"source\.example\.com.*", {"status": 200, "body": "Metric page content."})
    vm.mock_llm(
        r"Metric being tracked",
        json.dumps({"value": value, "notes": notes}),
    )


def _make_sentinel(contract, vm, creator, sentinel_id="gas1"):
    vm.sender = creator
    return contract.create_sentinel(
        sentinel_id,
        "Ethereum average gas price in gwei",
        SRC_URL,
        "Extract the current average gas price in gwei shown on this page",
    )


def _submit(vm, contract, sentinel_id, value):
    vm.clear_mocks()
    _setup(vm, value)
    contract.submit_observation(sentinel_id)


# ──────────────────────────────────────────────────────────────────────
# create_sentinel
# ──────────────────────────────────────────────────────────────────────


def test_create_sentinel(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/anomaly_sentinel.py")
    creator = create_address("creator")
    _make_sentinel(contract, direct_vm, creator)
    assert contract.list_sentinel_ids() == ["gas1"]
    status = contract.get_current_status("gas1")
    assert status["tier"] == "no_data"
    assert status["observation_count"] == 0


def test_create_sentinel_validation(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/anomaly_sentinel.py")
    creator = create_address("creator")
    direct_vm.sender = creator

    with direct_vm.expect_revert("sentinel_id cannot be empty"):
        contract.create_sentinel("", "desc", SRC_URL, "instr")
    with direct_vm.expect_revert("metric_description cannot be empty"):
        contract.create_sentinel("x", "   ", SRC_URL, "instr")
    with direct_vm.expect_revert("source_url must start with http"):
        contract.create_sentinel("x", "desc", "ftp://x", "instr")
    with direct_vm.expect_revert("source_url cannot be empty"):
        contract.create_sentinel("x", "desc", "", "instr")
    with direct_vm.expect_revert("extraction_instruction cannot be empty"):
        contract.create_sentinel("x", "desc", SRC_URL, "  ")

    _make_sentinel(contract, direct_vm, creator)
    with direct_vm.expect_revert("sentinel_id already exists"):
        _make_sentinel(contract, direct_vm, creator)


# ──────────────────────────────────────────────────────────────────────
# submit_observation lifecycle
# ──────────────────────────────────────────────────────────────────────


def test_first_observation_insufficient_history(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/anomaly_sentinel.py")
    creator = create_address("creator")
    _make_sentinel(contract, direct_vm, creator)

    _submit(direct_vm, contract, "gas1", 100)
    status = contract.get_current_status("gas1")
    assert status["tier"] == "insufficient_history"
    assert status["observation_count"] == 1

    history = contract.get_history("gas1")
    assert len(history) == 1
    assert history[0]["epoch"] == 1
    assert history[0]["value_millionths"] == 100_000_000
    assert history[0]["tier"] == "insufficient_history"


def test_build_history_then_classify(direct_vm, direct_deploy):
    """After 5 prior points, later values get real z-score tiers."""
    contract = direct_deploy("contracts/anomaly_sentinel.py")
    creator = create_address("creator")
    _make_sentinel(contract, direct_vm, creator)

    for value in (100, 110, 105, 115, 108):
        _submit(direct_vm, contract, "gas1", value)

    status = contract.get_current_status("gas1")
    assert status["observation_count"] == 5
    assert status["tier"] == "insufficient_history"

    # 6th value close to the mean -> normal.
    _submit(direct_vm, contract, "gas1", 108)
    assert contract.get_current_status("gas1")["tier"] == "normal"

    # A value far from history -> anomaly (z >= 3).
    _submit(direct_vm, contract, "gas1", 500)
    assert contract.get_current_status("gas1")["tier"] == "anomaly"
    assert contract.get_anomaly_count("gas1") == 1


def test_constant_series_no_crash(direct_vm, direct_deploy):
    """stddev == 0 edge case: identical values -> normal, a deviation -> anomaly."""
    contract = direct_deploy("contracts/anomaly_sentinel.py")
    creator = create_address("creator")
    _make_sentinel(contract, direct_vm, creator)

    for _ in range(5):
        _submit(direct_vm, contract, "gas1", 42)

    _submit(direct_vm, contract, "gas1", 42)
    assert contract.get_current_status("gas1")["tier"] == "normal"

    _submit(direct_vm, contract, "gas1", 43)
    assert contract.get_current_status("gas1")["tier"] == "anomaly"


def test_unknown_sentinel_reverts(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/anomaly_sentinel.py")
    _setup(direct_vm, 100)
    with direct_vm.expect_revert("unknown sentinel_id"):
        contract.submit_observation("nope")


# ──────────────────────────────────────────────────────────────────────
# validator guarantee (exact-binding of the normalized observation)
# ──────────────────────────────────────────────────────────────────────


def test_validator_agrees(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/anomaly_sentinel.py")
    creator = create_address("creator")
    _make_sentinel(contract, direct_vm, creator)
    _submit(direct_vm, contract, "gas1", 100)

    # Validator re-extracts the exact same normalized value -> agrees.
    assert direct_vm.run_validator() is True


def test_validator_rejects_different_value(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/anomaly_sentinel.py")
    creator = create_address("creator")
    _make_sentinel(contract, direct_vm, creator)
    _submit(direct_vm, contract, "gas1", 100)

    # Validator sees 120.0 (20% apart) -> not the exact same observation.
    direct_vm.clear_mocks()
    _setup(direct_vm, 120)
    assert direct_vm.run_validator() is False


def test_validator_rejects_same_tier_but_not_exact(direct_vm, direct_deploy):
    """The steward-requested guarantee: a leader value and a validator value
    MAY map to the same tier now, but any difference between them changes the
    running variance baseline and therefore future classifications. Only the
    exact normalized observation is accepted."""
    contract = direct_deploy("contracts/anomaly_sentinel.py")
    creator = create_address("creator")
    _make_sentinel(contract, direct_vm, creator)

    # Build prior history so a later value can sit near the watch/normal
    # boundary: mean ~107.6, stddev ~5.0.
    for value in (100, 110, 105, 115, 108):
        _submit(direct_vm, contract, "gas1", value)

    # Leader reads 116 (z ~1.68 -> watch).
    _submit(direct_vm, contract, "gas1", 116)
    assert contract.get_current_status("gas1")["tier"] == "watch"

    # A validator reading 118 is within any former tolerance and still maps to
    # watch (z ~2.08) - same tier now - but it is a DIFFERENT value, so it
    # would change sum/sum_sq and shift every future z-score. Reject.
    direct_vm.clear_mocks()
    _setup(direct_vm, 118)
    assert direct_vm.run_validator() is False


def test_validator_rejects_tier_flip(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/anomaly_sentinel.py")
    creator = create_address("creator")
    _make_sentinel(contract, direct_vm, creator)

    # Build prior history so the 6th value can sit near the watch/normal
    # boundary: mean ~107.6, stddev ~5.0.
    for value in (100, 110, 105, 115, 108):
        _submit(direct_vm, contract, "gas1", value)

    # Leader reads 116 (z ~1.68 -> watch); a validator reading 112 (z ~0.88 ->
    # normal) would classify the observation differently.
    _submit(direct_vm, contract, "gas1", 116)
    assert contract.get_current_status("gas1")["tier"] == "watch"

    direct_vm.clear_mocks()
    _setup(direct_vm, 112)
    assert direct_vm.run_validator() is False  # different value -> reject


def test_validator_rejects_non_numeric(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/anomaly_sentinel.py")
    creator = create_address("creator")
    _make_sentinel(contract, direct_vm, creator)

    direct_vm.mock_web(r"source\.example\.com.*", {"status": 200, "body": "page"})
    direct_vm.mock_llm(r"Metric being tracked", json.dumps({"value": "not-a-number", "notes": "x"}))
    with direct_vm.expect_revert("Could not extract a numeric value"):
        contract.submit_observation("gas1")


# ──────────────────────────────────────────────────────────────────────
# views
# ──────────────────────────────────────────────────────────────────────


def test_views(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/anomaly_sentinel.py")
    creator = create_address("creator")
    _make_sentinel(contract, direct_vm, creator, "price1")

    for value in (100, 100, 100, 100, 100, 100, 250):
        _submit(direct_vm, contract, "price1", value)

    history = contract.get_history("price1")
    assert len(history) == 7
    assert [h["tier"] for h in history][:5] == ["insufficient_history"] * 5
    assert history[5]["tier"] == "normal"
    assert history[6]["tier"] == "anomaly"
    assert history[6]["z_score_abs_millionths"] / 1_000_000 > 3.0
    assert history[6]["z_score_negative"] is False
    assert contract.get_anomaly_count("price1") == 1
    assert contract.get_current_status("price1")["tier"] == "anomaly"
    assert contract.list_sentinel_ids() == ["price1"]
