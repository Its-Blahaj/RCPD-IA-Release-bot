# RCPD Internal Affairs Bot

This package extends the existing RCPD IA release bot with a configurable template system, rank manager, approval workflow, SQLite records, case management, appeals, officer history, statistics, audit logs, module toggles, and backups.

## Requirements

- Windows 10/11
- Python 3.10 or newer
- A Discord application/bot with the `bot` and `applications.commands` OAuth scopes
- Bot permissions in the release/review channels: View Channel, Send Messages, Embed Links, Read Message History
- `violations.xlsx` in the same folder as `bot.py`

## Install

1. Extract this ZIP into a new folder. Do not overwrite your current bot until you have a backup.
2. Rename `RENAME-ME-TO-.env.txt` to `.env`.
3. Open `.env` and set `DISCORD_TOKEN` to your bot token and `GUILD_ID` to your Discord server ID. Never share `.env`.
4. Open Command Prompt in the extracted folder.
5. Run:

   ```bat
   py -m pip install -r requirements.txt
   py bot.py
   ```

6. On first start, the bot creates `data/ia.sqlite3` and records the existing template versions.
7. Run `/ia-config` in your Discord server.

If the bot already has a running process, stop it with `Ctrl+C` before starting this version. The `GUILD_ID` setting enables fast slash-command sync to one server. If you leave it blank, global command registration may take longer to appear.

## Config Panel

`/ia-config` is restricted to server administrators and roles listed in `config_admin_role_ids`.

The panel supports:

- Release channel, IA release roles, config/moderator roles, and standard embed settings.
- Create custom template types with an embed title, body, and color.
- Edit existing template title/body/color/footer/author, media URLs/icons, and up to 25 embed fields.
- Template preview, duplication, disabling (history is retained), and pagination through more than 25 templates.
- Rank management with custom names, ordering, and optional linked Discord role IDs.
- Approval rules per template or globally (`*`), punishment triggers, approver roles, and an approval review channel.
- Create a case, submit an appeal, view statistics/audit log, create a backup, and view module status.

### Templates

Create a template in `/ia-config` → **Create template**. Templates can use:

- `{officer}`
- `{rank}`
- `{case_reference}`
- `{reasons}`
- `{punishments}`

Use `/ia-template-duplicate source_key:<key> new_name:<name>` to duplicate an existing template. Use `/ia-template-delete template_key:<key>` to disable a custom template without deleting its history. Existing template edits are versioned in SQLite. Custom templates can be selected in `/ia-release` → **Custom templates**.

The editor uses Discord's embed constraints: at most 25 fields per embed, 256 characters for a field name, and 1024 characters for a field value. Some template settings are edited as a JSON list in the existing advanced editor.

### Ranks

In `/ia-config` → **Ranks**, add ranks with a display name, optional Discord role ID, and sort order. The release flow asks the user to select a configured rank. If no ranks are configured, a manual rank fallback is available.

Optional role validation can be enabled with `/ia-rank-role-check enabled:true`. When enabled, selecting a Discord officer with a linked rank requires that officer to have the linked role. Manual officer names cannot be checked against Discord roles.

### Approval workflow

In `/ia-config` → **Approval rules**, set:

- A template key, or `*` for all templates.
- Punishment triggers (keys such as `suspension`, `termination`, `blacklist`, or punishment names).
- Approver role IDs.
- Approval review channel ID.
- Whether the template always requires approval.

When a rule matches, the release is saved as pending and is not published to the release channel until approved. Reviewers can use the review buttons in the configured review channel, `/ia-approvals`, and `/ia-approval-decide`. If no valid review channel is configured, the pending release is still queued and reviewers must use the slash commands. Configure the review channel to receive review notifications.

## Commands

- `/ia-release` — create a disciplinary, investigation, or custom-template release.
- `/ia-config` — configuration panel.
- `/ia-reload-config` — validate config and the Excel offense matrix.
- `/ia-template-duplicate` — duplicate a template.
- `/ia-template-delete` — disable a custom template.
- `/ia-rank-role-check` — enable/disable linked-rank role validation.
- `/ia-approval-decide` — approve or reject a pending release.
- `/ia-approvals` — list pending approvals.
- `/ia-case-create`, `/ia-cases`, `/ia-case-update`, `/ia-case-note` — case management.
- `/ia-officer-history` — show matching cases, releases, and appeals.
- `/ia-appeal`, `/ia-appeals`, `/ia-appeal-decide` — appeal handling.
- `/ia-stats` — release/case/approval/appeal statistics.
- `/ia-audit` — recent audit events.
- `/ia-backup` — download a ZIP backup (token excluded).
- `/ia-restore` — upload a prior backup ZIP, confirm restoration, and create a safety backup before replacing active data.
- `/ia-module-toggle` — enable/disable cases, appeals, or custom templates.

## Excel violation matrix

The bot reads worksheets with the header columns `ID`, `Offense`, `Severity`, and `Punishment`. Each matching worksheet becomes a category. Other worksheets are ignored for offense selection. Replace `violations.xlsx` with a new copy to update the matrix, then run `/ia-reload-config`.

## Data and security

- `data/ia.sqlite3` stores ranks, template versions, pending/approved releases, cases, case notes, appeals, and audit events.
- `config/` contains the active JSON configuration, templates, and punishments.
- `backups/` stores local backups created by the bot. `/ia-backup` also sends a backup to the authorized requester. `/ia-restore` validates the ZIP and database integrity, then asks for confirmation before replacing active data.
- `.env` and the Discord token are intentionally excluded from backups and ZIPs.
- Published embeds suppress automatic Discord mentions.

## Operational limitations to understand

- Review-button views are time-limited; if the bot restarts and the view expires, reviewers can still use `/ia-approvals` and `/ia-approval-decide`.
- Approval buttons do not persist through a restart; the pending approval itself is stored in SQLite.
- Case IDs and records are stored locally on the PC hosting the bot. Back up `data/ia.sqlite3` regularly.
- The bot does not automatically create a private review channel. Create one in Discord, grant the bot access, then configure its ID in the approval settings.
- The bot does not provide an external web dashboard; the integrated control surface is Discord's private slash-command panel and modals.
