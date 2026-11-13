# Shawn M1 Container — Deployment Record

**Date:** 2026-11-13  
**Container Ref:** SM1-53686177-6e204d31  
**Host:** qxrp (209.74.87.249)  
**Port:** 42881

---

## Services (both enabled in multi-user.target.wants)

| Unit | Purpose | Status |
|------|---------|--------|
| `qvtx-shawn-m1.service` | Podman container, auto-restart | active |
| `qvtx-shawn-m1-he.service` | HE IPv6 bridge → `[2001:470:1f27:a5::1]:42881` | active |

---

## Accounts

| Currency | Account No | IBAN |
|----------|-----------|------|
| USD | 000000062 | LU680010000000000062 |
| EUR | 000000063 | DE28500700100000000063 |

---

## Persistent Storage

| Volume | Path | Purpose |
|--------|------|---------|
| `shawn_m1_ledger` | `/opt/shawn-m1/ledger/` | registrations.jsonl + tokens.jsonl |
| `shawn_m1_logs` | `/opt/shawn-m1/logs/` | service logs |

Volumes survive container restarts and OS reboots.

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Status + account IBANs |
| POST | `/receive` | ISO 20022 pacs.008.001.10 intake |
| POST | `/m1` | Alias for /receive |
| POST | `/tokenize` | Mint token (HELD, 20% reserve gate) |
| POST | `/release` | Release token (10% fiat gate) |
| GET | `/token/{byteid}` | Token state |
| GET | `/ledger` | JSON ledger |
| GET | `/` | HTML ledger |

**IPv4:** `http://209.74.87.249:42881/health`  
**IPv6 (HE):** `http://[2001:470:1f27:a5::1]:42881/health`

---

## Q4 Coherence Gate

- Threshold: 1/φ = **0.618034**
- Raw structural score (no phi modulation)
- Valid pacs.008 scores ~0.70 → passes
- Score < 0.618034 → 400 REJECTED

---

## Tokenization Protocol

```
reserve_required  = amount × 0.20   (20% held on reserve)
release_required  = amount × 0.10   (10% fiat payment to release)
state: MINTED_HELD → RELEASED
```

Token identity chain: L0 intake → L1 reserve → L2 token → L3 release → L4 cascade  
Anchors: chain 42000 calldata `QVTX-TOKENIZE {token_id}` + Polygon JSON record

---

## GitHub Source

Repo: `quant-oss/shawn-m1-intake`  
Deploy key: `/root/.ssh/shawn_m1_deploy` on qxrp (read-only)  
Build: `podman build -t qvtx/shawn-m1-intake:latest .`

---

## Staged Items

| Item | Unblocks |
|------|---------|
| X-chain cascade (Polygon, D-BIS, 8825) | Louis names target per tokenization |
| Token contract deploy on chain 42000 | Louis fires `--execute` |
| Pool creation on chain 42000 | After token deploy |
| Release gate auto-watch service | Supercore (203.161.33.24) online |
