from __future__ import annotations

import argparse
from pathlib import Path

from examdesk import edition
from examdesk.db.migrations import initialize_database
from examdesk.paths import AppPaths
from examdesk.version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="examdesk")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--init-data",
        action="store_true",
        help="初始化当前用户的数据目录和数据库。",
    )
    parser.add_argument("--data-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    paths = AppPaths.from_root(args.data_root) if args.data_root else AppPaths.for_current_user()
    if args.init_data:
        paths.ensure()
        initialize_database(paths.database)
        print(str(paths.root))
        return 0

    from examdesk.ui import run_application

    return run_application(
        paths,
        screenshot_path=args.screenshot,
        admin_enabled=edition.admin_enabled(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
