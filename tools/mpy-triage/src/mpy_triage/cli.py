"""CLI entry point for mpy-triage."""

import argparse
import logging


def main() -> None:
    """Main entry point for the mpy-triage CLI."""
    parser = argparse.ArgumentParser(
        prog="mpy-triage",
        description="Detect duplicate and related MicroPython issues/PRs using semantic search.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging.")
    parser.add_argument(
        "--db",
        default="data/triage.db",
        help="Path to the SQLite database (default: data/triage.db).",
    )

    _args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if _args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


if __name__ == "__main__":
    main()
