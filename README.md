# RCPD Internal Affairs Bot

A configurable Discord bot for publishing two official RCPD Internal Affairs release types:
- **Disciplinary Release** — rank, officer, multiple violations, multiple punishments, and appeal information.
- **Investigation Release** — rank and officer with the standard investigation notice.

The bot uses Discord slash commands, ephemeral previews, and a confirmation button before publishing.

## Requirements

- Python 3.10+
- A Discord application/bot with the `bot` and `applications.commands` OAuth scopes
- Bot permissions in the release channel: **View Channel**, **Send Messages**, **Embed Links**
- Your server's role IDs and release channel ID

## Setup

1. Clone or download this repository.
2. Install Python 3.10 or newer.
3. Install dependencies:

   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env`, then set:
   - `DISCORD_TOKEN`: bot token from the Discord Developer Portal.
   - `GUILD_ID`: your test server ID. Keeping this set makes slash commands sync quickly to that server. Leave blank for global command registration.

5. Edit `config/config.json`:
   - `release_channel_id`: ID of the channel where releases should be published.
   - `authorized_role_ids`: list of role IDs allowed to create and publish releases. Example: `[123456789012345678]`.
   - `embed_color`: hexadecimal embed color.
   - `embed_footer`: optional footer text.
   - `allow_custom_reasons_and_punishments`: when `true`, staff can type any violation/punishment. When `false`, only configured keys/names are accepted.

6. Customize `config/violations.json`, `config/punishments.json`, and `config/templates.json`.
7. Start the bot:

   ```bash
   python bot.py
   ```

8. In your server, use `/ia-release`.

## Workflow

1. Run `/ia-release`.
2. Choose **Disciplinary Release** or **Investigation Release**.
3. Enter the officer rank and, optionally, a manual officer name.
4. Select a Discord user if appropriate. If a manual name was entered, leave the user selector empty to use that name. This supports people who are no longer in the server.
5. For disciplinary releases, enter one or more violations and punishments, separated by new lines. Commas and semicolons also work as separators.
6. Review the private preview and click **Publish Release** to post it to the configured channel.

The bot suppresses automatic mentions when publishing. A selected Discord user is rendered as their mention in the release text, but it will not ping them.

## Template placeholders

`config/templates.json` contains the release templates.

Disciplinary template placeholders:
- `{rank}`
- `{officer}`
- `{punishments}`
- `{reasons}`

Investigation template placeholders:
- `{rank}`
- `{officer}`

Use `\n` in JSON strings for line breaks. Keep the supplied IA seal emoji markup and appeal channel ID if those are correct for your server.

## Configuration commands

- `/ia-reload-config` — server administrators can validate/reload the configuration files after edits. The files are read when a release is created as well, so a restart is not usually needed.

## Security notes

- Never commit `.env` or share your bot token. If it leaks, reset it immediately in the Discord Developer Portal.
- Configure at least one IA role ID in `authorized_role_ids`. If the list is empty, release creation is denied (administrators are also allowed).
- Give the bot only the permissions it needs.
- Test the bot in a private channel before using it for official releases.

## Current scope

This initial version supports the two requested release types and text entry for multiple violations/punishments. The JSON files are included as the central place to maintain the approved list. Optional future enhancements include dropdown-based multi-selects populated directly from those JSON files, case logging, duration fields, and audit history.
