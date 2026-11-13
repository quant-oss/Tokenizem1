# QVTX-BOUNDARY: E2E test client — boundary artefact for external ISO 20022 test tool
# QVTX-CAPABILITIES: none — read-only test probe
"""
End-to-end test: POST pacs.008 → verify Q4 gate + OMNL credit → check camt.054 response + ledger.
Run against local container or qxrp.
Usage:
  python3 test_e2e.py                        # localhost:8881
  python3 test_e2e.py <host>                 # remote host
"""
import sys, json, time, datetime
import urllib.request, urllib.error

HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost"
BASE = f"http://{HOST}:42881"

PACS008_USD = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.10">
  <FIToFICstmrCdtTrf>
    <GrpHdr>
      <MsgId>TEST-SHAWN-USD-001</MsgId>
      <CreDtTm>{ts}</CreDtTm>
      <NbOfTxs>1</NbOfTxs>
      <TtlIntrBkSttlmAmt Ccy="USD">50000.00</TtlIntrBkSttlmAmt>
      <SttlmInf><SttlmMtd>CLRG</SttlmMtd></SttlmInf>
    </GrpHdr>
    <CdtTrfTxInf>
      <PmtId>
        <InstrId>INSTR-001</InstrId>
        <EndToEndId>E2E-SHAWN-USD-{stamp}</EndToEndId>
        <TxId>TXN-{stamp}</TxId>
      </PmtId>
      <IntrBkSttlmAmt Ccy="USD">50000.00</IntrBkSttlmAmt>
      <ChrgBr>SHAR</ChrgBr>
      <Dbtr><Nm>TEST SENDER INSTITUTION</Nm></Dbtr>
      <DbtrAgt><FinInstnId><BIC>DEUTDEFFXXX</BIC></FinInstnId></DbtrAgt>
      <CdtrAgt><FinInstnId><BIC>QVTXBIC0XXX</BIC></FinInstnId></CdtrAgt>
      <Cdtr><Nm>SHAWN M1 Q4 CONTAINER</Nm></Cdtr>
      <CdtrAcct><Id><IBAN>LU680010000000000062</IBAN></Id></CdtrAcct>
    </CdtTrfTxInf>
  </FIToFICstmrCdtTrf>
</Document>"""

PACS008_EUR = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.10">
  <FIToFICstmrCdtTrf>
    <GrpHdr>
      <MsgId>TEST-SHAWN-EUR-001</MsgId>
      <CreDtTm>{ts}</CreDtTm>
      <NbOfTxs>1</NbOfTxs>
      <TtlIntrBkSttlmAmt Ccy="EUR">25000.00</TtlIntrBkSttlmAmt>
      <SttlmInf><SttlmMtd>CLRG</SttlmMtd></SttlmInf>
    </GrpHdr>
    <CdtTrfTxInf>
      <PmtId>
        <InstrId>INSTR-002</InstrId>
        <EndToEndId>E2E-SHAWN-EUR-{stamp}</EndToEndId>
        <TxId>TXN-EUR-{stamp}</TxId>
      </PmtId>
      <IntrBkSttlmAmt Ccy="EUR">25000.00</IntrBkSttlmAmt>
      <ChrgBr>SHAR</ChrgBr>
      <Dbtr><Nm>TEST SENDER INSTITUTION</Nm></Dbtr>
      <DbtrAgt><FinInstnId><BIC>DEUTDEFFXXX</BIC></FinInstnId></DbtrAgt>
      <CdtrAgt><FinInstnId><BIC>QVTXBIC0XXX</BIC></FinInstnId></CdtrAgt>
      <Cdtr><Nm>SHAWN M1 Q4 CONTAINER</Nm></Cdtr>
      <CdtrAcct><Id><IBAN>DE28500700100000000063</IBAN></Id></CdtrAcct>
    </CdtTrfTxInf>
  </FIToFICstmrCdtTrf>
</Document>"""

def run():
    stamp = int(time.time())
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    passed = 0
    failed = 0

    print(f"\n{'='*60}")
    print(f"SHAWN M1 Q4 CONTAINER — END-TO-END TEST")
    print(f"Target: {BASE}")
    print(f"{'='*60}\n")

    # 1. Health check
    print("[1] Health check")
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=10) as r:
            h = json.loads(r.read())
            cref = h.get('container', h.get('byteid', '?'))
            print(f"    OK  status={h['status']}  container={cref}")
            passed += 1
    except Exception as e:
        print(f"    FAIL  {e}")
        failed += 1

    # 2. USD intake
    print("\n[2] USD pacs.008 intake (50,000 USD)")
    xml = PACS008_USD.format(ts=ts, stamp=stamp)
    try:
        req = urllib.request.Request(f"{BASE}/receive",
            data=xml.encode(), method="POST",
            headers={"Content-Type": "application/xml", "Accept": "application/xml"})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode()
            byteid = r.headers.get("X-QVTX-ByteID", "?")
            q4 = r.headers.get("X-QVTX-Q4-Score", "?")
            status = r.headers.get("X-QVTX-Status", "?")
            assert "BOOK" in body, "camt.054 missing BOOK status"
            assert "camt.054" in body, "Response not camt.054"
            print(f"    OK  status={status}  q4={q4}  byteid={byteid[:30]}...")
            print(f"        camt.054 response received — BOOK credit confirmed")
            passed += 1
    except Exception as e:
        print(f"    FAIL  {e}")
        failed += 1

    time.sleep(1)

    # 3. EUR intake
    print("\n[3] EUR pacs.008 intake (25,000 EUR)")
    xml_eur = PACS008_EUR.format(ts=ts, stamp=stamp)
    try:
        req = urllib.request.Request(f"{BASE}/receive",
            data=xml_eur.encode(), method="POST",
            headers={"Content-Type": "application/xml", "Accept": "application/xml"})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode()
            byteid = r.headers.get("X-QVTX-ByteID", "?")
            q4 = r.headers.get("X-QVTX-Q4-Score", "?")
            assert "BOOK" in body
            print(f"    OK  q4={q4}  byteid={byteid[:30]}...")
            passed += 1
    except Exception as e:
        print(f"    FAIL  {e}")
        failed += 1

    time.sleep(1)

    # 4. Ledger check
    print("\n[4] Ledger registration check")
    try:
        with urllib.request.urlopen(f"{BASE}/ledger", timeout=10) as r:
            ledger = json.loads(r.read())
            count = ledger.get("count", 0)
            print(f"    OK  {count} total registrations in ledger")
            if ledger.get("entries"):
                last = ledger["entries"][-1]
                print(f"        last: {last.get('tx_ref','?')} {last.get('amount','?')} {last.get('currency','?')} {last.get('status','?')}")
            passed += 1
    except Exception as e:
        print(f"    FAIL  {e}")
        failed += 1

    # 5. Ledger HTML page check
    print("\n[5] Ledger HTML page (Docker-hosted)")
    try:
        req = urllib.request.Request(f"{BASE}/",
            headers={"Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode()
            assert "SHAWN M1" in html
            assert "REGISTRATION LEDGER" in html
            print(f"    OK  HTML ledger page served ({len(html)} bytes)")
            passed += 1
    except Exception as e:
        print(f"    FAIL  {e}")
        failed += 1

    print(f"\n{'='*60}")
    print(f"RESULT: {passed} passed / {failed} failed")
    print(f"{'='*60}\n")

    if failed:
        sys.exit(1)

if __name__ == "__main__":
    run()
