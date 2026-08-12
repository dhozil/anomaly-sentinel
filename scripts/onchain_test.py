"""Deploy AnomalySentinel to GenLayer Studio (studionet) and exercise it live.

Usage:
    python scripts/onchain_test.py [--address <ADDR>]

Deploys (or re-tests an existing) AnomalySentinel, registers a stargazers-count
sentinel backed by the genlayer-py GitHub API, and submits several observations
so the statistical history (z-score tiers) activates on-chain.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from genlayer_py import create_client, create_account
from genlayer_py.chains import studionet
from genlayer_py.types.transactions import TransactionStatus
from gltest.assertions import tx_execution_succeeded

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contracts" / "anomaly_sentinel.py"

# Stable, low-rate-limit source, compact enough to survive the contract's
# 4000-char page truncation: the genlayerlabs org's follower count on GitHub.
SOURCE_URL = "https://api.github.com/users/genlayerlabs"
EXTRACTION_INSTRUCTION = (
    "Extract the value of the 'followers' field from the JSON"
)


def rate_retry(fn, *args, **kwargs):
    while True:
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if "rate limit" in msg.lower() or "Rate limit" in msg or "-32029" in msg:
                print("  rate limited; waiting 65s...")
                time.sleep(65)
                continue
            raise


def send_tx(client, tx_hash, label, interval=6000, retries=120):
    def _wait():
        return client.wait_for_transaction_receipt(
            tx_hash, status=TransactionStatus.ACCEPTED, interval=interval, retries=retries
        )

    receipt = rate_retry(_wait)
    ok = tx_execution_succeeded(receipt)
    print(f"  [{label}] tx={tx_hash} success={ok}")
    if not ok:
        leader = receipt["consensus_data"]["leader_receipt"][0]
        print(f"    stderr: {leader.get('genvm_result', {}).get('stderr', '')[:300]}")
    time.sleep(3)
    return ok


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Deploy + test AnomalySentinel on studionet")
    parser.add_argument("--address", help="Existing AnomalySentinel address (skip deploy)")
    args = parser.parse_args()

    alice = create_account()
    print(f"  alice: {alice.address}")

    client = create_client(chain=studionet, account=alice)
    if args.address:
        addr = args.address
        print(f"=== Using existing AnomalySentinel at {addr} ===")
    else:
        print("=== Deploying AnomalySentinel ===")
        code = CONTRACT_PATH.read_text(encoding="utf-8")
        deploy_hash = rate_retry(lambda: client.deploy_contract(code=code, account=alice))
        receipt = rate_retry(lambda: client.wait_for_transaction_receipt(
            deploy_hash, status=TransactionStatus.ACCEPTED, interval=4000, retries=90
        ))
        assert tx_execution_succeeded(receipt), f"Deploy failed: {receipt}"
        addr = receipt["to_address"]
        print(f"  contract at {addr}")

    read = lambda fn, *a, **kw: rate_retry(client.read_contract, addr, fn, args=list(a), **kw)
    write = lambda fn, account, args=None, value=0: rate_retry(
        client.write_contract, addr, fn, account=account, args=args or [], value=value
    )

    print("\n[1] create_sentinel")
    # Pick a sentinel id that does not already exist on this deployment so the
    # script can be re-run against the same contract.
    existing = read("list_sentinel_ids") or []
    sid = "btc_price1"
    n = 1
    while sid in existing:
        n += 1
        sid = f"btc_price{n}"
    assert send_tx(client, write("create_sentinel", alice, args=[
        sid, "GitHub follower count of the genlayerlabs org",
        SOURCE_URL,
        EXTRACTION_INSTRUCTION,
    ]), "create_sentinel")
    assert sid in read("list_sentinel_ids")
    print(f"    sentinels={read('list_sentinel_ids')}")

    print("\n[2] submit_observation x6 (build statistical history)")
    for i in range(6):
        # External sources (web APIs) can fail transiently; retry the submit
        # rather than treating an upstream outage as a contract failure.
        for attempt in range(4):
            ok = send_tx(client, write("submit_observation", alice, args=[sid]), f"submit {i+1}")
            if ok:
                break
            print(f"    [submit {i+1}] external failure, retrying ({attempt+1}/4)...")
            time.sleep(10)
        assert ok, f"submit {i+1} never succeeded after retries"

    print("\n[3] views")
    status = read("get_current_status", sid)
    print(f"    current_status={status}")
    history = read("get_history", sid)
    print(f"    history_len={len(history)}")
    for h in history:
        z = h["z_score_abs_millionths"] / 1_000_000
        if h["z_score_negative"]:
            z = -z
        print(f"      epoch={h['epoch']} value={h['value_millionths']/1_000_000} z={z:.3f} tier={h['tier']}")
    print(f"    anomaly_count={read('get_anomaly_count', sid)}")

    tiers = [h["tier"] for h in history]
    assert tiers[:5] == ["insufficient_history"] * 5, f"first 5 should be insufficient_history, got {tiers[:5]}"
    print("\n=== ANOMALY SENTINEL PASSED ON STUDIONET ===")
    print(f"contract: {addr}")


if __name__ == "__main__":
    main()
