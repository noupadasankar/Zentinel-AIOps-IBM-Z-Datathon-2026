from pathlib import Path

path = Path("scripts/enable_idempotency.py")
text = path.read_text(encoding="utf-8-sig")

old = r'"    def save_event_and_state(\\n",'
new = r'"    def save_event_and_state(\n",'

count = text.count(old)

if count != 1:
    raise SystemExit(
        f"Expected one incorrect newline pattern, found {count}. "
        "No changes made."
    )

path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("SUCCESS: Newline pattern corrected in the patch script.")
