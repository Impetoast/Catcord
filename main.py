import os
import asyncio
from pathlib import Path
from datetime import datetime, timezone
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)

# === ENV laden ===
ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_TOKEN = os.getenv("OPENAI_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")  # optional (Dev-Server für schnellen Sync)
GUILD = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None

if not DISCORD_TOKEN:
    raise RuntimeError("❌ DISCORD_TOKEN fehlt in .env")


# === Bot-Klasse ===
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members         = True
        super().__init__(command_prefix="!", intents=intents)  # <- Prefix hier
        bot = commands.Bot(command_prefix="!", intents=intents)

        # Meta
        self.start_time: datetime | None = None
        self.last_global_sync: datetime | None = None
        self.last_guild_syncs: dict[int, datetime] = {}

    async def setup_hook(self):
        # 1) Cogs laden
        cogs_dir = ROOT / "cogs"
        if cogs_dir.exists():
            for f in cogs_dir.iterdir():
                if f.suffix == ".py" and not f.name.startswith("_"):
                    await self.load_extension(f"cogs.{f.stem}")
                    print(f"🔌 Cog geladen: cogs.{f.stem}")

        # 2) Commands synchronisieren
        if GUILD:
            # Globale → Guild kopieren für sofortige Sichtbarkeit
            self.tree.copy_global_to(guild=GUILD)
            synced = await self.tree.sync(guild=GUILD)
            self.last_guild_syncs[int(GUILD.id)] = datetime.now(timezone.utc)
            print(f"✅ Slash-Commands (Guild) synchronisiert: {[c.name for c in synced]}")
        else:
            synced = await self.tree.sync()
            self.last_global_sync = datetime.now(timezone.utc)
            print(f"🌍 Slash-Commands (global) synchronisiert: {[c.name for c in synced]}")

        # 3) zusätzlich global im Hintergrund syncen (für alle Server)
        async def sync_global_later():
            await asyncio.sleep(2)
            try:
                synced = await self.tree.sync()
                self.last_global_sync = datetime.now(timezone.utc)
                print(f"🌍 Slash-Commands (global) synchronisiert: {[c.name for c in synced]}")
            except Exception as e:
                print(f"⚠️ Global-Sync fehlgeschlagen: {e}")

        self.loop.create_task(sync_global_later())


bot = MyBot()


# === Events ===
@bot.event
async def on_ready():
    bot.start_time = datetime.now(timezone.utc)
    print(f"✅ Eingeloggt als {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_guild_join(guild: discord.Guild):
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=guild.id))
        bot.last_guild_syncs[guild.id] = datetime.now(timezone.utc)
        print(f"🆕 Slash-Commands auf neuem Server synchronisiert ({guild.name}): {[c.name for c in synced]}")
    except Exception as e:
        print(f"⚠️ Konnte auf neuem Server nicht syncen ({guild.name}): {e}")


# === Beispiel-Slashcommand ===
@bot.tree.command(name="hello", description="Sagt Hallo zurück.")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hallo, {interaction.user.mention}! 👋")


# === Cog-Management (nur Owner) ===
@bot.command(name="reload")
@commands.is_owner()
async def reload_ext(ctx: commands.Context, ext: str):
    try:
        await bot.unload_extension(ext)
        await bot.load_extension(ext)
        await ctx.reply(f"🔁 `{ext}` neu geladen")
    except Exception as e:
        await ctx.reply(f"❌ {type(e).__name__}: {e}")


@bot.command(name="load")
@commands.is_owner()
async def load_ext(ctx: commands.Context, ext: str):
    try:
        await bot.load_extension(ext)
        await ctx.reply(f"✅ `{ext}` geladen")
    except Exception as e:
        await ctx.reply(f"❌ {type(e).__name__}: {e}")


@bot.command(name="unload")
@commands.is_owner()
async def unload_ext(ctx: commands.Context, ext: str):
    try:
        await bot.unload_extension(ext)
        await ctx.reply(f"🛑 `{ext}` entladen")
    except Exception as e:
        await ctx.reply(f"❌ {type(e).__name__}: {e}")


# === Start ===
async def amain():
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(amain())
