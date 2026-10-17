from pathlib import Path

path = Path("scripts/follow_log_file.py")
text = path.read_text(encoding="utf-8-sig")


def replace_once(source, old, new, description):
    count = source.count(old)
    if count != 1:
        raise RuntimeError(
            f"{description}: expected one match, found {count}. "
            "No changes were written."
        )
    return source.replace(old, new, 1)


if '"idempotency_key": idempotency_key' in text:
    raise SystemExit("Idempotency is already wired into the watcher.")

# Add hashlib using the import block that actually exists.
patched = replace_once(
    text,
    "import argparse\nimport json",
    "import argparse\nimport hashlib\nimport json",
    "watcher import block",
)

old_request = '''                        response = client.post(
                            f"{args.url.rstrip('/')}/api/v1/live/ingest",
                            json={"raw_line": line},
                        )'''

new_request = '''                        # Stable key: the same source line gets the same key
                        # when the watcher retries after an interruption.
                        idempotency_key = hashlib.sha256(
                            f"{file_path.resolve()}:{offset}".encode("utf-8")
                        ).hexdigest()

                        response = client.post(
                            f"{args.url.rstrip('/')}/api/v1/live/ingest",
                            json={
                                "raw_line": line,
                                "idempotency_key": idempotency_key,
                            },
                        )'''

patched = replace_once(
    patched,
    old_request,
    new_request,
    "watcher API request",
)

backup = path.with_suffix(".py.idempotency.bak")
backup.write_text(text, encoding="utf-8")
path.write_text(patched, encoding="utf-8")

print("SUCCESS: File watcher now uses deterministic idempotency keys.")
print(f"Backup saved: {backup}")
