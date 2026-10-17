# Zentinel AIOps: Security Architecture and Threat Model

Zentinel AIOps is engineered with defensive security principles to safeguard critical observability pipelines from tampering, injection, and unauthorized data exfiltration.

---

## 1. Threat Model & Security Posture

| Threat Category | Potential Attack Vector | Zentinel Defensive Countermeasure |
| :--- | :--- | :--- |
| **Log Injection / Tampering** | Malicious payloads injected into raw log streams to poison template parsing. | Strict regex pre-filtering; Drain3 matches only against frozen training templates; unmapped text maps to `UNKNOWN_TOKEN` without modifying model state. |
| **SQL Injection** | Attacker crafting log text to manipulate internal SQLite queries. | 100% parameterized SQL statements across all SQLite operations (`?` placeholders). No string interpolation in queries. |
| **Cross-Site Scripting (XSS)** | Malicious HTML/JS embedded in log lines targeting dashboard operators. | All raw log messages rendered via sanitized Streamlit text blocks or explicitly escaped via `html.escape()`. No raw HTML formatting on log bodies. |
| **Timing Side-Channel Attacks** | Measuring response latency to guess administrative API keys. | API keys verified using constant-time cryptographic equality (`hmac.compare_digest`). |
| **Ingestion Denial of Service** | Flooding API with unmetered log volumes to exhaust server resources. | In-memory token-bucket rate limiting per API key. Batch limits enforced at $\le 500$ events. |
| **Model Weight Tampering** | Unauthorized modification or replacement of frozen models on disk. | Startup integrity verification against `artifacts/manifest.json` SHA-256 digests. Server refuses to boot on mismatch. |
| **Container Privilege Escalation** | Container breakout targeting host kernel. | Dockerfile runs strictly as an unprivileged non-root user (`zentinel`, UID 1000). |
| **Credential Leakage in Git** | Accidental commit of private keys, certificates, or environment secrets. | Strict `.gitignore` targeting `*.pem`, `*.key`, `*.crt`, `.env`. Automated `gitleaks` scanning in CI. |

---

## 2. Authentication & Authorization

### 2.1 Key Management
- API keys are never stored in plaintext on disk or in Git repositories.
- The server expects pre-hashed SHA-256 digests in environment variables:
  ```bash
  ZENTINEL_API_KEY_SHA256=a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e
  ```
- Clients transmit keys via the `X-API-Key` HTTP header.

### 2.2 Constant-Time Comparison
```python
client_hash = hashlib.sha256(provided_key.encode("utf-8")).hexdigest()
if not hmac.compare_digest(client_hash, configured_hash):
    raise HTTPException(status_code=401, detail="Invalid API key")
```

### 2.3 Role-Based Access Control (RBAC)
- `ingest`: Permitted to submit single and batch logs to `/api/v1/live/ingest`.
- `read`: Permitted to query alerts, telemetry, review queue, and models.
- `admin`: Full access including incident acknowledgment, closure, and configuration.

---

## 3. Cryptographic Artifact Integrity

Non-Negotiable Rule 2 dictates that frozen model weights and states are immutable.
- `artifacts/manifest.json` tracks the canonical SHA-256 digest and file size for every model asset.
- During the FastAPI lifespan startup hook, `verify_manifest()` recalculates digests on disk:
  ```python
  if calculated_sha256 != manifest_sha256:
      raise RuntimeError(f"Integrity check failed for {artifact_path}. Expected {manifest_sha256}")
  ```
- If an attacker or corrupt disk alters any model checkpoint or state file, the server halts immediately with an error log.

---

## 4. Operational Ingestion Hardening

### 4.1 Idempotency Key Collision Resistance
To prevent duplicate processing after log file resets or rotations:
$$\text{idempotency\_key} = \text{SHA-256}(\text{file\_id} : \text{byte\_offset})$$
The server stores `payload_sha256` alongside each key in SQLite. If a duplicate key arrives with a conflicting payload, the API halts processing with `HTTP 409 Conflict`.

### 4.2 Network & Localhost Binding
- By default, Docker Compose binds published ports exclusively to the loopback interface (`127.0.0.1:8000` and `127.0.0.1:8501`), preventing accidental public internet exposure.

---

## 5. Security Scanning & Continuous Auditing

- **Static Analysis**: `ruff check .` validates code quality and flags insecure function patterns.
- **Dependency CVE Auditing**: `pip-audit -r requirements.lock` runs in GitHub Actions to detect known vulnerabilities.
- **Secret Scanning**: `gitleaks` runs on every git push and pull request.
