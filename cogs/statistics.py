from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

import core
from cogs.investigators import is_ia_member


def parse_period(start_date: str | None, end_date: str | None) -> tuple[str, str, str, str]:
    today = datetime.now(timezone.utc).date()
    try:
        start = date.fromisoformat(start_date) if start_date else today - timedelta(days=29)
        end = date.fromisoformat(end_date) if end_date else today
    except ValueError as exc:
        raise ValueError("Dates must use YYYY-MM-DD format.") from exc
    if end < start:
        raise ValueError("End date must be on or after start date.")
    if (end - start).days > 366:
        raise ValueError("Please select a period of 366 days or fewer.")
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start.isoformat(), end.isoformat(), start_dt.isoformat(timespec="seconds"), end_dt.isoformat(timespec="seconds")


def configured_punishment_counts(release_rows) -> tuple[dict[str, int], int]:
    """Count configured punishment labels from config/punishments.json."""
    try:
        configured = core.load_options("punishments.json")
    except (OSError, ValueError, json.JSONDecodeError):
        configured = {}
    labels = {
        str(key): str(item.get("name", key))
        for key, item in configured.items()
        if isinstance(item, dict)
    }
    counts = {label: 0 for label in labels.values()}
    custom_count = 0
    disciplinary_releases = 0

    for row in release_rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        raw = str(payload.get("punishments", "") or "").strip()
        if raw.casefold() in {"", "not specified", "none", "n/a", "-"}:
            continue
        disciplinary_releases += 1
        entries = [part.strip() for part in re.split(r"[,;\n]+|\band\b", raw, flags=re.IGNORECASE) if part.strip()]
        if not entries:
            entries = [raw]
        for entry in entries:
            folded = entry.casefold()
            matches = [label for key, label in labels.items() if key.casefold() in folded or label.casefold() in folded]
            if matches:
                # One selected entry represents one punishment; prefer the most specific match.
                label = max(set(matches), key=len)
                counts[label] = counts.get(label, 0) + 1
            else:
                custom_count += 1

    if custom_count:
        counts["Custom / Unconfigured"] = custom_count
    return counts, disciplinary_releases


class StatisticsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ia-stats", description="View disciplinary and case statistics for a selected period.")
    @app_commands.guild_only()
    @app_commands.describe(start_date="Start date (YYYY-MM-DD); defaults to 30 days ago", end_date="End date (YYYY-MM-DD); defaults to today", investigator="Optional investigator filter")
    async def stats(self, interaction: discord.Interaction, start_date: str | None = None, end_date: str | None = None, investigator: discord.Member | None = None) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_ia_member(interaction.user):
            await interaction.response.send_message("This command is restricted to IA personnel.", ephemeral=True)
            return
        try:
            start_label, end_label, start_iso, end_iso = parse_period(start_date, end_date)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        where = "created_at >= ? AND created_at < ? AND status='published'"
        params: list[str] = [start_iso, end_iso]
        if investigator:
            where += " AND requested_by=?"
            params.append(str(investigator.id))
        with core.db_connect() as conn:
            releases = conn.execute(f"SELECT payload_json,requested_by,created_at FROM releases WHERE {where} ORDER BY created_at", params).fetchall()
            case_where = "created_at >= ? AND created_at < ?"
            case_params: list[str] = [start_iso, end_iso]
            if investigator:
                case_where += " AND assigned_to=?"
                case_params.append(str(investigator.id))
            cases_total = conn.execute(f"SELECT COUNT(*) FROM cases WHERE {case_where}", case_params).fetchone()[0]
            closed_where = "updated_at >= ? AND updated_at < ? AND status IN ('Closed','Dismissed','Resolved')"
            closed_params: list[str] = [start_iso, end_iso]
            if investigator:
                closed_where += " AND assigned_to=?"
                closed_params.append(str(investigator.id))
            cases_closed = conn.execute(f"SELECT COUNT(*) FROM cases WHERE {closed_where}", closed_params).fetchone()[0]
            appeal_where = "updated_at >= ? AND updated_at < ? AND reviewer_id IS NOT NULL"
            appeal_params: list[str] = [start_iso, end_iso]
            if investigator:
                appeal_where += " AND reviewer_id=?"
                appeal_params.append(str(investigator.id))
            appeals = conn.execute(f"SELECT COUNT(*) FROM appeals WHERE {appeal_where}", appeal_params).fetchone()[0]
        counts, disciplinary_releases = configured_punishment_counts(releases)
        title = "RCPD IA Statistics"
        if investigator:
            title += f" — {investigator.display_name}"
        embed = discord.Embed(title=title, color=discord.Color.from_str(core.load_config().get("embed_color", "#202225")))
        embed.description = f"**Reporting period:** {start_label} to {end_label} (UTC)\n**Published disciplinary releases:** {disciplinary_releases}\n**Total disciplinary actions:** {sum(counts.values())}"
        embed.add_field(name="Disciplinary Actions", value=("\n".join(f"**{name}:** {count}" for name, count in counts.items()) or "No punishment types are configured."), inline=False)
        embed.add_field(name="Case Management", value=f"**Cases opened in period:** {cases_total}\n**Closed / resolved in period:** {cases_closed}", inline=True)
        embed.add_field(name="Appeals", value=f"**Appeals reviewed in period:** {appeals}", inline=True)
        footer = core.load_config().get("embed_footer", "RCPD Internal Affairs • Official Release")
        if not disciplinary_releases and not cases_total and not appeals:
            embed.set_footer(text=f"{footer} • No matching records")
        else:
            embed.set_footer(text=f"{footer} • Counts reflect records stored by the bot")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatisticsCog(bot))
