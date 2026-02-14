!/usr/bin/env python3

"""
diffcheck — detect changes in Wikipedia pages between archived snapshots.

Compares today's wikisnap archive against one from N days ago.
Reports which pages have changed content (ignoring whitespace differences).

Designed to run after wikisnap on a cron schedule to track page edits
over time.

Usage:
   diffcheck --days 7 --output-dir ./archives
   diffcheck --days 30 --output-dir ./archives --verbose
diffcheck --help
"""

import sys
import argparse
import tempfile
import tarfile
from pathlib import Path
from datetime import date, timedelta, datetime


# --- Exit codes ---
EXIT_OK = 0
EXIT_BAD_ARGS = 1
EXIT_NO_ARCHIVES = 2

VERBOSE = False


def log(msg):
    """Print to stderr only when --verbose is set."""
    if VERBOSE:
        print(msg, file=sys.stderr)


def parse_archive_time(path):
    """Extract a datetime from an archive filename (YYYY-MM-DD_HH-MM-SS.tar.gz)."""
    name = path.name
    if not name.endswith(".tar.gz"):
        return None
    base = name[:-7]
    try:
        return datetime.strptime(base, "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None


def normalize_content(text):
    """Collapse all whitespace for content comparison."""
    return " ".join(text.split())


def parse_frontmatter(text):
    """Extract title and url from YAML frontmatter at the top of a Markdown file."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, None

    end_index = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_index = i
            break
    if end_index is None:
        return None, None

    mapping = {}
    for line in lines[1:end_index]:
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        mapping[key.strip()] = value.strip()

    return mapping.get("title"), mapping.get("url")


def extract_archive(archive_path, target_dir):
    """Extract a .tar.gz archive into target_dir."""
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(path=target_dir)


def main():
    parser = argparse.ArgumentParser(
        prog="diffcheck",
        description="Compare today's wikisnap archive against one from N days ago to detect page changes.",
    )
    parser.add_argument(
        "-n", "--days",
        type=int,
        required=True,
        help="number of days to look back (compare today vs N days ago)",
    )
    parser.add_argument(
        "-o", "--output-dir",
        required=True,
        help="directory containing wikisnap .tar.gz archives",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="print progress messages to stderr",
    )
    args = parser.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    if args.days < 0:
        print("Error: --days must be a non-negative integer.", file=sys.stderr)
        sys.exit(EXIT_BAD_ARGS)

    output_dir = Path(args.output_dir)
    if not output_dir.is_dir():
        print(f"Error: directory not found: {output_dir}", file=sys.stderr)
        sys.exit(EXIT_BAD_ARGS)

    # Discover archives
    archives = []
    for path in output_dir.iterdir():
        if not path.is_file():
            continue
        dt = parse_archive_time(path)
        if dt is not None:
            archives.append((dt, path))

    if not archives:
        print("Error: no archives found in the specified directory.", file=sys.stderr)
        sys.exit(EXIT_NO_ARCHIVES)

    today = date.today()
    target_day = today - timedelta(days=args.days)

    today_candidates = [item for item in archives if item[0].date() == today]
    past_candidates = [item for item in archives if item[0].date() == target_day]

    if not past_candidates:
        print(f"Error: no archive found from {target_day.isoformat()} ({args.days} days ago).", file=sys.stderr)
        sys.exit(EXIT_NO_ARCHIVES)

    if not today_candidates:
        print("Error: no archive from today. Run wikisnap first to create one.", file=sys.stderr)
        sys.exit(EXIT_NO_ARCHIVES)

    past_dt, past_path = max(past_candidates, key=lambda x: x[0])
    today_dt, today_path = max(today_candidates, key=lambda x: x[0])

    log(f"Comparing: {today_path.name} vs {past_path.name}")

    changed = []

    with tempfile.TemporaryDirectory() as past_tmp, tempfile.TemporaryDirectory() as today_tmp:
        past_dir = Path(past_tmp)
        today_dir = Path(today_tmp)

        extract_archive(past_path, past_dir)
        extract_archive(today_path, today_dir)

        for today_file in sorted(today_dir.glob("*.md")):
            past_file = past_dir / today_file.name
            if not past_file.is_file():
                log(f"New page (no past version): {today_file.name}")
                continue

            try:
                today_text = today_file.read_text(encoding="utf-8")
                past_text = past_file.read_text(encoding="utf-8")
            except Exception:
                continue

            if normalize_content(today_text) == normalize_content(past_text):
                log(f"Unchanged: {today_file.name}")
                continue

            title, url = parse_frontmatter(today_text)
            if title is None:
                title = today_file.stem
            if url is None:
                url = ""
            changed.append((title, url))

    if not changed:
        print(f"No changes detected in any page over the last {args.days} days.")
        sys.exit(EXIT_OK)

    print(f"The following pages changed in the last {args.days} days:\n")
    for title, url in changed:
        if url:
            print(f"  - {title} ({url})")
        else:
            print(f"  - {title}")

    sys.exit(EXIT_OK)

if __name__ == "__main__":
    main()
