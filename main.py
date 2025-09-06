import os
import asyncio
from pathlib import Path
from datetime import datetime, timezone
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)

# === Load environment ===
ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_TOKEN = os.getenv("OPENAI_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")  # optional (dev server for quick sync)
GUILD = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None

if not DISCORD_TOKEN:
    raise RuntimeError("❌ DISCORD_TOKEN missing in .env")


def is_allowed_guild(guild_id: int | None) -> bool:
    """Return True if the provided guild_id matches the configured ``GUILD_ID``.

    If no ``GUILD_ID`` is set, the bot is allowed to work on any guild. Passing
    ``None`` (e.g. for direct messages) will always return ``False`` when a
    ``GUILD_ID`` is configured.
    """
    if GUILD_ID is None:
        return True
    if guild_id is None:
        return False
    return str(guild_id) == GUILD_ID


# === Bot class ===
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)  # <- prefix here

        # Metadata
        self.start_time: datetime | None = None
        self.last_global_sync: datetime | None = None
        self.last_guild_syncs: dict[int, datetime] = {}

    async def setup_hook(self):
        # 1) Load cogs
        cogs_dir = ROOT / "cogs"
        if cogs_dir.exists():
            for f in cogs_dir.iterdir():
                if f.suffix == ".py" and not f.name.startswith("_"):
                    await self.load_extension(f"cogs.{f.stem}")
                    print(f"🔌 Cog loaded: cogs.{f.stem}")

        # 2) Sync commands
        if GUILD:
            # Copy global to guild for immediate visibility
            self.tree.copy_global_to(guild=GUILD)
            synced = await self.tree.sync(guild=GUILD)
            self.last_guild_syncs[int(GUILD.id)] = datetime.now(timezone.utc)
            print(f"✅ Slash commands (guild) synced: {[c.name for c in synced]}")
        else:
            synced = await self.tree.sync()
            self.last_global_sync = datetime.now(timezone.utc)
            print(f"🌍 Slash commands (global) synced: {[c.name for c in synced]}")

        # 3) additionally sync globally in the background (for all servers)
        async def sync_global_later():
            await asyncio.sleep(2)
            try:
                synced = await self.tree.sync()
                self.last_global_sync = datetime.now(timezone.utc)
                print(f"🌍 Slash commands (global) synced: {[c.name for c in synced]}")
            except Exception as e:
                print(f"⚠️ Global sync failed: {e}")

        self.loop.create_task(sync_global_later())


bot = MyBot()


@bot.check
def guild_only(ctx: commands.Context) -> bool:
    return is_allowed_guild(ctx.guild.id if ctx.guild else None)


# === Events ===
@bot.event
async def on_ready():
    bot.start_time = datetime.now(timezone.utc)
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_guild_join(guild: discord.Guild):
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=guild.id))
        bot.last_guild_syncs[guild.id] = datetime.now(timezone.utc)
        print(f"🆕 Slash commands synced on new server ({guild.name}): {[c.name for c in synced]}")
    except Exception as e:
        print(f"⚠️ Could not sync on new server ({guild.name}): {e}")


# === Example slash command ===
@bot.tree.command(name="hello", description="Says hello back.")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hello, {interaction.user.mention}! 👋")


# === Cog management (owner only) ===
@bot.command(name="reload")
@commands.is_owner()
async def reload_ext(ctx: commands.Context, ext: str):
    try:
        await bot.unload_extension(ext)
        await bot.load_extension(ext)
        await ctx.reply(f"🔁 `{ext}` reloaded")
    except Exception as e:
        await ctx.reply(f"❌ {type(e).__name__}: {e}")


@bot.command(name="load")
@commands.is_owner()
async def load_ext(ctx: commands.Context, ext: str):
    try:
        await bot.load_extension(ext)
        await ctx.reply(f"✅ `{ext}` loaded")
    except Exception as e:
        await ctx.reply(f"❌ {type(e).__name__}: {e}")


@bot.command(name="unload")
@commands.is_owner()
async def unload_ext(ctx: commands.Context, ext: str):
    try:
        await bot.unload_extension(ext)
        await ctx.reply(f"🛑 `{ext}` unloaded")
    except Exception as e:
        await ctx.reply(f"❌ {type(e).__name__}: {e}")


# === Start ===
async def amain():
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(amain())
