# GenLayer Portal Submission — Anomaly Sentinel

## Title

**Anomaly Sentinel — Statistical Anomaly Detection with Consensus-Stable Classifications**

## Notes (1000 characters)

Anomaly Sentinel tracks a numeric metric over time and classifies each new observation as normal, watch, or anomaly - not against a fixed threshold, but against a statistical baseline (mean/std) the contract builds on-chain.

Consensus (Equivalence Principle): a leader/validator pair agrees only on the raw numeric value extracted from the external source (LLM extraction, independent re-extraction by validators). The tier is then computed deterministically in plain Python as a z-score against the recorded history - the LLM is never asked for a subjective severity judgement.

Reliability property: a naive 5% value tolerance lets a boundary-crossing value silently flip normal/watch/anomaly between agreeing nodes. Here validators also require that classifying the leader's and their own reading against the SAME history yields the SAME tier, and that appending either keeps the future baseline statistically equivalent. Every accepted reading therefore records the same classification all agreeing nodes derived.

Security: no hallucinated classifications; bounded inputs; non-negative metrics enforced. Verified: 12 tests; deployed on GenLayer Studio; 6 live consensus rounds on a real GitHub metric.

---

## Useful submission links

- Reference deployment: https://explorer-studio.genlayer.com/address/0x0dD4870705Adbf1a12f6C8ccE87a75d6D9e3AEeF
- Repo README (design, security, threat model): see `README.md` in this folder
- Direct-mode tests: `pytest tests/test_anomaly_sentinel.py`
- On-chain test script: `scripts/onchain_test.py`
