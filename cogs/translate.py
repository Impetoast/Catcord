# cogs/translate.py
import os
from typing import Optional, Dict, List, Tuple
from textwrap import wrap

import discord
from discord import app_commands
from discord.ext import commands
import httpx

# --- Config aus Environment ---
DEEPL_TOKEN = os.getenv("DEEPL_TOKEN")
DEEPL_API_URL = os.getenv("DEEPL_API_URL", "https://api-free.deepl.com/v2")
TRANSLATE_URL = f"{DEEPL_API_URL}/translate"
LANG_URL = f"{DEEPL_API_URL}/languages"

# --- Fallback-Sprachen (falls API-Aufruf fehlschlägt) ---
FALLBACK_LANGS: List[Tuple[str, str]] = [
    ("BG", "Bulgarisch"), ("CS", "Tschechisch"), ("DA", "Dänisch"),
    ("DE", "Deutsch"), ("EL", "Griechisch"), ("EN", "Englisch"),
    ("EN-GB", "Englisch (GB)"), ("EN-US", "Englisch (US)"),
    ("ES", "Spanisch"), ("ET", "Estnisch"), ("FI", "Finnisch"),
    ("FR", "Französisch"), ("HU", "Ungarisch"), ("ID", "Indonesisch"),
    ("IT", "Italienisch"), ("JA", "Japanisch"), ("KO", "Koreanisch"),
    ("LT", "Litauisch"), ("LV", "Lettisch"), ("NB", "Norwegisch (Bokmål)"),
    ("NL", "Niederländisch"), ("PL", "Polnisch"),
    ("PT-PT", "Portugiesisch (EU)"), ("PT-BR", "Portugiesisch (BR)"),
    ("RO", "Rumänisch"), ("RU", "Russisch"), ("SK", "Slowakisch"),
    ("SL", "Slowenisch"), ("SV", "Schwedisch"), ("TR", "Türkisch"),
    ("UK", "Ukrainisch"), ("ZH", "Chinesisch (vereinfacht)"),
]

def _norm(code: str) -> str:
    return code.strip().upper().replace("_", "-")

class Translate(commands.Cog):
    """DeepL-Commands: /translate, /detect, /languages (dynamische Sprachlisten + Autocomplete)"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not DEEPL_TOKEN:
            print("⚠️  DEEPL_TOKEN fehlt – DeepL-Funktionen werden Fehler melden.")
        self.target_langs: List[Tuple[str, str]] = FALLBACK_LANGS[:]
        self.source_langs: List[Tuple[str, str]] = []
        self.CODE_TO_LABEL: Dict[str, str] = {c: l for c, l in self.target_langs}
        self.bot.loop.create_task(self._load_languages_bg())

    # ------------------ Language Loading ------------------
    async def _load_languages_bg(self):
        if not DEEPL_TOKEN:
            return
        try:
            timeout = httpx.Timeout(15.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                r_t = await client.get(LANG_URL, params={"auth_key": DEEPL_TOKEN, "type": "target"})
                r_t.raise_for_status()
                r_s = await client.get(LANG_URL, params={"auth_key": DEEPL_TOKEN, "type": "source"})
                r_s.raise_for_status()

            def to_list(items):
                out: List[Tuple[str, str]] = []
                for it in items:
                    code = _norm(it.get("language", ""))
                    name = it.get("name") or code
                    if code:
                        out.append((code, name))
                return out

            tlist = to_list(r_t.json())
            slist = to_list(r_s.json())

            if not any(c.startswith("EN-") for c, _ in tlist):
                tlist.extend([("EN-GB", "Englisch (GB)"), ("EN-US", "Englisch (US)")])

            self.target_langs = sorted({c: l for c, l in tlist}.items())
            self.source_langs = sorted({c: l for c, l in slist}.items())
            self.CODE_TO_LABEL = {c: l for c, l in self.target_langs + self.source_langs}
            print(f"🗺️  DeepL-Sprachen geladen: {len(self.source_langs)} source, {len(self.target_langs)} target")
        except Exception as e:
            print(f"⚠️  Konnte DeepL-Sprachen nicht laden, nutze Fallback: {e}")

    # ------------------ HTTP Helper ------------------
    async def _deepl_request(self, data: dict) -> dict:
        if not DEEPL_TOKEN:
            raise RuntimeError("DEEPL_TOKEN fehlt (in .env setzen).")
        timeout = httpx.Timeout(20.0, connect=10.0)
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
            return resp.json()

    # ------------------ Core: Translate ------------------
    async def deepl_translate(
        self,
        text: str,
        target_lang: str,
        source_lang: Optional[str] = None,
        formality: Optional[str] = None
    ) -> str:
        data = {
            "auth_key": DEEPL_TOKEN,
            "text": text,
            "target_lang": _norm(target_lang),
            "preserve_formatting": "1",
        }
        if source_lang:
            data["source_lang"] = _norm(source_lang)
        if formality and formality.lower() in {"default", "less", "more"}:
            data["formality"] = formality.lower()

        payload = await self._deepl_request(data)
        translations = payload.get("translations") or []
        if not translations:
            raise RuntimeError("DeepL: Keine Übersetzung erhalten.")
        return translations[0].get("text", "").strip()

    # ------------------ Autocomplete Helpers ------------------
    def _choices(self, query: str, pool: List[Tuple[str, str]], limit: int = 20):
        q = query.lower().strip()
        items = pool or FALLBACK_LANGS
        filt = [(c, l) for c, l in items if (q in c.lower() or q in l.lower())] if q else items
        return [app_commands.Choice(name=f"{label} [{code}]", value=code) for code, label in filt[:limit]]

    # ------------------ Commands ------------------
    @app_commands.command(name="tping", description="Sanity-Check des Translate-Cogs.")
    async def tping_cmd(self, interaction):
        await interaction.response.send_message("🔧 tping OK (Cog reagiert).", ephemeral=True)

    @app_commands.command(name="translate", description="Übersetzt Text mit DeepL.")
    @app_commands.describe(
        text="Zu übersetzender Text",
        target="Zielsprache (Code/Name, z.B. EN, Deutsch …) – Autocomplete",
        source="Quellsprache (optional, sonst Auto-Detect) – Autocomplete",
        formality="Stil: default / less / more (falls unterstützt)",
        ephemeral="Antwort nur für dich sichtbar?",
    )
    async def translate_cmd(
        self,
        interaction,
        text: str,
        target: str,
        source: Optional[str] = None,
        formality: Optional[str] = None,
        ephemeral: Optional[bool] = True,
    ):
        await interaction.response.defer(ephemeral=bool(ephemeral))

        txt = text.strip()
        if not txt:
            await interaction.followup.send("❌ Bitte Text angeben.", ephemeral=True)
            return
        if len(txt) > 5000:
            txt = txt[:5000] + " …"

        try:
            translated = await self.deepl_translate(
                text=txt, target_lang=target, source_lang=source, formality=formality
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Übersetzung fehlgeschlagen: {e}", ephemeral=True)
            return

        t_code = _norm(target)
        s_code = _norm(source) if source else None
        t_label = self.CODE_TO_LABEL.get(t_code, t_code)
        s_label = self.CODE_TO_LABEL.get(s_code, s_code) if s_code else "Auto-Detect"

        # Embed, mit Text-Fallback falls Rechte fehlen
        try:
            embed = discord.Embed(title=f"Übersetzung → {t_label}", description=translated)
            embed.add_field(name="Quelle", value=s_label, inline=True)
            embed.add_field(name="Ziel", value=t_label, inline=True)
            embed.set_footer(text="DeepL")
            await interaction.followup.send(embed=embed, ephemeral=bool(ephemeral))
        except discord.Forbidden:
            msg = f"**Übersetzung → {t_label}**\nQuelle: {s_label}\nZiel: {t_label}\n\n{translated}"
            await interaction.followup.send(msg, ephemeral=bool(ephemeral))

    @translate_cmd.autocomplete("target")
    async def target_autocomplete(self, interaction, current: str):
        return self._choices(current, self.target_langs)

    @translate_cmd.autocomplete("source")
    async def source_autocomplete(self, interaction, current: str):
        pool = self.source_langs or self.target_langs
        return self._choices(current, pool)

    @app_commands.command(name="detect", description="Erkennt die Sprache eines Textes via DeepL.")
    @app_commands.describe(
        text="Text für die Spracherkennung",
        target="Minimale Übersetzung für den Detect-Call (Standard EN)",
        ephemeral="Antwort nur für dich sichtbar?",
    )
    async def detect_cmd(
        self,
        interaction,
        text: str,
        target: Optional[str] = "EN",
        ephemeral: Optional[bool] = True,
    ):
        await interaction.response.defer(ephemeral=bool(ephemeral))

        txt = text.strip()
        if not txt:
            await interaction.followup.send("❌ Bitte Text angeben.", ephemeral=True)
            return
        if len(txt) > 5000:
            txt = txt[:5000] + " …"

        data = {
            "auth_key": DEEPL_TOKEN,
            "text": txt,
            "target_lang": _norm(target or "EN"),
            "preserve_formatting": "1",
            "split_sentences": "0",
        }

        try:
            payload = await self._deepl_request(data)
            translations = payload.get("translations") or []
            if not translations:
                raise RuntimeError("Keine Antwort erhalten.")
            code = _norm(translations[0].get("detected_source_language") or "")
            label = self.CODE_TO_LABEL.get(code, code or "Unbekannt")
        except Exception as e:
            await interaction.followup.send(f"❌ Erkennung fehlgeschlagen: {e}", ephemeral=True)
            return

        try:
            embed = discord.Embed(title="Spracherkennung", description=f"**Erkannt:** `{code}` ({label})")
            excerpt = (txt[:250] + " …") if len(txt) > 250 else txt
            embed.add_field(name="Ausschnitt", value=excerpt, inline=False)
            embed.set_footer(text="DeepL")
            await interaction.followup.send(embed=embed, ephemeral=bool(ephemeral))
        except discord.Forbidden:
            msg = f"**Spracherkennung**\nErkannt: `{code}` ({label})\n\nAusschnitt:\n{(txt[:250] + ' …') if len(txt)>250 else txt}"
            await interaction.followup.send(msg, ephemeral=bool(ephemeral))

    @app_commands.command(name="languages", description="Listet die verfügbaren DeepL-Sprachen.")
    @app_commands.describe(ephemeral="Antwort nur für dich sichtbar?")
    async def languages_cmd(self, interaction, ephemeral: bool = True):
        await interaction.response.defer(ephemeral=ephemeral)

        targets = self.target_langs if self.target_langs else FALLBACK_LANGS
        sources = self.source_langs if self.source_langs else targets

        def fmt(lst: List[Tuple[str, str]]) -> str:
            return ", ".join(f"{label} [{code}]" for code, label in lst)

        sections = [
            ("Zielsprachen", fmt(targets)),
            ("Quellsprachen", fmt(sources)),
        ]

        for title, text in sections:
            chunks = wrap(text, width=1800, break_long_words=False, replace_whitespace=False)
            for i, chunk in enumerate(chunks, start=1):
                header = f"{title} (Teil {i}/{len(chunks)})" if len(chunks) > 1 else title
                embed = discord.Embed(title=header, description=chunk)
                embed.set_footer(text="DeepL")
                await interaction.followup.send(embed=embed, ephemeral=ephemeral)

async def setup(bot: commands.Bot):
    await bot.add_cog(Translate(bot))
