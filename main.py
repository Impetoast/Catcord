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
GUILD_ID = os.getenv("GUILD_ID")  # optional (Dev-Server für schnellen Sync)
GUILD = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None

if not DISCORD_TOKEN:
    raise RuntimeError("❌ DISCORD_TOKEN fehlt in .env")

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # für Prefix-/Hybrid-Commands
        super().__init__(command_prefix="!", intents=intents)

        # Meta/Telemetry
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

        # 2) DEV: sofortiger Guild-Sync (falls GUILD_ID gesetzt)
        if GUILD:
            synced = await self.tree.sync(guild=GUILD)
            self.last_guild_syncs[int(GUILD.id)] = datetime.now(timezone.utc)
            print(f"✅ Slash-Commands (Guild) synchronisiert: {[c.name for c in synced]}")

        # 3) GLOBAL: im Hintergrund synchronisieren (damit’s überall verfügbar ist)
        async def sync_global_later():
            await asyncio.sleep(2)
            try:
                synced = await self.tree.sync()
                self.last_global_sync = datetime.now(timezone.utc)
                print(f"🌍 Slash-Commands (global) synchronisiert: {[c.name for c in synced]}")
                print("ℹ️ Global kann es einige Zeit dauern, bis die Commands überall sichtbar sind.")
            except Exception as e:
                print(f"⚠️ Global-Sync fehlgeschlagen: {e}")

        self.loop.create_task(sync_global_later())

bot = MyBot()

# --- Events ---
@bot.event
async def on_ready():
    bot.start_time = datetime.now(timezone.utc)
    print(f"✅ Eingeloggt als {bot.user} (ID: {bot.user.id})")

@bot.event
async def on_guild_join(guild: discord.Guild):
    # Bei Beitritt: sofort für diesen Server synchronisieren
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=guild.id))
        bot.last_guild_syncs[guild.id] = datetime.now(timezone.utc)
        print(f"🆕 Slash-Commands auf neuem Server synchronisiert ({guild.name}): {[c.name for c in synced]}")
    except Exception as e:
        print(f"⚠️ Konnte auf neuem Server nicht syncen ({guild.name}): {e}")

# --- Beispiel: einfacher Slash-Command ---
@bot.tree.command(name="hello", description="Sagt Hallo zurück.")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hallo, {interaction.user.mention}! 👋")

# --- Owner-only Prefix-Reload für Cogs ---
@bot.command(name="reload")
@commands.is_owner()
async def reload_ext(ctx: commands.Context, ext: str):
    try:
        await bot.unload_extension(ext)
        await bot.load_extension(ext)
        await ctx.reply(f"🔁 `{ext}` neu geladen")
    except Exception as e:
        await ctx.reply(f"❌ {type(e).__name__}: {e}")

@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command: discord.app_commands.Command):
    print(f"✅ /{command.name} ausgeführt von {interaction.user} in #{interaction.channel}")

@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    print(f"❌ Slash-Error bei /{getattr(interaction.command, 'name', '?')}: {type(error).__name__}: {error}")
    try:
        if interaction.response.is_done():
            await interaction.followup.send(f"❌ Fehler: {error}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Fehler: {error}", ephemeral=True)
    except Exception as e:
        print(f"⚠️ Konnte Fehlermeldung nicht senden: {e}")


# --- Start ---
async def amain():
    async with bot:
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(amain())
