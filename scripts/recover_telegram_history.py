# scripts/recover_telegram_history.py
"""Recover missed Telegram group pins/reports into PostgreSQL via Telethon Client API.

Usage examples:
  1) Login once and save local session:
     python scripts/recover_telegram_history.py --login --api-id 123456 --api-hash abcdef...

  2) Dry-run preview across all 65 groups for today:
     python scripts/recover_telegram_history.py --date 2026-11-18

  3) Apply recovery + reconcile false missing alerts:
     python scripts/recover_telegram_history.py --date 2026-11-18 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)

from app.core.config import get_settings
from app.services.telethon_recovery import sync_history_with_telethon
from app.services.time_service import local_now


def _parse_time(raw: str) -> time:
    return datetime.strptime(raw.strip(), "%H:%M").time()


async def _login_interactive(api_id: int, api_hash: str, session_path: str, print_string_session: bool) -> None:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    resolved = Path(session_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(resolved), api_id, api_hash)
    await client.start()
    me = await client.get_me()
    print(
        f"LOGIN OK: {getattr(me, 'first_name', '')} (@{getattr(me, 'username', None)}) "
        f"session saved to {resolved}"
    )
    if print_string_session:
        str_client = StringSession.save(client.session)
        print("TELETHON_STRING_SESSION (keep secret!):")
        print(str_client)
    await client.disconnect()


def main() -> None:
    settings = get_settings()

    parser = argparse.ArgumentParser(
        description="Backfill missed SA pins/reports from Telegram group history via Telethon."
    )
    parser.add_argument("--api-id", type=int, default=settings.TELETHON_API_ID or int(os.getenv("TELETHON_API_ID") or 0))
    parser.add_argument("--api-hash", default=settings.TELETHON_API_HASH or os.getenv("TELETHON_API_HASH", ""))
    parser.add_argument("--session", default=settings.TELETHON_SESSION_FILE)
    parser.add_argument("--string-session", default=settings.TELETHON_STRING_SESSION)
    parser.add_argument("--login", action="store_true", help="Authenticate Telethon user account once and exit.")
    parser.add_argument("--print-string-session", action="store_true", help="Also output StringSession during --login.")
    parser.add_argument("--date", dest="target_date", default=local_now().strftime("%Y-%m-%d"), help="Target local date YYYY-MM-DD.")
    parser.add_argument("--from", dest="from_time", default="00:00", help="Start local time HH:MM.")
    parser.add_argument("--to", dest="to_time", default="23:59", help="End local time HH:MM.")
    parser.add_argument("--group", default=None, help="Optional group name substring filter (e.g. BTB1).")
    parser.add_argument("--apply", action="store_true", help="Write changes to database (default is dry-run).")
    parser.add_argument("--no-reconcile", action="store_true", help="Skip clearing false missing_sale_photo_alerts.")
    args = parser.parse_args()

    if not args.api_id or not args.api_hash.strip():
        raise SystemExit(
            "Missing Telegram API credentials. Set TELETHON_API_ID and TELETHON_API_HASH in .env "
            "or pass --api-id and --api-hash (from https://my.telegram.org)."
        )

    if args.login:
        asyncio.run(_login_interactive(args.api_id, args.api_hash.strip(), args.session, args.print_string_session))
        return

    target_date = date.fromisoformat(args.target_date)
    start_t = _parse_time(args.from_time)
    end_t = _parse_time(args.to_time).replace(second=59)
    dry_run = not args.apply

    summary = asyncio.run(
        sync_history_with_telethon(
            api_id=args.api_id,
            api_hash=args.api_hash.strip(),
            target_date=target_date,
            start_time=start_t,
            end_time=end_t,
            session_path=args.session,
            string_session=args.string_session or None,
            group_filter=args.group,
            dry_run=dry_run,
            reconcile_missing=not args.no_reconcile,
        )
    )

    mode = "DRY-RUN" if summary.dry_run else "APPLIED"
    print(
        f"[{mode}] Date={summary.target_date} Groups={summary.groups_synced}/{summary.groups_total} "
        f"RecoveredPins={summary.recovered_pins} RecoveredReports={summary.recovered_reports} "
        f"RepairedTimestamps={summary.repaired_timestamps} SkippedDuplicates={summary.skipped_duplicates} "
        f"ClearedFalseAlerts={summary.reconciled_missing_alerts}"
    )
    if summary.groups_missing_dialog:
        print("Groups not visible to this Telegram account:")
        for g in summary.groups_missing_dialog:
            print("  -", g)
    for row in summary.per_group:
        if row.recovered_pins or row.recovered_reports or row.repaired_timestamps:
            print(
                f"  {row.group_name}: +{row.recovered_pins} pins, +{row.recovered_reports} reports, "
                f"~{row.repaired_timestamps} timestamps repaired (scanned {row.scanned_messages})"
            )


if __name__ == "__main__":
    main()
