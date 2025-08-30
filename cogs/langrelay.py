# cogs/langrelay.py
import os
import re
import json
import asyncio
from typing import Dict, Optional, List, Any
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
import httpx

# === ENV / Provider-Keys ===
DEEPL_TOKEN = os.getenv("DEEPL_TOKEN")                 # optional
OPENAI_TOKEN = os.getenv("OPENAI_TOKEN")               # optional
DEEPL_API_URL = os.getenv("DEEPL_API_URL", "https://api-free.deepl.com/v2")
OPENAI_API_URL = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # überschreibbar

TRANSLATE_URL = f"{DEEPL_API_URL}/translate"

# === Unterstützte Zielcodes (DeepL-kompatibel) ===
SUPPORTED_TARGETS: List[str] = [
    "BG","CS","DA","DE","EL","EN","EN-GB","EN-US","ES","ET","FI","FR",
    "HU","ID","IT","JA","KO","LT","LV","NB","NL","PL","PT","PT-PT","PT-BR",
    "RO","RU","SK","SL","SV","TR","UK","ZH"
]

# === Optionales Default-Mapping für NEUE Guilds ===
# Schlüssel = Channel-Name (genau wie in Discord), Wert = DeepL-Sprachcode
DEFAULT_CHANNEL_LANG_MAP: Dict[str, str] = {
    # "channel_de": "DE",
    # "channel_en": "EN",
}

# === Speicherort (relativ zum Projekt) ===
DATA_DIR = (Path(__file__).resolve().parent.parent / "data" / "langrelay")
DATA_DIR.mkdir(parents=True, exist_ok=True)

# === Standard-Provider bestimmen ===
DEFAULT_PROVIDER = "deepl" if DEEPL_TOKEN else ("openai" if OPENAI_TOKEN else "deepl")


def _norm(code: Optional[str]) -> Optional[str]:
    return code.strip().upper().replace("_", "-") if code else None


# ---- Helfer: "Admin ODER Whitelist" ----
def has_langrelay_control():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure("Dieser Befehl ist nur in Servern verfügbar.")
        # Administrator -> immer erlaubt
        if interaction.user.guild_permissions.administrator:
            return True
        cog = interaction.client.get_cog("LangRelay")
        if not isinstance(cog, LangRelay):
            raise app_commands.CheckFailure("LangRelay ist nicht verfügbar.")
        if cog._is_allowed(interaction.guild.id, interaction.user):
            return True
        # Explizite, erklärende Fehlermeldung:
        raise app_commands.CheckFailure(
            "Dir fehlen die Berechtigungen für diesen Befehl.\n"
            "Erlaubt sind **Administratoren** sowie Nutzer/Rollen auf der Whitelist.\n"
            "Tipp: Bitte einen Admin um Aufnahme via `/langrelay_access_add_role` oder `/langrelay_access_add_user`."
        )
    return app_commands.check(predicate)


class LangRelay(commands.Cog):
    """
    Spiegelt Nachrichten aus definierten Sprachkanälen in andere definierte Sprachkanäle – übersetzt via DeepL ODER OpenAI.

    Persistenz (pro Guild):
      ./data/langrelay/<guild_id>.json
      {
        "mapping":  { "channel_name": "LANGCODE", ... },
        "provider": "deepl" | "openai",
        "access":   { "roles": [role_id, ...], "users": [user_id, ...] }
      }

    Zugriff:
      - Administratoren dürfen Konfig-Befehle immer.
      - Zusätzlich können Rollen/Users in "access" whitelisted werden (sehen die Commands und dürfen ausführen).
      - Access-Management-Commands selbst sind per default_permissions **nur für Admins sichtbar**.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not DEEPL_TOKEN and not OPENAI_TOKEN:
            print("⚠️  Weder DEEPL_TOKEN noch OPENAI_TOKEN gesetzt – Übersetzen wird fehlschlagen, bis einer vorhanden ist.")
        # pro Guild:
        # {"mapping": {channel_name: code}, "provider": str, "access": {"roles": [], "users": []}}
        self.guild_config: Dict[int, Dict[str, Any]] = {}
        # Cache: guild_id -> {channel_name -> TextChannel}
        self._guild_channel_cache: Dict[int, Dict[str, discord.TextChannel]] = {}
        # Concurrency
        self._sem_per_guild: Dict[int, asyncio.Semaphore] = {}
        # kosmetik
        self.preview_max = 200

    # -------------------- Utility: Antworten sicher senden --------------------
    async def _reply_ephemeral(self, interaction: discord.Interaction, content: str):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)
        except Exception:
            # Als Fallback versuchen wir noch einmal followup
            try:
                await interaction.followup.send(content, ephemeral=True)
            except Exception:
                pass

    # -------------------- Persistence --------------------
    def _guild_path(self, guild_id: int) -> Path:
        return DATA_DIR / f"{guild_id}.json"

    def _ensure_access_block(self, cfg: Dict[str, Any]):
        acc = cfg.get("access")
        if not isinstance(acc, dict):
            acc = {}
        roles = acc.get("roles")
        users = acc.get("users")
        if not isinstance(roles, list):
            roles = []
        if not isinstance(users, list):
            users = []
        cfg["access"] = {"roles": [int(x) for x in roles], "users": [int(x) for x in users]}

    def _load_guild(self, guild: discord.Guild):
        """Lädt Mapping/Provider/Access aus Datei; wenn nicht vorhanden, setzt Defaults (gefiltert auf existierende Channels)."""
        p = self._guild_path(guild.id)
        mapping: Dict[str, str] = {}
        provider = DEFAULT_PROVIDER
        access = {"roles": [], "users": []}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    mapping = {str(k): str(v) for k, v in (data.get("mapping") or {}).items()}
                    provider = str(data.get("provider") or DEFAULT_PROVIDER)
                    cfg = {"mapping": mapping, "provider": provider, "access": data.get("access")}
                    self._ensure_access_block(cfg)
                    mapping = cfg["mapping"]
                    provider = cfg["provider"]
                    access = cfg["access"]
            except Exception as e:
                print(f"⚠️ Konnte Mapping/Provider/Access für Guild {guild.name} nicht laden: {e}")
        else:
            if DEFAULT_CHANNEL_LANG_MAP:
                existing = {ch.name for ch in guild.text_channels}
                mapping = {name: code for name, code in DEFAULT_CHANNEL_LANG_MAP.items() if name in existing}

        if provider not in {"deepl", "openai"}:
            provider = DEFAULT_PROVIDER

        self.guild_config[guild.id] = {"mapping": mapping, "provider": provider, "access": access}
        self._save_guild(guild.id)

    def _save_guild(self, guild_id: int):
        cfg = self.guild_config.get(
            guild_id,
            {"mapping": {}, "provider": DEFAULT_PROVIDER, "access": {"roles": [], "users": []}}
        )
        self._ensure_access_block(cfg)
        p = self._guild_path(guild_id)
        try:
            p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"⚠️ Konnte Mapping/Provider/Access für Guild {guild_id} nicht speichern: {e}")

    def _mapping(self, guild_id: int) -> Dict[str, str]:
        return self.guild_config.setdefault(
            guild_id,
            {"mapping": {}, "provider": DEFAULT_PROVIDER, "access": {"roles": [], "users": []}}
        )["mapping"]

    def _provider(self, guild_id: int) -> str:
        return self.guild_config.setdefault(
            guild_id,
            {"mapping": {}, "provider": DEFAULT_PROVIDER, "access": {"roles": [], "users": []}}
        )["provider"]

    def _access(self, guild_id: int) -> Dict[str, List[int]]:
        cfg = self.guild_config.setdefault(
            guild_id,
            {"mapping": {}, "provider": DEFAULT_PROVIDER, "access": {"roles": [], "users": []}}
        )
        self._ensure_access_block(cfg)
        return cfg["access"]

    def _is_allowed(self, guild_id: int, member: discord.Member) -> bool:
        """Nicht-Admins: Erlaubt, wenn Member in Whitelist (Rolle oder User) ist."""
        acc = self._access(guild_id)
        allow_roles = set(acc.get("roles", []))
        allow_users = set(acc.get("users", []))
        if member.id in allow_users:
            return True
        for r in member.roles:
            if r.id in allow_roles:
                return True
        return False

    # -------------------- Utilities --------------------
    def _sem(self, guild_id: int) -> asyncio.Semaphore:
        if guild_id not in self._sem_per_guild:
            self._sem_per_guild[guild_id] = asyncio.Semaphore(2)
        return self._sem_per_guild[guild_id]

    async def _ensure_cache(self, guild: discord.Guild):
        """Baut/aktualisiert den Channel-Cache und lädt ggf. die Guild-Konfiguration."""
        by_name: Dict[str, discord.TextChannel] = {}
        for ch in guild.text_channels:
            by_name[ch.name] = ch
        self._guild_channel_cache[guild.id] = by_name
        if guild.id not in self.guild_config:
            self._load_guild(guild)

    def _get_channel_by_name(self, guild_id: int, name: str) -> Optional[discord.TextChannel]:
        cache = self._guild_channel_cache.get(guild_id) or {}
        return cache.get(name)

    def _safe_mentions(self, text: str) -> str:
        # Mentions entschärfen
        text = re.sub(r"@", "@\u200B", text)
        text = re.sub(r"&", "&\u200B", text)
        return text

    # -------------------- Übersetzer (Provider-Switch) --------------------
    async def _translate(self, text: str, target_lang: str, source_lang: Optional[str], guild_id: int) -> str:
        provider = self._provider(guild_id)
        if provider == "openai":
            if not OPENAI_TOKEN:
                if DEEPL_TOKEN:
                    return await self._deepl_translate(text, target_lang, source_lang)
                raise RuntimeError("OPENAI_TOKEN fehlt und kein DEEPL_TOKEN als Fallback vorhanden.")
            return await self._openai_translate(text, target_lang, source_lang)
        # default = deepl
        if DEEPL_TOKEN:
            return await self._deepl_translate(text, target_lang, source_lang)
        if OPENAI_TOKEN:
            return await self._openai_translate(text, target_lang, source_lang)
        raise RuntimeError("Weder DEEPL_TOKEN noch OPENAI_TOKEN vorhanden – Übersetzung nicht möglich.")

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

    async def _openai_translate(self, text: str, target_lang: str, source_lang: Optional[str] = None) -> str:
        if not OPENAI_TOKEN:
            raise RuntimeError("OPENAI_TOKEN fehlt (in .env setzen).")

        sys_prompt = (
            "You are a professional translator. "
            "Translate the user's message into the requested target language code (e.g., EN, EN-GB, DE). "
            "Preserve meaning, tone, and formatting. Do not add explanations or quotes. "
            "Return ONLY the translated text."
        )
        user_prompt = f"Target language code: {_norm(target_lang)}.\n"
        if source_lang:
            user_prompt += f"Source language code (hint): {_norm(source_lang)}.\n"
        user_prompt += "Text to translate:\n" + text

        headers = {
            "Authorization": f"Bearer {OPENAI_TOKEN}",
            "Content-Type": "application/json",
        }
        body = {
            "model": OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }

        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{OPENAI_API_URL}/chat/completions", headers=headers, json=body)
            if resp.status_code >= 400:
                try:
                    detail = resp.json()
                except Exception:
                    detail = resp.text
                raise RuntimeError(f"OpenAI-Fehler ({resp.status_code}): {detail}")

            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError("OpenAI: keine Antwort erhalten.")
            content = (choices[0].get("message") or {}).get("content") or ""
            return content.strip()

    # -------------------- Listeners --------------------
    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self._ensure_cache(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
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
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        if not message.content:
            return

        guild = message.guild
        await self._ensure_cache(guild)
        mapping = self._mapping(guild.id)

        src_channel = message.channel
        src_lang = mapping.get(src_channel.name)
        if not src_lang:
            return

        async with self._sem(guild.id):
            tasks = []
            for tgt_name, tgt_lang in mapping.items():
                if tgt_name == src_channel.name:
                    continue
                tgt_channel = self._get_channel_by_name(guild.id, tgt_name)
                if not tgt_channel:
                    continue

                async def _one_target(_tgt_channel=tgt_channel, _tgt_lang=tgt_lang):
                    try:
                        translated = await self._translate(
                            text=message.content,
                            target_lang=_tgt_lang,
                            source_lang=src_lang,
                            guild_id=guild.id,
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

    # -------------------- Globaler Error-Handler für Slash-Commands --------------------
    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        # Permissions & Checks freundlich behandeln
        if isinstance(error, app_commands.CheckFailure):
            await self._reply_ephemeral(
                interaction,
                f"🚫 **Keine Berechtigung:**\n{str(error) or 'Du darfst diesen Befehl nicht ausführen.'}"
            )
            return
        # Rest: generische, ephemere Antwort + Console-Log
        await self._reply_ephemeral(
            interaction,
            "❌ Es ist ein Fehler aufgetreten. Bitte versuche es erneut oder kontaktiere einen Admin."
        )
        try:
            raise error
        except Exception:
            pass

    # -------------------- Admin/Access: Hilfsanzeigen --------------------
    @app_commands.command(name="langrelay_status", description="Zeigt die aktuelle Channel→Sprache-Zuordnung und den Provider (persistiert).")
    async def cmd_status(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)

        await self._ensure_cache(interaction.guild)
        mapping = self._mapping(interaction.guild.id)
        provider = self._provider(interaction.guild.id)
        access = self._access(interaction.guild.id)

        lines = [f"**Provider:** `{provider}`", ""]
        missing = []
        for ch_name, code in mapping.items():
            ch_obj = self._get_channel_by_name(interaction.guild.id, ch_name)
            if ch_obj:
                lines.append(f"• <#{ch_obj.id}>  →  `{code}`")
            else:
                lines.append(f"• `#{ch_name}`  →  `{code}`  (❌ nicht gefunden)")
                missing.append(ch_name)

        role_mentions = []
        for rid in access.get("roles", []):
            r = interaction.guild.get_role(int(rid))
            role_mentions.append(r.mention if r else f"`{rid}`")
        user_mentions = []
        for uid in access.get("users", []):
            m = interaction.guild.get_member(int(uid))
            user_mentions.append(m.mention if m else f"`{uid}`")

        if role_mentions or user_mentions:
            lines.append("\n**Access (zusätzlich zu Admin):**")
            if role_mentions:
                lines.append("• Rollen: " + ", ".join(role_mentions))
            if user_mentions:
                lines.append("• Nutzer: " + ", ".join(user_mentions))
        else:
            lines.append("\n**Access:** nur Administratoren (keine Whitelist-Einträge)")

        desc = "\n".join(lines) if lines else "_Kein Mapping definiert._"
        embed = discord.Embed(title="LangRelay – Status", description=desc)
        if missing:
            embed.set_footer(text="Hinweis: Manche Channels fehlen oder wurden umbenannt.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="langrelay_reload", description="Lädt die Channel-Liste neu (z. B. nach Umbenennen/Anlegen).")
    async def cmd_reload(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        await self._ensure_cache(interaction.guild)
        await interaction.response.send_message("🔁 Channel-Cache aktualisiert.", ephemeral=True)

    # -------------------- Konfig: Mapping/Provider (Admin ODER Whitelist) --------------------
    @app_commands.command(name="langrelay_set", description="Setzt/ändert die Sprache eines Channels (persistiert).")
    @app_commands.describe(channel="Ziel-Textkanal", language="DeepL Sprachcode (z. B. DE, EN, EN-GB, FR …)")
    @has_langrelay_control()
    async def cmd_set(self, interaction: discord.Interaction, channel: discord.TextChannel, language: str):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        lang = _norm(language or "")
        if lang not in SUPPORTED_TARGETS:
            return await interaction.response.send_message(
                f"❌ Ungültiger Sprachcode `{lang}`. Beispiele: DE, EN, EN-GB, EN-US, FR, ES, PT-BR, ZH …",
                ephemeral=True
            )

        await self._ensure_cache(interaction.guild)
        mapping = self._mapping(interaction.guild.id)
        mapping[channel.name] = lang
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message(
            f"✅ Mapping gesetzt: {channel.mention} → `{lang}` (gespeichert)",
            ephemeral=True
        )

    @cmd_set.autocomplete("language")
    async def ac_language(self, interaction: discord.Interaction, current: str):
        q = (current or "").strip().lower()
        pool = SUPPORTED_TARGETS
        items = [c for c in pool if q in c.lower()] if q else pool
        return [app_commands.Choice(name=c, value=c) for c in items[:20]]

    @app_commands.command(name="langrelay_remove", description="Entfernt die Zuordnung eines Channels (persistiert).")
    @app_commands.describe(channel="Textkanal, dessen Mapping entfernt werden soll")
    @has_langrelay_control()
    async def cmd_remove(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)

        await self._ensure_cache(interaction.guild)
        mapping = self._mapping(interaction.guild.id)
        if channel.name in mapping:
            mapping.pop(channel.name, None)
            self._save_guild(interaction.guild.id)
            await interaction.response.send_message(f"🗑️ Entfernt: {channel.mention} (gespeichert)", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ Für {channel.mention} war kein Mapping vorhanden.", ephemeral=True)

    @app_commands.command(name="langrelay_clear", description="Löscht alle Channel→Sprache-Zuordnungen dieser Guild.")
    @has_langrelay_control()
    async def cmd_clear(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        self.guild_config.setdefault(
            interaction.guild.id,
            {"mapping": {}, "provider": DEFAULT_PROVIDER, "access": {"roles": [], "users": []}}
        )
        self.guild_config[interaction.guild.id]["mapping"] = {}
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message("🧹 Alle Mappings gelöscht (gespeichert).", ephemeral=True)

    @app_commands.command(name="langrelay_provider", description="Setzt den Übersetzungsprovider (deepl oder openai) für diese Guild.")
    @app_commands.choices(provider=[
        app_commands.Choice(name="DeepL", value="deepl"),
        app_commands.Choice(name="OpenAI", value="openai"),
    ])
    @has_langrelay_control()
    async def cmd_provider(self, interaction: discord.Interaction, provider: app_commands.Choice[str]):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)

        choice = provider.value
        if choice == "deepl" and not DEEPL_TOKEN:
            return await interaction.response.send_message("❌ DeepL ist nicht konfiguriert (DEEPL_TOKEN fehlt).", ephemeral=True)
        if choice == "openai" and not OPENAI_TOKEN:
            return await interaction.response.send_message("❌ OpenAI ist nicht konfiguriert (OPENAI_TOKEN fehlt).", ephemeral=True)

        self.guild_config.setdefault(
            interaction.guild.id,
            {"mapping": {}, "provider": DEFAULT_PROVIDER, "access": {"roles": [], "users": []}}
        )
        self.guild_config[interaction.guild.id]["provider"] = choice
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message(f"✅ Provider für diese Guild gesetzt: `{choice}`", ephemeral=True)

    # -------------------- Access-Management (NUR Admins sehen dank default_permissions) --------------------
    @app_commands.command(name="langrelay_access_status", description="Zeigt die Whitelist (Rollen/Benutzer), die zusätzlich zu Admins konfigurieren dürfen.")
    @app_commands.default_permissions(administrator=True)
    async def cmd_access_status(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        await self._ensure_cache(interaction.guild)
        access = self._access(interaction.guild.id)

        role_mentions = []
        for rid in access.get("roles", []):
            r = interaction.guild.get_role(int(rid))
            role_mentions.append(r.mention if r else f"`{rid}`")
        user_mentions = []
        for uid in access.get("users", []):
            m = interaction.guild.get_member(int(uid))
            user_mentions.append(m.mention if m else f"`{uid}`")

        desc_lines = []
        desc_lines.append("**Admins**: always allowed")
        desc_lines.append("**Roles**: " + (", ".join(role_mentions) if role_mentions else "—"))
        desc_lines.append("**Users**: " + (", ".join(user_mentions) if user_mentions else "—"))
        embed = discord.Embed(title="LangRelay – Access", description="\n".join(desc_lines))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="langrelay_access_add_role", description="Fügt eine Rolle zur Whitelist hinzu (darf konfigurieren).")
    @app_commands.default_permissions(administrator=True)
    async def cmd_access_add_role(self, interaction: discord.Interaction, role: discord.Role):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        acc = self._access(interaction.guild.id)
        if role.id not in acc["roles"]:
            acc["roles"].append(int(role.id))
            self._save_guild(interaction.guild.id)
        await interaction.response.send_message(f"✅ Rolle hinzugefügt: {role.mention}", ephemeral=True)

    @app_commands.command(name="langrelay_access_remove_role", description="Entfernt eine Rolle aus der Whitelist.")
    @app_commands.default_permissions(administrator=True)
    async def cmd_access_remove_role(self, interaction: discord.Interaction, role: discord.Role):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        acc = self._access(interaction.guild.id)
        if role.id in acc["roles"]:
            acc["roles"].remove(int(role.id))
            self._save_guild(interaction.guild.id)
            return await interaction.response.send_message(f"🗑️ Rolle entfernt: {role.mention}", ephemeral=True)
        await interaction.response.send_message("ℹ️ Rolle war nicht gelistet.", ephemeral=True)

    @app_commands.command(name="langrelay_access_add_user", description="Fügt einen Benutzer zur Whitelist hinzu (darf konfigurieren).")
    @app_commands.default_permissions(administrator=True)
    async def cmd_access_add_user(self, interaction: discord.Interaction, user: discord.Member):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        acc = self._access(interaction.guild.id)
        if user.id not in acc["users"]:
            acc["users"].append(int(user.id))
            self._save_guild(interaction.guild.id)
        await interaction.response.send_message(f"✅ Benutzer hinzugefügt: {user.mention}", ephemeral=True)

    @app_commands.command(name="langrelay_access_remove_user", description="Entfernt einen Benutzer aus der Whitelist.")
    @app_commands.default_permissions(administrator=True)
    async def cmd_access_remove_user(self, interaction: discord.Interaction, user: discord.Member):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        acc = self._access(interaction.guild.id)
        if user.id in acc["users"]:
            acc["users"].remove(int(user.id))
            self._save_guild(interaction.guild.id)
            return await interaction.response.send_message(f"🗑️ Benutzer entfernt: {user.mention}", ephemeral=True)
        await interaction.response.send_message("ℹ️ Benutzer war nicht gelistet.", ephemeral=True)

    @app_commands.command(name="langrelay_access_clear", description="Leert die Whitelist (nur Administratoren dürfen konfigurieren).")
    @app_commands.default_permissions(administrator=True)
    async def cmd_access_clear(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        cfg = self.guild_config.setdefault(
            interaction.guild.id,
            {"mapping": {}, "provider": DEFAULT_PROVIDER, "access": {"roles": [], "users": []}}
        )
        cfg["access"] = {"roles": [], "users": []}
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message("🧹 Whitelist geleert. Nur Administratoren dürfen konfigurieren.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LangRelay(bot))
