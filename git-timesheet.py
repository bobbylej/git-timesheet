#!/usr/bin/env python3
"""
Analyze git reflog checkouts and show how long you worked on each branch.

Usage:
  git-timesheet                    # all reflog history
  git-timesheet --since 1 day      # last 24 hours
  git-timesheet --since 1 week     # last 7 days
  git-timesheet --since 2026-05-18 # since a specific date
  git-timesheet --max-gap 8h       # cap long idle gaps in totals
  git-timesheet --work-hours 8:00-17:00  # only count time within work hours
  git-timesheet --work-days mon-fri      # only count time on work days
  git-timesheet --group-by-day           # group sessions by calendar day
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone


CHECKOUT_RE = re.compile(
    r"(\S+) HEAD@\{(\d+)\}: checkout: moving from .+ to (.+)$"
)

DAY_NAMES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}
DAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class Session:
    branch: str
    start: datetime
    end: datetime

    @property
    def duration_seconds(self) -> int:
        return max(0, int((self.end - self.start).total_seconds()))


def parse_duration(value: str) -> int:
    """Parse durations like 8h, 30m, 1d into seconds."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd])", value.strip().lower())
    if not match:
        raise argparse.ArgumentTypeError(
            f"invalid duration '{value}', use forms like 30m, 8h, 1d"
        )

    amount = float(match.group(1))
    unit = match.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return int(amount * multipliers[unit])


def parse_time(value: str) -> time:
    """Parse times like 8:00 or 17:30."""
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt).time()
        except ValueError:
            continue

    raise argparse.ArgumentTypeError(
        f"invalid time '{value}', use forms like 8:00 or 17:30"
    )


def parse_work_day(value: str) -> int:
    """Parse a weekday token like mon, Monday, or ISO day number 1-7."""
    token = value.strip().lower()
    if token in DAY_NAMES:
        return DAY_NAMES[token]

    match = re.fullmatch(r"([1-7])", token)
    if match:
        return int(match.group(1)) - 1

    raise argparse.ArgumentTypeError(
        f"invalid weekday '{value}', use names like mon or numbers 1-7 (Mon-Sun)"
    )


def expand_work_day_range(start: int, end: int) -> frozenset[int]:
    if start <= end:
        return frozenset(range(start, end + 1))

    raise argparse.ArgumentTypeError(
        f"invalid work-day range '{DAY_LABELS[start]}-{DAY_LABELS[end]}'"
    )


def parse_work_days(value: str) -> frozenset[int]:
    """Parse work-day sets like mon-fri, mon,wed,fri, or 1-5."""
    cleaned = value.strip().lower()
    if not cleaned:
        raise argparse.ArgumentTypeError("work days cannot be empty")

    range_match = re.fullmatch(r"([^,-]+)-([^,-]+)", cleaned)
    if range_match and "," not in cleaned:
        start = parse_work_day(range_match.group(1))
        end = parse_work_day(range_match.group(2))
        return expand_work_day_range(start, end)

    days: set[int] = set()
    for part in cleaned.split(","):
        token = part.strip()
        if not token:
            continue
        days.add(parse_work_day(token))

    if not days:
        raise argparse.ArgumentTypeError(
            f"invalid work days '{value}', use forms like mon-fri or mon,wed,fri"
        )
    return frozenset(days)


def format_work_days(work_days: frozenset[int]) -> str:
    ordered = sorted(work_days)
    if ordered == list(range(5)):
        return "Mon-Fri"
    if ordered == list(range(7)):
        return "Mon-Sun"
    if len(ordered) >= 2 and ordered == list(range(ordered[0], ordered[-1] + 1)):
        return f"{DAY_LABELS[ordered[0]]}-{DAY_LABELS[ordered[-1]]}"
    return ", ".join(DAY_LABELS[day] for day in ordered)


def parse_work_hours(value: str) -> tuple[time, time]:
    """Parse work-hour ranges like 8:00-17:00."""
    match = re.fullmatch(r"([^-]+)-([^-]+)", value.strip())
    if not match:
        raise argparse.ArgumentTypeError(
            f"invalid work hours '{value}', use a range like 8:00-17:00"
        )

    start = parse_time(match.group(1))
    end = parse_time(match.group(2))
    if start >= end:
        raise argparse.ArgumentTypeError(
            f"work hours start must be before end (got {value})"
        )
    return start, end


UNIT_SECONDS = {
    "second": 1,
    "sec": 1,
    "minute": 60,
    "min": 60,
    "hour": 3600,
    "hr": 3600,
    "day": 86400,
    "week": 7 * 86400,
    "month": 30 * 86400,
    "year": 365 * 86400,
}

RELATIVE_SINCE_RE = re.compile(
    r"^(\d+)\s*(second|sec|minute|min|hour|hr|day|week|month|year)s?(?:\s+ago)?$"
)


def parse_since(value: str) -> datetime:
    """Parse --since values for git or explicit dates."""
    lowered = value.strip().lower()
    now = datetime.now().astimezone()

    fixed = {
        "now": timedelta(0),
        "today": timedelta(0),
        "yesterday": timedelta(days=1),
    }

    if lowered in fixed:
        return now - fixed[lowered]

    match = RELATIVE_SINCE_RE.match(lowered)
    if match:
        amount, unit = match.groups()
        return now - timedelta(seconds=int(amount) * UNIT_SECONDS[unit])

    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=now.tzinfo)
        except ValueError:
            continue

    raise argparse.ArgumentTypeError(
        f"could not parse --since value '{value}' "
        "(try: 1 day, 2 weeks, 3 days ago, or 2026-05-18)"
    )


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"

    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s" if secs else f"{minutes}m"

    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {mins:02d}m" if mins else f"{hours}h"

    days, hrs = divmod(hours, 24)
    return f"{days}d {hrs:02d}h" if hrs else f"{days}d"


def format_timestamp(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def format_time(value: datetime) -> str:
    return value.astimezone().strftime("%H:%M")


def format_date_header(value: date) -> str:
    return f"{value.strftime('%Y-%m-%d')} ({DAY_LABELS[value.weekday()]})"


def capped_session_end(session: Session, max_gap: int | None) -> datetime:
    end = session.end
    if max_gap is not None:
        capped_end = session.start + timedelta(seconds=max_gap)
        if end > capped_end:
            end = capped_end
    return end


def iter_session_day_segments(
    start: datetime,
    end: datetime,
    work_hours: tuple[time, time] | None = None,
    work_days: frozenset[int] | None = None,
) -> list[tuple[date, datetime, datetime, int]]:
    """Return day segments as (local_date, start, end, counted_seconds)."""
    if end <= start:
        return []

    local_start = start.astimezone()
    local_end = end.astimezone()
    tz = local_start.tzinfo
    segments: list[tuple[date, datetime, datetime, int]] = []
    current_date = local_start.date()

    while current_date <= local_end.date():
        if work_days is not None and current_date.weekday() not in work_days:
            current_date += timedelta(days=1)
            continue

        if work_hours is not None:
            work_start, work_end = work_hours
            window_start = datetime.combine(current_date, work_start, tzinfo=tz)
            window_end = datetime.combine(current_date, work_end, tzinfo=tz)
        else:
            window_start = datetime.combine(current_date, time.min, tzinfo=tz)
            window_end = datetime.combine(
                current_date + timedelta(days=1),
                time.min,
                tzinfo=tz,
            )

        overlap_start = max(local_start, window_start)
        overlap_end = min(local_end, window_end)
        if overlap_end > overlap_start:
            seconds = int((overlap_end - overlap_start).total_seconds())
            segments.append((current_date, overlap_start, overlap_end, seconds))

        current_date += timedelta(days=1)

    return segments


def load_checkouts(repo: str) -> list[tuple[str, datetime]]:
    output = subprocess.check_output(
        [
            "git",
            "-C",
            repo,
            "reflog",
            "--date=unix",
            "--grep-reflog=checkout: moving",
        ],
        text=True,
    )

    checkouts: list[tuple[str, datetime]] = []
    for line in output.splitlines():
        match = CHECKOUT_RE.match(line)
        if not match:
            continue
        ts = int(match.group(2))
        branch = match.group(3)
        checkouts.append((branch, datetime.fromtimestamp(ts, tz=timezone.utc)))

    return checkouts


def build_sessions(checkouts: list[tuple[str, datetime]]) -> list[Session]:
    if not checkouts:
        return []

    now = datetime.now(tz=timezone.utc)
    sessions: list[Session] = []
    newest_branch, newest_ts = checkouts[0]
    prev_ts: datetime | None = None

    for branch, ts in checkouts:
        if prev_ts is not None:
            sessions.append(Session(branch=branch, start=ts, end=prev_ts))
        prev_ts = ts

    sessions.append(Session(branch=newest_branch, start=newest_ts, end=now))
    return sessions


def duration_in_work_window(
    start: datetime,
    end: datetime,
    work_hours: tuple[time, time] | None = None,
    work_days: frozenset[int] | None = None,
) -> int:
    """Return seconds of [start, end) inside optional work days and/or hours."""
    return sum(
        seconds
        for _, _, _, seconds in iter_session_day_segments(
            start, end, work_hours, work_days
        )
    )


def effective_duration(
    session: Session,
    max_gap: int | None,
    work_hours: tuple[time, time] | None = None,
    work_days: frozenset[int] | None = None,
) -> int:
    end = capped_session_end(session, max_gap)

    if work_hours is None and work_days is None:
        return max(0, int((end - session.start).total_seconds()))

    return duration_in_work_window(session.start, end, work_hours, work_days)


def session_overlaps_filter(session: Session, since: datetime | None) -> bool:
    if since is None:
        return True
    since_utc = since.astimezone(timezone.utc)
    return session.end.astimezone(timezone.utc) >= since_utc


def segment_overlaps_filter(segment_end: datetime, since: datetime | None) -> bool:
    if since is None:
        return True
    return segment_end.astimezone(timezone.utc) >= since.astimezone(timezone.utc)


def print_report_header(
    repo: str,
    since: datetime | None,
    max_gap: int | None,
    work_hours: tuple[time, time] | None,
    work_days: frozenset[int] | None,
    group_by_day: bool,
    day_count: int | None = None,
    branch_count: int | None = None,
) -> None:
    repo_name = subprocess.check_output(
        ["git", "-C", repo, "rev-parse", "--show-toplevel"],
        text=True,
    ).strip()

    print(f"Repository: {repo_name}")
    if since:
        print(f"Since:      {format_timestamp(since)}")
    if max_gap is not None:
        print(f"Max gap:    {format_duration(max_gap)} (idle time capped in totals)")
    if work_hours is not None:
        start, end = work_hours
        print(
            f"Work hours: {start.strftime('%H:%M')}-{end.strftime('%H:%M')} "
            "(local time, counted in totals)"
        )
    if work_days is not None:
        print(
            f"Work days:  {format_work_days(work_days)} "
            "(local time, counted in totals)"
        )
    if group_by_day:
        print(f"Days:       {day_count or 0}")
    else:
        print(f"Branches:   {branch_count or 0}")
    print()


def print_report_by_day(
    sessions: list[Session],
    since: datetime | None,
    max_gap: int | None,
    work_hours: tuple[time, time] | None,
    work_days: frozenset[int] | None,
    repo: str,
) -> None:
    filtered = [s for s in sessions if session_overlaps_filter(s, since)]

    by_day: dict[date, list[tuple[str, datetime, datetime, int]]] = defaultdict(list)
    for session in filtered:
        end = capped_session_end(session, max_gap)
        for day, seg_start, seg_end, seconds in iter_session_day_segments(
            session.start,
            end,
            work_hours,
            work_days,
        ):
            if not segment_overlaps_filter(seg_end, since):
                continue
            by_day[day].append((session.branch, seg_start, seg_end, seconds))

    if not by_day:
        print("No branch checkout sessions found for the selected time range.")
        return

    sorted_days = sorted(by_day.keys())
    print_report_header(
        repo,
        since,
        max_gap,
        work_hours,
        work_days,
        group_by_day=True,
        day_count=len(sorted_days),
    )

    now = datetime.now(tz=timezone.utc)
    for day in sorted_days:
        day_segments = by_day[day]
        day_total = sum(seconds for _, _, _, seconds in day_segments)

        by_branch: dict[str, list[tuple[datetime, datetime, int]]] = defaultdict(list)
        for branch, seg_start, seg_end, seconds in day_segments:
            by_branch[branch].append((seg_start, seg_end, seconds))

        sorted_branches = sorted(
            by_branch.keys(),
            key=lambda branch: max(seg_end for _, seg_end, _ in by_branch[branch]),
            reverse=True,
        )

        print(f"▸ {format_date_header(day)}")
        print(f"  Total: {format_duration(day_total)}")
        print("  Branches:")

        for branch in sorted_branches:
            branch_segments = sorted(
                by_branch[branch],
                key=lambda segment: segment[0],
                reverse=True,
            )
            branch_total = sum(seconds for _, _, seconds in branch_segments)

            print(f"    {branch}  {format_duration(branch_total)}")
            for seg_start, seg_end, seconds in branch_segments:
                end_label = format_time(seg_end)
                if seg_end.timestamp() >= now.timestamp() - 1:
                    end_label = "now"
                print(
                    f"      {format_time(seg_start)} → {end_label}  "
                    f"({format_duration(seconds)})"
                )

        print()


def print_report(
    sessions: list[Session],
    since: datetime | None,
    max_gap: int | None,
    work_hours: tuple[time, time] | None,
    work_days: frozenset[int] | None,
    repo: str,
) -> None:
    filtered = [s for s in sessions if session_overlaps_filter(s, since)]

    if not filtered:
        print("No branch checkout sessions found for the selected time range.")
        return

    by_branch: dict[str, list[Session]] = defaultdict(list)
    for session in filtered:
        by_branch[session.branch].append(session)

    branch_totals = {
        branch: sum(
            effective_duration(s, max_gap, work_hours, work_days)
            for s in branch_sessions
        )
        for branch, branch_sessions in by_branch.items()
    }

    sorted_branches = sorted(
        by_branch.keys(),
        key=lambda branch: max(s.end.timestamp() for s in by_branch[branch]),
    )

    print_report_header(
        repo,
        since,
        max_gap,
        work_hours,
        work_days,
        group_by_day=False,
        branch_count=len(sorted_branches),
    )

    for branch in sorted_branches:
        branch_sessions = sorted(by_branch[branch], key=lambda s: s.start, reverse=True)
        total = branch_totals[branch]
        visits = len(branch_sessions)
        last_active = max(s.end for s in branch_sessions)

        print(f"▸ {branch}")
        print(
            f"  Total: {format_duration(total)}  |  "
            f"Visits: {visits}  |  "
            f"Last active: {format_timestamp(last_active)}"
        )
        print("  Sessions:")

        for session in branch_sessions:
            duration = session.duration_seconds
            counted = effective_duration(session, max_gap, work_hours, work_days)
            duration_label = format_duration(duration)
            if counted < duration:
                duration_label += f" (counted: {format_duration(counted)})"

            end_label = format_timestamp(session.end)
            if session.end.timestamp() >= datetime.now(tz=timezone.utc).timestamp() - 1:
                end_label = "now"

            print(
                f"    {format_timestamp(session.start)} → {end_label}  "
                f"({duration_label})"
            )

        print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Show time spent on each git branch based on checkout reflog.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s
  %(prog)s --since 1 day
  %(prog)s --since 1 week
  %(prog)s --since 2026-05-18 --max-gap 8h
  %(prog)s --work-hours 8:00-17:00
  %(prog)s --work-days mon-fri
  %(prog)s --since 1 week --work-days mon-fri --work-hours 9:00-17:30
  %(prog)s --group-by-day --since 1 week
        """,
    )
    parser.add_argument(
        "--since",
        nargs="+",
        metavar="WHEN",
        help="Only show sessions ending after this time (e.g. 1 day, 1 week, 2026-05-18)",
    )
    parser.add_argument(
        "--max-gap",
        type=parse_duration,
        help="Cap idle gaps in totals (e.g. 8h ignores overnight checkouts)",
    )
    parser.add_argument(
        "--work-hours",
        type=parse_work_hours,
        metavar="START-END",
        help="Only count time within daily work hours (e.g. 8:00-17:00, local time)",
    )
    parser.add_argument(
        "--work-days",
        type=parse_work_days,
        metavar="DAYS",
        help="Only count time on selected weekdays (e.g. mon-fri, mon,wed,fri, 1-5)",
    )
    parser.add_argument(
        "--group-by-day",
        action="store_true",
        help="Group sessions by calendar day instead of by branch",
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Path to git repository (default: current directory)",
    )
    args = parser.parse_args()

    since = parse_since(" ".join(args.since)) if args.since else None

    try:
        subprocess.check_output(
            ["git", "-C", args.repo, "rev-parse", "--git-dir"],
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        print(f"error: '{args.repo}' is not a git repository", file=sys.stderr)
        return 1

    checkouts = load_checkouts(args.repo)
    sessions = build_sessions(checkouts)
    report_kwargs = {
        "sessions": sessions,
        "since": since,
        "max_gap": args.max_gap,
        "work_hours": args.work_hours,
        "work_days": args.work_days,
        "repo": args.repo,
    }
    if args.group_by_day:
        print_report_by_day(**report_kwargs)
    else:
        print_report(**report_kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
