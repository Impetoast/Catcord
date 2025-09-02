# 🐱 Catcord

Catcord is a Discord bot that enables **seamless multilingual communication**.  
It mirrors and translates messages across language-specific channels, using **webhooks** to re-post messages with the **original author’s name and avatar** – making the relay invisible to regular users.

---

## ✨ Features

- 🌐 **Cross-Channel Relay**  
  Messages in one channel are translated and mirrored into all other mapped channels.

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

- 🔒 **Access Control**  
  Admins can always configure Catcord. Additional roles/users can be whitelisted.

- 💾 **Persistent Configuration**  
  Per-guild config is stored in `./data/langrelay/<guild_id>.json`.

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
   ```

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

- `/langrelay_set channel:<#> language:<code>` – Map a channel to a language code (EN, DE, FR, ...).  
- `/langrelay_status` – Show mappings, provider, and options.  
- `/langrelay_provider <deepl|openai>` – Select translation provider.  
- `/langrelay_replymode <on|off>` – Toggle reply context.  
- `/langrelay_thread_mirroring <on|off>` – Toggle thread mirroring.  
- `/langrelay_remove` / `/langrelay_clear` – Remove mappings.  
- `/langrelay_access_*` – Manage whitelist for roles and users.  
- `/translate <text>` – Translate text manually.  
- `/detect <text>` – Detect the language of a given text.  
- `/languages` – List available translation languages.  
- `/ping` – Check bot responsiveness.  
- `/about` – Show bot info.

---

## 🛠️ Troubleshooting

- **Nothing is mirrored** → Ensure channels are mapped with `/langrelay_status` and the bot has **Manage Webhooks**.  
- **Translation fails** → Verify API keys and provider choice.  
- **Thread mirroring not working** → Check that the bot has **Create Public Threads** permission.  
- **Mentions** → Mentions are sanitized to prevent cross-channel pings.  

---

## 📖 Cogs Overview

- **autotranslate.py** – Automatically translates incoming messages in a single channel.  
- **translate.py** – Provides slash commands for manual translation, detection, and listing languages.  
- **langrelay.py** – Core feature: cross-channel relay with translation, webhooks, thread mirroring.  
- **info.py** – Displays bot information (`/about`).  
- **ping.py** – Simple connectivity test (`/ping`).  

---

## 📄 License

MIT License
