# QVTX-BOUNDARY: ISO 20022 pacs.008/camt.054 — receives from SWIFT/INFIDIME/external TPPs
# QVTX-CAPABILITIES: byteid, q4-coherence-gate, omnl-credit, ledger-registration
"""
Shawn M1 Intake — Q4 Core Container
ISO 20022 auto-receive gate: pacs.008 in → Q4 gate → OMNL credit → ByteID → ledger → camt.054 out
"""
import os, json, time, hashlib, base64, datetime, logging, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import xml.etree.ElementTree as ET
import urllib.request, urllib.error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SHAWN-M1] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("/opt/shawn-m1/logs/server.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("shawn-m1")

# ── Config ──────────────────────────────────────────────────────────────
CFG = {
    "container_id": "SHAWN-Q4-M1-CONTAINER-001",
    "byteid":       "QVTX-53686177-6e204d31-1a1d2226-436f",
    "omnl_base":    "https://omnl.hybxfinance.io/fineract-provider/api/v1",
    "omnl_creds":   base64.b64encode(b"app.omnl:S!Tw'+LHT(Twxi+BE3sk").decode(),
    "omnl_tenant":  "omnl",
    "accounts": {
        "USD": {"savingsId": 62, "accountNo": "000000062", "iban": "LU680010000000000062"},
        "EUR": {"savingsId": 63, "accountNo": "000000063", "iban": "DE28500700100000000063"},
    },
    "byteid_api":   "https://66.29.143.215:5002/api/fold",
    "ledger_path":  "/opt/shawn-m1/ledger/registrations.jsonl",
    "port":         42881,
    "q4": {
        "phi_inv":   0.618034,
        "coherence": 0.882,
        "freq_hz":   8825,
    }
}
os.makedirs("/opt/shawn-m1/logs",   exist_ok=True)
os.makedirs("/opt/shawn-m1/ledger", exist_ok=True)

# ── Q4 Coherence Gate ────────────────────────────────────────────────────
def q4_coherence_score(xml_text: str) -> float:
    """Score the pacs.008 message against the Q4 golden ratio gate."""
    text = xml_text.upper()
    bases = {"A": 0, "C": 1, "G": 2, "T": 3}
    base_counts = {b: 0 for b in "ACGT"}
    for ch in text:
        if ch in base_counts:
            base_counts[ch] += 1
    total = sum(base_counts.values())
    if total == 0:
        return 0.0
    freqs = [base_counts[b] / total for b in "ACGT"]
    # Score = 1 - std_dev (higher when frequencies are balanced ~ golden ratio distribution)
    mean = sum(freqs) / 4
    variance = sum((f - mean) ** 2 for f in freqs) / 4
    std_dev = variance ** 0.5
    score = max(0.0, 1.0 - std_dev * 4)
    return round(score, 6)

def q4_gate_pass(xml_text: str) -> tuple:
    score = q4_coherence_score(xml_text)
    passed = score >= CFG["q4"]["phi_inv"]
    return passed, score

# ── ByteID fold via daemon API ───────────────────────────────────────────
def fold_to_byteid(content: str) -> str:
    try:
        payload = json.dumps({"content": content}).encode()
        req = urllib.request.Request(
            CFG["byteid_api"],
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("byteid", "QVTX-FOLD-ERROR")
    except Exception as e:
        log.warning(f"ByteID fold failed: {e}")
        sha = hashlib.sha256(content.encode()).hexdigest()
        return f"QVTX-LOCAL-{sha[:8]}-{sha[8:16]}-{sha[16:20]}-L-{sha[20:24]}"

# ── OMNL Credit ──────────────────────────────────────────────────────────
def omnl_credit(currency: str, amount: float, tx_ref: str) -> dict:
    acct = CFG["accounts"].get(currency)
    if not acct:
        return {"error": f"no account for {currency}"}
    sid = acct["savingsId"]
    today = datetime.date.today().strftime("%d %B %Y")
    payload = json.dumps({
        "transactionDate": today,
        "transactionAmount": str(amount),
        "paymentTypeId": 1,
        "note": f"M1-INTAKE {tx_ref}",
        "dateFormat": "dd MMMM yyyy",
        "locale": "en"
    }).encode()
    headers = {
        "Fineract-Platform-TenantId": CFG["omnl_tenant"],
        "Authorization": "Basic " + CFG["omnl_creds"],
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    req = urllib.request.Request(
        f"{CFG['omnl_base']}/savingsaccounts/{sid}/transactions?command=deposit",
        data=payload, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return {"ok": True, "result": json.loads(resp.read())}
    except Exception as e:
        return {"error": str(e)}

# ── Ledger append ────────────────────────────────────────────────────────
_ledger_lock = threading.Lock()

def ledger_append(entry: dict):
    with _ledger_lock:
        with open(CFG["ledger_path"], "a") as f:
            f.write(json.dumps(entry) + "\n")
    log.info(f"LEDGER: {entry.get('byteid')} | {entry.get('amount')} {entry.get('currency')} | {entry.get('status')}")

# ── ISO 20022 pacs.008 parser ────────────────────────────────────────────
def parse_pacs008(xml_text: str) -> dict:
    try:
        root = ET.fromstring(xml_text)
        ns = {"p": "urn:iso:std:iso:20022:tech:xsd:pacs.008.001.10"}
        # Try without namespace if namespace not found
        def find(path):
            r = root.find(path, ns)
            if r is None:
                r = root.find(path.replace("p:", "").replace("{urn:iso:std:iso:20022:tech:xsd:pacs.008.001.10}", ""))
            return r
        amt_el  = root.find(".//{*}IntrBkSttlmAmt") or root.find(".//{*}TxAmt")
        ccy     = amt_el.get("Ccy", "USD") if amt_el is not None else "USD"
        amount  = float(amt_el.text or "0") if amt_el is not None else 0.0
        ref_el  = root.find(".//{*}EndToEndId") or root.find(".//{*}TxId")
        tx_ref  = ref_el.text if ref_el is not None else f"M1-{int(time.time())}"
        return {"ccy": ccy, "amount": amount, "tx_ref": tx_ref, "raw_hash": hashlib.sha256(xml_text.encode()).hexdigest()[:16]}
    except Exception as e:
        return {"error": str(e)}

# ── camt.054 credit notification ─────────────────────────────────────────
def build_camt054(tx_ref: str, amount: float, ccy: str, byteid: str) -> str:
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    acct = CFG["accounts"].get(ccy, CFG["accounts"]["USD"])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.054.001.10">
  <BkToCstmrDbtCdtNtfctn>
    <GrpHdr>
      <MsgId>CAMT054-{tx_ref}</MsgId>
      <CreDtTm>{ts}</CreDtTm>
      <AddtlInf>QVTX-Q4-CONTAINER ByteID:{byteid}</AddtlInf>
    </GrpHdr>
    <Ntfctn>
      <Id>{CFG["container_id"]}</Id>
      <Acct>
        <Id><IBAN>{acct["iban"]}</IBAN></Id>
        <Nm>SHAWN M1 INTAKE CONTAINER</Nm>
      </Acct>
      <Ntry>
        <Amt Ccy="{ccy}">{amount:.2f}</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Sts><Cd>BOOK</Cd></Sts>
        <BookgDt><DtTm>{ts}</DtTm></BookgDt>
        <NtryRef>{tx_ref}</NtryRef>
        <AddtlNtryInf>ByteID:{byteid} Q4:PASSED</AddtlNtryInf>
      </Ntry>
    </Ntfctn>
  </BkToCstmrDbtCdtNtfctn>
</Document>"""

# ── HTTP Handler ─────────────────────────────────────────────────────────
class M1IntakeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info(fmt % args)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "alive", "container": CFG["container_id"],
                             "byteid": CFG["byteid"], "accounts": CFG["accounts"]})
        elif self.path == "/ledger":
            accept = self.headers.get("Accept", "")
            if "text/html" in accept:
                self._ledger_html()
            else:
                try:
                    with open(CFG["ledger_path"]) as f:
                        entries = [json.loads(l) for l in f if l.strip()]
                    self._json(200, {"count": len(entries), "entries": entries[-20:]})
                except FileNotFoundError:
                    self._json(200, {"count": 0, "entries": []})
        elif self.path == "/":
            self._ledger_html()
        else:
            self._json(404, {"error": "not found"})

    def _ledger_html(self):
        try:
            with open(CFG["ledger_path"]) as f:
                entries = [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError:
            entries = []
        rows = ""
        for e in reversed(entries[-100:]):
            status = e.get("status", "?")
            pill = "pill-green" if status == "RECEIVED" else "pill-orange" if "PENDING" in status else "pill-red"
            q4 = e.get("q4_score", "")
            rows += f"""<tr>
  <td class="mono">{e.get('ts','')[:19].replace('T',' ')}</td>
  <td class="mono">{e.get('tx_ref','')}</td>
  <td style="text-align:right">{e.get('amount','')}</td>
  <td>{e.get('currency','')}</td>
  <td class="mono" style="font-size:11px">{e.get('byteid','')}</td>
  <td style="text-align:right">{q4}</td>
  <td><span class="pill {pill}">{status}</span></td>
  <td class="mono">{e.get('iban','')}</td>
</tr>"""
        html = f"""<!doctype html><html><head>
<meta charset="utf-8"><title>Shawn M1 Ledger</title>
<meta http-equiv="refresh" content="15">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;600;700&display=swap');
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Open Sans',sans-serif;font-size:13px;background:#f5f5f5;color:#000;padding:24px}}
  .header{{background:#000;color:#fff;padding:14px 20px;margin-bottom:12px;display:flex;justify-content:space-between;align-items:center}}
  .header h1{{font-size:15px;font-weight:700;letter-spacing:1px}}
  .header .sub{{font-size:11px;color:#aaa}}
  .kpi-row{{display:flex;gap:1px;background:#cfcfcf;margin-bottom:12px}}
  .kpi{{flex:1;background:#fff;padding:10px 14px}}
  .kpi label{{font-size:10px;color:#666;text-transform:uppercase;letter-spacing:0.5px;display:block;margin-bottom:3px}}
  .kpi .val{{font-size:16px;font-weight:700}}
  .section{{background:#fff;border:1px solid #cfcfcf;margin-bottom:12px}}
  .section-head{{background:#000;color:#fff;font-weight:700;font-size:11px;letter-spacing:1px;padding:5px 10px;text-transform:uppercase}}
  table{{width:100%;border-collapse:collapse}}
  th,td{{padding:4px 8px;border-bottom:1px solid #e8e8e8;text-align:left;vertical-align:top}}
  tr:last-child td{{border-bottom:none}}
  th{{background:#f5f5f5;font-size:10px;text-transform:uppercase;letter-spacing:0.5px;color:#555;border-bottom:1px solid #cfcfcf}}
  .mono{{font-family:monospace;font-size:11px}}
  .pill{{display:inline-block;padding:2px 7px;border-radius:2px;font-size:11px;font-weight:700}}
  .pill-green{{background:#d4f4d4;color:#1a7a1a}}
  .pill-orange{{background:#fff3cd;color:#856404}}
  .pill-red{{background:#f8d7da;color:#842029}}
  .footer{{font-size:10px;color:#888;border-top:1px solid #cfcfcf;padding-top:8px;margin-top:12px}}
</style></head><body>
<div class="header">
  <div><h1>SHAWN M1 Q4 CONTAINER — LEDGER</h1>
  <div class="sub">ByteID: {CFG['byteid']} · ISO 20022 pacs.008 Auto-Receive · Refreshes every 15s</div></div>
  <div style="font-size:11px;color:#aaa">{len(entries)} total</div>
</div>
<div class="kpi-row">
  <div class="kpi"><label>Total Received</label><div class="val">{len([e for e in entries if 'RECEIVED' in e.get('status',''')])}</div></div>
  <div class="kpi"><label>USD Acct</label><div class="val" style="font-size:13px">{CFG['accounts']['USD']['accountNo']}</div><div style="font-size:10px;color:#888">{CFG['accounts']['USD']['iban']}</div></div>
  <div class="kpi"><label>EUR Acct</label><div class="val" style="font-size:13px">{CFG['accounts']['EUR']['accountNo']}</div><div style="font-size:10px;color:#888">{CFG['accounts']['EUR']['iban']}</div></div>
  <div class="kpi"><label>Q4 Threshold</label><div class="val">{CFG['q4']['phi_inv']}</div><div style="font-size:10px;color:#888">1/φ golden ratio</div></div>
</div>
<div class="section">
  <div class="section-head">REGISTRATION LEDGER — LAST 100</div>
  <table><tr><th>Timestamp</th><th>Ref</th><th style="text-align:right">Amount</th><th>CCY</th><th>ByteID</th><th style="text-align:right">Q4</th><th>Status</th><th>IBAN</th></tr>
  {rows if rows else '<tr><td colspan="8" style="padding:16px;text-align:center;color:#888">No registrations yet</td></tr>'}
  </table>
</div>
<div class="footer">Container: {CFG['container_id']} · OMNL clientId 30 · omnl.hybxfinance.io · NOT published publicly</div>
</body></html>"""
        resp = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(resp))
        self.end_headers()
        self.wfile.write(resp)

    def do_POST(self):
        if self.path not in ("/receive", "/m1"):
            self._json(404, {"error": "endpoint not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        self._process_m1(body)

    def _process_m1(self, xml_text: str):
        ts = datetime.datetime.utcnow().isoformat() + "Z"

        # 1. Q4 Coherence Gate
        gate_pass, q4_score = q4_gate_pass(xml_text)
        if not gate_pass:
            log.warning(f"Q4 GATE REJECT: score={q4_score} < {CFG['q4']['phi_inv']}")
            entry = {"ts": ts, "status": "Q4_REJECT", "q4_score": q4_score}
            ledger_append(entry)
            self._json(422, {"error": "Q4 gate failed", "score": q4_score, "threshold": CFG["q4"]["phi_inv"]})
            return

        # 2. Parse pacs.008
        parsed = parse_pacs008(xml_text)
        if "error" in parsed:
            self._json(400, {"error": "parse failed", "detail": parsed["error"]})
            return
        ccy, amount, tx_ref = parsed["ccy"], parsed["amount"], parsed["tx_ref"]
        log.info(f"M1 INTAKE: {amount} {ccy} ref={tx_ref} q4={q4_score}")

        # 3. Fold to ByteID
        content_to_fold = f"M1|{tx_ref}|{amount}|{ccy}|{parsed['raw_hash']}"
        byteid = fold_to_byteid(content_to_fold)

        # 4. Credit OMNL account
        credit_result = omnl_credit(ccy, amount, tx_ref)
        credit_ok = credit_result.get("ok", False)

        # 5. Ledger registration
        entry = {
            "ts": ts,
            "tx_ref": tx_ref,
            "amount": amount,
            "currency": ccy,
            "byteid": byteid,
            "q4_score": q4_score,
            "omnl_credit": credit_ok,
            "status": "RECEIVED" if credit_ok else "RECEIVED_PENDING_CREDIT",
            "raw_hash": parsed["raw_hash"],
            "container": CFG["container_id"],
            "accounts": CFG["accounts"][ccy]["accountNo"] if ccy in CFG["accounts"] else "?",
            "iban": CFG["accounts"][ccy]["iban"] if ccy in CFG["accounts"] else "?"
        }
        ledger_append(entry)

        # 6. Build camt.054 response
        camt = build_camt054(tx_ref, amount, ccy, byteid)
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("X-QVTX-ByteID", byteid)
        self.send_header("X-QVTX-Q4-Score", str(q4_score))
        self.send_header("X-QVTX-Status", "RECEIVED")
        self.end_headers()
        self.wfile.write(camt.encode())

    def _json(self, code: int, body: dict):
        resp = json.dumps(body, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(resp))
        self.end_headers()
        self.wfile.write(resp)


if __name__ == "__main__":
    port = int(os.environ.get("M1_PORT", CFG["port"]))
    srv = HTTPServer(("0.0.0.0", port), M1IntakeHandler)
    log.info(f"Shawn M1 Intake Container LIVE on 0.0.0.0:{port}")
    log.info(f"Container ByteID: {CFG['byteid']}")
    log.info(f"OMNL USD: acct {CFG['accounts']['USD']['accountNo']} IBAN {CFG['accounts']['USD']['iban']}")
    log.info(f"OMNL EUR: acct {CFG['accounts']['EUR']['accountNo']} IBAN {CFG['accounts']['EUR']['iban']}")
    srv.serve_forever()
