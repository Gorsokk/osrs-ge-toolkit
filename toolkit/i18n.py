"""Translations for alerts, the tray menu and messages (English + French).

The dashboard has its own copy of UI strings in web/dashboard.html (it asks the
local server which language to use). Add a language by adding a column here.
"""
import time

STRINGS = {
    # ---- GE offers
    "buy_done": {"en": "{name}: buy COMPLETE", "fr": "{name} : achat TERMINÉ"},
    "buy_pct": {"en": "{name}: buy {pct}%", "fr": "{name} : achat {pct} %"},
    "sell_done": {"en": "{name}: sale COMPLETE", "fr": "{name} : vente TERMINÉE"},
    "sell_pct": {"en": "{name}: sale {pct}%", "fr": "{name} : vente {pct} %"},
    "bought_body": {"en": "{filled}/{total} bought (avg {avg}). {extra}",
                    "fr": "{filled}/{total} achetés (moy. {avg}). {extra}"},
    "sold_body": {"en": "{filled}/{total} sold (avg {avg}). {extra}",
                  "fr": "{filled}/{total} vendus (moy. {avg}). {extra}"},
    "resell_tip": {"en": "Resell at ~{target} (+{profit} on {qty}).",
                   "fr": "Revends à ~{target} (+{profit} sur {qty})."},
    "wait_tip": {"en": "The market doesn't pay enough yet: wait.",
                 "fr": "Le marché ne paye pas encore assez : attends."},
    "cost_line": {"en": "Avg cost {avg}, break-even at {be}. {tip}",
                  "fr": "Coût moyen {avg}, rentable dès {be}. {tip}"},
    "unknown_cost": {"en": "(purchase cost unknown: bought before the toolkit was running)",
                     "fr": "(coût d'achat inconnu : acheté avant le lancement de l'outil)"},
    "profit_line": {"en": "Net profit {profit} (after tax). Last 24h: {day}.",
                    "fr": "Profit net {profit} (taxe incluse). Total 24 h : {day}."},
    "sell_now": {"en": "{name}: sell now", "fr": "{name} : vends maintenant"},
    "sell_now_body": {"en": "The market pays {hi}. You have {qty} at {avg} -> +{profit} net.",
                      "fr": "Le marché paye {hi}. Tu en as {qty} à {avg} -> +{profit} net."},
    "stuck": {"en": "{name}: offer stuck?", "fr": "{name} : offre bloquée ?"},
    "stuck_sell": {"en": "You're selling at {price}, buyers pay ~{ref}.",
                   "fr": "Tu vends à {price}, le marché paye ~{ref}."},
    "stuck_buy": {"en": "You're buying at {price}, sellers are at ~{ref}.",
                  "fr": "Tu achètes à {price}, les vendeurs sont à ~{ref}."},
    "stuck_tail": {"en": " Nothing has moved for {min} min.", "fr": " Rien ne bouge depuis {min} min."},
    # ---- watch list
    "watch_hit": {"en": "{name} at {price}", "fr": "{name} à {price}"},
    "watch_sell_body": {"en": "The market pays at least your target of {target}.{extra}",
                        "fr": "Le marché paye au moins ton seuil de {target}.{extra}"},
    "watch_sell_extra": {"en": " Net profit ~{profit}/each.", "fr": " Profit net ~{profit}/u."},
    "watch_buy_body": {"en": "Buyable below your target of {target}.",
                       "fr": "Achetable sous ton seuil de {target}."},
    # ---- flips
    "flip_title": {"en": "Flip: {name} (+{profit}/4h)", "fr": "Flip : {name} (+{profit}/4 h)"},
    "flip_body": {"en": "Buy {buy} -> sell {sell} = {margin} net/each after tax ({roi}%). Qty {qty} "
                        "(capital {capital}). Check the price in game first.",
                  "fr": "Achat {buy} -> vente {sell} = {margin} net/u après taxe ({roi} %). Qté {qty} "
                        "(capital {capital}). Vérifie le prix en jeu avant."},
    # ---- market moves
    "spike": {"en": "SPIKE", "fr": "PIC"},
    "crash": {"en": "CRASH", "fr": "CRASH"},
    "move_title": {"en": "{kind} {name}: {move}%", "fr": "{kind} {name} : {move} %"},
    "move_body": {"en": "{avg24} (24h avg) -> {now} (5 min), {vol} trades in 5 min, confirmed over 1h. "
                        "Often an update announcement or a bot ban wave.",
                  "fr": "{avg24} (moy. 24 h) -> {now} (5 min), {vol} échanges en 5 min, confirmé sur 1 h. "
                        "Souvent une annonce de mise à jour ou une vague de bans de bots."},
    # ---- bond
    "bond_rel": {"en": "Bond at {price} (-{pct}% vs 7-day avg)", "fr": "Bond à {price} (-{pct} % vs 7 j)"},
    "bond_rel_body": {"en": "7-day average: {avg}. Alert level: -{thr}% = {level}.",
                      "fr": "Moyenne 7 jours : {avg}. Seuil : -{thr} % = {level}."},
    "bond_fixed": {"en": "Bond at {price}", "fr": "Bond à {price}"},
    "bond_fixed_body": {"en": "Below your target of {target}.", "fr": "Sous ton seuil de {target}."},
    # ---- news / stockpile
    "news_title": {"en": "OSRS: {title}", "fr": "OSRS : {title}"},
    "stock_title": {"en": "Stock up: {name} at {price}", "fr": "Stocker : {name} à {price}"},
    "stock_body": {"en": "{disc}% below its 90-day median ({med}), 90-day low: {low}. GE limit: {limit} / 4h.",
                   "fr": "{disc} % sous la médiane 90 j ({med}), plus bas 90 j : {low}. Limite GE : {limit} / 4 h."},
    "open": {"en": "Open", "fr": "Ouvrir"},
    # ---- tray / app
    "tray_open": {"en": "Open dashboard", "fr": "Ouvrir le dashboard"},
    "tray_settings": {"en": "Settings", "fr": "Paramètres"},
    "tray_play": {"en": "Play (open RuneLite)", "fr": "Jouer (ouvrir RuneLite)"},
    "runelite_fail": {"en": "Could not open RuneLite: {error}", "fr": "Impossible d'ouvrir RuneLite : {error}"},
    "legacy_title": {"en": "Old scripts imported", "fr": "Anciens scripts importés"},
    "legacy_body": {"en": "Your history from \"{folder}\" is now in the app. You can stop using run_scan.bat / run_alerts.bat.",
                    "fr": "Ton historique de « {folder} » est maintenant dans l'app. Tu n'as plus besoin de run_scan.bat / run_alerts.bat."},
    "tray_claude_connect": {"en": "Connect to Claude", "fr": "Connecter à Claude"},
    "tray_claude_connected": {"en": "Connected to Claude ✓", "fr": "Connecté à Claude ✓"},
    "tray_folder": {"en": "Open settings folder", "fr": "Ouvrir le dossier de configuration"},
    "tray_update": {"en": "Update available: {version}", "fr": "Mise à jour disponible : {version}"},
    "tray_quit": {"en": "Quit", "fr": "Quitter"},
    "tray_tip": {"en": "OSRS GE Toolkit – {status}", "fr": "OSRS GE Toolkit – {status}"},
    "status_waiting": {"en": "waiting for game data", "fr": "en attente des données du jeu"},
    "status_running": {"en": "running ({name})", "fr": "actif ({name})"},
    "started_title": {"en": "OSRS GE Toolkit is running", "fr": "OSRS GE Toolkit est lancé"},
    "started_body": {"en": "It lives in the tray (bottom-right, near the clock). Right-click the icon for the menu.",
                     "fr": "Il est dans la zone de notification (en bas à droite, près de l'horloge). "
                           "Clic droit sur l'icône pour le menu."},
    "setup_title": {"en": "OSRS GE Toolkit – one-time setup", "fr": "OSRS GE Toolkit – installation"},
    "setup_body": {"en": "No game data yet. In RuneLite: wrench icon -> Plugin Hub -> install \"Character Export\" "
                         "and \"Position Exporter\", then log in and open your bank once. The toolkit will pick it up "
                         "automatically.",
                   "fr": "Aucune donnée de jeu pour l'instant. Dans RuneLite : clé à molette -> Plugin Hub -> installe "
                         "« Character Export » et « Position Exporter », puis connecte-toi et ouvre ta banque une fois. "
                         "L'outil le détectera tout seul."},
    "claude_done_title": {"en": "Connected to Claude", "fr": "Connecté à Claude"},
    "claude_done_body": {"en": "Restart the Claude desktop app, then ask it about your account "
                               "(\"how's my GE doing?\").",
                         "fr": "Redémarre l'application Claude, puis pose-lui une question sur ton compte "
                               "(« comment va mon GE ? »)."},
    "claude_fail": {"en": "Could not connect to Claude: {error}", "fr": "Impossible de connecter Claude : {error}"},
    "already_running": {"en": "OSRS GE Toolkit is already running.", "fr": "OSRS GE Toolkit est déjà lancé."},
}

_cache = {"lang": None, "at": 0.0}


def lang():
    """Current UI language, re-read from settings at most every 5 s (changes apply live)."""
    now = time.time()
    if _cache["lang"] is None or now - _cache["at"] > 5:
        try:
            import settings
            _cache["lang"] = settings.load()["language"]
        except Exception:
            _cache["lang"] = "en"
        _cache["at"] = now
    return _cache["lang"]


def t(key, **kw):
    entry = STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(lang()) or entry["en"]
    try:
        return text.format(**kw)
    except (KeyError, IndexError, ValueError):
        return text
