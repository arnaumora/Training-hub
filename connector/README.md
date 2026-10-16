# Training Hub connector for the Claude desktop app

`traininghub_mcp.py` lets Claude, in the Claude desktop app on the PC, read the Training Hub: activities and
their per-run numbers, weeks, glucose, the insulin and carbs log, similar runs, and the app's own reports. It
runs on the PC (Claude Desktop starts it), reaches `https://training.example.com` on the LAN or through
Tailscale, and uses a read-only token: it can read, never change anything. It runs on the Claude
subscription; it makes no API calls of its own.

Only the desktop app: claude.ai on the web and the phone app reach connectors from Anthropic's cloud, which
cannot see a Tailscale-only server.

## Tools

| Tool | What it reads |
| --- | --- |
| list_activities(days, sport) | every activity in the period with its per-run numbers |
| get_activity(activity_id) | one activity in full, as the app's run report sees it, and the report itself |
| get_week(day) | the week holding that day: load, sessions, glucose figures, nights, days, flags, summary |
| similar_runs(glucose, trend, iob, minutes_since_meal, sport) | past runs that started closest to a state |
| glucose_between(start, end) | readings and figures, up to 14 days (15-min buckets over 12 h) |
| log_between(start, end) | doses and carbs, up to 62 days |
| current_state() | latest glucose and trend, insulin on board and time since carbs, per the log |
| training_settings() | zones, LTHR, target range, sensor lag |

Insulin settings (ratios, sensitivity) are deliberately not offered.

## Set-up

1. Install uv, which runs the script and fetches its two dependencies by itself.
   - Fedora: `sudo dnf install uv`, then `command -v uv` for its full path.
   - Windows: `winget install --id=astral-sh.uv -e`, then `where.exe uv` in a new PowerShell.
2. Make a token, on the server: `docker exec traininghub-api python -m app.api_tokens create "claude desktop"`.
3. Claude Desktop: Settings (from the app menu) > Developer > Edit Config, which opens
   `claude_desktop_config.json` (Windows: `%APPDATA%\Claude\`; Linux: under `~/.config/`, the app shows where).
   Put the `traininghub` entry in it, with the full uv path from step 1, the full path of this script and the
   token:
   `"traininghub": {"command": "/usr/bin/uv", "args": ["run", "--script", "/home/you/traininghub/connector/traininghub_mcp.py"],
   "env": {"TRAININGHUB_URL": "https://training.example.com", "TRAININGHUB_TOKEN": "th_..."}}`
4. Quit Claude Desktop completely and start it again.

Tokens: `python -m app.api_tokens list` and `python -m app.api_tokens revoke ID` in the API container.

If it does not connect, Claude Desktop writes the connector's errors to `mcp-server-traininghub.log` in its
logs folder (Windows: `%APPDATA%\Claude\logs`). Running the command from the config by hand in a terminal
shows the same errors.
