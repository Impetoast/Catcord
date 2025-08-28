# cogs/autotranslate.py
import os
import asyncio
import time
from typing import Optional, Dict, Tuple, List

import discord
from discord import app_commands
from discord.ext import commands
import httpx

DEEPL_TOKEN = os.getenv("DEEPL_TOKEN")
DEEPL_API_URL = os.getenv("DEEPL_API_URL", "https://api-free.deepl.com/v2")
TRANSLATE_URL = f"{DEEPL_API_URL}/translate"

def _norm(code: Optional[str]) -> Optional[str]:
    return code.strip().upper().replace("_", "-") if code else None

# ✅ Liste gängiger, von DeepL akzeptierter Zielcodes (erweiterbar)
SUPPORTED_TARGETS: List[str] = [
    "BG","CS","DA","DE","EL","EN","EN-GB","EN-US","ES","ET","FI","FR",
    "HU","ID","IT","JA","KO","LT","LV","NB","NL","PL","PT","PT-PT","PT-BR",
    "RO","RU","SK","SL","SV","TR","UK","ZH"
]

class AutoTranslate(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not DEEPL_TOKEN:
            print("⚠️  DEEPL_TOKEN fehlt – Auto-Translate wird beim Aufruf Fehler melden.")
        # channel_id -> (target_lang, source_lang|None, formality|None, min_chars)
        self.enabled: Dict[int, Tuple[str, Optional[str], Optional[str], int]] = {}
        self.last_action_ts: Dict[int, float] = {}
        self.cooldown_seconds = 0.5
        self._sem_per_channel: Dict[int, asyncio.Semaphore] = {}

    async def _deepl_translate(self, text: str, target: str, source: Optional[str], formality: Optional[str]) -> Tuple[str, Optional[str]]:
        if not DEEPL_TOKEN:
            raise RuntimeError("DEEPL_TOKEN fehlt (in .env setzen).")
        data = {
            "auth_key": DEEPL_TOKEN,
            "text": text,
            "target_lang": _norm(target),
            "preserve_formatting": "1",
        }
        if source:
            data["source_lang"] = _norm(source)
        if formality and formality.lower() in {"default","less","more"}:
            data["formality"] = formality.lower()

        timeout = httpx.Timeout(15.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(TRANSLATE_URL, data=data)
            if resp.status_code == 429:
                raise RuntimeError("DeepL: Rate limit erreicht.")
            if resp.status_code >= 400:
                try:
                    detail = resp.json()
                except Exception:
                    detail = resp.text
                raise RuntimeError(f"DeepL-Fehler ({resp.status_code}): {detail}")

            payload = resp.json()
            tr = (payload.get("translations") or [])
            if not tr:
                raise RuntimeError("DeepL: Keine Übersetzung erhalten.")
            out = tr[0]
            return out.get("text","").strip(), _norm(out.get("detected_source_language"))

    def _get_sem(self, channel_id: int) -> asyncio.Semaphore:
        sem = self._sem_per_channel.get(channel_id)
        if not sem:
            sem = asyncio.Semaphore(2)
            self._sem_per_channel[channel_id] = sem
        return sem

    # ------------ Listener ------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not message.content or message.content.startswith(("/", "!", ".")):
            return

        cfg = self.enabled.get(message.channel.id)
        if not cfg:
            return

        target, source, formality, min_chars = cfg
        txt = message.content.strip()
        if len(txt) < min_chars:
            return

        now = time.time()
        if now - self.last_action_ts.get(message.channel.id, 0) < self.cooldown_seconds:
            return

        sem = self._get_sem(message.channel.id)
        async with sem:
            try:
                translated, detected = await self._deepl_translate(txt, target=target, source=source, formality=formality)
            except Exception as e:
                print(f"⚠️ Auto-Translate Fehler in #{message.channel} ({message.guild}): {e}")
                return

        if _norm(detected) and _norm(detected) == _norm(target):
            return

        self.last_action_ts[message.channel.id] = now
        try:
            await message.reply(
                content=f"🌐 **{detected or 'AUTO'} → {target}**\n{translated}",
                mention_author=False,
                suppress_embeds=True,
            )
        except discord.Forbidden:
            try:
                await message.channel.send(f"🌐 **{detected or 'AUTO'} → {target}**\n{translated}")
            except Exception as e:
                print(f"⚠️ Konnte Übersetzung nicht senden: {e}")

    # ------------ Commands ------------
    # ✅ Autocomplete für Ziel-/Quellsprache
    def _lang_choices(self, current: str):
        q = (current or "").strip().lower()
        pool = SUPPORTED_TARGETS
        items = [c for c in pool if q in c.lower()] if q else pool
        return [app_commands.Choice(name=c, value=c) for c in items[:20]]

    @app_commands.command(name="autotranslate_on", description="Aktiviere automatische Übersetzung in diesem Kanal.")
    @app_commands.describe(
        target="Zielsprache (z. B. EN, EN-US, DE, FR, PT-BR, ZH …)",
        source="Quellsprache (optional; leer = Auto-Detect)",
        formality="Stil (optional): default / less / more",
        min_chars="Mindestlänge des Textes (Standard 5)"
    )
    async def autotranslate_on(self, interaction, target: str, source: Optional[str] = None, formality: Optional[str] = None, min_chars: Optional[int] = 5):
        t = _norm(target)
        s = _norm(source)
        if t not in SUPPORTED_TARGETS:
            return await interaction.response.send_message(
                f"❌ Zielcode `{t}` ist nicht gültig.\n"
                f"Beispiele: `EN`, `EN-GB`, `EN-US`, `DE`, `FR`, `ES`, `PT-PT`, `PT-BR`, `ZH`.\n"
                f"Tipp: Tippe den Code und nutze die Autovervollständigung.",
                ephemeral=True
            )
        if s and s not in SUPPORTED_TARGETS:
            return await interaction.response.send_message(
                f"❌ Quellcode `{s}` ist nicht gültig. Lass das Feld leer für Auto-Detect oder nutze einen gültigen Code.",
                ephemeral=True
            )

        if not interaction.channel or not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            return await interaction.response.send_message("❌ Nur in Textkanälen möglich.", ephemeral=True)

        self.enabled[interaction.channel.id] = (t, s, (formality or None), int(min_chars or 5))
        await interaction.response.send_message(
            f"✅ Auto-Translate **aktiv** in <#{interaction.channel.id}> – Ziel: `{t}`"
            + (f", Quelle: `{s}`" if s else ", Quelle: Auto-Detect")
            + (f", Stil: `{formality}`" if formality else "")
            + f", min. Zeichen: {int(min_chars or 5)}",
            ephemeral=True
        )

    @autotranslate_on.autocomplete("target")
    async def ac_target(self, interaction, current: str):
        return self._lang_choices(current)

    @autotranslate_on.autocomplete("source")
    async def ac_source(self, interaction, current: str):
        return self._lang_choices(current)

    @app_commands.command(name="autotranslate_off", description="Deaktiviere automatische Übersetzung in diesem Kanal.")
    async def autotranslate_off(self, interaction):
        if interaction.channel and interaction.channel.id in self.enabled:
            self.enabled.pop(interaction.channel.id, None)
            await interaction.response.send_message(f"🛑 Auto-Translate **deaktiviert** in <#{interaction.channel.id}>.", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ Auto-Translate war hier nicht aktiv.", ephemeral=True)

    @app_commands.command(name="autotranslate_status", description="Zeigt den Auto-Translate-Status für diesen Kanal.")
    async def autotranslate_status(self, interaction):
        if not interaction.channel:
            return await interaction.response.send_message("❌ Nur in Textkanälen möglich.", ephemeral=True)
        cfg = self.enabled.get(interaction.channel.id)
        if not cfg:
            return await interaction.response.send_message("ℹ️ Auto-Translate ist hier **inaktiv**.", ephemeral=True)
        target, source, formality, min_chars = cfg
        await interaction.response.send_message(
            f"🔧 Aktiv in <#{interaction.channel.id}>\n"
            f"- Ziel: `{target}`\n"
            f"- Quelle: `{source or 'Auto-Detect'}`\n"
            f"- Stil: `{formality or '—'}`\n"
            f"- Mindestlänge: `{min_chars}` Zeichen",
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(AutoTranslate(bot))
