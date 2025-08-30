# 🤖 Thiscord – Discord Translation & Utility Bot

Thiscord ist ein modular aufgebauter Discord-Bot mit Fokus auf **Übersetzung** (DeepL), **Channel-Spiegelung** und nützliche **Utility-Funktionen**.  
Er ist vollständig in **Python** mit [discord.py](https://github.com/Rapptz/discord.py) implementiert und nutzt **Cogs** für eine klare Struktur.

---

## ✨ Features

- 🔄 **Automatische Übersetzung** (`cogs/autotranslate.py`)  
  Übersetzt Nachrichten im gleichen Channel (Antwort mit Übersetzung).

- 🌐 **LangRelay – Sprach-Channel-Spiegelung** (`cogs/langrelay.py`)  
  Nachrichten in einem Sprach-Channel werden automatisch in alle anderen gemappten Channels übersetzt und gespiegelt.  
  ➝ mit persistenter Speicherung pro Guild (`./data/langrelay/<guild_id>.json`).

- 📝 **Manuelle Übersetzung** (`cogs/translate.py`)  
  Slash-Commands zum Übersetzen von Texten, Erkennen der Sprache und Auflisten aller verfügbaren DeepL-Sprachen.

- 📊 **Info** (`cogs/info.py`)  
  Zeigt Bot-Metadaten: Uptime, Latenz, Versionen, letzter Sync.

- 🏓 **Ping** (`cogs/ping.py`)  
  Einfacher Check, ob der Bot reagiert.

---

## ⚙️ Installation & Setup

1. **Repository klonen**
```bash
git clone https://github.com/<dein-repo>/thiscord.git
cd thiscord
```

2. **Dependencies installieren**
```bash
# Wenn eine requirements.txt vorhanden ist
pip install -r requirements.txt

# Alternativ (direkt)
pip install discord.py httpx python-dotenv
```

3. **.env Datei anlegen**
```env
DISCORD_TOKEN=dein-discord-bot-token
DEEPL_TOKEN=dein-deepl-api-key
# Optional: GUILD_ID=123456789012345678  (für schnellen Command-Sync im Dev-Server)
# Optional: DEEPL_API_URL=https://api.deepl.com/v2  (für Pro; Default ist https://api-free.deepl.com/v2)
```

4. **Bot starten**
```bash
python main.py
```

> Getestet mit **Python 3.12** (kompatibel zu 3.11+).

---

## 🔨 Commands

### 🔄 AutoTranslate (Channel-Reply)
- `/autotranslate_on target:<lang> [source:<lang>] [formality:<style>] [min_chars:<n>]`  
  Aktiviert Übersetzung im Channel (Antwort mit Übersetzung).  
- `/autotranslate_off` – deaktiviert.  
- `/autotranslate_status` – zeigt aktuellen Status.

---

### 🌐 LangRelay (Channel-Spiegelung)

- `/langrelay_set channel:<#channel> language:<code>`  
  Mapping für einen Channel setzen/ändern.  
  ➝ Beispiel: `/langrelay_set channel:#channel_de language:DE`

- `/langrelay_status`  
  Zeigt die aktuelle Zuordnung.

- `/langrelay_reload`  
  Baut Channel-Cache neu auf.

- `/langrelay_remove channel:<#channel>`  
  Entfernt Mapping.

- `/langrelay_clear`  
  Löscht alle Mappings dieser Guild.

📂 **Persistenz**:  
- Pro Guild in `./data/langrelay/<guild_id>.json`  
- Änderungen via Slash-Commands sofort gespeichert.

---

### 📝 Translate (manuell)

- `/translate text:<string> target:<lang> [source:<lang>] [formality:<style>]`  
  Übersetzt Text mit DeepL.

- `/detect text:<string>`  
  Erkennt Sprache.

- `/languages`  
  Listet alle verfügbaren Sprachen.

---

### 📊 Info
- `/about` – zeigt Bot-Status, Uptime, Latenz, Versionen.

---

### 🏓 Utility
- `/ping` – Antwortet mit „Pong!“.  

---

## 📸 Beispiele

### `/langrelay_status`
```text
LangRelay – Status
• #channel_de → DE
• #channel_en → EN
• #channel_fr → FR (❌ nicht gefunden)
```

### Übersetzung (LangRelay-Beispiel)

User schreibt in `#channel_de`:
```text
Hallo zusammen! Wie geht’s?
```

Bot postet automatisch in `#channel_en`:
```text
🌐 Max schrieb in #channel_de:
> Hallo zusammen! Wie geht’s?

Übersetzung → EN:
Hello everyone! How are you?

[Zum Original](https://discord.com/channels/...)
```

---

## 🔒 Rechte

- Für Mapping-Änderungen (`/langrelay_set`, `/remove`, `/clear`) sind **Manage Server**-Rechte erforderlich.  
- Übersetzungen werden mit entschärften Mentions gepostet, damit keine unerwünschten Pings ausgelöst werden.

---

## 🛠️ Tech-Stack

- Python 3.12+  
- [discord.py](https://github.com/Rapptz/discord.py)  
- [DeepL API](https://www.deepl.com/docs-api/)  
- Struktur: **Cogs** für modulare Erweiterbarkeit  
- Persistenz: JSON-Dateien pro Guild (`./data/langrelay/`)

---

## 🚀 Roadmap

- [ ] Support für Dateianhänge (Links spiegeln)  
- [ ] Konfigurierbare „Formality“ für LangRelay (wie bei /translate)  
- [ ] Globale Defaults via Config  
- [ ] Webinterface für Mapping-Verwaltung  

---

## 📄 Lizenz

MIT License – siehe [LICENSE](LICENSE)
