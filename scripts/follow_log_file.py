import argparse
from pathlib import Path

from zentinel.ingestion.watcher import DEFAULT_FILE, LogFileWatcher


def main():
    parser = argparse.ArgumentParser(
        description="Watch an appended log file and send new lines to Zentinel."
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_FILE,
        help="Input file containing one BGL-format log record per line.",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8001",
        help="Base URL of the live inference API.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="X-API-Key for authenticated ingestion endpoints.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=0.25,
        help="Seconds to wait when no complete new line is available.",
    )
    parser.add_argument(
        "--from-start",
        action="store_true",
        help="Reset this file's cursor to byte zero and replay from the start.",
    )
    args = parser.parse_args()

    watcher = LogFileWatcher(
        file_path=args.file,
        api_url=args.url,
        api_key=args.api_key,
        poll_seconds=args.poll_seconds,
        from_start=args.from_start,
    )
    watcher.start()


if __name__ == "__main__":
    main()
