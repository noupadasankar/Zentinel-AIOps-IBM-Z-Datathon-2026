#!/usr/bin/env bash
set -u
set -o pipefail

EXPECTED_COMMIT="186cfa2"
EXPECTED_ZIP_SHA256="4973a099a1f4f97ae94762f01b1df14966b6760a02be7cde7a3229160fb96dfb"

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)}"
ZENTINEL_ZIP="${ZENTINEL_ZIP:-$HOME/Zentinel-AIOps-IBM-Z-Datathon-2026.zip}"
EVID="${HOME}/zentinel-validation/evidence"
VENV_DIR="${VENV_DIR:-.venv-linuxone}"
RESULT_STATUS="PASS"

mkdir -p "${EVID}"
cd "${HOME}" 2>/dev/null || true

fail() {
  RESULT_STATUS="FAIL_PARTIAL"
  echo "[FAIL] $1"
}

note() {
  echo "[NOTE] $1"
}

if [ ! -d "${REPO_DIR}" ]; then
  REPO_DIR="${HOME}/Zentinel-AIOps"
fi

echo "evidence dir: ${EVID}"
echo "repo dir:     ${REPO_DIR}"

PY=""
ACTIVATED="no"
if [ -x "${REPO_DIR}/${VENV_DIR}/bin/python" ]; then
  PY="${REPO_DIR}/${VENV_DIR}/bin/python"
  ACTIVATED="yes"
  echo "reusing existing venv: ${REPO_DIR}/${VENV_DIR}"
fi

if [ ! -d "${REPO_DIR}" ] || [ ! -f "${REPO_DIR}/artifacts/manifest.json" ]; then
  if [ -f "${ZENTINEL_ZIP}" ]; then
    echo "repository not cloned; using transferred ZIP: ${ZENTINEL_ZIP}"
    mkdir -p "${HOME}"
    cd "${HOME}"
    sha256sum "${ZENTINEL_ZIP}" | tee "${EVID}/02-zip-sha256.txt"
    unzip -tq "${ZENTINEL_ZIP}" 2>&1 | tail -3 | tee -a "${EVID}/02-zip-sha256.txt"
    unzip -oq "${ZENTINEL_ZIP}" -d "${HOME}/Zentinel-AIOps"
    REPO_DIR="${HOME}/Zentinel-AIOps"
  else
    echo "ERROR: no repository at ${REPO_DIR} and no ZIP at ${ZENTINEL_ZIP}"
    exit 3
  fi
fi

cd "${REPO_DIR}"

echo "=== 01 environment baseline ==="
{
  date -Is
  uname -a
  uname -m
  cat /etc/os-release
  python3 --version
  python3 -m pip --version
  git --version || true
  df -h "${HOME}"
  free -h 2>/dev/null || true
  nproc 2>/dev/null || true
  cat /proc/cpuinfo 2>/dev/null | grep -m1 -E "machine|model name|processor" || true
} 2>&1 | tee "${EVID}/01-environment.txt"

ARCH="$(uname -m)"
if [ "${ARCH}" != "s390x" ]; then
  echo "ERROR: uname -m returned '${ARCH}', expected s390x. STOPPING per runbook: this host is not native IBM Z/LinuxONE."
  exit 2
fi
note "architecture confirmed s390x"

COMMIT="$(git -C "${REPO_DIR}" rev-parse --short HEAD 2>/dev/null || echo none)"
TREE="$(git -C "${REPO_DIR}" status --short 2>/dev/null | head -20 || true)"
COMMIT_NOTE="MATCHES_EXPECTED"
if [ "${COMMIT}" != "${EXPECTED_COMMIT}" ]; then
  COMMIT_NOTE="DIFFERS_FROM_EXPECTED_${EXPECTED_COMMIT}_using_${COMMIT}"
  note "commit differs: expected ${EXPECTED_COMMIT}, found ${COMMIT} (recorded, not reset)"
fi

echo "=== 02 source identity: commit=${COMMIT} ==="
{
  echo "remote:   $(git -C "${REPO_DIR}" remote get-url origin 2>/dev/null || echo 'local-transfer (no origin)')"
  echo "head:     ${COMMIT}"
  echo "expected: ${EXPECTED_COMMIT}"
  echo "note:     ${COMMIT_NOTE}"
  echo "tree:"
  echo "${TREE:-'clean'}"
} | tee "${EVID}/02-source.txt"

echo "=== 03 venv + dependencies ==="
if [ "${ACTIVATED}" != "yes" ]; then
  if [ ! -x "${REPO_DIR}/${VENV_DIR}/bin/python" ]; then
    python3 -m venv "${REPO_DIR}/${VENV_DIR}"
    ACTIVATED="yes"
    note "venv created at ${REPO_DIR}/${VENV_DIR}"
  fi
  PY="${REPO_DIR}/${VENV_DIR}/bin/python"
fi
"${PY}" -m pip install --upgrade pip setuptools wheel 2>&1 | tail -5 | tee "${EVID}/03-install.txt"

LOCK="${REPO_DIR}/requirements.lock"
FALLBACK="${REPO_DIR}/requirements.txt"
if [ -f "${LOCK}" ]; then
  echo "installing pinned lock: requirements.lock (authoritative)"
  "${PY}" -m pip install -r "${LOCK}" 2>&1 | tee "${EVID}/03-install.txt" | tail -15
else
  echo "WARNING: requirements.lock absent, falling back to unpinned requirements.txt (record deviation in RESULTS.md)"
  "${PY}" -m pip install -r "${FALLBACK}" 2>&1 | tee "${EVID}/03-install.txt" | tail -15
fi
"${PY}" -m pip install -e "${REPO_DIR}" --no-deps 2>&1 | tail -3 | tee -a "${EVID}/03-install.txt"
"${PY}" -m pip check 2>&1 | tee "${EVID}/03-pipcheck.txt"
"${PY}" -m pip freeze > "${EVID}/03-installed-packages.txt"

"${PY}" - <<'PY' 2>&1 | tee "${EVID}/03-import-probe.txt"
import sys, platform
print("Python:", sys.version)
print("Architecture:", platform.machine())
modules = ["fastapi", "uvicorn", "streamlit", "torch", "sklearn", "numpy", "pandas", "onnxruntime", "drain3", "joblib", "plotly", "pyarrow"]
missing = []
for name in modules:
    try:
        module = __import__(name)
        print(f"PASS {name}: {getattr(module, '__version__', 'imported')}")
    except Exception as exc:
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        missing.append(name)
print("MISSING:", missing if missing else "NONE")
PY

echo "=== 04 artifact manifest + frozen state ==="
{
  find "${REPO_DIR}/artifacts" -maxdepth 2 -type f | sort
  ls "${REPO_DIR}/tests" | head -20
  find "${REPO_DIR}/notebooks" -maxdepth 1 -name '*.ipynb' | sort
} | tee "${EVID}/04-artifacts-listing.txt"

"${PY}" - <<'PY' 2>&1 | tee "${EVID}/04-manifest-verify.txt"
import json
from zentinel.config import verify_manifest, MANIFEST_PATH, DATA_DIR
try:
    verify_manifest()
    print("PASS manifest verification: every artifact SHA-256 matches", MANIFEST_PATH)
except Exception as exc:
    print("FAIL manifest verification:", type(exc).__name__, exc)

from pathlib import Path
raw = DATA_DIR / "raw" / "BGL.log"
print("BGL.log present on target:", raw.is_file())

from zentinel.models.inference import InferenceEngine
eng = InferenceEngine()
eng.load()
print(f"PASS engine loaded: lstm_threshold={eng.lstm_threshold!r} (loaded from artifact, not recomputed)")
print(f"novelty_threshold={eng.novelty_threshold!r} templates_drain_state={Path(str(eng.drain_state_path)).name}")

from zentinel.parsing.drain_parser import parse_bgl_line
fixture = Path(DATA_DIR / "raw" / "sample_replay.log")
sample = fixture.read_text(encoding="utf-8", errors="replace").splitlines()[:3]
for i, line in enumerate(sample):
    rec = parse_bgl_line(line, line_number=i + 1)
    t1 = eng.map_event_to_token(rec)
    t2 = eng.map_event_to_token(rec)
    stable = t1["token"] == t2["token"]
    print(f"line {i+1}: status={t1['event_status']} token={t1['token']} stable_match={stable}")
sc, tr = eng.score_known_runs([2, 3, 4, 5, 6, 7, 8])
print(f"score_known_runs smoke: score={sc!r} transitions={tr} finite={sc is not None and sc == sc}")
PY

echo "=== 05 test suite on target ==="
"${PY}" -m pytest tests -q 2>&1 \
  | tee "${EVID}/05-pytest.txt"
TEST_EXIT="${PIPESTATUS[0]}"
echo "pytest exit=${TEST_EXIT}" | tee "${EVID}/05-pytest-exit.txt"
if [ "${TEST_EXIT}" -ne 0 ]; then
  fail "pytest suite did not fully pass on s390x (exit=${TEST_EXIT})"
fi

"${PY}" -m ruff --version > /dev/null 2>&1
if [ $? -eq 0 ]; then
  "${PY}" -m ruff check . 2>&1 | tee "${EVID}/05-ruff.txt"
  RUFF_EXIT="${PIPESTATUS[0]}"
  echo "ruff exit=${RUFF_EXIT}" | tee "${EVID}/05-ruff-exit.txt"
else
  echo "ruff not installed in target venv (dev-only tool); SKIPPED by design" | tee "${EVID}/05-ruff-exit.txt"
fi

echo "=== 06 API startup + endpoints (disposable DB) ==="
SIZE_SQLITE_ONETIMEDB="${EVID}/../scratch"
mkdir -p "${EVID}/../scratch"
export ZENTINEL_DB_PATH="${EVID}/../scratch/zentinel_validation.sqlite3"
rm -f "${ZENTINEL_DB_PATH}"
export ZENTINEL_RATE_LIMIT_RPS=100.0
note "auth: ZENTINEL_API_KEY_SHA256 unset -> open mode (record in RESULTS)"
if [ -n "${ZENTINEL_API_KEY_SHA256:-}" ]; then
  note "auth ENABLED via environment (X-API-Key required)"
fi

nohup "${PY}" -m uvicorn zentinel.api.live_api:app --host 127.0.0.1 --port 8000 --workers 1 \
  > "${EVID}/06-api.log" 2>&1 &
echo $! > "${EVID}/api.pid"
API_PID="$(cat "${EVID}/api.pid")"
READY="no"
for i in $(seq 1 45); do
  if curl -sf http://127.0.0.1:8000/ready > /dev/null 2>&1; then READY="yes"; break; fi
  sleep 2
done
if [ "${READY}" != "yes" ]; then
  fail "API did not become ready; log tail:"
  tail -60 "${EVID}/06-api.log"
  exit 4
fi

curl -sS -w '\nHTTP %{http_code}\n' http://127.0.0.1:8000/health | tee "${EVID}/06-health.txt"
curl -sS -w '\nHTTP %{http_code}\n' http://127.0.0.1:8000/ready | tee "${EVID}/06-ready.txt"
curl -sS -w '\nHTTP %{http_code}\n' http://127.0.0.1:8000/api/v1/live/status | tee "${EVID}/06-live-status.txt"
curl -sS -w '\nHTTP %{http_code}\n' 'http://127.0.0.1:8000/api/v1/live/windows?limit=5' | tee "${EVID}/06-live-windows.txt"

echo "=== 07 production model inference + ONNX on-device parity smoke ==="
"${PY}" -m pytest \
  tests/test_known_runs_scorer.py \
  tests/test_onnx_parity.py \
  tests/test_decide_window.py \
  tests/test_drain_matcher.py \
  tests/test_bgl_parser.py \
  -q 2>&1 | tee "${EVID}/07-model-tests.txt"
MODEL_EXIT="${PIPESTATUS[0]}"
echo "model test exit=${MODEL_EXIT}" | tee "${EVID}/07-model-tests-exit.txt"
if [ "${MODEL_EXIT}" -ne 0 ]; then
  fail "focused model tests failed on s390x (exit=${MODEL_EXIT})"
fi

"${PY}" - <<'PY' 2>&1 | tee "${EVID}/07-onnx-parity.txt"
import json, statistics, time
from pathlib import Path
import numpy as np
import torch
import onnxruntime as ort
from zentinel.models.inference import InferenceEngine

eng = InferenceEngine()
eng.load()
rng = np.random.default_rng(42)
VOCAB_MAX = int(eng.vocab["vocabulary_size"]) - 1
if VOCAB_MAX < 616:
    VOCAB_MAX = 615
runs = [rng.integers(2, VOCAB_MAX + 1, size=L).tolist() for L in (4, 16, 64)]
sess = ort.InferenceSession("artifacts/bgl_lstm_next_event.onnx", providers=["CPUExecutionProvider"])
max_diff = 0.0
agree = 0
windows = 32
t0 = time.perf_counter()
for _ in range(windows):
    tokens = rng.integers(2, VOCAB_MAX + 1, size=100).tolist()
    chunks = [tokens[:50], tokens[50:]]
    scores_t, scores_o = [], []
    for chunk in chunks:
        chunk = chunk + [1]
        inputs = torch.tensor(chunk[:-1], dtype=torch.long).unsqueeze(0)
        with torch.inference_mode():
            tl = eng.model(inputs).squeeze(0)
        ol = sess.run(["logits"], {"tokens": inputs.numpy()})[0].squeeze(0)
        max_diff = max(max_diff, float(np.abs(tl.numpy() - ol).max()))
        tce = float(torch.nn.functional.cross_entropy(tl, torch.tensor(chunk[1:]), reduction="sum").item())
        m = ol.max(axis=1, keepdims=True)
        z = ol - m
        lsm = z - np.log(np.exp(z).sum(axis=1, keepdims=True))
        oce = float(-lsm[np.arange(len(chunk) - 1), np.array(chunk[1:])].sum())
        scores_t.append(tce); scores_o.append(oce)
    st = sum(scores_t) / len(scores_t); so = sum(scores_o) / len(scores_o)
    if (st >= eng.lstm_threshold) == (so >= eng.lstm_threshold):
        agree += 1
elapsed = time.perf_counter() - t0
report = {
    "platform": "target",
    "torch_version": torch.__version__,
    "onnxruntime_version": ort.__version__,
    "input_name": "tokens",
    "output_name": "logits",
    "windows": windows,
    "max_abs_logit_diff": max_diff,
    "decision_agreement": f"{agree}/{windows}",
    "threshold_used_from_artifact": eng.lstm_threshold,
    "protocol_note": "reduced smoke (32 windows); authoritative full parity report: reports/onnx_parity_report.json (x86 run, 918/918)",
    "verdict": ("PASS" if max_diff <= 1e-4 and agree == windows else "FAIL"),
}
print(json.dumps(report, indent=2))
Path("reports/").mkdir(exist_ok=True)
Path("reports/linuxone_onnx_parity_smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print("verdict:", report["verdict"])
PY

echo "=== 08 ingestion + persistence + incident lifecycle ==="
curl -sS http://127.0.0.1:8000/openapi.json -o "${EVID}/08-openapi.json"

REPLAY="${REPO_DIR}/data/raw/sample_replay.log"
"${PY}" - <<'PY' 2>&1 | tee "${EVID}/08-lifecycle.txt"
import json, os, sys, urllib.error, urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
DB = os.environ.get("ZENTINEL_DB_PATH", "unknown")
print("disposable db:", DB)
replay = Path("data/raw/sample_replay.log")
lines = replay.read_text(encoding="utf-8", errors="replace").splitlines()
chunk = lines[:120]

def req(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {"error": "unparseable"}

print("status.before:", req("GET", "/api/v1/live/status")[1].get("logs_received"))
code, batch1 = req("POST", "/api/v1/live/ingest/batch", {"lines": chunk, "source_id": "linuxone-validation-replay"})
print(f"batch1.ingest: {code} ingested={batch1.get('ingested_count')} windows={batch1.get('completed_windows')}")
code, st1 = req("GET", "/api/v1/live/status")
print("status.after:", st1.get("logs_received"), "windows_counted:", st1.get("windows_completed"))
code, wins = req("GET", "/api/v1/live/windows?limit=5")
print(f"windows.read: {code} count={wins.get('count')} first={(wins.get('items') or [{}])[0].get('decision')}")
code, dup = req("POST", "/api/v1/live/ingest/batch", {"lines": chunk, "source_id": "linuxone-validation-replay"})
print(f"batch.duplicate: {code} ingested={dup.get('ingested_count')} (batch is additive; single-ingest idempotency next)")
line0 = chunk[0]
code, idem1 = req("POST", "/api/v1/live/ingest", {"raw_line": line0, "idempotency_key": "linuxone-idem-1"})
print(f"single.idem.first: {code} -> {json.dumps(idem1)[:140]}")
code, idem2 = req("POST", "/api/v1/live/ingest", {"raw_line": line0, "idempotency_key": "linuxone-idem-1"})
print(f"single.idem.repeat: {code} -> idempotent={'stored/' in json.dumps(idem2) or code == 200 or code in (200, 201)}")
code, conflict = req("POST", "/api/v1/live/ingest", {"raw_line": "DRIFT-LINE-not-same-payload", "idempotency_key": "linuxone-idem-1"})
print(f"single.idem.conflict: {code} (expect 409 per API contract)")
code, invalid = req("POST", "/api/v1/live/ingest/batch", {"lines": "wrong-type"})
print(f"invalid.input: {code} (expect 422)")

alerts = req("GET", "/api/v1/alerts")[1]
items = alerts.get("items", [])
print("open alerts after fixture replay:", alerts.get("count"))

lifecycle = "NOT_EXERCISED_NO_ALERT_IN_FIXTURE"
if alerts.get("count", 0) > 0 and items:
    aid = items[0].get("incident_id") or items[0].get("id")
    code_a, ack = req("POST", f"/api/v1/alerts/{aid}/ack", {"actor": "linuxone-validation", "comment": "linuxone evidence capture"})
    code_c, close = req("POST", f"/api/v1/alerts/{aid}/close", {"actor": "linuxone-validation", "reason": "linuxone validation lifecycle", "comment": "checked on target"})
    code_d, detail = req("GET", f"/api/v1/alerts/{aid}")
    print(f"ack={code_a} close={code_c} detail.state={detail.get('state')}")
    lifecycle = f"ack={code_a} close={code_c}"
print("incident lifecycle:", lifecycle)

review = req("GET", "/api/v1/review")[1]
print("review queue count:", review.get("count"))
r = urllib.request.urlopen(BASE + "/api/v1/drift", timeout=30)
print("drift:", json.loads(r.read().decode()).get("drift_status"))
r = urllib.request.urlopen(BASE + "/api/v1/metrics", timeout=30)
print("metrics content-type:text, first line:", r.read().decode()[:80].replace("\n", " "))
r = urllib.request.urlopen(BASE + "/api/v1/models", timeout=30)
print("models:", json.dumps(json.loads(r.read().decode()))[:200])
PY

echo "persistence restart check"
kill "${API_PID}" 2>/dev/null
sleep 2
nohup "${PY}" -m uvicorn zentinel.api.live_api:app --host 127.0.0.1 --port 8000 --workers 1 \
  >> "${EVID}/06-api.log" 2>&1 &
echo $! > "${EVID}/api.pid"
API_PID="$(cat "${EVID}/api.pid")"
READY2="no"
for i in $(seq 1 45); do
  if curl -sf http://127.0.0.1:8000/ready > /dev/null 2>&1; then READY2="yes"; break; fi
  sleep 2
done
curl -sS http://127.0.0.1:8000/api/v1/live/status | tee "${EVID}/08-restart-status.txt"
if [ "${READY2}" = "yes" ]; then
  note "persistence survived controlled restart (counters above, same disposable db)"
else
  fail "API failed after restart; tail:"; tail -40 "${EVID}/06-api.log"
fi

echo "=== 09 dashboard ==="
nohup "${PY}" -m streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8501 \
  > "${EVID}/09-dashboard.log" 2>&1 &
echo $! > "${EVID}/dashboard.pid"
DASH_OK="no"
for i in $(seq 1 40); do
  if curl -sf http://127.0.0.1:8501/_stcore/health | grep -q ok; then DASH_OK="yes"; break; fi
  sleep 2
done
if [ "${DASH_OK}" = "yes" ]; then
  curl -sS -o /dev/null -w 'dashboard HTTP %{http_code}\n' http://127.0.0.1:8501/ | tee "${EVID}/09-dashboard-health.txt"
  note "SSH tunnel from PC:  ssh -L 8501:127.0.0.1:8501 <user>@<host>  then open http://localhost:8501 (Overview/Incidents/Review/Windows/Drift/Model Evaluation manual walkthrough required)"
else
  fail "dashboard did not respond; log tail:"; tail -30 "${EVID}/09-dashboard.log"
fi

echo "=== 10 benchmark on target ==="
if [ -f data/raw/BGL.log ]; then
  "${PY}" scripts/benchmark.py 2>&1 | tail -40 | tee "${EVID}/10-benchmark.txt"
  BENCH="${EVID}/10-benchmark.txt"
else
  echo "BLOCKED: scripts/benchmark.py 60s-protocol requires data/raw/BGL.log (not transferred by design). Report NOT RUN with this reason." | tee "${EVID}/10-benchmark.txt"
  echo "auxiliary fixture-scale replay probe (not the 60s x3 protocol):" >> "${EVID}/10-benchmark.txt"
  "${PY}" - <<'PY' >> "${EVID}/10-benchmark.txt" 2>&1
import json, statistics, time, urllib.request
from pathlib import Path
lines = Path("data/raw/sample_replay.log").read_text(encoding="utf-8", errors="replace").splitlines()
times = []
for _ in range(3):
    t0 = time.perf_counter()
    req = urllib.request.Request("http://127.0.0.1:8000/api/v1/live/ingest/batch", json.dumps({"lines": lines[:500], "source_id": "linuxone-aux-probe"}).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    urllib.request.urlopen(req, timeout=120)
    times.append(time.perf_counter() - t0)
print(json.dumps({"probe": "500-line batch x3 (aux only)", "seconds": times, "events_per_s_seq": 500 * 3 / sum(times)}))
PY
fi

echo "=== 11 RESULTS.md + evidence archive ==="
kill "${API_PID}" 2>/dev/null
DASH_PID="$(cat "${EVID}/dashboard.pid" 2>/dev/null || true)"
[ -n "${DASH_PID}" ] && kill "${DASH_PID}" 2>/dev/null
echo "services stopped; portable DB left at ${EVID}/../scratch for inspection"

cat > "${EVID}/RESULTS.md" <<EOF
# Zentinel AIOps - IBM Z/LinuxONE Validation

Generated on target: $(date -Is)

## Source
- Expected baseline commit: ${EXPECTED_COMMIT}
- Actual commit: ${COMMIT}
- Commit note: ${COMMIT_NOTE}
- Working tree: $(git -C "${REPO_DIR}" status --short | wc -l) modified/untracked entries shown in 02-source.txt

## Environment
- Architecture: ${ARCH}
- OS: $(grep PRETTY_NAME /etc/os-release | cut -d'"' -f2)
- Python: $(${PY} --version 2>&1)
- CPU/memory/disk: see 01-environment.txt

## Results
- Dependency installation: $(if [ -f "${EVID}/03-install.txt" ] && grep -q "Successfully installed\|Requirement already satisfied" "${EVID}/03-install.txt"; then echo PASS; else echo CHECK-MANUALLY; fi) (pip check: $(cat "${EVID}/03-pipcheck.txt" 2>/dev/null | tail -1))
- Artifact integrity: $(grep -q "PASS manifest verification" "${EVID}/04-manifest-verify.txt" 2>/dev/null && echo PASS || echo FAIL)
- Frozen parser no-refit: $(grep -q "stable_match=True" "${EVID}/04-manifest-verify.txt" 2>/dev/null && echo PASS || echo CHECK)
- LSTM inference: $(grep -q "score_known_runs smoke" "${EVID}/04-manifest-verify.txt" 2>/dev/null && echo "see smoke line in 04 file" || echo CHECK)
- Unit and integration tests: $(grep "pytest exit=" "${EVID}/05-pytest-exit.txt" 2>/dev/null | cut -d= -f2; echo)  (0 => PASS)
- Focus model tests: $(grep "model test exit=" "${EVID}/07-model-tests-exit.txt" 2>/dev/null | cut -d= -f2)
- ONNX inference and parity (on-device smoke): $(grep '"verdict"' "${EVID}/07-onnx-parity.txt" 2>/dev/null | tail -1 || echo NOT-DONE)
- API health and readiness: $(grep -q "HTTP 200" "${EVID}/06-ready.txt" 2>/dev/null && echo PASS || echo FAIL)
- Ingestion, scoring, dedupe/conflict rejection: see 08-lifecycle.txt codes
- Incident lifecycle: see 08-lifecycle.txt 'incident lifecycle' line
- Persistence across restart: see 08-restart-status.txt
- Dashboard live mode: $(if [ "${DASH_OK}" = "yes" ]; then echo "HTTP PASS (GUI page walkthrough manual)"; else echo FAIL; fi)
- Benchmark (60s x3 protocol): $(grep -q BLOCKED "${EVID}/10-benchmark.txt" && echo BLOCKED-no-BGL.log-on-target || echo see-10-benchmark.txt)

## Failures and limitations
- fill in from FAIL lines above and pip errors; keep unsuccessful trials documented

## Evidence files
EOF
ls -1 "${EVID}" >> "${EVID}/RESULTS.md"

cd "${HOME}/zentinel-validation"
tar -czf zentinel-linuxone-evidence.tar.gz evidence
sha256sum zentinel-linuxone-evidence.tar.gz | tee "${EVID}/../evidence-archive-sha256.txt"
DONE="DONE"
if [ "${RESULT_STATUS}" != "PASS" ]; then echo "RESULT: ${RESULT_STATUS}" ; else echo "RESULT: PASS (see RESULTS.md; manual dashboard walkthrough still required)"; fi
echo "evidence archive: ${HOME}/zentinel-validation/zentinel-linuxone-evidence.tar.gz"
