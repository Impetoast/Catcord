# cogs/langrelay.py
from __future__ import annotations
import os
import re
import json
import io
import time
import asyncio
from typing import Dict, Optional, List, Any, Set, Tuple
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
import httpx

# Hilfsmodul mit Labels/Aliasen/Autocomplete (deins)
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

DEFAULT_PROVIDER = "openai" if OPENAI_TOKEN else ("deepl" if DEEPL_TOKEN else "openai")
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
        targets = {(item.get("language") or "").upper() for item in data if item.get("language")}
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
        if any(role.name.lower() == "moderator" for role in interaction.user.roles):
            return True
        raise app_commands.CheckFailure("Nur Administratoren oder Moderatoren dürfen diesen Befehl ausführen.")
    return app_commands.check(predicate)


class LangRelay(commands.Cog):
    """
    Spiegelt Nachrichten zwischen Sprachkanälen (Übersetzung, Webhook-Impersonation,
    klickbare Mentions ohne Ping).

    **Neu: Mehrere unabhängige Relay-Gruppen.**
    Beispiel:
      Gruppe "EU": #german ↔ #english ↔ #french
      Gruppe "LATAM": #spanish ↔ #portuguese-br ↔ #english-us

    Jede Gruppe ist voll bidirektional. Ein Channel darf in mehreren Gruppen sein;
    Pro Nachricht werden Duplikate pro Zielkanal vermieden.

    Persistenz pro Guild: ./data/langrelay/<guild_id>.json
    Struktur (vereinfacht):
    {
      "provider": "deepl|openai",
      "options": {"enabled": true, "replymode": false, "thread_mirroring": false, "reaction_mirroring": false},
      "groups": {
        "default": {"🇩🇪-german": "DE", "🇺🇸-english": "EN"},
        "americas": {"spanish": "ES", "english-us": "EN-US"}
      }
    }
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not DEEPL_TOKEN and not OPENAI_TOKEN:
            print("⚠️  Weder DEEPL_TOKEN noch OPENAI_TOKEN gesetzt – Übersetzung nicht möglich, bis einer vorhanden ist.")
        self.guild_config: Dict[int, Dict[str, Any]] = {}
        self._guild_channel_cache: Dict[int, Dict[str, discord.TextChannel]] = {}
        self._sem_per_guild: Dict[int, asyncio.Semaphore] = {}
        self._webhook_cache: Dict[int, Dict[int, discord.Webhook]] = {}
        self._relay_map: Dict[int, Dict[int, int]] = {}
        self._relay_lookup: Dict[int, int] = {}
        self._channel_locks: Dict[int, asyncio.Lock] = {}

    # -------------------- Persistence --------------------
    def _guild_path(self, guild_id: int) -> Path:
        return DATA_DIR / f"{guild_id}.json"

    def _ensure_blocks(self, cfg: Dict[str, Any]):
        # options
        opt = cfg.get("options") or {}
        cfg["options"] = {
            "enabled": bool(opt.get("enabled", True)),
            "replymode": bool(opt.get("replymode", False)),
            "thread_mirroring": bool(opt.get("thread_mirroring", False)),
            "reaction_mirroring": bool(opt.get("reaction_mirroring", False)),
        }
        # groups (Migration altes mapping -> default)
        groups = cfg.get("groups")
        if not isinstance(groups, dict):
            groups = {}
            cfg["groups"] = groups
        gopt = cfg.get("group_options")
        if not isinstance(gopt, dict):
            gopt = {}
            cfg["group_options"] = gopt

        # migrate legacy formats
        legacy = cfg.get("mapping")
        if isinstance(legacy, dict) and legacy:
            groups.setdefault("default", {})
            groups["default"].update({str(k): str(v) for k, v in legacy.items()})
            cfg.pop("mapping", None)

        # some previous versions mis-saved group entries as booleans in the
        # `groups` dict. Move such flags to `group_options` so real mappings
        # remain in `groups`.
        for gname, val in list(groups.items()):
            if isinstance(val, bool):
                gopt[gname] = bool(val)
                groups.pop(gname, None)

        # sanitize groups in-place
        new_groups = {
            str(g): {str(ch): str(code) for ch, code in channels.items()}
            for g, channels in groups.items() if isinstance(channels, dict)
        }
        groups.clear()
        groups.update(new_groups)

        # group options
        new_opts = {str(g): bool(v) for g, v in gopt.items()}
        gopt.clear()
        gopt.update(new_opts)
        # provider
        prov = cfg.get("provider")
        cfg["provider"] = prov if prov in {"deepl", "openai"} else DEFAULT_PROVIDER

    def _load_guild(self, guild: discord.Guild):
        p = self._guild_path(guild.id)
        data: Dict[str, Any] = {}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"⚠️ Konnte Konfiguration für {guild.name} nicht laden: {e} → verwende Defaults")
        self._ensure_blocks(data)
        self.guild_config[guild.id] = {
            "provider": data.get("provider", DEFAULT_PROVIDER),
            "options": data.get("options", {"enabled": True, "replymode": False, "thread_mirroring": False, "reaction_mirroring": False}),
            "groups": data.get("groups", {}),
            "group_options": data.get("group_options", {}),
        }
        self._save_guild(guild.id)

    def _save_guild(self, guild_id: int):
        cfg = self.guild_config.setdefault(guild_id, {
            "provider": DEFAULT_PROVIDER,
            "options": {"enabled": True, "replymode": False, "thread_mirroring": False, "reaction_mirroring": False},
            "groups": {},
            "group_options": {},
        })
        self._ensure_blocks(cfg)
        try:
            self._guild_path(guild_id).write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"⚠️ Konnte Konfiguration für Guild {guild_id} nicht speichern: {e}")

    # ---- accessors ----

    def _group_choice_list(self, guild: discord.Guild, current: str):
        groups = self._groups(guild.id)
        keys = sorted(groups.keys())
        if current:
            keys = [g for g in keys if current.lower() in g.lower()]
        return [app_commands.Choice(name=g, value=g) for g in keys][:25]

    def _groups(self, guild_id: int) -> Dict[str, Dict[str, str]]:
        cfg = self.guild_config.setdefault(guild_id, {
            "provider": DEFAULT_PROVIDER,
            "options": {"enabled": True, "replymode": False, "thread_mirroring": False, "reaction_mirroring": False},
            "groups": {},
            "group_options": {},
        })
        self._ensure_blocks(cfg)
        return cfg["groups"]

    def _provider(self, guild_id: int) -> str:
        return self.guild_config.setdefault(guild_id, {
            "provider": DEFAULT_PROVIDER,
            "options": {"enabled": True, "replymode": False, "thread_mirroring": False, "reaction_mirroring": False},
            "groups": {},
            "group_options": {},
        })["provider"]

    def _set_provider(self, guild_id: int, provider: str):
        self.guild_config.setdefault(guild_id, {"provider": DEFAULT_PROVIDER})["provider"] = provider
        self._save_guild(guild_id)

    def _options(self, guild_id: int) -> Dict[str, bool]:
        cfg = self.guild_config.setdefault(guild_id, {
            "provider": DEFAULT_PROVIDER,
            "options": {"enabled": True, "replymode": False, "thread_mirroring": False, "reaction_mirroring": False},
            "groups": {},
            "group_options": {},
        })
        self._ensure_blocks(cfg)
        return cfg["options"]

    def _group_options(self, guild_id: int) -> Dict[str, bool]:
        cfg = self.guild_config.setdefault(guild_id, {
            "provider": DEFAULT_PROVIDER,
            "options": {"enabled": True, "replymode": False, "thread_mirroring": False, "reaction_mirroring": False},
            "groups": {},
            "group_options": {},
        })
        self._ensure_blocks(cfg)
        return cfg["group_options"]

    # Helper: normalize language codes like "en_gb" -> "EN-GB"
    def _norm(code: Optional[str]) -> Optional[str]:
        return code.strip().upper().replace("_", "-") if code else None

    # -------------------- Caching / Semaphores --------------------
    def _sem(self, guild_id: int) -> asyncio.Semaphore:
        if guild_id not in self._sem_per_guild:
            self._sem_per_guild[guild_id] = asyncio.Semaphore(2)
        return self._sem_per_guild[guild_id]

    def _channel_lock(self, channel_id: int) -> asyncio.Lock:
        lock = self._channel_locks.get(channel_id)
        if lock is None:
            lock = asyncio.Lock()
            self._channel_locks[channel_id] = lock
        return lock

    def _ensure_config_loaded(self, guild: discord.Guild):
        if guild.id not in self.guild_config:
            self._load_guild(guild)

    async def _ensure_cache(self, guild: discord.Guild, *, refresh: bool = False):
        if refresh or guild.id not in self._guild_channel_cache:
            self._guild_channel_cache[guild.id] = {ch.name: ch for ch in guild.text_channels}
        self._ensure_config_loaded(guild)

    def _get_channel_by_name(self, guild_id: int, name: str) -> Optional[discord.TextChannel]:
        return (self._guild_channel_cache.get(guild_id) or {}).get(name)

    # -------------------- Mentions: klickbar ohne Ping --------------------
    async def _resolve_mentions(self, message: discord.Message) -> str:
        """
        Bevorzuge Discord-Tokens (<@id>, <@&id>, <#id>) → klickbar;
        Fallback auf @Name/#kanal. (AllowedMentions.none() verhindert Pings)
        """
        text = message.content or ""
        guild = message.guild
        if not guild or not text:
            return text

        # evtl. Zero-width entfernen
        for z in ("\u200b", "\u200e", "\u200f", "\u2060"):
            text = text.replace(z, "")

        user_ids = {int(m) for m in re.findall(r"<@!?\u200b*([0-9]+)>", text)}
        role_ids = {int(m) for m in re.findall(r"<@&\u200b*([0-9]+)>", text)}
        chan_ids = {int(m) for m in re.findall(r"<#\u200b*([0-9]+)>", text)}

        user_map = {m.id: m.display_name for m in getattr(message, "mentions", [])}
        role_map = {r.id: r.name for r in getattr(message, "role_mentions", [])}
        chan_map = {c.id: c.name for c in getattr(message, "channel_mentions", [])}

        # fehlende Namen bestmöglich nachladen
        for uid in [u for u in user_ids if u not in user_map]:
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
            return None
        return await webhook.send(wait=True, **kwargs)

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
            return None
        return await dest.send(**kwargs)

    # -------------------- Thread-Hilfen --------------------
    async def _get_or_create_target_thread(
        self, base_channel: discord.TextChannel, thread_name: str, auto_archive_duration: int = 10080
    ) -> Optional[discord.Thread]:
        try:
            for th in base_channel.threads:
                if th.name == thread_name and not th.archived:
                    return th
            archived = []
            try:
                async for th in base_channel.archived_threads(private=False, limit=50):
                    archived.append(th)
            except Exception:
                pass
            for th in archived:
                if th.name == thread_name:
                    try:
                        await th.edit(archived=False, locked=False)
                        return th
                    except Exception:
                        pass
            return await base_channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.public_thread,
                auto_archive_duration=auto_archive_duration,
                reason="LangRelay Thread-Mirroring",
            )
        except discord.Forbidden:
            print(f"⚠️ Keine Berechtigung zum Erstellen von Threads in #{base_channel.name}.")
            return None
        except Exception as e:
            print(f"⚠️ Thread-Erstellung in #{base_channel.name} fehlgeschlagen: {e}")
            return None

    # -------------------- Listeners --------------------
    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self._ensure_cache(guild, refresh=True)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await self._ensure_cache(guild, refresh=True)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        if isinstance(channel, discord.TextChannel):
            await self._ensure_cache(channel.guild, refresh=True)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        if isinstance(after, discord.TextChannel):
            await self._ensure_cache(after.guild, refresh=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Scope / Schutz
        if message.author.bot or not message.guild:
            return
        if message.webhook_id is not None:
            return
        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return
        if not message.content and not message.attachments:
            return

        guild = message.guild
        self._ensure_config_loaded(guild)
        groups = self._groups(guild.id)
        gopts = self._group_options(guild.id)
        opts = self._options(guild.id)

        if not opts.get("enabled", True):
            return

        replymode = bool(opts.get("replymode", False))
        thread_mirroring = bool(opts.get("thread_mirroring", False))

        # Quelle: TextChannel oder Thread?
        src_thread: Optional[discord.Thread] = None
        if isinstance(message.channel, discord.Thread):
            src_thread = message.channel
            parent = src_thread.parent
            if not isinstance(parent, discord.TextChannel):
                return
            src_channel = parent
        else:
            src_channel = message.channel

        # Alle Gruppen finden, in denen der Quellkanal Mitglied ist
        src_groups: List[str] = [
            gname for gname, chans in groups.items()
            if src_channel.name in chans and gopts.get(gname, True)
        ]
        if not src_groups:
            return  # kein Relay-Channel

        display_name = message.author.display_name
        try:
            avatar_url = (message.author.display_avatar or message.author.avatar).url
        except Exception:
            avatar_url = None

        base_text = await self._resolve_mentions(message)

        lock = self._channel_lock(message.channel.id)
        async with lock:
            async with self._sem(guild.id):
                sent_to: Set[int] = set()
                tasks = []
                links: List[Tuple[int, int]] = [(message.channel.id, message.id)]
                for gname in src_groups:
                    chans = groups.get(gname, {})
                    src_lang = chans.get(src_channel.name)
                    for tgt_name, tgt_lang in chans.items():
                        if tgt_name == src_channel.name:
                            continue
                        tgt_channel = self._get_channel_by_name(guild.id, tgt_name)
                        if not tgt_channel or tgt_channel.id in sent_to:
                            continue
                        sent_to.add(tgt_channel.id)

                        async def _one(_tgt=tgt_channel, _tgt_lang=tgt_lang, _src_lang=src_lang):
                            out_text = base_text
                            try:
                                if out_text and _tgt_lang:
                                    out_text = await self._translate(out_text, _tgt_lang, _src_lang, guild.id)
                            except Exception as e:
                                print(f"⚠️ Übersetzung fehlgeschlagen ({src_channel.name} → {_tgt.name}): {e}")
                                out_text = base_text

                            if replymode and message.reference and isinstance(message.reference.resolved, discord.Message):
                                replied = message.reference.resolved
                                replied_clean = await self._resolve_mentions(replied)
                                preview = (replied_clean[:90] + "…") if len(replied_clean) > 90 else replied_clean
                                ctx = f"(reply to {replied.author.display_name}: {preview})"
                                try:
                                    ctx_tr = await self._translate(ctx, _tgt_lang, _src_lang, guild.id)
                                except Exception:
                                    ctx_tr = ctx
                                out_text = f"{out_text}\n\n> {ctx_tr}" if out_text else f"> {ctx_tr}"

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

                            target_thread: Optional[discord.Thread] = None
                            if thread_mirroring and src_thread is not None:
                                target_thread = await self._get_or_create_target_thread(
                                    base_channel=_tgt,
                                    thread_name=src_thread.name,
                                    auto_archive_duration=src_thread.auto_archive_duration or 1440
                                )

                            webhook = await self._get_or_create_webhook(_tgt)
                            dest = target_thread or _tgt
                            sent_msg: Optional[discord.Message] = None
                            if not webhook:
                                try:
                                    sent_msg = await self._safe_channel_send(
                                        dest,
                                        content=out_text or None,
                                        files=files or None,
                                        allowed_mentions=discord.AllowedMentions.none(),
                                    )
                                except Exception as e:
                                    print(f"⚠️ Nachricht in #{_tgt.name} konnte nicht gesendet werden: {e}")
                                else:
                                    if sent_msg:
                                        links.append((dest.id, sent_msg.id))
                                return

                            try:
                                sent_msg = await self._safe_webhook_send(
                                    webhook,
                                    content=out_text or None,
                                    files=files or None,
                                    allowed_mentions=discord.AllowedMentions.none(),  # klickbar, stumm
                                    thread=target_thread,
                                    username=display_name,
                                    avatar_url=avatar_url,
                                )
                            except TypeError:
                                # ältere discord.py ohne thread=
                                try:
                                    sent_msg = await self._safe_channel_send(
                                        dest,
                                        content=out_text or None,
                                        files=files or None,
                                        allowed_mentions=discord.AllowedMentions.none(),
                                    )
                                except Exception as e:
                                    print(f"⚠️ Webhook/Thread-Fallback in #{_tgt.name} fehlgeschlagen: {e}")
                            except Exception as e:
                                print(f"⚠️ Webhook-Senden in #{_tgt.name} fehlgeschlagen: {e}")
                            if sent_msg:
                                links.append((dest.id, sent_msg.id))

                        tasks.append(_one())
                if tasks:
                    await asyncio.gather(*tasks)
                    if len(links) > 1:
                        self._relay_map[message.id] = {ch: mid for ch, mid in links}
                        for ch, mid in links:
                            self._relay_lookup[mid] = message.id

    async def _mirror_reaction(self, payload: discord.RawReactionActionEvent, adding: bool):
        if payload.user_id == self.bot.user.id:
            return
        if not payload.guild_id or not self._options(payload.guild_id).get("reaction_mirroring"):
            return
        root_id = self._relay_lookup.get(payload.message_id)
        if not root_id:
            return
        channel_map = self._relay_map.get(root_id)
        if not channel_map:
            return
        for ch_id, msg_id in channel_map.items():
            if ch_id == payload.channel_id and msg_id == payload.message_id:
                continue
            channel = self.bot.get_channel(ch_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(ch_id)
                except Exception:
                    continue
            try:
                msg = await channel.fetch_message(msg_id)
            except Exception:
                continue
            try:
                if adding:
                    await msg.add_reaction(payload.emoji)
                else:
                    await msg.remove_reaction(payload.emoji, self.bot.user)
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._mirror_reaction(payload, True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._mirror_reaction(payload, False)

    # -------------------- Commands --------------------

    @app_commands.command(name="langrelay_status", description="Zeigt Provider, Optionen & Gruppen.")
    @admins_only()
    async def cmd_status(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        self._ensure_config_loaded(interaction.guild)
        provider = self._provider(interaction.guild.id)
        opts = self._options(interaction.guild.id)
        groups = self._groups(interaction.guild.id)
        gopts = self._group_options(interaction.guild.id)

        lines = [f"**Provider:** `{provider}`", "", "**Gruppen:**"]
        if not groups:
            lines.append("_keine definiert_ (nutze /langrelay_group_create)\n")
        else:
            for gname, chans in groups.items():
                state = "on" if gopts.get(gname, True) else "off"
                lines.append(f"• **{gname}** (`{state}`):")
                for ch_name, code in chans.items():
                    ch_obj = self._get_channel_by_name(interaction.guild.id, ch_name)
                    lines.append(f"  - {(f'<#{ch_obj.id}>' if ch_obj else f'#{ch_name}') } → `{code}`")
                lines.append("")
        lines.append("**Optionen:**")
        lines.append(f"• power: `{'on' if opts.get('enabled', True) else 'off'}`")
        lines.append(f"• replymode: `{'on' if opts.get('replymode') else 'off'}`")
        lines.append(f"• thread_mirroring: `{'on' if opts.get('thread_mirroring') else 'off'}`")
        lines.append(f"• reaction_mirroring: `{'on' if opts.get('reaction_mirroring') else 'off'}`")
        embed = discord.Embed(title="LangRelay – Status", description="\n".join(lines))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="langrelay_group_power", description="Schaltet das Relaying für eine Gruppe an/aus.")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"),
                                 app_commands.Choice(name="off", value="off")])
    @admins_only()
    async def cmd_group_power(self, interaction: discord.Interaction, group: str, state: app_commands.Choice[str]):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        self._ensure_config_loaded(interaction.guild)
        groups = self._groups(interaction.guild.id)
        if group not in groups:
            return await interaction.response.send_message(f"ℹ️ Gruppe **{group}** nicht gefunden.", ephemeral=True)
        gopts = self._group_options(interaction.guild.id)
        gopts[group] = (state.value == "on")
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message(
            f"🔌 Gruppe **{group}** ist jetzt **{state.value.upper()}**.", ephemeral=True
        )

    @cmd_group_power.autocomplete("group")
    async def ac_group_power(self, interaction: discord.Interaction, current: str):
        if not interaction.guild:
            return []
        self._ensure_config_loaded(interaction.guild)
        return self._group_choice_list(interaction.guild, current)

    # ---- Gruppen-Management ----
    @app_commands.command(name="langrelay_group_create", description="Erstellt eine neue Relay-Gruppe.")
    @admins_only()
    async def cmd_group_create(self, interaction: discord.Interaction, name: str):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        name = name.strip()
        self._ensure_config_loaded(interaction.guild)
        groups = self._groups(interaction.guild.id)
        gopts = self._group_options(interaction.guild.id)
        if name in groups:
            return await interaction.response.send_message(f"ℹ️ Gruppe **{name}** existiert bereits.", ephemeral=True)
        groups[name] = {}
        gopts[name] = True
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message(f"✅ Gruppe **{name}** erstellt.", ephemeral=True)

    @app_commands.command(name="langrelay_group_delete", description="Löscht eine Relay-Gruppe samt Zuordnungen.")
    @admins_only()
    async def cmd_group_delete(self, interaction: discord.Interaction, group: str):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)

        self._ensure_config_loaded(interaction.guild)
        groups = self._groups(interaction.guild.id)
        gopts = self._group_options(interaction.guild.id)
        if group not in groups:
            return await interaction.response.send_message(f"ℹ️ Gruppe **{group}** nicht gefunden.", ephemeral=True)

        groups.pop(group, None)
        gopts.pop(group, None)
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message(f"🗑️ Gruppe **{group}** gelöscht.", ephemeral=True)

    @cmd_group_delete.autocomplete("group")
    async def ac_group_delete(self, interaction: discord.Interaction, current: str):
        if not interaction.guild:
            return []
        self._ensure_config_loaded(interaction.guild)
        return self._group_choice_list(interaction.guild, current)
    @app_commands.command(name="langrelay_group_add", description="Fügt Channel+Sprachcode zu einer Gruppe hinzu.")
    @app_commands.describe(group="Gruppenname", channel="Textkanal", language="DeepL Sprachcode, z. B. DE, EN, EN-GB …")
    @admins_only()
    async def cmd_group_add(self, interaction: discord.Interaction, group: str, channel: discord.TextChannel,
                            language: str):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)

        lang = (language or "").strip().upper().replace("_", "-")
        # keine SUPPORTED_TARGETS-Prüfung → akzeptiert z. B. EN-AU, EN-IN etc.

        self._ensure_config_loaded(interaction.guild)
        groups = self._groups(interaction.guild.id)
        gopts = self._group_options(interaction.guild.id)
        if group not in groups:
            groups[group] = {}
        gopts.setdefault(group, True)

        # speichere wie gehabt (Name oder ID – je nach deinem aktuellen Modell)
        groups[group][channel.name] = lang
        self._save_guild(interaction.guild.id)

        await interaction.response.send_message(
            f"✅ Gruppe **{group}**: {channel.mention} → `{lang}` hinzugefügt.", ephemeral=True)

    @cmd_group_add.autocomplete("group")
    async def ac_group_add(self, interaction: discord.Interaction, current: str):
        if not interaction.guild:
            return []
        self._ensure_config_loaded(interaction.guild)
        return self._group_choice_list(interaction.guild, current)

    @cmd_group_add.autocomplete("language")
    async def ac_group_lang(self, interaction: discord.Interaction, current: str):
        # schicke die gleichen Vorschläge wie bei /set, nur ohne /set zu brauchen
        if interaction.guild:
            self._ensure_config_loaded(interaction.guild)
        provider = self._provider(interaction.guild.id) if interaction.guild else "deepl"
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

    @app_commands.command(name="langrelay_group_remove", description="Entfernt einen Channel aus einer Gruppe.")
    @admins_only()
    async def cmd_group_remove(self, interaction: discord.Interaction, group: str, channel: discord.TextChannel):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)

        self._ensure_config_loaded(interaction.guild)
        groups = self._groups(interaction.guild.id)
        gopts = self._group_options(interaction.guild.id)
        if group not in groups or channel.name not in groups[group]:
            return await interaction.response.send_message("ℹ️ Eintrag nicht gefunden.", ephemeral=True)

        groups[group].pop(channel.name, None)
        if not groups[group]:
            groups.pop(group, None)
            gopts.pop(group, None)
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message(f"🗑️ Aus Gruppe **{group}** entfernt: {channel.mention}",
                                                ephemeral=True)

    @cmd_group_remove.autocomplete("group")
    async def ac_group_remove(self, interaction: discord.Interaction, current: str):
        if not interaction.guild:
            return []
        self._ensure_config_loaded(interaction.guild)
        return self._group_choice_list(interaction.guild, current)
    @app_commands.command(name="langrelay_group_list", description="Listet alle Gruppen und Zuordnungen.")
    @admins_only()
    async def cmd_group_list(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        self._ensure_config_loaded(interaction.guild)
        groups = self._groups(interaction.guild.id)
        gopts = self._group_options(interaction.guild.id)
        if not groups:
            return await interaction.response.send_message("_Keine Gruppen definiert._", ephemeral=True)
        lines = []
        for gname, chans in groups.items():
            state = "on" if gopts.get(gname, True) else "off"
            lines.append(f"**{gname}** (`{state}`)")
            for ch, code in chans.items():
                ch_obj = self._get_channel_by_name(interaction.guild.id, ch)
                lines.append(f"• {(ch_obj.mention if ch_obj else '#'+ch)} → `{code}`")
            lines.append("")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ---- Provider & Optionen ----
    @app_commands.command(name="langrelay_power", description="Schaltet das Relaying serverweit an/aus.")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"),
                                 app_commands.Choice(name="off", value="off")])
    @admins_only()
    async def cmd_power(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        self._ensure_config_loaded(interaction.guild)
        opts = self._options(interaction.guild.id)
        opts["enabled"] = (state.value == "on")
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message(
            f"🔌 LangRelay is now **{state.value.upper()}**.", ephemeral=True
        )

    @app_commands.command(name="langrelay_provider", description="Setzt den Übersetzungsprovider (deepl|openai).")
    @app_commands.choices(provider=[
        app_commands.Choice(name="DeepL", value="deepl"),
        app_commands.Choice(name="OpenAI", value="openai"),
    ])
    @admins_only()
    async def cmd_provider(self, interaction: discord.Interaction, provider: app_commands.Choice[str]):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        self._ensure_config_loaded(interaction.guild)
        choice = provider.value
        if choice == "deepl" and not DEEPL_TOKEN:
            return await interaction.response.send_message("❌ DeepL ist nicht konfiguriert (DEEPL_TOKEN fehlt).", ephemeral=True)
        if choice == "openai" and not OPENAI_TOKEN:
            return await interaction.response.send_message("❌ OpenAI ist nicht konfiguriert (OPENAI_TOKEN fehlt).", ephemeral=True)
        self._set_provider(interaction.guild.id, choice)
        await interaction.response.send_message(f"✅ Provider gesetzt: `{choice}`", ephemeral=True)

    @app_commands.command(name="langrelay_replymode", description="Reply-Kontext an/aus (persistiert).")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    @admins_only()
    async def cmd_replymode(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        self._ensure_config_loaded(interaction.guild)
        self._options(interaction.guild.id)["replymode"] = (state.value == "on")
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message(f"✅ replymode: `{state.value}`", ephemeral=True)

    @app_commands.command(name="langrelay_thread_mirroring", description="Thread-Mirroring an/aus (persistiert).")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    @admins_only()
    async def cmd_thread_mirroring(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        self._ensure_config_loaded(interaction.guild)
        self._options(interaction.guild.id)["thread_mirroring"] = (state.value == "on")
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message(f"✅ thread_mirroring: `{state.value}`", ephemeral=True)

    @app_commands.command(name="langrelay_reaction_mirroring", description="Reaktions-Mirroring an/aus (persistiert).")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    @admins_only()
    async def cmd_reaction_mirroring(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur in Servern nutzbar.", ephemeral=True)
        self._ensure_config_loaded(interaction.guild)
        self._options(interaction.guild.id)["reaction_mirroring"] = (state.value == "on")
        self._save_guild(interaction.guild.id)
        await interaction.response.send_message(f"✅ reaction_mirroring: `{state.value}`", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(LangRelay(bot))
