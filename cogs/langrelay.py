# cogs/langrelay.py
import os
import re
import asyncio
from typing import Dict, Optional, Tuple, List

import discord
from discord import app_commands
from discord.ext import commands
import httpx

# === ENV / DeepL ===
DEEPL_TOKEN = os.getenv("DEEPL_TOKEN")
DEEPL_API_URL = os.getenv("DEEPL_API_URL", "https://api-free.deepl.com/v2")
TRANSLATE_URL = f"{DEEPL_API_URL}/translate"

# === Unterstützte Zielcodes (DeepL) ===
SUPPORTED_TARGETS: List[str] = [
    "BG","CS","DA","DE","EL","EN","EN-GB","EN-US","ES","ET","FI","FR",
    "HU","ID","IT","JA","KO","LT","LV","NB","NL","PL","PT","PT-PT","PT-BR",
    "RO","RU","SK","SL","SV","TR","UK","ZH"
]

# === Default-Mapping (kann via Slash-Commands zur Laufzeit geändert werden) ===
# Schlüssel = Channel-Name, Wert = Sprachcode
CHANNEL_LANG_MAP: Dict[str, str] = {
    "channel_de": "DE",
    "channel_en": "EN",
    # "channel_fr": "FR",
    # "channel_es": "ES",
}

def _norm(code: Optional[str]) -> Optional[str]:
    return code.strip().upper().replace("_", "-") if code else None


class LangRelay(commands.Cog):
    """
    Spiegelt Nachrichten aus definierten Sprachkanälen in die anderen Sprachkanäle – übersetzt via DeepL.
    - Nachrichten in z. B. #channel_de werden nach #channel_en/#channel_fr usw. gepusht (nicht in den Ursprung zurück).
    - Mapping kann per Slash-Commands verwaltet werden.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not DEEPL_TOKEN:
            print("⚠️  DEEPL_TOKEN fehlt – LangRelay kann nicht übersetzen.")
        # Laufzeit-Mapping (startet mit Defaults)
        self.mapping: Dict[str, str] = dict(CHANNEL_LANG_MAP)
        # Cache: guild_id -> {channel_name -> channel_obj}
        self._guild_channel_cache: Dict[int, Dict[str, discord.TextChannel]] = {}
        # Concurrency
        self._sem_per_guild: Dict[int, asyncio.Semaphore] = {}
        # kosmetisch
        self.preview_max = 200

    # ------------- Utilities -------------
    def _sem(self, guild_id: int) -> asyncio.Semaphore:
        if guild_id not in self._sem_per_guild:
            self._sem_per_guild[guild_id] = asyncio.Semaphore(2)
        return self._sem_per_guild[guild_id]

    async def _deepl_translate(self, text: str, target_lang: str, source_lang: Optional[str] = None) -> str:
        if not DEEPL_TOKEN:
            raise RuntimeError("DEEPL_TOKEN fehlt (in .env setzen).")
        data = {
            "auth_key": DEEPL_TOKEN,
            "text": text,
            "target_lang": _norm(target_lang),
            "preserve_formatting": "1",
        }
        if source_lang:
            data["source_lang"] = _norm(source_lang)

        timeout = httpx.Timeout(20.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(TRANSLATE_URL, data=data)
            if resp.status_code == 429:
                raise RuntimeError("DeepL: Rate limit erreicht.")
            resp.raise_for_status()
            payload = resp.json()

        tr = payload.get("translations") or []
        if not tr:
            raise RuntimeError("DeepL: keine Übersetzung erhalten.")
        return tr[0].get("text", "").strip()

    def _safe_mentions(self, text: str) -> str:
        # Mentions entschärfen
        text = re.sub(r"@", "@\u200B", text)
        text = re.sub(r"&", "&\u200B", text)
        return text

    async def _ensure_cache(self, guild: discord.Guild):
        """Baut/aktualisiert den Channel-Cache für eine Guild."""
        by_name: Dict[str, discord.TextChannel] = {}
        for ch in guild.text_channels:
            by_name[ch.name] = ch
        self._guild_channel_cache[guild.id] = by_name

    def _get_channel_by_name(self, guild_id: int, name: str) -> Optional[discord.TextChannel]:
        cache = self._guild_channel_cache.get(guild_id) or {}
        return cache.get(name)

    # ------------- Listener -------------
    @commands.Cog.listener()
    async def on_ready(self):
        # initiale Caches
        for guild in self.bot.guilds:
            await self._ensure_cache(guild)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        if isinstance(channel, discord.TextChannel):
            await self._ensure_cache(channel.guild)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        if isinstance(after, discord.TextChannel):
            await self._ensure_cache(after.guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await self._ensure_cache(guild)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignorier Bots/DMs/System
        if message.author.bot or not message.guild:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        if not message.content:
            # Anhänge ignorieren – (kann man später ergänzen)
            return

        guild = message.guild
        src_channel = message.channel
        src_lang = self.mapping.get(src_channel.name)
        if not src_lang:
            return  # kein definierter Sprachkanal

        async with self._sem(guild.id):
            # Ziel: alle anderen Sprachkanäle im Mapping dieser Guild
            # Cache sicherstellen
            await self._ensure_cache(guild)

            tasks = []
            for tgt_name, tgt_lang in self.mapping.items():
                if tgt_name == src_channel.name:
                    continue
                tgt_channel = self._get_channel_by_name(guild.id, tgt_name)
                if not tgt_channel:
                    continue

                async def _one_target(_tgt_channel=tgt_channel, _tgt_lang=tgt_lang):
                    try:
                        translated = await self._deepl_translate(
                            text=message.content, target_lang=_tgt_lang, source_lang=src_lang
                        )
                    except Exception as e:
                        print(f"⚠️ Übersetzung fehlgeschlagen ({src_channel.name} → {_tgt_channel.name}): {e}")
                        return

                    preview = message.content[: self.preview_max] + ("…" if len(message.content) > self.preview_max else "")
                    safe_translated = self._safe_mentions(translated)

                    try:
                        await _tgt_channel.send(
                            f"🌐 **{message.author.display_name}** schrieb in {src_channel.mention}:\n"
                            f"> {preview}\n\n"
                            f"**Übersetzung → {_tgt_lang}:**\n{safe_translated}\n\n"
                            f"[Zum Original]({message.jump_url})",
                            allowed_mentions=discord.AllowedMentions.none()
                        )
                    except Exception as e:
                        print(f"⚠️ Nachricht in #{_tgt_channel.name} konnte nicht gesendet werden: {e}")

                tasks.append(_one_target())

            if tasks:
                await asyncio.gather(*tasks)

    # ------------- Admin: Commands -------------
    def _lang_choices(self, current: str):
        q = (current or "").strip().lower()
        pool = SUPPORTED_TARGETS
        items = [c for c in pool if q in c.lower()] if q else pool
        return [app_commands.Choice(name=c, value=c) for c in items[:20]]

    @app_commands.command(name="langrelay_status", description="Zeigt die aktuelle Channel→Sprache-Zuordnung.")
    async def cmd_status(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)

        await self._ensure_cache(interaction.guild)

        lines = []
        missing = []
        for ch_name, code in self.mapping.items():
            ch_obj = self._get_channel_by_name(interaction.guild.id, ch_name)
            if ch_obj:
                lines.append(f"• <#{ch_obj.id}>  →  `{code}`")
            else:
                lines.append(f"• `#{ch_name}`  →  `{code}`  (❌ nicht gefunden)")
                missing.append(ch_name)

        if not lines:
            desc = "_Kein Mapping definiert._"
        else:
            desc = "\n".join(lines)

        embed = discord.Embed(
            title="LangRelay – Status",
            description=desc
        )
        if missing:
            embed.set_footer(text="Hinweis: Manche Channels fehlen oder wurden umbenannt.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="langrelay_reload", description="Lädt die Channel-Liste neu (z. B. nach Umbenennen/Anlegen).")
    async def cmd_reload(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)

        await self._ensure_cache(interaction.guild)
        await interaction.response.send_message("🔁 Channel-Cache aktualisiert.", ephemeral=True)

    @app_commands.command(name="langrelay_set", description="Setzt/ändert die Sprache eines Channels.")
    @app_commands.describe(channel="Ziel-Textkanal", language="DeepL Sprachcode (z. B. DE, EN, EN-US, FR …)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_set(self, interaction: discord.Interaction, channel: discord.TextChannel, language: str):
        lang = _norm(language or "")
        if lang not in SUPPORTED_TARGETS:
            return await interaction.response.send_message(
                f"❌ Ungültiger Sprachcode `{lang}`. Beispiele: DE, EN, EN-GB, EN-US, FR, ES, PT-BR, ZH …",
                ephemeral=True
            )

        self.mapping[channel.name] = lang
        await self._ensure_cache(interaction.guild)
        await interaction.response.send_message(
            f"✅ Mapping gesetzt: {channel.mention} → `{lang}`",
            ephemeral=True
        )

    @cmd_set.autocomplete("language")
    async def ac_language(self, interaction: discord.Interaction, current: str):
        return self._lang_choices(current)

    @app_commands.command(name="langrelay_remove", description="Entfernt die Zuordnung eines Channels.")
    @app_commands.describe(channel="Textkanal, dessen Mapping entfernt werden soll")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_remove(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if channel.name in self.mapping:
            self.mapping.pop(channel.name, None)
            await interaction.response.send_message(f"🗑️ Entfernt: {channel.mention}", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ Für {channel.mention} war kein Mapping vorhanden.", ephemeral=True)

    @app_commands.command(name="langrelay_clear", description="Löscht alle Channel→Sprache-Zuordnungen.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_clear(self, interaction: discord.Interaction):
        self.mapping.clear()
        await interaction.response.send_message("🧹 Alle Mappings gelöscht.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LangRelay(bot))
