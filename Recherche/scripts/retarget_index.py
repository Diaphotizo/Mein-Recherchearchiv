"""Rebuild local PDF paths after moving the complete archive folder.

Run with the application stopped. No online source is contacted, and PDFs and
personal annotations are retained. Metadata is read from relative inventory paths.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", choices=("courts", "un", "all"), default="all")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if args.collection in ("courts", "all"):
        from research_portal.index_courts import build_index
        build_index(args.workers)
    if args.collection in ("un", "all"):
        from research_portal.index_un import build_index
        build_index(args.workers)


if __name__ == "__main__":
    main()
