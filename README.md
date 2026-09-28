# OSRS Toolkit

**Free tools for Old School RuneScape players and streamers**, installed in one click:

- **GE module**: ask Claude about your account, a live Grand Exchange dashboard and smart Windows alerts.
- **Stream module**: Twitch chat commands, a Meld Studio / OBS overlay and scenes, and Claude as your stream co-host.

*Formerly "OSRS GE Toolkit": updating keeps all your settings and history.*

> *"How are my GE offers doing?"* · *"Where am I, what's the next step of my quest?"* · *"Plan my way to a bond."* · *"What should I High Alch with my cash right now?"*

*Français plus bas ⬇*

## What you get: GE module

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

## What you get: Stream module

For OSRS streamers on Twitch, with **Meld Studio** or **OBS**:

- **Chat commands** answered from your live data:
  `!ge <item>` (live GE price and margin) · `!bond` (your progress toward a bond) · `!flip` (top flip right now) · `!toolkit` · `!kofi` · `!ask <question>` · `!commands`
- **Claude as co-host.** Viewers ask with `!ask`; you (or Claude, when you ask it to) answer from the Claude desktop app, and the answer pops up on stream and in chat. Claude can also switch your Meld Studio scenes on request.
- **Overlay + scenes** (browser sources, 1920×1080): an in-game overlay (bond progress, Claude's answers, GE alerts, a live ticker) and full-screen **Starting soon** (with countdown), **BRB** and **Ending** scenes in the same OSRS style.
- Works **read-only** without any token (answers show on the overlay). Add a bot account token to also reply in chat.

Set it up in the dashboard → **Settings → Stream**: channel name, optional bot account, links. The page lists the four browser-source URLs to paste in Meld Studio or OBS (for example `http://localhost:8765/stream/overlay`). Options: `?min=5` (countdown), `?topic=...`, `?lang=fr`, `?bond=0`, `?alerts=0`.

For scene switching in **Meld Studio**, turn on its local API (Settings, port 13376) and put the overlay in every scene.

## Remote bridge: Claude voice mode, phone and claude.ai

Claude's voice mode, the mobile app and claude.ai only reach connectors on the internet. The optional **remote bridge** shares the toolkit's Claude tools through a free [ngrok](https://ngrok.com) tunnel, so you can talk to Claude while you play ("answer the chat", "switch to BRB", "best flip right now?").

1. Create a free ngrok account and claim your free static domain (Dashboard → Domains).
2. Install ngrok (`winget install ngrok.ngrok`) and run `ngrok config add-authtoken <your token>` once.
3. Dashboard → **Settings → Remote bridge**: paste the domain, enable it, save. The toolkit starts ngrok for you.
4. In Claude: **Settings → Connectors → Add custom connector**, paste the connector URL shown in the dashboard.

The URL contains a secret: anyone who has it can read your character data and post on your stream, so keep it private ("New secret address" changes it). Nothing else of the toolkit is reachable from the internet.

## Install (Windows 10/11)

1. **Download** `OSRS-Toolkit-Setup-x.y.z.exe` from [Releases](https://github.com/Gorsokk/osrs-toolkit/releases/latest) and run it. No admin rights and no Python needed.
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

Once connected, Claude gets these tools (the `stream_*` ones only act on your stream, never on the game):

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
| `stream_get_chat` / `stream_say` | "Answer the !ask questions from chat" |
| `stream_show_scene` / `stream_status` | "Switch to the BRB scene" |

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

**Des outils gratuits pour les joueurs et streamers OSRS**, installés en un clic : pose des questions à Claude sur ton compte, un dashboard GE en direct, des alertes Windows, et un **module Stream** (commandes de chat Twitch, overlay et scènes pour Meld Studio / OBS, Claude comme co-animateur).

1. **Télécharge** le `Setup.exe` dans les [Releases](https://github.com/Gorsokk/osrs-toolkit/releases/latest) et lance-le. Si Windows affiche *« Windows a protégé votre ordinateur »*, clique sur **Informations complémentaires → Exécuter quand même**.
2. Laisse cochée l'option **« Connecter à l'application Claude »**.
3. Dans RuneLite, **clé à molette → Plugin Hub** : installe **Character Export** et **Position Exporter**.
4. Connecte-toi et ouvre ta banque une fois.
5. **Redémarre l'application Claude**, puis pose tes questions : *« Comment va mon GE ? »*, *« Où suis-je, quelle est la prochaine étape de ma quête ? »*

L'interface est en anglais ou en français : dashboard → **Paramètres → Langue**.

**Streamers :** dashboard → **Paramètres → Stream**. Entre ta chaîne Twitch, puis ajoute les 4 adresses affichées comme sources navigateur dans Meld Studio ou OBS. Les viewers utilisent `!ge`, `!bond`, `!flip`, `!ask`… et les réponses de Claude s'affichent à l'écran.

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
