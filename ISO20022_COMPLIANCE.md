# Shawn M1 Q4 Container — ISO 20022 Protocol Compliance

## Standards Reference
Source: https://www.iso20022.org/iso-20022-message-definitions

---

## Supported Message Types

| Message ID | Full Name | Direction | Use |
|---|---|---|---|
| `pacs.008.001.10` | FIToFICustomerCreditTransfer | **INBOUND** | M1 intake — customer credit transfers received from SWIFT/INFIDIME |
| `camt.054.001.10` | BankToCustomerDebitCreditNotification | **OUTBOUND** | Auto-issued on receipt — credit confirmation to originating institution |

---

## M1 Intake Procedure (pacs.008 → Q4 Gate → OMNL → camt.054)

Fully compliant with ISO 20022 Payment Clearing and Settlement (PCSS) workflow:

### Step 1 — RECEIVE (pacs.008.001.10)
- POST to `/receive` or `/m1`
- Accepts XML body with namespace `urn:iso:std:iso:20022:tech:xsd:pacs.008.001.10`
- Required fields parsed:
  - `IntrBkSttlmAmt` — interbank settlement amount + currency (Ccy attribute)
  - `EndToEndId` — end-to-end transaction reference
- Reference: ISO 20022 message definition pacs.008 → https://www.iso20022.org/message/pacs008

### Step 2 — Q4 COHERENCE GATE
- Validates message integrity via Q4 quaternary scoring
- Threshold: `score ≥ 1/φ = 0.618034` (golden ratio gate)
- Messages scoring below threshold receive HTTP 422 and are logged as `Q4_REJECT`
- This gate enforces QVTX Delivery Process (QDP) Step 7: **golden fit-gate**

### Step 3 — CREDIT (OMNL Fineract)
- Credits the appropriate OMNL savings account based on currency:
  - USD → Account `000000062` (IBAN: LU680010000000000062)
  - EUR → Account `000000063` (IBAN: DE28500700100000000063)
- Posts via Fineract `/savingsaccounts/{id}/transactions?command=deposit`
- Fineract tenant: `omnl` (omnl.hybxfinance.io)

### Step 4 — BYTEID FOLD
- Folds `M1|{tx_ref}|{amount}|{ccy}|{raw_hash}` → ByteID
- ByteID is the QVTX L0 identity of this intake event
- Stored in ledger and returned in response headers

### Step 5 — LEDGER REGISTRATION
- Appended to `/opt/shawn-m1/ledger/registrations.jsonl`
- Each entry carries: `ts`, `tx_ref`, `amount`, `currency`, `byteid`, `q4_score`, `omnl_credit`, `status`, `iban`
- Status values: `RECEIVED`, `RECEIVED_PENDING_CREDIT`, `Q4_REJECT`

### Step 6 — ISSUE RECEIPT (camt.054.001.10)
- Returns XML body with namespace `urn:iso:std:iso:20022:tech:xsd:camt.054.001.10`
- Conforms to: BankToCustomerDebitCreditNotification
  - `MsgId` = `CAMT054-{tx_ref}`
  - `Acct/Id/IBAN` = receiving IBAN
  - `Ntry/Amt` = credited amount + currency
  - `CdtDbtInd` = `CRDT`
  - `Sts/Cd` = `BOOK` (booked)
  - `AddtlNtryInf` includes ByteID and Q4 gate result
- Reference: ISO 20022 message definition camt.054 → https://www.iso20022.org/message/camt054

---

## HTTP Response Headers (QVTX Extensions)
| Header | Value |
|---|---|
| `X-QVTX-ByteID` | ByteID of the intake event |
| `X-QVTX-Q4-Score` | Q4 coherence score |
| `X-QVTX-Status` | `RECEIVED` |
| `Content-Type` | `application/xml` |

---

## SWIFT/INFIDIME Debit Path
Incoming SWIFT MT103/MT202 → INFIDIME DEUTDEFFXXX (DE39500700100036816700) → OMNL Fineract → pacs.008 posted to this endpoint.

---

## ISO 20022 Compliance Points
1. XML namespace is validated by parser (ElementTree with wildcard namespace matching)
2. Amount element `Ccy` attribute is preserved verbatim from inbound message
3. `EndToEndId` is preserved as `NtryRef` in outbound camt.054
4. `CreDtTm` in camt.054 uses UTC ISO 8601 format per standard
5. `CdtDbtInd` = `CRDT` per camt.054 Booked Credit Notification pattern
6. `Sts/Cd` = `BOOK` per ACTC (accepted technical confirmation) → BOOK flow

## References
- ISO 20022 Message Catalogue: https://www.iso20022.org/iso-20022-message-definitions
- pacs.008.001.10: https://www.iso20022.org/message/pacs008
- camt.054.001.10: https://www.iso20022.org/message/camt054
- SWIFT Message Implementation Guidelines: https://www.swift.com/standards/data-standards/iso-20022
