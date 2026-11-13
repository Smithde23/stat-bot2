# PlayCHL Discord bot (experimental public-page reader)

This bot reads public PlayCHL standings and skater tables using Playwright, posts standings checks every 30 minutes (only when standings have changed), and exposes `/standings`, `/team`, `/leaders`, and `/player` slash commands. It does NOT use an undocumented API.

## Setup (Windows PowerShell)

1. Install Python 3.11 or newer from python.org and check **Add Python to PATH**.
2. Extract this ZIP, open PowerShell in the extracted folder.
3. Run `py -m pip install -r requirements.txt`.
4. Run `py -m playwright install chromium`.
5. Copy `.env.example` to `.env` and edit the values.
6. In Discord Developer Portal, create an application and bot, copy its token. Never share this token. Under Installation/OAuth2, invite with `bot` and `applications.commands` scopes, and `View Channels`, `Send Messages`, `Embed Links` permissions.
7. In Discord, turn on Developer Mode (Settings > Advanced), right-click the target channel > Copy Channel ID. Paste as `CHANNEL_ID`. Copy your server ID into `GUILD_ID` for faster slash command sync.
8. Run `py bot.py`.

## Important limitations

- PlayCHL's publicly readable pages were verified through search results, but direct access from the build environment was blocked. The selectors have NOT been live-tested. If PlayCHL renders div-based grids instead of actual HTML `<table>` elements, the extractor will fail **explicitly** rather than invent statistics. Send the error and a page HTML sample (without credentials) for adjustment.
- `/leaders` and `/player` read only skater rows currently rendered on the statistics page. Filters, pagination, and goalie statistics require additional integration.
- Automatic updates check every 30 minutes and post only when standings differ from the last successful check. On restart, a new post is sent.
- The process must stay running. For 24/7 use, host on a VPS or a Python-capable always-on service.
- Respect site terms, avoid excessive requests, and seek a documented API from site administrators if available.
