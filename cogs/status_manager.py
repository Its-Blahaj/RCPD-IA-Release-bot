"""Configurable rotating bot presence and IA maintenance mode."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import ROOT, can_manage_config, load_config

log = logging.getLogger("rcpd-ia.status")
STATUS_PATH = ROOT / "config" / "status.json"
DEFAULT_SETTINGS: dict[str, Any] = {
    "rotation_interval_seconds": 60,
    "statuses": [
        {"type": "watching", "text": "{member_count} members | {guild_count} servers"},
        {"type": "playing", "text": "RCPD Internal Affairs"},
        {"type": "listening", "text": "IA reports and investigations"}
    ],
    "maintenance_mode": False,
    "maintenance_text": "MAINTENANCE"
}


def load_status_settings() -> dict[str, Any]:
    if not STATUS_PATH.exists():
        save_status_settings(DEFAULT_SETTINGS.copy())
    try:
        with STATUS_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        log.exception("Could not read config/status.json; using defaults")
        data = DEFAULT_SETTINGS.copy()
    merged = DEFAULT_SETTINGS.copy()
    merged.update(data if isinstance(data, dict) else {})
    return merged


def save_status_settings(data: dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = STATUS_PATH.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(STATUS_PATH)


class StatusManager(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.bot = client
        self.rotation_index = 0
        interval = load_status_settings().get("rotation_interval_seconds", 60)
        try:
            self.rotate_presence.change_interval(seconds=max(15, min(3600, int(interval))))
        except (TypeError, ValueError):
            self.rotate_presence.change_interval(seconds=60)
        self.rotate_presence.start()

    def cog_unload(self) -> None:
        self.rotate_presence.cancel()

    def _placeholder_values(self) -> dict[str, str]:
        """Return public-facing server and IA activity metrics for status templates.

        Punishment placeholders use the configured punishment ID, e.g. warning_count.
        Counts are lifetime totals from published releases unless otherwise named.
        """
        guilds = list(self.bot.guilds)
        member_count = sum(guild.member_count or len(guild.members) for guild in guilds)
        primary = guilds[0] if guilds else None
        values: dict[str, str] = {
            "member_count": str(member_count),
            "guild_count": str(len(guilds)),
            "server_name": primary.name if primary else "RCPD",
            "server_member_count": str(primary.member_count or len(primary.members)) if primary else "0",
            "bot_name": self.bot.user.name if self.bot.user else "RCPD IA",
        }

        # Add useful IA metrics. Database tables are created by the core/cogs;
        # tolerate a fresh or partially initialized database during startup.
        try:
            import core
            configured = core.load_options("punishments.json")
            punishment_counts = {str(key): 0 for key in configured}
            with core.db_connect() as conn:
                rows = conn.execute("SELECT payload_json FROM releases WHERE status='published'").fetchall()
                values["published_release_count"] = str(len(rows))
                for row in rows:
                    try:
                        payload = json.loads(row[0] or "{}")
                    except (json.JSONDecodeError, TypeError):
                        continue
                    raw = str(payload.get("punishments", "") or "").casefold()
                    if raw.strip() in {"", "not specified", "none", "n/a", "-"}:
                        continue
                    # A release can list multiple punishments separated by commas,
                    # semicolons, newlines, or the word 'and'.
                    entries = [part.strip() for part in re.split(r"[,;\n]+|\band\b", raw, flags=re.IGNORECASE) if part.strip()]
                    for entry in entries:
                        matches = [key for key, item in configured.items()
                                   if isinstance(item, dict) and
                                   (str(key).casefold() in entry or str(item.get("name", key)).casefold() in entry)]
                        if matches:
                            punishment_counts[max(matches, key=len)] += 1
                for key, count in punishment_counts.items():
                    values[f"{key}_count"] = str(count)

                values["case_count"] = str(conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0])
                values["open_case_count"] = str(conn.execute("SELECT COUNT(*) FROM cases WHERE lower(status)='open'").fetchone()[0])
                values["closed_case_count"] = str(conn.execute("SELECT COUNT(*) FROM cases WHERE lower(status) IN ('closed','dismissed','resolved')").fetchone()[0])
                values["pending_appeal_count"] = str(conn.execute("SELECT COUNT(*) FROM appeals WHERE lower(status)='pending'").fetchone()[0])
                values["appeal_count"] = str(conn.execute("SELECT COUNT(*) FROM appeals").fetchone()[0])
                try:
                    values["investigator_count"] = str(conn.execute("SELECT COUNT(*) FROM investigator_profiles").fetchone()[0])
                except Exception:
                    values["investigator_count"] = "0"
        except Exception:
            log.exception("Could not load IA metrics for rotating presence")
        return values

    def _build_activity(self, entry: dict[str, Any]) -> discord.BaseActivity:
        values = self._placeholder_values()
        text = str(entry.get("text", "RCPD Internal Affairs"))
        try:
            text = text.format_map(values)
        except (KeyError, ValueError) as exc:
            log.warning("Invalid status placeholder in %r: %s", text, exc)
        text = text[:128]
        activity_type = str(entry.get("type", "playing")).lower()
        if activity_type == "listening":
            return discord.Activity(type=discord.ActivityType.listening, name=text)
        if activity_type == "watching":
            return discord.Activity(type=discord.ActivityType.watching, name=text)
        if activity_type == "competing":
            return discord.Activity(type=discord.ActivityType.competing, name=text)
        return discord.Game(name=text)

    @tasks.loop(seconds=60)
    async def rotate_presence(self) -> None:
        settings = load_status_settings()
        if settings.get("maintenance_mode"):
            activity = discord.Game(name=str(settings.get("maintenance_text", "MAINTENANCE"))[:128])
            await self.bot.change_presence(status=discord.Status.idle, activity=activity)
            return

        entries = settings.get("statuses", [])
        entries = [entry for entry in entries if isinstance(entry, dict) and str(entry.get("text", "")).strip()]
        if not entries:
            await self.bot.change_presence(status=discord.Status.online, activity=None)
            return

        self.rotation_index %= len(entries)
        entry = entries[self.rotation_index]
        self.rotation_index = (self.rotation_index + 1) % len(entries)
        activity = self._build_activity(entry)
        status_name = str(entry.get("status", "online")).lower()
        presence_status = {
            "online": discord.Status.online,
            "idle": discord.Status.idle,
            "dnd": discord.Status.dnd,
            "invisible": discord.Status.invisible,
        }.get(status_name, discord.Status.online)
        await self.bot.change_presence(status=presence_status, activity=activity)

    @rotate_presence.before_loop
    async def before_rotate_presence(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="ia-maintanance", description="Enable or disable IA maintenance mode.")
    @app_commands.guild_only()
    @app_commands.describe(enabled="Whether maintenance mode should be active", message="Optional activity text shown while maintenance is active")
    async def ia_maintanance(self, interaction: discord.Interaction, enabled: bool, message: str | None = None) -> None:
        settings = load_status_settings()
        if not can_manage_config(interaction, load_config()):
            await interaction.response.send_message("Only server administrators or configured config roles can change maintenance mode.", ephemeral=True)
            return
        settings["maintenance_mode"] = enabled
        if message and message.strip():
            settings["maintenance_text"] = message.strip()[:128]
        save_status_settings(settings)
        if enabled:
            await self.bot.change_presence(status=discord.Status.idle, activity=discord.Game(name=str(settings.get("maintenance_text", "MAINTENANCE"))[:128]))
            await interaction.response.send_message(f"Maintenance mode enabled. Bot status: **{settings.get('maintenance_text', 'MAINTENANCE')}** (Abwesend).", ephemeral=True)
        else:
            self.rotation_index = 0
            await interaction.response.send_message("Maintenance mode disabled. The configured status rotation will resume shortly.", ephemeral=True)
            await self.rotate_presence()


async def setup(client: commands.Bot) -> None:
    await client.add_cog(StatusManager(client))
