from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

import core

log = logging.getLogger("rcpd-ia.investigators")


def settings() -> dict:
    cfg = core.load_config()
    value = cfg.get("investigator_settings", {})
    return value if isinstance(value, dict) else {}


def role_ids(key: str) -> set[int]:
    values = settings().get(key, [])
    try:
        return {int(x) for x in values if str(x).isdigit()}
    except TypeError:
        return set()


def is_ia_member(member: discord.Member) -> bool:
    configured = role_ids("ia_role_ids")
    if configured and any(r.id in configured for r in member.roles):
        return True
    return core.authorized(type("I", (), {"guild": member.guild, "user": member})(), core.load_config())


def is_profile_moderator(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    configured = role_ids("profile_moderator_role_ids")
    if any(r.id in configured for r in member.roles):
        return True
    return core.can_manage_config(type("I", (), {"guild": member.guild, "user": member})(), core.load_config())


def ensure_schema() -> None:
    with core.db_connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS investigator_profiles (
            user_id TEXT PRIMARY KEY,
            joined_ia_at TEXT,
            joined_ia_accuracy TEXT NOT NULL DEFAULT 'tracked',
            timezone TEXT NOT NULL DEFAULT 'UTC',
            special_information TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS investigator_role_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            role_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('added','removed')),
            occurred_at TEXT NOT NULL,
            actor_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_investigator_history_user ON investigator_role_history(user_id, id DESC);
        CREATE TABLE IF NOT EXISTS investigator_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            investigator_id TEXT NOT NULL,
            officer_id TEXT NOT NULL,
            case_code TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_investigator_claims_investigator ON investigator_claims(investigator_id, created_at);
        """)


def ensure_profile(user_id: int, joined_at: str | None = None, accuracy: str = "tracked") -> None:
    now = core.utc_now()
    with core.db_connect() as conn:
        conn.execute("""INSERT OR IGNORE INTO investigator_profiles
            (user_id,joined_ia_at,joined_ia_accuracy,timezone,special_information,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)""",
            (str(user_id), joined_at, accuracy, "UTC", "", now, now))
        if joined_at:
            conn.execute("UPDATE investigator_profiles SET joined_ia_at=COALESCE(joined_ia_at,?), updated_at=? WHERE user_id=?",
                         (joined_at, now, str(user_id)))


def get_rank(member: discord.Member) -> str:
    with core.db_connect() as conn:
        rows = conn.execute("SELECT name,discord_role_id FROM ranks WHERE enabled=1 AND discord_role_id IS NOT NULL ORDER BY sort_order ASC,id ASC").fetchall()
    member_roles = {r.id for r in member.roles}
    for row in rows:
        try:
            if int(row["discord_role_id"]) in member_roles:
                return str(row["name"])
        except (TypeError, ValueError):
            continue
    # Compatibility with role mappings in config if the ranks table has not been populated.
    mapping = settings().get("rank_role_mapping", {})
    for name, rid in mapping.items():
        try:
            if int(rid) in member_roles:
                return str(name)
        except (TypeError, ValueError):
            continue
    return "Not configured"


def stats_for(user_id: int) -> dict[str, int]:
    uid = str(user_id)
    with core.db_connect() as conn:
        assigned = conn.execute("SELECT COUNT(*) FROM cases WHERE assigned_to=?", (uid,)).fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM cases WHERE assigned_to=? AND status IN ('Closed','Dismissed','Resolved')", (uid,)).fetchone()[0]
        release_rows = conn.execute("SELECT payload_json FROM releases WHERE requested_by=? AND status='published'", (uid,)).fetchall()
        releases = 0
        for release_row in release_rows:
            try:
                payload = json.loads(release_row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            if str(payload.get("punishments", "") or "").strip().casefold() not in {"", "not specified", "none", "n/a", "-"}:
                releases += 1
        appeals = conn.execute("SELECT COUNT(*) FROM appeals WHERE reviewer_id=?", (uid,)).fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM cases WHERE assigned_to=? AND status NOT IN ('Closed','Dismissed','Resolved')", (uid,)).fetchone()[0]
    return {"assigned": assigned, "completed": completed, "active": active, "releases": releases, "appeals": appeals}


class InvestigatorCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_schema()

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        tracked = role_ids("ia_role_ids")
        if not tracked:
            return
        before_ids = {r.id for r in before.roles}
        after_ids = {r.id for r in after.roles}
        added = (after_ids - before_ids) & tracked
        removed = (before_ids - after_ids) & tracked
        if not added and not removed:
            return
        now = core.utc_now()
        ensure_profile(after.id, now if added else None)
        with core.db_connect() as conn:
            for rid in sorted(added):
                conn.execute("INSERT INTO investigator_role_history(user_id,role_id,action,occurred_at,actor_id) VALUES(?,?,?,?,NULL)",
                             (str(after.id), str(rid), "added", now))
            for rid in sorted(removed):
                conn.execute("INSERT INTO investigator_role_history(user_id,role_id,action,occurred_at,actor_id) VALUES(?,?,?,?,NULL)",
                             (str(after.id), str(rid), "removed", now))
        if added:
            log.info("Recorded first IA role assignment for Discord user %s", after.id)

    @app_commands.command(name="ia-profile", description="View an Internal Affairs investigator profile.")
    @app_commands.guild_only()
    async def profile(self, interaction: discord.Interaction, investigator: discord.Member | None = None) -> None:
        target = investigator or interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if target.id != interaction.user.id and not is_ia_member(interaction.user):
            await interaction.response.send_message("Only IA personnel can view another investigator's profile.", ephemeral=True)
            return
        if not is_ia_member(target):
            await interaction.response.send_message("That member does not currently have a configured IA role.", ephemeral=True)
            return
        ensure_profile(target.id)
        with core.db_connect() as conn:
            row = conn.execute("SELECT * FROM investigator_profiles WHERE user_id=?", (str(target.id),)).fetchone()
        zone_name = row["timezone"] if row else "UTC"
        try:
            zone = ZoneInfo(zone_name)
        except ZoneInfoNotFoundError:
            zone_name, zone = "UTC", ZoneInfo("UTC")
        local_now = datetime.now(zone)
        joined = row["joined_ia_at"] if row else None
        if joined:
            try:
                joined_text = discord.utils.format_dt(datetime.fromisoformat(joined), style="D")
            except ValueError:
                joined_text = "Unknown"
            if row["joined_ia_accuracy"] == "estimated":
                joined_text += " (estimated)"
        else:
            joined_text = "Not yet tracked"
        stats = stats_for(target.id)
        embed = discord.Embed(title=f"{target.display_name} — Investigator Profile", color=discord.Color.from_str(core.load_config().get("embed_color", "#202225")))
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Investigator Information", value=(
            f"**Discord:** {target.mention}\n**Rank:** {discord.utils.escape_markdown(get_rank(target))}\n"
            f"**Joined IA:** {joined_text}\n**Timezone:** `{zone_name}`\n"
            f"**Local Time:** {local_now.strftime('%Y-%m-%d %H:%M %Z')}\n**UTC Offset:** `{local_now.strftime('%z')}`"), inline=False)
        embed.add_field(name="Performance Statistics", value=(
            f"**Assigned Cases:** {stats['assigned']}\n**Active Cases:** {stats['active']}\n"
            f"**Completed Cases:** {stats['completed']}\n**Officer Claims:** {stats['claims']}\n"
            f"**Disciplinary Releases:** {stats['releases']}\n**Appeals Handled:** {stats['appeals']}"), inline=False)
        special = (row["special_information"] if row else "") or "No additional information has been added."
        embed.add_field(name="Special Information", value=special[:1024], inline=False)
        embed.set_footer(text=core.load_config().get("embed_footer", "RCPD Internal Affairs • Investigator Record"))
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="ia-profile-edit", description="Set your own investigator profile timezone.")
    @app_commands.guild_only()
    @app_commands.describe(timezone="IANA timezone, e.g. Europe/Berlin or America/New_York")
    async def profile_edit(self, interaction: discord.Interaction, timezone: str) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_ia_member(interaction.user):
            await interaction.response.send_message("This command is restricted to configured IA personnel.", ephemeral=True)
            return
        try:
            ZoneInfo(timezone.strip())
        except (ZoneInfoNotFoundError, ValueError):
            await interaction.response.send_message("Invalid timezone. Use an IANA name such as `Europe/Berlin` or `America/New_York`.", ephemeral=True)
            return
        ensure_profile(interaction.user.id)
        with core.db_connect() as conn:
            conn.execute("UPDATE investigator_profiles SET timezone=?,updated_at=? WHERE user_id=?",
                         (timezone.strip(), core.utc_now(), str(interaction.user.id)))
        core.audit(interaction.user.id, "investigator.timezone_updated", "investigator", interaction.user.id, {"timezone": timezone.strip()})
        await interaction.response.send_message(f"Your investigator timezone has been set to `{timezone.strip()}`.", ephemeral=True)

    @app_commands.command(name="ia-special-info", description="Set or clear special information on an investigator profile.")
    @app_commands.guild_only()
    @app_commands.describe(investigator="Investigator whose profile should be updated", information="Text to display; leave blank to clear")
    async def special_info(self, interaction: discord.Interaction, investigator: discord.Member, information: str) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_profile_moderator(interaction.user):
            await interaction.response.send_message("Only configured bot moderators can edit Special Information.", ephemeral=True)
            return
        if len(information) > 1000:
            await interaction.response.send_message("Special Information must be 1,000 characters or fewer.", ephemeral=True)
            return
        ensure_profile(investigator.id)
        clean = "" if information.strip().casefold() in {"clear", "remove", "none"} else discord.utils.escape_mentions(information.strip())
        with core.db_connect() as conn:
            conn.execute("UPDATE investigator_profiles SET special_information=?,updated_at=? WHERE user_id=?",
                         (clean, core.utc_now(), str(investigator.id)))
        core.audit(interaction.user.id, "investigator.special_info_updated", "investigator", investigator.id,
                   {"cleared": not bool(clean), "length": len(clean)})
        await interaction.response.send_message(f"Special Information for {investigator.mention} was {'cleared' if not clean else 'updated'}.", ephemeral=True)

    @app_commands.command(name="ia-investigators", description="List current IA investigators.")
    @app_commands.guild_only()
    async def investigators(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_ia_member(interaction.user):
            await interaction.response.send_message("This command is restricted to IA personnel.", ephemeral=True)
            return
        tracked = role_ids("ia_role_ids")
        if not tracked:
            await interaction.response.send_message("No IA role IDs are configured in `config.json`.", ephemeral=True)
            return
        members = [m for m in interaction.guild.members if any(r.id in tracked for r in m.roles)]
        members.sort(key=lambda m: m.display_name.casefold())
        lines = []
        for member in members[:40]:
            ensure_profile(member.id)
            with core.db_connect() as conn:
                row = conn.execute("SELECT joined_ia_at FROM investigator_profiles WHERE user_id=?", (str(member.id),)).fetchone()
            joined = row["joined_ia_at"] if row else None
            joined_label = joined[:10] if joined else "not tracked"
            lines.append(f"{member.mention} — **{discord.utils.escape_markdown(get_rank(member))}** — Joined IA: `{joined_label}`")
        embed = discord.Embed(title="RCPD Internal Affairs — Investigators", description="\n".join(lines) if lines else "No current investigators found.", color=discord.Color.from_str(core.load_config().get("embed_color", "#202225")))
        if len(members) > 40:
            embed.set_footer(text=f"Showing 40 of {len(members)} investigators.")
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="ia-claim", description="Assign yourself as the IA investigator for an officer.")
    @app_commands.guild_only()
    @app_commands.describe(officer="Officer being contacted", case_code="Optional existing IA case code")
    async def claim(self, interaction: discord.Interaction, officer: discord.Member, case_code: str | None = None) -> None:
        investigator = interaction.user
        if not isinstance(investigator, discord.Member) or not is_ia_member(investigator):
            await interaction.response.send_message("Only configured IA personnel can claim an officer.", ephemeral=True)
            return
        ensure_profile(investigator.id)
        if case_code:
            with core.db_connect() as conn:
                case = conn.execute("SELECT id FROM cases WHERE case_code=?", (case_code.upper(),)).fetchone()
                if not case:
                    await interaction.response.send_message("That IA case code was not found.", ephemeral=True)
                    return
                conn.execute("UPDATE cases SET assigned_to=?,updated_at=? WHERE id=?", (str(investigator.id), core.utc_now(), case["id"]))
        with core.db_connect() as conn:
            conn.execute("INSERT INTO investigator_claims(investigator_id,officer_id,case_code,created_at) VALUES(?,?,?,?)",
                         (str(investigator.id), str(officer.id), case_code.upper() if case_code else None, core.utc_now()))
        core.audit(investigator.id, "investigator.officer_claimed", "investigator_claim", officer.id,
                   {"case_code": case_code.upper() if case_code else None})
        with core.db_connect() as conn:
            row = conn.execute("SELECT timezone FROM investigator_profiles WHERE user_id=?", (str(investigator.id),)).fetchone()
        zone_name = row["timezone"] if row else "UTC"
        try:
            local = datetime.now(ZoneInfo(zone_name))
        except ZoneInfoNotFoundError:
            zone_name, local = "UTC", datetime.now(timezone.utc)
        unix_time = int(local.timestamp())
        investigator_clock = local.strftime("%Y-%m-%d %H:%M %Z")
        embed = discord.Embed(
            title="RCPD Internal Affairs — Investigator Assignment",
            description=(f"Good day, {officer.mention}. An Internal Affairs Investigator has been assigned to you.\n\n"
                         "Please keep the investigator's local working hours in mind when arranging communication."),
            color=discord.Color.from_str(core.load_config().get("embed_color", "#202225")),
        )
        embed.add_field(name="Assigned Investigator", value=investigator.mention, inline=True)
        if case_code:
            embed.add_field(name="Case Reference", value=f"`{discord.utils.escape_markdown(case_code.upper())}`", inline=True)
        embed.add_field(name="Investigator's Timezone", value=f"`{zone_name}`", inline=True)
        embed.add_field(name="Investigator's Current Local Time", value=f"`{investigator_clock}`", inline=True)
        embed.add_field(name="Your Local Equivalent", value=f"<t:{unix_time}:F> (<t:{unix_time}:R>)", inline=True)
        embed.set_footer(text=core.load_config().get("embed_footer", "RCPD Internal Affairs • Official Assignment"))
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))

    @app_commands.command(name="ia-investigator-sync", description="Initialize profiles for current IA role holders; dates are marked estimated.")
    @app_commands.guild_only()
    async def sync_current(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not is_profile_moderator(interaction.user):
            await interaction.response.send_message("Only configured bot moderators can initialize investigator profiles.", ephemeral=True)
            return
        tracked = role_ids("ia_role_ids")
        if not tracked:
            await interaction.response.send_message("Configure `investigator_settings.ia_role_ids` first.", ephemeral=True)
            return
        members = [m for m in interaction.guild.members if any(r.id in tracked for r in m.roles)]
        count = 0
        with core.db_connect() as conn:
            for member in members:
                now = core.utc_now()
                cur = conn.execute("INSERT OR IGNORE INTO investigator_profiles(user_id,joined_ia_at,joined_ia_accuracy,timezone,special_information,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                                   (str(member.id), now, "estimated", "UTC", "", now, now))
                count += cur.rowcount
                if cur.rowcount:
                    for role in member.roles:
                        if role.id in tracked:
                            conn.execute("INSERT INTO investigator_role_history(user_id,role_id,action,occurred_at,actor_id) VALUES(?,?,?,?,?)",
                                         (str(member.id), str(role.id), "added", now, str(interaction.user.id)))
        await interaction.response.send_message(f"Initialized {count} new profiles. Existing profiles and their original Joined IA dates were preserved. New dates are marked estimated because Discord does not expose historical role assignment dates.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InvestigatorCog(bot))
