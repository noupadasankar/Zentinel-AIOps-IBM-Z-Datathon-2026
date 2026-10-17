from pathlib import Path

path = Path("scripts/follow_log_file.py")
text = path.read_text(encoding="utf-8-sig")

old = '''                    with file_path.open("rb") as source:
                        source.seek(offset)
                        raw_bytes = source.readline()

                        # Do not submit a partial line still being written.
                        if not raw_bytes or not raw_bytes.endswith(b"\\\\n"):
                            time.sleep(args.poll_seconds)
                            continue

                        next_offset = source.tell()
'''

new = '''                    with file_path.open("rb") as source:
                        source.seek(offset)
                        raw_bytes = source.readline()
                        next_offset = source.tell()

                    # Release the file handle before sleeping so Windows
                    # processes can append new data.
                    if not raw_bytes or not raw_bytes.endswith(b"\\\\n"):
                        time.sleep(args.poll_seconds)
                        continue
'''

if old not in text:
    raise SystemExit(
        "Expected code block not found. No changes were made. "
        "Please share the relevant section of follow_log_file.py."
    )

backup = path.with_suffix(".py.filelock.bak")
backup.write_text(text, encoding="utf-8")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

print("SUCCESS: File-locking patch applied.")
print(f"Backup: {backup}")
