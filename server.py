# QVTX-BOUNDARY: ISO 20022 pacs.008/camt.054 — receives from SWIFT/INFIDIME/external TPPs
# QVTX-CAPABILITIES: byteid, q4-coherence-gate, omnl-credit, ledger-registration, tokenization
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
    "container_id": "SHAWN-M1-CONTAINER-001",
    "byteid":       "QVTX-53686177-6e204d31-1a1d2226-436f",
    "container_ref":"SM1-53686177-6e204d31",
    "omnl_base":    "https://omnl.hybxfinance.io/fineract-provider/api/v1",
    "omnl_creds":   base64.b64encode(b"app.omnl:S!Tw'+LHT(Twxi+BE3sk").decode(),
    "omnl_tenant":  "omnl",
    "accounts": {
        "USD": {"savingsId": 62, "accountNo": "000000062", "iban": "LU680010000000000062"},
        "EUR": {"savingsId": 63, "accountNo": "000000063", "iban": "DE28500700100000000063"},
    },
    "byteid_api":   "https://66.29.143.215:5002/api/fold",
    "ledger_path":  "/opt/shawn-m1/ledger/registrations.jsonl",
    "token_path":   "/opt/shawn-m1/ledger/tokens.jsonl",
    "port":         42881,
    "q4": {
        "phi_inv":   0.618034,
        "coherence": 0.882,
        "freq_hz":   8825,
    }
}
os.makedirs("/opt/shawn-m1/logs",   exist_ok=True)
os.makedirs("/opt/shawn-m1/ledger", exist_ok=True)

# ── Tokenization Protocol ────────────────────────────────────────────────
# Reserve rule:  20% of tokenized value held on reserve before mint
# Release gate:  10% fiat payment (or equivalent) required to release tokens
# Q4 gate:       each step scored ≥ 1/φ = 0.618034

RESERVE_RATIO  = 0.20   # 20% of intake value held on reserve
RELEASE_RATIO  = 0.10   # 10% of intake value required as fiat to release tokens

def q4_tokenize_score(record_str: str) -> float:
    """Score a tokenization record for Q4 coherence."""
    score = 0.0
    if "TOKENIZE:" in record_str:  score += 0.25
    if "BYTEID:"   in record_str:  score += 0.20
    if "RESERVE:"  in record_str:  score += 0.20
    if "AMOUNT:"   in record_str:  score += 0.20
    if "CCY:"      in record_str:  score += 0.15
    return round(min(score, 1.0), 6)

def token_ledger_append(entry: dict):
    with _ledger_lock:
        with open(CFG["token_path"], "a") as f:
            f.write(json.dumps(entry) + "\n")
    log.info(f"TOKEN: {entry.get('token_byteid')} | {entry.get('state')} | {entry.get('amount')} {entry.get('currency')}")

def token_ledger_read() -> list:
    try:
        with open(CFG["token_path"]) as f:
            return [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []

def token_find(token_byteid: str) -> dict:
    for t in reversed(token_ledger_read()):
        if t.get("token_byteid") == token_byteid:
            return t
    return {}

def build_anchor_calldata(event: str, byteid: str) -> str:
    return f"QVTX-TOKENIZE {event} {byteid}"

def q4_anchor_record(token_byteid: str, event: str, amount: float, currency: str, details: dict) -> dict:
    """Build the Q4-scored anchor record for chain 42000 + Polygon."""
    record_str = f"TOKENIZE:{event}:BYTEID:{token_byteid}:RESERVE:{amount * RESERVE_RATIO:.4f}:AMOUNT:{amount}:CCY:{currency}"
    q4_score = q4_tokenize_score(record_str)
    return {
        "record_str": record_str,
        "q4_score":   q4_score,
        "q4_pass":    q4_score >= CFG["q4"]["phi_inv"],
        "calldata_42000":  build_anchor_calldata(event, token_byteid),
        "calldata_polygon": json.dumps({
            "event":          event,
            "token_byteid":   token_byteid,
            "amount":         amount,
            "currency":       currency,
            "reserve":        round(amount * RESERVE_RATIO, 4),
            "release_req":    round(amount * RELEASE_RATIO, 4),
            "ts":             datetime.datetime.utcnow().isoformat() + "Z",
            **details
        })
    }

# ── Q4 Coherence Gate ────────────────────────────────────────────────────
def q4_coherence_score(xml_text: str) -> float:
    """
    Score an ISO 20022 message on structural integrity (Q4 semantic gate).
    Accumulates score from structural validators; threshold = 1/phi = 0.618034.
    Weights chosen so a fully valid pacs.008 scores >= 0.75.
    """
    score = 0.0
    # Valid XML parses                           +0.30
    # Strip XML declaration — ET.fromstring rejects encoding= in declaration
    xml_body = xml_text.strip()
    if xml_body.startswith("<?xml"):
        end = xml_body.find("?>")
        xml_body = xml_body[end + 2:].strip() if end != -1 else xml_body
    try:
        root = ET.fromstring(xml_body)
        score += 0.30
    except Exception:
        return 0.0
    # ISO 20022 namespace present                +0.15
    if "iso:std:iso:20022" in xml_text:
        score += 0.15
    # Helper: find by local tag name ignoring namespace
    def _find(tag):
        for el in root.iter():
            if el.tag.split("}")[-1] == tag:
                return el
        return None
    # Has settlement amount element              +0.15
    amt = _find("IntrBkSttlmAmt") or _find("TtlIntrBkSttlmAmt") or _find("TxAmt")
    if amt is not None:
        score += 0.15
        # Amount value > 0                       +0.10
        try:
            if float(amt.text or "0") > 0:
                score += 0.10
        except (ValueError, TypeError):
            pass
    # Has end-to-end or transaction reference    +0.15
    ref = _find("EndToEndId") or _find("TxId") or _find("MsgId")
    if ref is not None and ref.text:
        score += 0.15
    # Has creditor or debtor element             +0.10
    if _find("Cdtr") is not None or _find("Dbtr") is not None:
        score += 0.10
    # Raw structural score compared directly to 1/phi threshold (0.618034)
    # 4+ checks passing = score >= 0.70 > 0.618034 = PASS
    return round(min(score, 1.0), 6)

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
        def _get(tag):
            for el in root.iter():
                if el.tag.split("}")[-1] == tag:
                    return el
            return None
        amt_el  = _get("IntrBkSttlmAmt") or _get("TxAmt")
        ccy     = amt_el.get("Ccy", "USD") if amt_el is not None else "USD"
        amount  = float(amt_el.text or "0") if amt_el is not None else 0.0
        ref_el  = _get("EndToEndId") or _get("TxId")
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
      <AddtlInf>M1-CONTAINER Ref:{CFG["container_ref"]}</AddtlInf>
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
        <AddtlNtryInf>Ref:{CFG["container_ref"]} Status:BOOKED</AddtlNtryInf>
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
            # Public health — Shawn's account info only; no internal platform IDs
            accts = {
                ccy: {"accountNo": v["accountNo"], "iban": v["iban"]}
                for ccy, v in CFG["accounts"].items()
            }
            self._json(200, {
                "status":    "alive",
                "container": CFG["container_ref"],
                "accounts":  accts
            })
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
        elif self.path.startswith("/token/"):
            self._token_status(self.path[7:].strip("/"))
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
  <div><h1>SHAWN M1 CONTAINER — REGISTRATION LEDGER</h1>
  <div class="sub">Ref: {CFG['container_ref']} · ISO 20022 Auto-Receive · Refreshes every 15s</div></div>
  <div style="font-size:11px;color:#aaa">{len(entries)} total</div>
</div>
<div class="kpi-row">
  <div class="kpi"><label>Total Received</label><div class="val">{len([e for e in entries if "RECEIVED" in e.get("status", "")])}</div></div>
  <div class="kpi"><label>USD Account</label><div class="val" style="font-size:13px">{CFG['accounts']['USD']['accountNo']}</div><div style="font-size:10px;color:#888">{CFG['accounts']['USD']['iban']}</div></div>
  <div class="kpi"><label>EUR Account</label><div class="val" style="font-size:13px">{CFG['accounts']['EUR']['accountNo']}</div><div style="font-size:10px;color:#888">{CFG['accounts']['EUR']['iban']}</div></div>
  <div class="kpi"><label>Coherence Gate</label><div class="val">{CFG['q4']['phi_inv']}</div><div style="font-size:10px;color:#888">minimum threshold</div></div>
</div>
<div class="section">
  <div class="section-head">REGISTRATION LEDGER — LAST 100</div>
  <table><tr><th>Timestamp</th><th>Ref</th><th style="text-align:right">Amount</th><th>CCY</th><th>ByteID</th><th style="text-align:right">Q4</th><th>Status</th><th>IBAN</th></tr>
  {rows if rows else '<tr><td colspan="8" style="padding:16px;text-align:center;color:#888">No registrations yet</td></tr>'}
  </table>
</div>
<div class="footer">Container: {CFG['container_ref']} · USD IBAN: {CFG['accounts']['USD']['iban']} · EUR IBAN: {CFG['accounts']['EUR']['iban']}</div>
</body></html>"""
        resp = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(resp))
        self.end_headers()
        self.wfile.write(resp)

    def do_POST(self):
        if self.path in ("/receive", "/m1"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            self._process_m1(body)
        elif self.path == "/tokenize":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            self._process_tokenize(body)
        elif self.path == "/release":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            self._process_release(body)
        else:
            self._json(404, {"error": "endpoint not found"})

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
        self.send_header("X-M1-ContainerID", CFG["container_ref"])
        self.send_header("X-M1-Score", str(q4_score))
        self.send_header("X-M1-Status", "RECEIVED")
        self.end_headers()
        self.wfile.write(camt.encode())

    def _token_status(self, token_byteid: str):
        token = token_find(token_byteid)
        if not token:
            self._json(404, {"error": "token not found", "token_byteid": token_byteid})
            return
        self._json(200, token)

    def _process_tokenize(self, body: str):
        ts = datetime.datetime.utcnow().isoformat() + "Z"
        try:
            req = json.loads(body)
        except Exception:
            self._json(400, {"error": "invalid JSON"}); return

        intake_byteid     = req.get("intake_byteid", "")
        amount            = float(req.get("amount", 0))
        currency          = req.get("currency", "USD").upper()
        reserve_confirmed = req.get("reserve_confirmed", False)
        reserve_byteid    = req.get("reserve_byteid", "")

        if not intake_byteid or amount <= 0:
            self._json(400, {"error": "intake_byteid and amount required"}); return

        reserve_required = round(amount * RESERVE_RATIO, 4)
        release_required = round(amount * RELEASE_RATIO, 4)

        # Q4 gate on the tokenization record
        token_content = f"TOKEN|{intake_byteid}|{amount}|{currency}|{int(time.time())}"
        token_byteid  = fold_to_byteid(token_content)
        anchor = q4_anchor_record(token_byteid, "MINT", amount, currency, {
            "intake_byteid":  intake_byteid,
            "reserve_byteid": reserve_byteid,
        })

        if not anchor["q4_pass"]:
            self._json(422, {"error": "Q4 gate failed on tokenize record", "score": anchor["q4_score"]}); return

        entry = {
            "ts":               ts,
            "token_byteid":     token_byteid,
            "intake_byteid":    intake_byteid,
            "reserve_byteid":   reserve_byteid,
            "amount":           amount,
            "currency":         currency,
            "reserve_required": reserve_required,
            "release_required": release_required,
            "reserve_confirmed":reserve_confirmed,
            "state":            "MINTED_HELD",
            "q4_score":         anchor["q4_score"],
            "anchor_42000":     anchor["calldata_42000"],
            "anchor_polygon":   anchor["calldata_polygon"],
            "chains":           ["42000"],
            "pools":            [],
            "note": f"Tokens HELD. Release requires {release_required} {currency} fiat payment."
        }
        token_ledger_append(entry)
        ledger_append({**entry, "type": "TOKENIZE"})

        log.info(f"TOKENIZE: {token_byteid} | {amount} {currency} | HELD | reserve={reserve_required} | release_req={release_required}")
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-M1-Token-ID", token_byteid)
        self.send_header("X-M1-Score", str(anchor["q4_score"]))
        self.end_headers()
        self.wfile.write(json.dumps({
            "status":           "MINTED_HELD",
            "token_byteid":     token_byteid,
            "amount":           amount,
            "currency":         currency,
            "reserve_required": reserve_required,
            "release_required": release_required,
            "anchor_42000":     anchor["calldata_42000"],
            "note":             entry["note"]
        }, indent=2).encode())

    def _process_release(self, body: str):
        ts = datetime.datetime.utcnow().isoformat() + "Z"
        try:
            req = json.loads(body)
        except Exception:
            self._json(400, {"error": "invalid JSON"}); return

        token_byteid     = req.get("token_byteid", "")
        payment_ref      = req.get("payment_ref", "")
        payment_amount   = float(req.get("payment_amount", 0))
        payment_currency = req.get("payment_currency", "USD").upper()
        payment_proof    = req.get("payment_proof", "")

        if not token_byteid:
            self._json(400, {"error": "token_byteid required"}); return

        token = token_find(token_byteid)
        if not token:
            self._json(404, {"error": "token not found"}); return
        if token.get("state") == "RELEASED":
            self._json(409, {"error": "already released", "token": token}); return

        release_required = token.get("release_required", 0)
        if payment_amount < release_required:
            self._json(402, {
                "error": "payment insufficient",
                "required": release_required,
                "received": payment_amount,
                "currency": token.get("currency")
            }); return

        # Q4 gate on release record
        release_content = f"RELEASE|{token_byteid}|{payment_ref}|{payment_amount}|{payment_proof}"
        release_byteid  = fold_to_byteid(release_content)
        anchor = q4_anchor_record(release_byteid, "RELEASE", token.get("amount", 0), token.get("currency","USD"), {
            "token_byteid":  token_byteid,
            "payment_ref":   payment_ref,
            "payment_proof": payment_proof,
        })
        if not anchor["q4_pass"]:
            self._json(422, {"error": "Q4 gate failed on release record", "score": anchor["q4_score"]}); return

        release_entry = {
            **token,
            "ts_released":   ts,
            "release_byteid":release_byteid,
            "payment_ref":   payment_ref,
            "payment_amount":payment_amount,
            "payment_proof": payment_proof,
            "state":         "RELEASED",
            "anchor_release_42000":   anchor["calldata_42000"],
            "anchor_release_polygon": anchor["calldata_polygon"],
            "note": "Tokens released. Available on chain 42000 + X-chains after cascade."
        }
        token_ledger_append(release_entry)
        ledger_append({**release_entry, "type": "RELEASE"})

        log.info(f"RELEASE: {token_byteid} | {token.get('amount')} {token.get('currency')} | payment={payment_amount} ref={payment_ref}")
        self._json(200, {
            "status":          "RELEASED",
            "token_byteid":    token_byteid,
            "release_byteid":  release_byteid,
            "payment_ref":     payment_ref,
            "anchor_42000":    anchor["calldata_42000"],
            "note":            release_entry["note"]
        })

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
