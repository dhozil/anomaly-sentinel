# GenLayer Portal Submission — Anomaly Sentinel

## Title

**Anomaly Sentinel — Statistical Anomaly Detection with Consensus-Stable Classifications**

## Notes (1000 characters)

Anomaly Sentinel tracks a numeric metric over time and classifies each new observation as normal, watch, or anomaly against a statistical baseline (mean/std) the contract builds on-chain.

Consensus: a leader/validator pair agrees only on the raw value extracted from an external source (LLM extraction, validator re-extraction). The tier is deterministic plain-Python arithmetic - a z-score against recorded history - never an LLM judgement.

Reliability: any difference between agreeing readings can produce the same tier now but different variance baselines and different tiers later. Validators bind the accepted value to the exact normalized observation; only readings each validator independently reproduces exactly are accepted, so future classifications are identical regardless of which node wins.

Security: no hallucinated classifications; bounded inputs; non-negative metrics; missing/non-finite (NaN/Inf) extractions fail - never stored as zeros. Verified: 15 tests; deployed on GenLayer.

---

## Useful submission links

- Reference deployment: https://explorer-studio.genlayer.com/address/0x6bfDb1c3403f92eF0AC414c0c8eA486A2bdEfbB1
- Repo README (design, security, threat model): see `README.md` in this folder
- Direct-mode tests: `pytest tests/test_anomaly_sentinel.py`
- On-chain test script: `scripts/onchain_test.py`
