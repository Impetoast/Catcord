# cogs/langrelay.py
from __future__ import annotations
import os
import re
import json
import io
import time
import asyncio
from typing import Dict, Optional, List, Any, Set
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
import httpx

# Hilfsmodul mit Labels/Aliasen/Autocomplete
from .langcodes import (
    normalize_code,
    alias_for_provider,
    suggest_codes,
)

# === ENV / Provider-Keys ===
DEEPL_TOKEN = os.getenv("DEEPL_TOKEN")
OPENAI_TOKEN = os.getenv("OPENAI_TOKEN")
DEEPL_API_URL = os.getenv("DEEPL_API_URL", "https://api-free.deepl.com/v2")
OPENAI_API_URL = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

TRANSLATE_URL = f"{DEEPL_API_URL}/translate"

# === Speicherort ===
DATA_DIR = (Path(__file__).resolve().parent.parent / "data" / "langrelay")
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PROVIDER = "deepl" if DEEPL_TOKEN else ("openai" if OPENAI_TOKEN else "deepl")
WEBHOOK_NAME = os.getenv("LANGRELAY_WEBHOOK_NAME", "Catcord")
WEBHOOK_CACHE_SIZE = 64

# === Sprachlisten-Cache für DeepL (Targets) ===
_DEEPL_LANG_CACHE = {"ts": 0.0, "targets": set()}

async def _deepl_targets() -> Set[str]:
    """Gültige DeepL-Target-Sprachen laden (1×/Stunde cachen)."""
    now = time.time()
    if _DEEPL_LANG_CACHE["targets"] and now - _DEEPL_LANG_CACHE["ts"] < 3600:
        return _DEEPL_LANG_CACHE["targets"]

    if not DEEPL_TOKEN:
        return set()

    url = f"{DEEPL_API_URL}/languages"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            resp = await client.get(url, params={"type": "target", "auth_key": DEEPL_TOKEN})
        resp.raise_for_status()
        data = resp.json() or []
        targets = { (item.get("language") or "").upper() for item in data if item.get("language") }
        _DEEPL_LANG_CACHE["targets"] = targets
        _DEEPL_LANG_CACHE["ts"] = now
        return targets
    except Exception:
        return set()

def admins_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure("Nur in Servern verfügbar.")
        if interaction.user.guild_permissions.administrator:
            return True
        raise app_commands.CheckFailure("Nur Administratoren dürfen diesen Befehl ausführen.")
    return app_commands.check(predicate)


class LangRelay(commands.Cog):
    """Spiegelt Nachrichten zwischen Sprachkanälen (Übersetzung, Webhook-Impersonation, klickbare Mentions ohne Ping)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not DEEPL_TOKEN and not OPENAI_TOKEN:
            print("⚠️  Weder DEEPL_TOKEN noch OPENAI_TOKEN gesetzt – Übersetzung nicht möglich, bis einer vorhanden ist.")
        self.guild_config: Dict[int, Dict[str, Any]] = {}
        self._guild_channel_cache: Dict[int, Dict[str, discord.TextChannel]] = {}
        self._sem_per_guild: Dict[int, asyncio.Semaphore] = {}
        self._webhook_cache: Dict[int, Dict[int, discord.Webhook]] = {}

    # -------------------- Persistence --------------------
    def _guild_path(self, guild_id: int) -> Path:
        return DATA_DIR / f"{guild_id}.json"

    def _ensure_subblocks(self, cfg: Dict[str, Any]):
        # keine Access-Whitelist mehr – nur Admins
        opt = cfg.get("options") or {}
        cfg["options"] = {
            "replymode": bool(opt.get("replymode", False)),
            "thread_mirroring": bool(opt.get("thread_mirroring", False)),
        }
        if "provider" not in cfg:
            cfg["provider"] = DEFAULT_PROVIDER
        if "mapping" not in cfg:
            cfg["mapping"] = {}

    def _load_guild(self, guild: discord.Guild):
        p = self._guild_path(guild.id)
        cfg = {"mapping": {}, "provider": DEFAULT_PROVIDER, "options": {"replymode": False, "thread_mirroring": False}}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    cfg.update(data)
            except Exception as e:
                print(f"⚠️ Konnte Konfiguration für {guild.name} nicht laden: {e}")
        self._ensure_subblocks(cfg)
        self.guild_config[guild.id] = cfg
        self._save_guild(guild.id)

    def _save_guild(self, guild_id: int):
        cfg = self.guild_config.setdefault(guild_id, {
            "mapping": {}, "provider": DEFAULT_PROVIDER,
            "options": {"replymode": False, "thread_mirroring": False},
        })
        self._ensure_subblocks(cfg)
        p = self._guild_path(guild_id)
        try:
            p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"⚠️ Konnte Konfiguration für Guild {guild_id} nicht speichern: {e}")

    def _mapping(self, guild_id: int) -> Dict[str, str]:
        return self.guild_config.setdefault(guild_id, {
            "mapping": {}, "provider": DEFAULT_PROVIDER,
            "options": {"replymode": False, "thread_mirroring": False},
        })["mapping"]

    def _provider(self, guild_id: int) -> str:
        return self.guild_config.setdefault(guild_id, {
            "mapping": {}, "provider": DEFAULT_PROVIDER,
            "options": {"replymode": False, "thread_mirroring": False},
        })["provider"]

    def _set_provider(self, guild_id: int, provider: str):
        cfg = self.guild_config.setdefault(guild_id, {
            "mapping": {}, "provider": DEFAULT_PROVIDER,
            "options": {"replymode": False, "thread_mirroring": False},
        })
        cfg["provider"] = provider
        self._save_guild(guild_id)

    def _options(self, guild_id: int) -> Dict[str, bool]:
        cfg = self.guild_config.setdefault(guild_id, {
            "mapping": {}, "provider": DEFAULT_PROVIDER,
            "options": {"replymode": False, "thread_mirroring": False},
        })
        self._ensure_subblocks(cfg)
        return cfg["options"]

    # -------------------- Caching / Semaphores --------------------
    def _sem(self, guild_id: int) -> asyncio.Semaphore:
        if guild_id not in self._sem_per_guild:
            self._sem_per_guild[guild_id] = asyncio.Semaphore(2)
        return self._sem_per_guild[guild_id]

    async def _ensure_cache(self, guild: discord.Guild):
        self._guild_channel_cache[guild.id] = {ch.name: ch for ch in guild.text_channels}
        if guild.id not in self.guild_config:
            self._load_guild(guild)

    def _get_channel_by_name(self, guild_id: int, name: str) -> Optional[discord.TextChannel]:
        return (self._guild_channel_cache.get(guild_id) or {}).get(name)

    # -------------------- Mentions: klickbar ohne Ping --------------------
    async def _resolve_mentions(self, message: discord.Message) -> str:
        """<@id>/<@&id>/<#id> bevorzugen (klickbar); Fallback @Name/#kanal. Lad fehlende Namen via REST nach."""
        text = message.content or ""
        guild = message.guild
        if not guild or not text:
            return text

        for z in ("\u200b", "\u200e", "\u200f", "\u2060"):
            text = text.replace(z, "")

        user_ids = {int(m) for m in re.findall(r"<@!?\u200b*([0-9]+)>", text)}
        role_ids = {int(m) for m in re.findall(r"<@&\u200b*([0-9]+)>", text)}
        chan_ids = {int(m) for m in re.findall(r"<#\u200b*([0-9]+)>", text)}

        user_map = {m.id: m.display_name for m in getattr(message, "mentions", [])}
        role_map = {r.id: r.name for r in getattr(message, "role_mentions", [])}
        chan_map = {c.id: c.name for c in getattr(message, "channel_mentions", [])}

        missing_user_ids = [uid for uid in user_ids if uid not in user_map]
        for uid in missing_user_ids:
            mem = guild.get_member(uid)
            if mem:
                user_map[uid] = mem.display_name
            else:
                try:
                    mem = await guild.fetch_member(uid)
                    user_map[uid] = mem.display_name
                except Exception:
                    try:
                        u = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                        user_map[uid] = getattr(u, "global_name", None) or u.name
                    except Exception:
                        pass

        def repl_user(m: re.Match) -> str:
            uid = int(m.group(1))
            return f"<@{uid}>" if guild.get_member(uid) else f"@{user_map.get(uid, str(uid))}"

        def repl_role(m: re.Match) -> str:
            rid = int(m.group(1))
            return f"<@&{rid}>" if guild.get_role(rid) else f"@{role_map.get(rid, str(rid))}"

        def repl_chan(m: re.Match) -> str:
            cid = int(m.group(1))
            ch = guild.get_channel(cid)
            return f"<#{cid}>" if isinstance(ch, discord.abc.GuildChannel) else f"#{chan_map.get(cid, str(cid))}"

        text = re.sub(r"<@!?\u200b*([0-9]+)>", repl_user, text)
        text = re.sub(r"<@&\u200b*([0-9]+)>", repl_role, text)
        text = re.sub(r"<#\u200b*([0-9]+)>", repl_chan, text)
        return text

    # -------------------- Übersetzer --------------------
    async def _translate(self, text: str, target_lang: str, source_lang: Optional[str], guild_id: int) -> str:
        provider = self._provider(guild_id)
        if provider == "openai":
            return await self._openai_translate(text, target_lang, source_lang)
        return await self._deepl_translate(text, target_lang, source_lang)

    async def _deepl_translate(self, text: str, target_lang: str, source_lang: Optional[str] = None) -> str:
        if not DEEPL_TOKEN:
            raise RuntimeError("DEEPL_TOKEN fehlt.")
        tgt = normalize_code(target_lang)
        src = normalize_code(source_lang)

        targets = await _deepl_targets()
        if targets:
            tgt = alias_for_provider(tgt or "", targets)
            if tgt not in targets:
                raise RuntimeError(
                    f"DeepL: Zielcode `{tgt}` wird nicht unterstützt. "
                    f"Beispiele: EN-GB, EN-US, PT-PT, PT-BR, ZH, ZH-HANT."
                )

        data = {"auth_key": DEEPL_TOKEN, "text": text, "target_lang": tgt, "preserve_formatting": "1"}
        if src:
            data["source_lang"] = src

        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
            resp = await client.post(TRANSLATE_URL, data=data)
        if resp.status_code == 400:
            raise RuntimeError(f"DeepL lehnt den Zielcode ab (`{tgt}`).")
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
            raise RuntimeError("OPENAI_TOKEN fehlt.")
        sys_prompt = (
            "You are a professional translator. Translate the user's message into the requested target language code. "
            "Preserve meaning, tone, and formatting. Return ONLY the translated text."
        )
        user_prompt = f"Target language code: {normalize_code(target_lang)}.\n"
        if source_lang:
            user_prompt += f"Source language code (hint): {normalize_code(source_lang)}.\n"
        user_prompt += "Text to translate:\n" + text

        headers = {"Authorization": f"Bearer {OPENAI_TOKEN}", "Content-Type": "application/json"}
        body = {
            "model": OPENAI_MODEL,
            "messages": [{"role": "system", "content": sys_prompt},
                         {"role": "user", "content": user_prompt}],
            "temperature": 0.2,
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            resp = await client.post(f"{OPENAI_API_URL}/chat/completions", headers=headers, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"OpenAI-Fehler ({resp.status_code}): {resp.text}")
        data = resp.json()
        choices = data.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else ""
        return (content or "").strip()

    # -------------------- Webhooks --------------------
    async def _get_or_create_webhook(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        try:
            wh = self._webhook_cache.setdefault(channel.guild.id, {}).get(channel.id)
            if wh:
                return wh
            hooks = await channel.webhooks()
            for h in hooks:
                if h.name == WEBHOOK_NAME:
                    self._webhook_cache[channel.guild.id][channel.id] = h
                    if len(self._webhook_cache[channel.guild.id]) > WEBHOOK_CACHE_SIZE:
                        # einfachen FIFO-Kahlschlag
                        first_key = next(iter(self._webhook_cache[channel.guild.id]))
                        if first_key != channel.id:
                            self._webhook_cache[channel.guild.id].pop(first_key, None)
                    return h
            new_hook = await channel.create_webhook(name=WEBHOOK_NAME, reason="Language relay")
            self._webhook_cache[channel.guild.id][channel.id] = new_hook
            return new_hook
        except discord.Forbidden:
            print(f"⚠️ Keine Berechtigung für Webhooks in #{channel.name} ({channel.guild.name}).")
            return None
        except Exception as e:
            print(f"⚠️ Webhook-Fehler in #{channel.name}: {e}")
            return None

    async def _safe_webhook_send(
        self,
        webhook: discord.Webhook,
        *,
        content: Optional[str] = None,
        files: Optional[List[discord.File]] = None,
        allowed_mentions: Optional[discord.AllowedMentions] = None,
        thread: Optional[discord.Thread] = None,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ):
        kwargs: Dict[str, Any] = {}
        if allowed_mentions is not None:
            kwargs["allowed_mentions"] = allowed_mentions
        if content:
            kwargs["content"] = content
        if files:
            kwargs["files"] = files
        if username:
            kwargs["username"] = username
        if avatar_url:
            kwargs["avatar_url"] = avatar_url
        if thread:
            kwargs["thread"] = thread
        if "content" not in kwargs and "files" not in kwargs:
            return
        await webhook.send(**kwargs)

    async def _safe_channel_send(
        self,
        dest: discord.abc.Messageable,
        *,
        content: Optional[str] = None,
        files: Optional[List[discord.File]] = None,
        allowed_mentions: Optional[discord.AllowedMentions] = None,
    ):
        kwargs: Dict[str, Any] = {}
        if allowed_mentions is not None:
            kwargs["allowed_mentions"] = allowed_mentions
        if content:
            kwargs["content"] = content
        if files:
            kwargs["files"] = files
        if "content" not in kwargs and "files" not in kwargs:
            return
        await dest.send(**kwargs)

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
        # Schutz / Scope
        if message.author.bot or not message.guild:
            return
        if message.webhook_id is not None:
            return
        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return
        if not message.content and not message.attachments:
            return

        guild = message.guild
        await self._ensure_cache(guild)
        mapping = self._mapping(guild.id)
        opts = self._options(guild.id)
        replymode = bool(opts.get("replymode", False))
        thread_mirroring = bool(opts.get("thread_mirroring", False))

        # Quelle: TextChannel oder Thread?
        src_thread: Optional[discord.Thread] = None
        if isinstance(message.channel, discord.Thread):
            src_thread = message.channel
            src_parent = src_thread.parent
            if not isinstance(src_parent, discord.TextChannel):
                return
            src_channel = src_parent
        else:
            src_channel = message.channel

        src_lang = mapping.get(src_channel.name)
        if not src_lang:
            return  # kein Relay-Channel

        # Name/Avatar des Autors übernehmen
        display_name = message.author.display_name
        try:
            avatar_url = (message.author.display_avatar or message.author.avatar).url
        except Exception:
            avatar_url = None

        base_text_with_tokens = await self._resolve_mentions(message)

        async with self._sem(guild.id):
            tasks = []
            for tgt_name, tgt_lang in mapping.items():
                if tgt_name == src_channel.name:
                    continue
                tgt_channel = self._get_channel_by_name(guild.id, tgt_name)
                if not tgt_channel:
                    continue

                async def _one_target(_tgt_channel=tgt_channel, _tgt_lang=tgt_lang):
                    out_text = base_text_with_tokens
                    try:
                        if out_text and _tgt_lang:
                            out_text = await self._translate(
                                text=out_text,
                                target_lang=_tgt_lang,
                                source_lang=src_lang,
                                guild_id=guild.id,
                            )
                    except Exception as e:
                        print(f"⚠️ Übersetzung fehlgeschlagen ({src_channel.name} → {_tgt_channel.name}): {e}")
                        out_text = base_text_with_tokens

                    # optionaler Reply-Kontext
                    if replymode and message.reference and isinstance(message.reference.resolved, discord.Message):
                        replied_to = message.reference.resolved
                        replied_text_clean = await self._resolve_mentions(replied_to)
                        preview = (replied_text_clean[:90] + "…") if len(replied_text_clean) > 90 else replied_text_clean
                        base_ctx = f"(reply to {replied_to.author.display_name}: {preview})"
                        try:
                            base_ctx_tr = await self._translate(base_ctx, _tgt_lang, src_lang, guild.id)
                        except Exception:
                            base_ctx_tr = base_ctx
                        out_text = f"{out_text}\n\n> {base_ctx_tr}" if out_text else f"> {base_ctx_tr}"

                    # Anhänge mitnehmen
                    files: List[discord.File] = []
                    try:
                        for att in message.attachments[:10]:
                            data = await att.read()
                            buf = io.BytesIO(data)
                            buf.seek(0)
                            files.append(discord.File(buf, filename=att.filename, spoiler=att.is_spoiler()))
                    except Exception as e:
                        print(f"⚠️ Konnte Anhänge nicht lesen: {e}")

                    if not out_text and not files:
                        return

                    # Thread-Mirroring
                    target_thread: Optional[discord.Thread] = None
                    if thread_mirroring and src_thread is not None:
                        try:
                            for th in _tgt_channel.threads:
                                if th.name == src_thread.name and not th.archived:
                                    target_thread = th
                                    break
                            if target_thread is None:
                                target_thread = await _tgt_channel.create_thread(
                                    name=src_thread.name,
                                    type=discord.ChannelType.public_thread,
                                    auto_archive_duration=src_thread.auto_archive_duration or 1440,
                                    reason="LangRelay Thread-Mirroring",
                                )
                        except Exception:
                            target_thread = None

                    webhook = await self._get_or_create_webhook(_tgt_channel)
                    dest = target_thread if target_thread else _tgt_channel
                    if not webhook:
                        try:
                            await self._safe_channel_send(
                                dest,
                                content=out_text if out_text else None,
                                files=files if files else None,
                                allowed_mentions=discord.AllowedMentions.none(),  # klickbar, aber stumm
                            )
                        except Exception as e:
                            print(f"⚠️ Nachricht in #{_tgt_channel.name} konnte nicht gesendet werden: {e}")
                        return

                    try:
                        await self._safe_webhook_send(
                            webhook,
                            content=out_text if out_text else None,
                            files=files if files else None,
                            allowed_mentions=discord.AllowedMentions.none(),  # klickbar, aber stumm
                            thread=target_thread,
                            username=display_name,
                            avatar_url=avatar_url,
                        )
                    except TypeError:
                        # ältere discord.py → ohne thread=
                        try:
                            await self._safe_channel_send(
                                dest,
                                content=out_text if out_text else None,
                                files=files if files else None,
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                        except Exception as e:
                            print(f"⚠️ Webhook/Thread-Fallback in #{_tgt_channel.name} fehlgeschlagen: {e}")
                    except Exception as e:
                        print(f"⚠️ Webhook-Senden in #{_tgt_channel.name} fehlgeschlagen: {e}")

                tasks.append(_one_target())
            if tasks:
                await asyncio.gather(*tasks)

    # -------------------- Commands --------------------

    @app_commands.command(name="langrelay_set", description="Setzt/ändert die Sprache eines Channels (persistiert).")
    @app_commands.describe(channel="Ziel-Textkanal", language="Sprachcode/Tag (z. B. DE, EN-GB, PT-BR, ZH, ZH-HANT …)")
    @admins_only()
    async def cmd_set(self, interaction: discord.Interaction, channel: discord.TextChannel, language: str):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)

        lang = normalize_code(language or "")
        targets = await _deepl_targets() if (DEEPL_TOKEN and self._provider(interaction.guild.id) == "deepl") else None
        lang = alias_for_provider(lang or "", targets or set())

        await self._ensure_cache(interaction.guild)
        mapping = self._mapping(interaction.guild.id)
        mapping[channel.name] = lang
        self._save_guild(interaction.guild.id)

        hint = "Hinweis: DeepL akzeptiert z. B. EN-GB/EN-US, PT-PT/PT-BR, ZH oder ZH-HANT."
        await interaction.response.send_message(
            f"✅ Mapping gesetzt: {channel.mention} → `{lang}` (gespeichert)\n_{hint}_",
            ephemeral=True
        )

    @cmd_set.autocomplete("language")
    async def ac_language(self, interaction: discord.Interaction, current: str):
        guild = interaction.guild
        gid = guild.id if guild else 0
        provider = self._provider(gid) if gid else "deepl"
        targets = None
        if provider == "deepl" and DEEPL_TOKEN:
            try:
                targets = await _deepl_targets()
            except Exception:
                targets = None

        items = suggest_codes(current, targets)
        out = []
        for code, label in items:
            aliased = alias_for_provider(code, targets or set())
            out.append(app_commands.Choice(name=f"{label} — {code}", value=aliased))
        return out[:20]

    @app_commands.command(name="langrelay_status", description="Zeigt Mappings, Provider & Optionen.")
    @admins_only()
    async def cmd_status(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        await self._ensure_cache(interaction.guild)
        mapping = self._mapping(interaction.guild.id)
        provider = self._provider(interaction.guild.id)
        opts = self._options(interaction.guild.id)

        lines = [f"**Provider:** `{provider}`", "", "**Mappings:**"]
        if mapping:
            for ch_name, code in mapping.items():
                ch = self._get_channel_by_name(interaction.guild.id, ch_name)
                if ch:
                    lines.append(f"• <#{ch.id}> → `{code}`")
                else:
                    lines.append(f"• `#{ch_name}` → `{code}` (❌ nicht gefunden)")
        else:
            lines.append("_keine Zuordnungen_")

        lines.append("\n**Optionen:**")
        lines.append(f"• replymode: `{'on' if opts.get('replymode') else 'off'}`")
        lines.append(f"• thread_mirroring: `{'on' if opts.get('thread_mirroring') else 'off'}`")

        desc = "\n".join(lines)
        embed = discord.Embed(title="LangRelay – Status", description=desc)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---- Mapping-Komfort ----

    @app_commands.command(name="langrelay_reload", description="Lädt die Channel-Liste neu (z. B. nach Umbenennen/Anlegen).")
    @admins_only()
    async def cmd_reload(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        await self._ensure_cache(interaction.guild)
        await interaction.response.send_message("🔁 Channel-Cache aktualisiert.", ephemeral=True)

    @app_commands.command(name="langrelay_remove", description="Entfernt die Zuordnung eines Channels (persistiert).")
    @admins_only()
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
    @admins_only()
    async def cmd_clear(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        self.guild_config.setdefault(
            interaction.guild.id,
            {"mapping": {}, "provider": DEFAULT_PROVIDER, "options": {"replymode": False, "thread_mirroring": False}}
        )
        self.guild_config[interaction.guild.id]["mapping"] = {}
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message("🧹 Alle Mappings gelöscht (gespeichert).", ephemeral=True)

    @app_commands.command(name="langrelay_help", description="Zeigt eine Übersicht aller LangRelay-Befehle.")
    @admins_only()
    async def cmd_help(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)

        desc = (
            "**Konfiguration:**\n"
            "• `/langrelay_set #channel <code>` — Sprache für Channel setzen\n"
            "• `/langrelay_remove #channel` — Mapping eines Channels entfernen\n"
            "• `/langrelay_clear` — Alle Mappings löschen\n"
            "• `/langrelay_reload` — Channel-Liste neu laden\n\n"
            "**Provider:**\n"
            "• `/langrelay_provider <deepl|openai>` — Übersetzungsprovider umschalten\n\n"
            "**Optionen:**\n"
            "• `/langrelay_replymode <on|off>` — Reply-Kontext anhängen oder nicht\n"
            "• `/langrelay_thread_mirroring <on|off>` — Threads spiegeln oder nicht\n\n"
            "**Status:**\n"
            "• `/langrelay_status` — Zeigt aktuelle Mappings, Provider & Optionen\n"
            "• `/langrelay_help` — Diese Übersicht\n\n"
            "_Alle Befehle können nur von Admins genutzt werden._"
        )

        embed = discord.Embed(
            title="LangRelay – Hilfe",
            description=desc,
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


    # ---- Provider-Switch ----

    @app_commands.command(name="langrelay_provider", description="Setzt den Übersetzungsprovider (deepl oder openai) für diese Guild.")
    @app_commands.choices(provider=[
        app_commands.Choice(name="DeepL", value="deepl"),
        app_commands.Choice(name="OpenAI", value="openai"),
    ])
    @admins_only()
    async def cmd_provider(self, interaction: discord.Interaction, provider: app_commands.Choice[str]):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        choice = provider.value
        if choice == "deepl" and not DEEPL_TOKEN:
            return await interaction.response.send_message("❌ DeepL ist nicht konfiguriert (DEEPL_TOKEN fehlt).", ephemeral=True)
        if choice == "openai" and not OPENAI_TOKEN:
            return await interaction.response.send_message("❌ OpenAI ist nicht konfiguriert (OPENAI_TOKEN fehlt).", ephemeral=True)

        self._set_provider(interaction.guild.id, choice)
        await interaction.response.send_message(f"✅ Provider gesetzt: `{choice}`", ephemeral=True)

    # ---- Option-Toggles ----

    @app_commands.command(name="langrelay_replymode", description="Reply-Kontext an/aus (persistiert).")
    @app_commands.choices(state=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
    ])
    @admins_only()
    async def cmd_replymode(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        opts = self._options(interaction.guild.id)
        opts["replymode"] = (state.value == "on")
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message(f"✅ replymode: `{state.value}`", ephemeral=True)

    @app_commands.command(name="langrelay_thread_mirroring", description="Thread-Mirroring an/aus (persistiert).")
    @app_commands.choices(state=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
    ])
    @admins_only()
    async def cmd_thread_mirroring(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        opts = self._options(interaction.guild.id)
        opts["thread_mirroring"] = (state.value == "on")
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message(f"✅ thread_mirroring: `{state.value}`", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LangRelay(bot))
