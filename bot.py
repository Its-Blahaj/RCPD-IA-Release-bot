"""RCPD Internal Affairs bot entry point. Feature commands live in cogs/."""
from core import TOKEN, bot

if __name__ == "__main__":
    if not TOKEN or TOKEN == "put_your_bot_token_here":
        raise SystemExit("DISCORD_TOKEN is missing. Copy .env.example to .env and add your bot token.")
    bot.run(TOKEN)
