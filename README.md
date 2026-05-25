# git-timesheet

Estimate how long you worked on each git branch by analyzing checkout entries in the reflog.

## Requirements

- Python 3.9+
- Git

No third-party Python packages are required.

## Setup

Run the script directly:

```bash
python3 git-timesheet.py
```

To use it from anywhere, copy the script into your `~/bin` folder and add a shell alias or function.

1. Copy the script:

```bash
mkdir -p ~/bin
cp git-timesheet.py ~/bin/git-timesheet
chmod +x ~/bin/git-timesheet
```

2. Make sure `~/bin` is on your `PATH` (add to `~/.bashrc` or `~/.zshrc` if needed):

```bash
export PATH="$HOME/bin:$PATH"
```

### Bash

Add this to your `~/.bashrc`:

```bash
git-timesheet() {
  "$HOME/bin/git-timesheet" "$@"
}
```

Then reload your shell:

```bash
source ~/.bashrc
```

### Zsh

Add this to your `~/.zshrc`:

```bash
git-timesheet() {
  "$HOME/bin/git-timesheet" "$@"
}
```

Then reload your shell:

```bash
source ~/.zshrc
```

## Usage

Run inside a git repository (or pass `--repo`):

```bash
git-timesheet
git-timesheet --since 1 week
git-timesheet --since 2026-05-18
git-timesheet --group-by-day --since 1 week
git-timesheet --work-days mon-fri --work-hours 8:00-17:00
git-timesheet --since 1 week --max-gap 8h
```

Recommended command with all options — useful for a weekly work log that counts only weekday office hours, caps long idle gaps, and shows a per-day breakdown:

```bash
git-timesheet \
  --since 1 week \
  --work-days mon-fri \
  --work-hours 8:00-17:00 \
  --max-gap 8h \
  --group-by-day
```

From another directory, add `--repo`:

```bash
git-timesheet \
  --repo /path/to/your/repo \
  --since 1 week \
  --work-days mon-fri \
  --work-hours 8:00-17:00 \
  --max-gap 8h \
  --group-by-day
```

## Options

| Option | Description |
|--------|-------------|
| `--since WHEN` | Only include sessions ending after this time (`1 day`, `1 week`, `2026-05-18`, etc.) |
| `--max-gap DURATION` | Cap idle time in totals (e.g. `8h` ignores long overnight gaps) |
| `--work-hours START-END` | Count only time inside daily hours (e.g. `8:00-17:00`, local time) |
| `--work-days DAYS` | Count only on selected weekdays (`mon-fri`, `mon,wed,fri`, `1-5`) |
| `--group-by-day` | Group output by calendar day instead of by branch |
| `--repo PATH` | Path to a git repository (default: current directory) |

## How it works

1. Reads `git reflog` entries for branch checkouts.
2. Builds sessions between consecutive checkouts (current branch runs until the next checkout or now).
3. Totals time per branch, with optional filters for work hours, work days, and idle gaps.

Times are shown in your local timezone.

## Example output

By branch (default):

```
▸ feature/my-branch
  Total: 3h 15m  |  Visits: 2  |  Last active: 2026-05-25 16:30
  Sessions:
    2026-05-25 14:00 → now  (2h 30m)
    2026-05-24 09:00 → 2026-05-24 09:45  (45m)
```

By day (`--group-by-day`):

```
▸ 2026-05-24 (Sat)
  Total: 45m
  Branches:
    feature/my-branch  45m
      09:00 → 09:45  (45m)

▸ 2026-05-25 (Sun)
  Total: 2h 30m
  Branches:
    feature/my-branch  2h 30m
      14:00 → now  (2h 30m)
```

## Notes

- Time is inferred from checkouts, not commits or file edits.
- If you stay on one branch for a long time without switching, that session may include idle time unless you use `--max-gap`.
- Reflog entries can expire; older history may be missing depending on git configuration.
