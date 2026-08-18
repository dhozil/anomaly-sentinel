# GenLayer Portal Submission — Anomaly Sentinel

## Title

**Anomaly Sentinel — Statistical Anomaly Detection with Consensus-Stable Classifications**

## Notes (1000 characters)

Anomaly Sentinel tracks a numeric metric over time and classifies each new observation as normal, watch, or anomaly against a statistical baseline (mean/std) the contract builds on-chain.

Consensus: a leader/validator pair agrees only on the raw numeric value extracted from an external source (LLM extraction, independent re-extraction by validators). The tier is deterministic plain-Python arithmetic - a z-score against the recorded history - never an LLM judgement.

Reliability: tolerating any difference between agreeing readings can produce the same tier now but different variance baselines and different tiers later. Validators therefore bind the accepted value to the exact normalized observation; only readings each validator independently reproduces exactly are accepted, so future classifications are identical no matter which agreeing node wins.

Security: no hallucinated classifications; bounded inputs; non-negative metrics. Verified: 12 tests; deployed on GenLayer Studio.

---

## Useful submission links

- Reference deployment: https://explorer-studio.genlayer.com/address/0x3babd34aaC6486675328d1db84bfb520bf7475db
- Repo README (design, security, threat model): see `README.md` in this folder
- Direct-mode tests: `pytest tests/test_anomaly_sentinel.py`
- On-chain test script: `scripts/onchain_test.py`
