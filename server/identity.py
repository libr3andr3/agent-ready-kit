"""Optional agent identity: an agent IS its key pair. Post-quantum first.

Any agent can do it alone, with no registry, operator or third party:
  1. create a key pair. Recommended: ML-DSA-65 (NIST FIPS 204, post-quantum), e.g.
     `openssl genpkey -algorithm ML-DSA-65` (OpenSSL 3.5+). Also accepted: ML-DSA-44/87, and Ed25519 for agents
     whose tooling has no ML-DSA yet (classic: not quantum-safe, labeled as such).
  2. POST /identity/challenge                         -> {"nonce", "message"} (one use, 5 minutes)
  3. sign `message` with its private key and POST /identity/register
     {"public_key": PEM | base64 DER (SubjectPublicKeyInfo) | base64 raw Ed25519, "nonce", "signature": base64,
      "label"?: str}
     -> {"agent_id": "<alg>:<16 hex of sha256(raw public key)>", "algorithm", "post_quantum", "token", "token_expires"}
We store the public key; its fingerprint is the agent's identifier from then on. The token is a convenience for
clients that can only send a fixed header (Authorization: Bearer <token>, e.g. `claude mcp add --header`);
registering again with the same key renews it.

Optional: anonymous agents keep the same public tools. The signature proves the agent holds the key, so nobody can
register someone else's public key as their own.
Verification: Ed25519 with `cryptography`; ML-DSA with the system `openssl` (3.5+), called without a shell, on
temporary files, with a timeout (the Python library has no ML-DSA yet). If either is missing, that algorithm is
reported unavailable and the rest keeps working.
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    HAVE_ED25519 = True
except ImportError:
    HAVE_ED25519 = False

PREFIX = "site identity v1"  # set by configure(): "<host> identity v1"
NONCE_TTL = 300
TOKEN_DAYS = 30
MAX_NONCES = 5000
SITE_URL = ""
SITE_NAME = "this site"
SPEC_URL = "/identity"
RECOMMENDED = "ml-dsa-65"

# SubjectPublicKeyInfo header (DER) and sizes per algorithm. ML-DSA OIDs 2.16.840.1.101.3.4.3.17/18/19 (FIPS 204).
ALGS = {
    "ml-dsa-44": {"hdr": bytes.fromhex("30820532300b06096086480165030403110382052100"), "pk": 1312, "sig": 2420, "pq": True},
    "ml-dsa-65": {"hdr": bytes.fromhex("308207b2300b0609608648016503040312038207a100"), "pk": 1952, "sig": 3309, "pq": True},
    "ml-dsa-87": {"hdr": bytes.fromhex("30820a32300b060960864801650304031303820a2100"), "pk": 2592, "sig": 4627, "pq": True},
    "ed25519": {"hdr": bytes.fromhex("302a300506032b6570032100"), "pk": 32, "sig": 64, "pq": False},
}
SHORT = {"ml-dsa-44": "mldsa44", "ml-dsa-65": "mldsa65", "ml-dsa-87": "mldsa87", "ed25519": "ed25519"}

_lock = threading.Lock()
_nonces = {}
_state = {"key": b"", "db": None, "openssl": None, "algs": []}


def configure(site_url, site_name):
    """Bind the identity texts to the site that serves them (call before init)."""
    global PREFIX, SITE_URL, SITE_NAME, SPEC_URL
    SITE_URL, SITE_NAME = site_url.rstrip("/"), site_name
    host = re.sub(r"^https?://", "", SITE_URL).split("/")[0] or "site"
    PREFIX, SPEC_URL = f"{host} identity v1", SITE_URL + "/identity"


def _openssl_mldsa():
    """Path of an `openssl` that lists ML-DSA, or None."""
    exe = shutil.which("openssl")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "list", "-signature-algorithms"], capture_output=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return exe if b"ML-DSA-65" in out.upper() else None


def init(secret_path, db_factory):
    """Token secret (created once, mode 600), the identities table and the available algorithms."""
    p = Path(secret_path)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(os.urandom(32))
    exe = _openssl_mldsa()
    algs = ([a for a in ALGS if a.startswith("ml-dsa")] if exe else []) + (["ed25519"] if HAVE_ED25519 else [])
    _state.update(key=p.read_bytes()[:32], db=db_factory, openssl=exe, algs=algs)
    with db_factory() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS agent_identities (agent_id TEXT PRIMARY KEY, algorithm TEXT NOT NULL,
                       public_key TEXT NOT NULL, label TEXT, first_seen TEXT, last_seen TEXT, registrations INTEGER)""")
    return bool(algs)


def ready():
    return bool(_state["key"]) and bool(_state["algs"])


def _iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t or time.time()))


def parse_public_key(text):
    """PEM, base64 DER (SubjectPublicKeyInfo) or base64 raw Ed25519 -> (algorithm, raw public key), or (None, b"").
    Never raises: only the exact SPKI layouts of ALGS are accepted."""
    if not isinstance(text, str) or not text.strip() or len(text) > 6000:
        return None, b""
    s = text.strip()
    if "BEGIN PUBLIC KEY" in s:
        m = re.search(r"-----BEGIN PUBLIC KEY-----(.*?)-----END PUBLIC KEY-----", s, re.S)
        if not m:
            return None, b""
        s = m.group(1)
    try:
        der = base64.b64decode(re.sub(r"\s+", "", s), validate=True)
    except (binascii.Error, ValueError):
        return None, b""
    if len(der) == 32:
        return "ed25519", der
    for alg, a in ALGS.items():
        if der.startswith(a["hdr"]) and len(der) == len(a["hdr"]) + a["pk"]:
            return alg, der[len(a["hdr"]):]
    return None, b""


def _verify_mldsa(alg, raw, msg, sig):
    exe = _state["openssl"]
    if not exe or len(sig) != ALGS[alg]["sig"]:
        return False
    der = ALGS[alg]["hdr"] + raw
    pem = "-----BEGIN PUBLIC KEY-----\n" + "\n".join(
        base64.b64encode(der).decode()[i:i + 64] for i in range(0, len(base64.b64encode(der)), 64)) + "\n-----END PUBLIC KEY-----\n"
    with tempfile.TemporaryDirectory(prefix="agent-id-") as d:
        (Path(d) / "pub.pem").write_text(pem)
        (Path(d) / "msg.bin").write_bytes(msg)
        (Path(d) / "sig.bin").write_bytes(sig)
        try:
            r = subprocess.run([exe, "pkeyutl", "-verify", "-pubin", "-inkey", str(Path(d) / "pub.pem"), "-rawin",
                                "-in", str(Path(d) / "msg.bin"), "-sigfile", str(Path(d) / "sig.bin")],
                               capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return False
    return r.returncode == 0 and b"Signature Verified Successfully" in r.stdout


def verify(alg, raw, msg, sig):
    if alg not in _state["algs"]:
        return False
    if alg == "ed25519":
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(sig, msg)
            return True
        except (InvalidSignature, ValueError):
            return False
    return _verify_mldsa(alg, raw, msg, sig)


def agent_id_of(alg, raw):
    return f"{SHORT[alg]}:" + hashlib.sha256(raw).hexdigest()[:16]


def challenge():
    if not ready():
        return 503, {"error": "identity unavailable"}
    now = time.time()
    nonce = os.urandom(16).hex()
    with _lock:
        for k in [k for k, t in _nonces.items() if now - t > NONCE_TTL][:1000]:
            _nonces.pop(k, None)
        if len(_nonces) >= MAX_NONCES:
            return 503, {"error": "too many open challenges; retry shortly"}
        _nonces[nonce] = now
    return 200, {"nonce": nonce, "message": f"{PREFIX}\n{nonce}", "expires_in": NONCE_TTL,
                 "recommended_algorithm": "ML-DSA-65", "accepted_algorithms": [a.upper() for a in _state["algs"]],
                 "next": "Sign `message` (UTF-8 bytes, no trailing newline) with your private key and POST "
                         "{public_key, nonce, signature} to " + SITE_URL + "/identity/register."}


def register(msg):
    if not ready():
        return 503, {"error": "identity unavailable"}
    if not isinstance(msg, dict):
        return 400, {"error": f"expected JSON {{public_key, nonce, signature}}; see {SPEC_URL}"}
    nonce = str(msg.get("nonce") or "")[:64]
    with _lock:
        t = _nonces.pop(nonce, None)  # one use: a replayed registration finds no nonce
    if t is None or time.time() - t > NONCE_TTL:
        return 400, {"error": "unknown, used or expired nonce; ask for a new one at /identity/challenge"}
    alg, raw = parse_public_key(msg.get("public_key"))
    if alg is None:
        return 400, {"error": "public_key must be ML-DSA-44/65/87 or Ed25519 (PEM or base64 SubjectPublicKeyInfo)"}
    if alg not in _state["algs"]:
        return 400, {"error": f"{alg.upper()} is not available on this server right now; accepted: "
                              + ", ".join(a.upper() for a in _state["algs"])}
    try:
        sig = base64.b64decode(re.sub(r"\s+", "", str(msg.get("signature") or ""))[:8000], validate=True)
    except (binascii.Error, ValueError):
        sig = b""
    if not verify(alg, raw, f"{PREFIX}\n{nonce}".encode(), sig):
        return 400, {"error": "signature does not verify with that public key"}
    aid, now = agent_id_of(alg, raw), _iso()
    label = re.sub(r"[\x00-\x1f]", " ", str(msg.get("label") or ""))[:100].strip()
    with _state["db"]() as con:
        con.execute("""INSERT INTO agent_identities VALUES (?,?,?,?,?,?,1)
                       ON CONFLICT(agent_id) DO UPDATE SET label=COALESCE(NULLIF(excluded.label, ''), label),
                       last_seen=excluded.last_seen, registrations=registrations+1""",
                    (aid, alg, base64.b64encode(raw).decode(), label, now, now))
    exp = int(time.time()) + TOKEN_DAYS * 86400
    payload = base64.urlsafe_b64encode(json.dumps({"id": aid, "exp": exp}, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(_state["key"], payload.encode(), hashlib.sha256).hexdigest()
    out = {"agent_id": aid, "algorithm": alg.upper(), "post_quantum": ALGS[alg]["pq"], "token": f"{payload}.{mac}",
           "token_expires": _iso(exp),
           "use": f"Send 'Authorization: Bearer <token>' to {SITE_URL}/a2a and {SITE_URL}/mcp. "
                  "Register again with the same key to renew."}
    if not ALGS[alg]["pq"]:
        out["note"] = "Ed25519 is not quantum-safe. When your tooling allows, register an ML-DSA-65 key."
    return 200, out


def from_header(auth):
    """'Bearer <token>' -> {"agent_id", "label"} for a registered agent, or None."""
    if not _state["key"] or not isinstance(auth, str) or not auth.startswith("Bearer "):
        return None
    payload, _, mac = auth[7:].strip()[:400].partition(".")
    if not mac or not hmac.compare_digest(mac, hmac.new(_state["key"], payload.encode(), hashlib.sha256).hexdigest()):
        return None
    try:
        d = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if int(d.get("exp", 0)) < time.time():
            return None
        with _state["db"]() as con:
            r = con.execute("SELECT agent_id, label FROM agent_identities WHERE agent_id=?", (str(d.get("id")),)).fetchone()
    except Exception:
        return None
    return {"agent_id": r[0], "label": r[1] or ""} if r else None


def spec():
    return {
        "what": f"Optional identity for AI agents talking to {SITE_NAME}: your agent is its key pair. We store your public "
                "key; its fingerprint is your agent_id. No registry, operator or third party involved. Without it, "
                "every public tool works the same.",
        "recommended_algorithm": "ML-DSA-65 (NIST FIPS 204, post-quantum)",
        "accepted_algorithms": [a.upper() for a in _state["algs"]],
        "classic_fallback": "Ed25519 is accepted for tooling without ML-DSA, but it is not quantum-safe.",
        "steps": [
            "Create an ML-DSA-65 key pair and keep the private key secret.",
            f"POST {SITE_URL}/identity/challenge -> {{nonce, message}}.",
            "Sign `message` exactly (UTF-8, no trailing newline) with your private key.",
            f"POST {SITE_URL}/identity/register {{public_key, nonce, signature, label (optional)}} -> {{agent_id, token}}.",
            f"Send 'Authorization: Bearer <token>' to /a2a and /mcp. The token lasts {TOKEN_DAYS} days; register again "
            "with the same key to renew (same agent_id).",
        ],
        "openssl_3_5": [
            "openssl genpkey -algorithm ML-DSA-65 -out agent.pem",
            "openssl pkey -in agent.pem -pubout -out agent.pub.pem   # send this file's text as public_key",
            f"printf '{PREFIX}\\n%s' \"$NONCE\" > msg.bin",
            "openssl pkeyutl -sign -inkey agent.pem -rawin -in msg.bin | base64 -w0   # signature",
        ],
        "formats": {"public_key": "PEM or base64 DER (SubjectPublicKeyInfo); Ed25519 also as base64 raw 32 bytes",
                    "signature": "base64 (ML-DSA-65: 3309 bytes; Ed25519: 64 bytes)", "nonce_seconds": NONCE_TTL},
        "available": ready(),
    }
