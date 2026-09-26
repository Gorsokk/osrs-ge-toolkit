# OSRS GE Toolkit

**Ask Claude about your Old School RuneScape account**, plus a live Grand Exchange dashboard and smart Windows alerts, all installed in one click.

> *"How are my GE offers doing?"* · *"Where am I, what's the next step of my quest?"* · *"Plan my way to a bond."* · *"What should I High Alch with my cash right now?"*

*Français plus bas ⬇*

## What you get

- **Claude, connected to your account.** Claude (desktop app) can read your stats, bank, inventory, GE offers, position and quests, plus live GE prices, and answer questions about *your* account. It reads and advises; it never plays for you.
- **Live dashboard** in your browser:
  - your cash and your 8 GE slots (is each offer priced right?)
  - the best **High Alch** items for your cash, with the max price to pay
  - the best **flips**, after tax and filtered to avoid fake 1 gp margins
  - quests, diaries and combat achievements
- **Windows alerts**, all adjustable in the dashboard's Settings page:
  - GE offer filled (25/50/75/100 %) or stuck
  - real flips
  - price crashes and spikes
  - cheap bond
  - official news
  - cheap skilling materials
- **▶ Play button**: opens RuneLite from the dashboard or the tray. It can also open RuneLite for you when you start the toolkit. It finds RuneLite by itself, or you pick the program in Settings.
- **Runs quietly** in the tray (next to the clock). English and French. Tells you when an update is out.
- **Used the old stand-alone scripts** (`run_scan.bat` / `run_alerts.bat`)? The app imports their history once (profits, purchase costs, alert settings). After that you only need the app.

## Install (Windows 10/11)

1. **Download** `OSRS-GE-Toolkit-Setup-x.y.z.exe` from [Releases](https://github.com/Gorsokk/osrs-ge-toolkit/releases/latest) and run it. No admin rights and no Python needed.
   > Windows may show *"Windows protected your PC"* because the installer isn't code-signed yet. Click **More info → Run anyway**.
   > The code is open, and every release is built by GitHub Actions from this repository.
2. Keep **"Connect to the Claude desktop app"** checked. That's the one-click Claude setup.
3. **In RuneLite**, open the wrench icon → **Plugin Hub** and install:
   - **Character Export** (by DZWNK): stats, bank, inventory, quests…
   - **Position Exporter**: Grand Exchange offers and position.
4. Log in and **open your bank once**.
5. **Restart the Claude desktop app** (quit it from its tray icon, then reopen it) and ask away.

Don't have Claude yet? Get the desktop app at [claude.ai/download](https://claude.ai/download). You can connect later from the dashboard's **Claude** tab or the tray menu.

## Using it with Claude

Once connected, Claude gets these tools (read-only):

| Tool | Answers questions like |
|---|---|
| `get_status` | "Is everything set up?" |
| `get_character` | "What should I train next?" (stats, quests, diaries, combat achievements) |
| `get_position` | "Where am I? What's the next quest step from here?" |
| `get_inventory` / `get_bank` | "What's my bank worth? What should I sell?" |
| `get_ge_offers` | "Is my offer priced right? What should I relist at?" |
| `get_market_opportunities` | "Best flips / alchs for my cash right now" |
| `get_item_price` | live price, volume, margin after tax, alch profit, 7/30-day history |
| `get_recent_alerts` | "What happened on the GE while I was away?" |

There are also ready-made prompts: **GE check-up**, **Where am I / quest help**, **Money plan** and **What should I do next?**

The toolkit adds itself to Claude Desktop's local connector list (`claude_desktop_config.json`) and keeps your other connectors untouched. A backup is saved as `claude_desktop_config.json.bak-osrs-ge-toolkit`.

## Where the data comes from

- **Prices:** the free, public [OSRS Wiki real-time prices API](https://prices.runescape.wiki) (crowd-sourced from RuneLite). Margins are after the 2% GE tax.
- **Your account:** local JSON files written by the two RuneLite plugins in `%USERPROFILE%\.runelite\character-exporter\<name>\`.
- **News:** the official OSRS news feed.

## Privacy

The toolkit sends nothing anywhere. It only downloads prices, news, and a check for new versions from GitHub.

When you ask Claude a question, the data Claude reads to answer (for example your bank) is sent to Claude, like anything else you share in a chat.

## Settings, data and uninstalling

- Everything is adjustable in the dashboard → **Settings**.
- Settings, history and logs live in `%APPDATA%\OSRS GE Toolkit` and are kept across updates.
- Uninstall from Windows Settings → Apps. This also removes the Claude connection.

## Is this allowed?

The toolkit only reads data that RuneLite plugins export and public price data. It does not interact with the game client, send inputs, or automate anything.

Never use any tool to automate gameplay: it breaks Jagex's rules.

---

## Français

**Pose des questions à Claude sur ton compte OSRS**, avec un dashboard GE en direct et des alertes Windows, le tout installé en un clic.

1. **Télécharge** le `Setup.exe` dans les [Releases](https://github.com/Gorsokk/osrs-ge-toolkit/releases/latest) et lance-le. Si Windows affiche *« Windows a protégé votre ordinateur »*, clique sur **Informations complémentaires → Exécuter quand même**.
2. Laisse cochée l'option **« Connecter à l'application Claude »**.
3. Dans RuneLite, **clé à molette → Plugin Hub** : installe **Character Export** et **Position Exporter**.
4. Connecte-toi et ouvre ta banque une fois.
5. **Redémarre l'application Claude**, puis pose tes questions : *« Comment va mon GE ? »*, *« Où suis-je, quelle est la prochaine étape de ma quête ? »*

L'interface est en anglais ou en français : dashboard → **Paramètres → Langue**.

Le bouton **▶ Jouer** ouvre RuneLite. Dans **Paramètres → RuneLite**, tu peux aussi faire ouvrir le jeu en même temps que l'outil. Si tu utilisais les anciens scripts (`run_scan.bat` / `run_alerts.bat`), l'app reprend leur historique au premier lancement. Ensuite, tu n'as plus besoin que de l'app.

## For developers

```
pip install -r requirements.txt
python toolkit/launcher.py --no-tray     # app (dashboard on http://localhost:8765)
python toolkit/launcher.py --play        # app + open RuneLite (also works if the app is already running)
python toolkit/mcp_server.py             # Claude connector (MCP over stdio)
```

Each `v*` tag triggers GitHub Actions. It builds the tray app and the connector (PyInstaller), smoke-tests the connector, packages them with Inno Setup and publishes a Release.

## License

BSD 2-Clause. Not affiliated with Jagex, RuneLite or Anthropic.
