import json
import logging
import os
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

ROOT = Path(__file__).parent
CONFIG_DIR = ROOT / "config"
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("rcpd-ia")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID", "").strip()


def load_json(filename: str) -> dict[str, Any]:
    path = CONFIG_DIR / filename
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_config() -> dict[str, Any]:
    return load_json("config.json")


def load_templates() -> dict[str, Any]:
    return load_json("templates.json")


def load_options(filename: str) -> dict[str, Any]:
    return load_json(filename)


def split_items(value: str) -> list[str]:
    """Split newline/comma/semicolon separated values, preserving order."""
    chunks = value.replace(";", "\n").replace(",", "\n").splitlines()
    result: list[str] = []
    for chunk in chunks:
        item = chunk.strip()
        if item and item not in result:
            result.append(item)
    return result


def format_list(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def safe_text(value: str, max_length: int = 1000) -> str:
    value = value.strip()
    if not value:
        raise ValueError("This field cannot be empty.")
    if len(value) > max_length:
        raise ValueError(f"Please keep this field under {max_length} characters.")
    return value


def display_officer(user: discord.User | discord.Member | None, manual_name: str | None) -> str:
    if user is not None:
        return user.mention
    if manual_name:
        # Avoid accidentally creating mentions when a plain name is entered.
        return discord.utils.escape_mentions(manual_name.strip())
    raise ValueError("Select a Discord user or enter an officer name.")


class ReleaseModal(discord.ui.Modal):
    """First step: rank, officer selection/manual name, and optional case note."""

    def __init__(self, release_type: str, config: dict[str, Any]):
        super().__init__(title="IA Release — Officer Details", timeout=300)
        self.release_type = release_type
        self.config = config

        self.rank = discord.ui.TextInput(
            label="Officer rank",
            placeholder="e.g. OFC., SGT., LT.",
            max_length=40,
            required=True,
        )
        self.manual_name = discord.ui.TextInput(
            label="Officer name (if not selecting a Discord user)",
            placeholder="Leave blank if you will select a Discord user next",
            max_length=100,
            required=False,
        )
        self.case_reference = discord.ui.TextInput(
            label="Case/reference (optional)",
            placeholder="Optional internal reference; not included unless configured",
            max_length=100,
            required=False,
        )
        self.add_item(self.rank)
        self.add_item(self.manual_name)
        self.add_item(self.case_reference)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            rank = safe_text(str(self.rank.value), 40)
            manual_name = str(self.manual_name.value).strip() or None
            case_reference = str(self.case_reference.value).strip() or None
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        view = OfficerSelectionView(
            release_type=self.release_type,
            config=self.config,
            rank=rank,
            manual_name=manual_name,
            case_reference=case_reference,
            author_id=interaction.user.id,
        )
        await interaction.response.send_message(
            "Choose the officer below, or use the manual name entered in the form.",
            view=view,
            ephemeral=True,
        )


class OfficerSelectionView(discord.ui.View):
    def __init__(
        self,
        release_type: str,
        config: dict[str, Any],
        rank: str,
        manual_name: str | None,
        case_reference: str | None,
        author_id: int,
    ):
        super().__init__(timeout=300)
        self.release_type = release_type
        self.config = config
        self.rank = rank
        self.manual_name = manual_name
        self.case_reference = case_reference
        self.author_id = author_id

        self.add_item(OfficerSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who started this release can use these controls.",
                ephemeral=True,
            )
            return False
        return True


class OfficerSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="Select an officer (optional if using a manual name)",
            min_values=0,
            max_values=1,
            custom_id="rcpd_ia:officer_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: OfficerSelectionView = self.view  # type: ignore[assignment]
        selected = self.values[0] if self.values else None
        if selected is None and not view.manual_name:
            await interaction.response.send_message(
                "Select a Discord user or start again and enter a manual officer name.",
                ephemeral=True,
            )
            return

        officer = display_officer(selected, view.manual_name)
        if view.release_type == "investigation":
            await publish_preview(
                interaction=interaction,
                config=view.config,
                release_type=view.release_type,
                values={
                    "rank": view.rank,
                    "officer": officer,
                    "case_reference": view.case_reference or "",
                },
                author_id=view.author_id,
            )
        else:
            await interaction.response.send_modal(
                DisciplinaryDetailsModal(
                    config=view.config,
                    rank=view.rank,
                    officer=officer,
                    case_reference=view.case_reference,
                    author_id=view.author_id,
                )
            )


class DisciplinaryDetailsModal(discord.ui.Modal):
    def __init__(
        self,
        config: dict[str, Any],
        rank: str,
        officer: str,
        case_reference: str | None,
        author_id: int,
    ):
        super().__init__(title="IA Release — Disciplinary Details", timeout=300)
        self.config = config
        self.rank = rank
        self.officer = officer
        self.case_reference = case_reference
        self.author_id = author_id

        self.violations = discord.ui.TextInput(
            label="Violations (one per line)",
            placeholder="Abuse of Authority\nFailure to Follow Procedures",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True,
        )
        self.punishments = discord.ui.TextInput(
            label="Punishments (one per line)",
            placeholder="Official Warning\nSuspension",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True,
        )
        self.add_item(self.violations)
        self.add_item(self.punishments)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            violations = split_items(safe_text(str(self.violations.value), 1000))
            punishments = split_items(safe_text(str(self.punishments.value), 1000))
            if not violations or not punishments:
                raise ValueError("Enter at least one violation and one punishment.")
            validate_configured_items(violations, load_options("violations.json"), "violation")
            validate_configured_items(punishments, load_options("punishments.json"), "punishment")
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            await interaction.response.send_message(
                f"Could not create release: {exc}", ephemeral=True
            )
            return

        await publish_preview(
            interaction=interaction,
            config=self.config,
            release_type="disciplinary",
            values={
                "rank": self.rank,
                "officer": self.officer,
                "reasons": format_list(violations),
                "punishments": format_list(punishments),
                "case_reference": self.case_reference or "",
            },
            author_id=self.author_id,
        )


def validate_configured_items(items: list[str], options: dict[str, Any], label: str) -> None:
    """Accept either configured display names or keys; keep arbitrary custom values configurable."""
    allowed = {
        str(key).casefold()
        for key in options.keys()
    } | {
        str(value.get("name", "")).casefold()
        for value in options.values()
        if isinstance(value, dict)
    }
    allow_custom = bool(load_config().get("allow_custom_reasons_and_punishments", True))
    if allow_custom:
        return
    invalid = [item for item in items if item.casefold() not in allowed]
    if invalid:
        raise ValueError(
            f"Unrecognized {label}(s): {', '.join(invalid)}. "
            "Use configured names or enable custom entries in config.json."
        )


def render_template(release_type: str, values: dict[str, str]) -> str:
    templates = load_templates()
    template_data = templates.get(release_type)
    if not template_data or "template" not in template_data:
        raise ValueError(f"Missing template for release type '{release_type}'.")
    template = template_data["template"]
    try:
        return template.format_map(values)
    except KeyError as exc:
        raise ValueError(f"Template contains an unknown placeholder: {exc}") from exc


def authorized(interaction: discord.Interaction, config: dict[str, Any]) -> bool:
    if interaction.guild is None:
        return False
    role_ids = {int(value) for value in config.get("authorized_role_ids", [])}
    if not role_ids:
        # Fail closed: no roles configured means no one can publish.
        return False
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    return any(role.id in role_ids for role in member.roles)


def release_embed(text: str, config: dict[str, Any], interaction: discord.Interaction) -> discord.Embed:
    color_value = str(config.get("embed_color", "#202225")).lstrip("#")
    try:
        color = discord.Color(int(color_value, 16))
    except ValueError:
        color = discord.Color.dark_grey()

    embed = discord.Embed(description=text, color=color)
    footer = str(config.get("embed_footer", "")).strip()
    if footer:
        embed.set_footer(text=footer)
    return embed


class PublishView(discord.ui.View):
    def __init__(
        self,
        text: str,
        config: dict[str, Any],
        author_id: int,
    ):
        super().__init__(timeout=300)
        self.text = text
        self.config = config
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who created this preview can publish it.", ephemeral=True
            )
            return False
        if not authorized(interaction, self.config):
            await interaction.response.send_message(
                "You do not have an authorized IA role to publish releases.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Publish Release", style=discord.ButtonStyle.success, emoji="📢")
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel_id = self.config.get("release_channel_id")
        channel = interaction.guild.get_channel(int(channel_id)) if interaction.guild and channel_id else None
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message(
                "The release channel is not configured correctly in config/config.json.",
                ephemeral=True,
            )
            return

        try:
            embed = release_embed(self.text, self.config, interaction)
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            log.exception("Failed to publish IA release")
            await interaction.response.send_message(
                "Discord could not publish the release. Check the bot's channel permissions.",
                ephemeral=True,
            )
            return

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"Release published in {channel.mention}.",
            view=self,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Release cancelled.", view=self)


async def publish_preview(
    interaction: discord.Interaction,
    config: dict[str, Any],
    release_type: str,
    values: dict[str, str],
    author_id: int,
) -> None:
    if not authorized(interaction, config):
        await interaction.response.send_message(
            "You do not have an authorized IA role to create releases. "
            "Ask an administrator to configure authorized_role_ids in config/config.json.",
            ephemeral=True,
        )
        return

    try:
        text = render_template(release_type, values)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        await interaction.response.send_message(f"Template error: {exc}", ephemeral=True)
        return

    max_length = int(config.get("max_release_length", 4000))
    if len(text) > max_length:
        await interaction.response.send_message(
            f"The rendered release is too long ({len(text)} characters; limit {max_length}).",
            ephemeral=True,
        )
        return

    embed = release_embed(text, config, interaction)
    view = PublishView(text, config, author_id)
    await interaction.response.send_message(
        content="**Preview — check the release before publishing:**",
        embed=embed,
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


class ReleaseTypeView(discord.ui.View):
    def __init__(self, config: dict[str, Any], author_id: int):
        super().__init__(timeout=180)
        self.config = config
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who started this command can select a release type.",
                ephemeral=True,
            )
            return False
        if not authorized(interaction, self.config):
            await interaction.response.send_message(
                "You do not have permission to create IA releases.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Disciplinary Release", style=discord.ButtonStyle.primary)
    async def disciplinary(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ReleaseModal("disciplinary", self.config))

    @discord.ui.button(label="Investigation Release", style=discord.ButtonStyle.primary)
    async def investigation(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ReleaseModal("investigation", self.config))


class IABot(commands.Bot):
    async def setup_hook(self) -> None:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %s command(s) to test guild %s", len(synced), GUILD_ID)
        else:
            synced = await self.tree.sync()
            log.info("Synced %s global command(s)", len(synced))


intents = discord.Intents.default()
bot = IABot(command_prefix="!", intents=intents)


@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id if bot.user else "unknown")


@bot.tree.command(name="ia-release", description="Create an official RCPD Internal Affairs release.")
@app_commands.guild_only()
async def ia_release(interaction: discord.Interaction) -> None:
    try:
        config = load_config()
    except (OSError, json.JSONDecodeError) as exc:
        await interaction.response.send_message(
            f"Could not load bot configuration: {exc}", ephemeral=True
        )
        return

    if not authorized(interaction, config):
        await interaction.response.send_message(
            "You do not have permission to create IA releases.", ephemeral=True
        )
        return

    await interaction.response.send_message(
        "Select the type of Internal Affairs release to create:",
        view=ReleaseTypeView(config, interaction.user.id),
        ephemeral=True,
    )


@bot.tree.command(name="ia-reload-config", description="Reload IA templates and configuration.")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def ia_reload_config(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Only server administrators can reload the configuration.", ephemeral=True
        )
        return
    try:
        load_config()
        load_templates()
        load_options("violations.json")
        load_options("punishments.json")
    except (OSError, json.JSONDecodeError) as exc:
        await interaction.response.send_message(f"Configuration error: {exc}", ephemeral=True)
        return
    await interaction.response.send_message("Configuration files loaded successfully.", ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing. Copy .env.example to .env and add your bot token.")
    bot.run(TOKEN)
