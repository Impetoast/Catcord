# 🤖 Thiscord – Discord Translation & Utility Bot

Thiscord is a modular Discord bot focused on **translation** (DeepL or OpenAI), **channel relaying**, and useful **utility functions**.  
It is written in **Python** using [discord.py](https://github.com/Rapptz/discord.py) and organized with **Cogs** for clarity.

---

## ✨ Features

- 🔄 **AutoTranslate (per-channel replies)**  
  Translate messages inside the same channel and reply with the translated text.

- 🌐 **LangRelay – Multi-language Channel Relay**  
  Messages posted in one language channel are automatically translated and relayed to all other configured language channels.  
  ➝ With per-guild **persistent storage** (`./data/langrelay/<guild_id>.json`).  
  ➝ **Translation provider can be switched between DeepL and OpenAI GPT models**.  
  ➝ **Access control**: Administrators can always configure. Additional roles or users can be whitelisted.

- 📝 **Manual Translation**  
  Slash commands for translating arbitrary text, detecting source languages, and listing supported languages.

- 📊 **Info**  
  Bot metadata: uptime, latency, versions, last sync.

- 🏓 **Ping**  
  Simple health check.

---

## ⚙️ Installation & Setup

1. **Clone the repository**
```bash
git clone https://github.com/<your-repo>/thiscord.git
cd thiscord
```

2. **Install dependencies**
```bash
# If requirements.txt exists
pip install -r requirements.txt

# Or manually
pip install discord.py httpx python-dotenv
```

3. **Configure environment variables (`.env`)**
```env
DISCORD_TOKEN=your-discord-bot-token

# At least one of the following:
DEEPL_TOKEN=your-deepl-api-key
OPENAI_TOKEN=your-openai-api-key

# Optional
GUILD_ID=123456789012345678     # for fast dev-server slash sync
DEEPL_API_URL=https://api.deepl.com/v2   # Pro endpoint (default = api-free)
OPENAI_MODEL=gpt-4o-mini        # override model (default = gpt-4o-mini)
```

4. **Run the bot**
```bash
python main.py
```

> Tested with **Python 3.12** (compatible with 3.11+).

---

## 🔨 Commands

### 🔄 AutoTranslate (inline)
- `/autotranslate_on target:<lang> [source:<lang>] [formality:<style>] [min_chars:<n>]`  
  Enables inline translation in the current channel.  
- `/autotranslate_off` – disable.  
- `/autotranslate_status` – show status.

---

### 🌐 LangRelay (cross-channel translation)

- `/langrelay_set channel:<#channel> language:<code>`  
  Map a text channel to a language.  
  ➝ Example: `/langrelay_set channel:#channel_de language:DE`

- `/langrelay_status`  
  Show current mappings, provider, and access list.

- `/langrelay_reload`  
  Rebuild channel cache (e.g. after renaming).

- `/langrelay_remove channel:<#channel>`  
  Remove a mapping.

- `/langrelay_clear`  
  Clear all mappings in the current guild.

- `/langrelay_provider provider:<deepl|openai>`  
  Switch the active translation provider for this guild.  
  - Requires appropriate API key in `.env`.  
  - Persisted per guild alongside the mappings.

📂 **Persistence**:  
- Stored per guild at `./data/langrelay/<guild_id>.json`  
- Example schema:
  ```json
  {
    "mapping": {
      "channel_de": "DE",
      "channel_en": "EN"
    },
    "provider": "openai",
    "access": {
      "roles": [123456789],
      "users": [234567890]
    }
  }
  ```

---

### 📝 Manual Translate

- `/translate text:<string> target:<lang> [source:<lang>] [formality:<style>]`  
  Translate any text.

- `/detect text:<string>`  
  Detect the language of a text.

- `/languages`  
  List supported languages.

---

### 📊 Info
- `/about` – show bot status, uptime, latency, versions.

---

### 🏓 Utility
- `/ping` – replies with “Pong!”.

---

### 🔒 Access Control

- **Administrators** can always configure LangRelay.  
- Additional **roles** and **users** can be whitelisted to allow them to configure too.  
- Non-whitelisted users will see the commands but get a clear **permission error message** when trying to use them.  
- **Access management commands** themselves are only visible to administrators.

#### Commands

- `/langrelay_access_status` – Show current whitelist.  
- `/langrelay_access_add_role role:@Mods` – Add a role.  
- `/langrelay_access_remove_role role:@Mods` – Remove a role.  
- `/langrelay_access_add_user user:@Alice` – Add a specific user.  
- `/langrelay_access_remove_user user:@Alice` – Remove a specific user.  
- `/langrelay_access_clear` – Clear whitelist (admins only remain).

---

## 📸 Examples

### `/langrelay_status`
```text
LangRelay – Status
Provider: openai

• #channel_de → DE
• #channel_en → EN
• #channel_fr → FR (❌ not found)

Access (in addition to Admin):
• Roles: @Mods
• Users: @Alice
```

### Relay translation example

User posts in `#channel_de`:
```text
Hallo zusammen! Wie geht’s?
```

Bot automatically posts in `#channel_en`:
```text
🌐 Max wrote in #channel_de:
> Hallo zusammen! Wie geht’s?

Translation → EN:
Hello everyone! How are you?

[Jump to original](https://discord.com/channels/...)
```

---

## 🛠️ Tech Stack

- Python 3.12+  
- [discord.py](https://github.com/Rapptz/discord.py)  
- [DeepL API](https://www.deepl.com/docs-api/) (optional)  
- [OpenAI API](https://platform.openai.com/docs/api-reference/chat) (optional)  
- Cogs for modularity  
- JSON persistence per guild (`./data/langrelay/`)

---

## 🚀 Roadmap

- [ ] Support for relaying attachments/links  
- [ ] Configurable formality setting for LangRelay (like manual translate)  
- [ ] Global defaults via config  
- [ ] Web dashboard for mapping & provider management  

---

## 📄 License

MIT License – see [LICENSE](LICENSE)
