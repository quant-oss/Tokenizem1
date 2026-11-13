# QVTX M1 Tokenization Protocol
# ByteID: QVTX-TOKEN-PROTOCOL-v1

**Authored:** 2026-11-13  
**Chain:** 42000 (native anchor) + X-chains (cascade)  
**Gate:** Q4 coherence threshold 1/φ = 0.618034  
**Reserve:** 20% of tokenized value held on reserve  
**Release gate:** 10% fiat cash (or equivalent) paid to release tokens

---

## 1. Overview

Every M1 intake (pacs.008 received by Shawn M1 container) that passes the Q4 gate
becomes a tokenization event. The M1 value is folded to a ByteID, anchored on chain
42000, then cascaded as tokens across X-chains. Tokens are held — not circulating —
until the 10% fiat release gate is satisfied.

```
pacs.008 → Q4 gate (≥0.618034) → ByteID fold → Reserve check (20%) →
  → Anchor chain 42000 → Polygon anchor → Token mint → X-chain cascade → Pool
     └── HOLD until 10% fiat release received → Release tokens
```

---

## 2. Reserve Rule

```
reserve_required = tokenized_value × 0.20
```

- 20% of the tokenized value MUST be on reserve before tokens are minted.
- Reserve is denominated in the M1 intake currency (USD or EUR).
- Reserve is held in the OMNL savings account for the intake currency (acct 62 / 63).
- Tokens are minted but NOT released until the fiat release gate (§3) is met.
- ByteID of the reserve commitment is anchored alongside the token mint event.

**Q4 encoding of the reserve gate:**

```
reserve_sense = q4_encode(f"RESERVE:{byteid}:{reserve_required}:{currency}")
reserve_coherence = coherence_check(reserve_sense)   # must ≥ 0.618034
```

---

## 3. Fiat Release Gate

```
release_payment_required = tokenized_value × 0.10
```

- Tokens DO NOT circulate until 10% of the tokenized value is received in fiat cash
  (or equivalent: QUSD, stablecoin, confirmed bank credit).
- Release payment is verified via ISO 20022 camt.054 credit advice OR on-chain ERC-20
  transfer to the QVTX reserve wallet.
- On verified receipt: tokens are released, ByteID of the release event is anchored
  on chain 42000 + Polygon.

**Q4 release gate:**

```
release_sense = q4_encode(f"RELEASE:{byteid}:{payment_ref}:{amount_received}")
release_coherence = coherence_check(release_sense)   # must ≥ 0.618034
```

---

## 4. ByteID Identity Chain

Every tokenization event produces an identity chain:

```
L0  intake_byteid     = byteid_v3_pair(pacs008_xml)          # M1 intake fold
L1  reserve_byteid    = byteid_v3_pair(reserve_commitment)    # 20% reserve lock
L2  token_byteid      = byteid_v3_pair(token_mint_record)     # token identity
L3  release_byteid    = byteid_v3_pair(release_payment_proof) # 10% fiat receipt
L4  cascade_byteid    = byteid_v3_pair(xchain_deploy_record)  # X-chain cascade
```

Each ByteID: sense + shadow (reverse-complement), verify_v3 = REAL, recover() == content.

---

## 5. Chain Anchor: 42000 (native) + Polygon

For each tokenization event:

**Chain 42000 (native anchor):**
```
calldata = f"QVTX-TOKENIZE {token_byteid.sense}"
tx: self-tx, value=0, to=own_address, data=calldata
```

**Polygon (second anchor leg):**
```
calldata = JSON record: {
  "event": "TOKENIZE",
  "intake_byteid": "...",
  "token_byteid": "...",
  "reserve_byteid": "...",
  "amount": ...,
  "currency": "...",
  "reserve_held": ...,
  "release_required": ...,
  "ts": "ISO8601"
}
tx: self-tx, value=0, type-2, to=own_address, data=calldata
```

Both tx hashes are journaled. Re-read each tx and compare calldata to confirm anchor.

---

## 6. Token Mint on Chain 42000

Tokens minted as ERC-20 on chain 42000, one token per M1 intake event:

```
symbol:   SHWN-{intake_ref}        # unique per M1 event
name:     "Shawn M1 Token {ref}"
amount:   tokenized_value           # 1:1 with M1 intake value
decimals: 6
```

Token contract: deploy on chain 42000 using Paris EVM (--evm-version paris, no PUSH0).
Private RPC only: 192.168.11.211 (LAN) or qxrp 42000 node.
Tokens minted to QVTX reserve wallet — NOT to any external address until release gate fires.

---

## 7. X-Chain Cascade

After chain 42000 anchor + mint, tokens are cascaded to X-chains:

| Chain | Protocol | Method |
|-------|----------|--------|
| Polygon | wQVTX bridge | ERC-20 mint on Polygon via bridge relay |
| Chain 138 (D-BIS) | Native ERC-20 | Paris EVM deploy + mint |
| Chain 8825 (QVTX native) | Native | Direct mint |

Each X-chain cascade event:
1. ByteID fold of the cross-chain mint record
2. Anchor on chain 42000 (calldata: `QVTX-XCHAIN {chain_id} {token_byteid}`)
3. Token available in pool on each chain (WETH pair on D-BIS per existing pool pattern)

X-chain cascades are STAGED until Louis names each target chain for a specific tokenization.

---

## 8. Pool

Native pool on chain 42000: token / QUSD pair.
- Pool depth = tokenized_value × 0.20 (the reserve provides the pool liquidity).
- Pool price: 1 token = 1 unit of M1 intake currency (USD/EUR), denominated in QUSD.
- Pool is LOCKED until the release gate fires (10% fiat payment received).

X-chain pools mirror the native pool after cascade.

---

## 9. Interfaces

### 9A. Tokenize API (container extension)

POST `http://[2001:470:1f27:a5::1]:42881/tokenize`

```json
{
  "intake_byteid": "QVTX-...",
  "amount": 50000.00,
  "currency": "USD",
  "reserve_confirmed": true,
  "reserve_byteid": "QVTX-..."
}
```

Response: `202 Accepted` + `X-QVTX-Token-ByteID` header + camt.054 confirmation.

### 9B. Release API

POST `http://[2001:470:1f27:a5::1]:42881/release`

```json
{
  "token_byteid": "QVTX-...",
  "payment_ref": "...",
  "payment_amount": 5000.00,
  "payment_currency": "USD",
  "payment_proof": "txhash_or_swift_ref"
}
```

Response: `200 OK` + release ByteID + anchor tx hashes.

### 9C. Token Status

GET `http://[2001:470:1f27:a5::1]:42881/token/{byteid}`

Returns: token state (HELD / RELEASED), reserve status, pool depth, cascade chains.

---

## 10. Protocol Compliance

- ISO 20022 pacs.008.001.10 intake (inbound M1)
- ISO 20022 camt.054.001.10 response (outbound credit advice)
- Q4 coherence gate (1/φ = 0.618034) on every step
- ByteID v3 (sense + shadow, verify_v3 = REAL) on every fold
- Anchor Method: fold → verify REAL → chain 42000 → Polygon → readback → journal
- Paris EVM for all contract compiles (no PUSH0, chain 42000 + D-BIS chain 138)
- Private RPC only — no public endpoints

---

## 11. Staged Items

| Item | Why staged | What unblocks it |
|------|-----------|-----------------|
| X-chain cascade (Polygon, D-BIS, 8825) | Louis names each target per tokenization | Louis: "cascade to [chain]" |
| Token contract deploy on chain 42000 | Needs private RPC + signing key to fire | Louis fires `--execute` |
| Pool creation on chain 42000 | Needs token address from deploy | After token deploy |
| Release gate auto-watch service | Service on supercore (down) | Supercore back online |
