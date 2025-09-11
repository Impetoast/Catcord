# 🐱 Catcord

Catcord is a Discord bot that enables **seamless multilingual communication**.  
It mirrors and translates messages across language-specific channels, using **webhooks** to re-post messages with the **original author’s name and avatar** – making the relay invisible to regular users.

---

## ✨ Features

- 🌐 **Cross-Channel Relay**  
  Messages in one channel are translated and mirrored into all other mapped channels.

- 🧩 **Relay Groups** *(new)*  
  Define multiple independent groups of channels (e.g., EU, LATAM). Messages relay only within the same group.

- 🔌 **Power Switch** *(new)*
  Turn relaying on/off per server via a slash command.
- 🔀 **Group Power** *(new)*
  Enable or disable relaying for individual relay groups.

- 🕵️ **Invisible Posting**  
  Webhooks make mirrored messages look like they were sent by the original user (no BOT tag).

- 🈺 **Automatic Translation**  
  Supports [DeepL](https://www.deepl.com/) and [OpenAI](https://platform.openai.com/) as providers.

- 🧵 **Thread Mirroring**  
  Optionally mirror messages inside threads with the same name across language channels.

- 💬 **Reply Context**  
  Optionally append a translated line showing the original reply for clarity.

- 📎 **Attachment Support**
  Files and images are re-uploaded along with translated messages.

- 💾 **Persistent Configuration**
  Per-guild config is stored in `./data/langrelay/<guild_id>.json`.

- 🔒 **Guild Restriction**
  Optionally limit command execution to a specific guild via `GUILD_ID` in `.env`.

---

## ⚙️ Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourname/catcord.git
   cd catcord
   ```

2. Create and activate a virtual environment (recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate   # Linux/macOS
   venv\Scripts\activate      # Windows
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Create a `.env` file with your credentials:
   ```env
   DISCORD_TOKEN=your-discord-bot-token
   DEEPL_TOKEN=your-deepl-key   # optional
   OPENAI_TOKEN=your-openai-key # optional
   LANGRELAY_WEBHOOK_NAME=Catcord
   GUILD_ID=123456789012345678  # optional, restricts bot to this guild
   ```

   If `GUILD_ID` is set, Catcord will only respond to commands in the specified guild.

5. Run the bot:
   ```bash
   python main.py
   ```

---

## 📂 Project Structure

```
.
├── cogs/               # Bot cogs (features split by module)
│   ├── autotranslate.py
│   ├── translate.py
│   ├── langrelay.py
│   ├── info.py
│   └── ping.py
├── data/               # Persistent guild configuration
├── main.py             # Bot entry point
├── .env                # Environment variables
├── requirements.txt    # Python dependencies
└── README.md
```

---

## 🔧 Commands (highlights)

- `/langrelay_status` – Show provider, groups, and options.  
- `/langrelay_power state:<on|off>` – Enable/disable relaying for this server.  *(new)*  
- `/langrelay_provider provider:<deepl|openai>` – Select translation provider.  
- `/langrelay_replymode state:<on|off>` – Toggle reply context.
- `/langrelay_thread_mirroring state:<on|off>` – Toggle thread mirroring.
- `/langrelay_reaction_mirroring state:<on|off>` – Toggle reaction mirroring (off by default).

**Groups (new):**
- `/langrelay_group_create name:<group>` – Create a relay group.  
- `/langrelay_group_add group:<group> channel:<#> language:<code>` – Add a channel with a language code (e.g., `DE`, `EN`, `EN-GB`, `ES`, `PT-BR`, `ZH`).  
- `/langrelay_group_remove group:<group> channel:<#>` – Remove a channel from a group.
- `/langrelay_group_list` – List all groups and mappings.
- `/langrelay_group_delete name:<group>` – Delete a group.
- `/langrelay_group_power group:<group> state:<on|off>` – Enable/disable relaying for a group.

**Other:**
- `/translate <text>` – Translate text manually.  
- `/detect <text>` – Detect the language of a given text.  
- `/languages` – List available translation languages.  
- `/ping` – Check bot responsiveness.  
- `/about` – Show bot info.

> Removed legacy mapping commands: `/langrelay_set`, `/langrelay_remove`, `/langrelay_clear`, `/langrelay_reload`, `/langrelay_help`.

---

## ⏰ Reminders

Use `/reminder` commands to schedule repeating messages.

**Syntax**

```
/reminder add name:<id> interval:<number> unit:<minutes|hours|days> channel:<#channel> message:<text> [weekday:<day>] [time:<HH:MM>]
```

**Examples**

- `/reminder add name:backup interval:1 unit:days channel:#general message:"Run backup" time:02:00`
- `/reminder remove name:backup`
- `/reminder list`

Reminders persist across bot restarts and are stored in `reminders.json`.

*Permissions*: members need **Use Application Commands** to create or remove reminders, and the bot must have **Send Messages** in the target channel.

---

## 🛠️ Troubleshooting

- **Nothing is mirrored** → Ensure groups are configured (`/langrelay_group_list`) and **Manage Webhooks** permission is granted.  
- **Translation fails** → Verify API keys and provider choice.  
- **Thread mirroring not working** → Check that the bot has **Create Public Threads** permission.
- **Reaction mirroring not working** → Ensure the option is enabled: `/langrelay_reaction_mirroring state:on`.
- **No relays at all** → Check that power is **on**: `/langrelay_power state:on`.
- **Mentions** → Mentions are sanitized to prevent cross-channel pings.  

---

## 📖 Cogs Overview

- **autotranslate.py** – Automatically translates incoming messages in a single channel.  
- **translate.py** – Provides slash commands for manual translation, detection, and listing languages.  
- **langrelay.py** – Core feature: cross-channel relay with translation, webhooks, thread and reaction mirroring, groups, power.
- **info.py** – Displays bot information (`/about`).  
- **ping.py** – Simple connectivity test (`/ping`).  

---

## 📄 License

MIT License
