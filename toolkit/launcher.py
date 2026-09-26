"""
OSRS GE Toolkit - lanceur "un bouton".

Demarre, dans une seule fenetre:
  1. le scanner GE (flips + alch), rafraichi toutes les ~5 min
  2. le dashboard live dans ton navigateur (http://localhost:8765/dashboard.html)
  3. les alertes Windows (offres GE, flips, crash/pic, bond, news, stockage)

Les donnees de jeu viennent de deux plugins RuneLite (Plugin Hub):
  - "Character Export"  (par DZWNK)   -> stats, banque, inventaire, quetes...
  - "Position Exporter" (ce projet)   -> position + offres du Grand Exchange
"""

import sys
import threading
import time
import traceback
import webbrowser

import paths
from paths import APP_NAME, VERSION, RUNELITE_ROOT, DATA_DIR

SCAN_INTERVAL_SEC = 275
PORTS = range(8765, 8776)

SETUP_HELP = f"""
Aucune donnee de jeu trouvee dans:
  {RUNELITE_ROOT}

Pour que {APP_NAME} fonctionne (a faire une seule fois):
  1. Ouvre RuneLite.
  2. Ouvre la configuration (icone de cle a molette) et clique sur "Plugin Hub".
  3. Installe "Character Export" (par DZWNK).
  4. Installe "Position Exporter".
  5. Connecte-toi en jeu et ouvre ta banque une fois.

Cette fenetre detecte automatiquement tes donnees des qu'elles apparaissent.
"""


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def message_box(title, body):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, body, title, 0x40 | 0x40000)
    except Exception:
        pass


def characters_ready():
    """Personnages dont l'export contient au moins character.json et bank.json."""
    if not RUNELITE_ROOT.exists():
        return []
    return sorted(
        p.name for p in RUNELITE_ROOT.iterdir()
        if p.is_dir() and (p / "character.json").exists() and (p / "bank.json").exists()
    )


def wait_for_exports():
    ready = characters_ready()
    if ready:
        return ready
    print(SETUP_HELP)
    threading.Thread(target=message_box, args=(APP_NAME + " - installation", SETUP_HELP.strip()),
                     daemon=True).start()
    while not ready:
        time.sleep(10)
        ready = characters_ready()
    log(f"Donnees trouvees pour: {', '.join(ready)}")
    return ready


def start_dashboard(ms):
    for port in PORTS:
        try:
            ms.start_dashboard_server(ms.EXPORT_DIR, port)
            return port
        except OSError:
            continue
    return None


def run_scanner(ms):
    try:
        ms.run_loop(SCAN_INTERVAL_SEC)
    except Exception:
        traceback.print_exc()
        log("Le scanner s'est arrete. Relance le lanceur.")


def run_alerts():
    import osrs_alerts
    while True:
        try:
            osrs_alerts.run_forever()
        except Exception:
            traceback.print_exc()
            log("Alertes: erreur inattendue, redemarrage dans 30 s.")
            time.sleep(30)


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(f"{APP_NAME} {VERSION}")
        except Exception:
            pass

    print(f"=== {APP_NAME} {VERSION} ===")
    print(f"Config et historique: {DATA_DIR}")
    print("Laisse cette fenetre ouverte pendant que tu joues. Ferme-la pour tout arreter.\n")

    wait_for_exports()

    import market_scan as ms
    ms.setup_paths(None)          # choisit le personnage (demande une fois s'il y en a plusieurs)
    log(f"Personnage: {ms.CHAR_NAME}")

    ms.write_dashboard(ms.EXPORT_DIR)
    port = start_dashboard(ms)

    threading.Thread(target=run_scanner, args=(ms,), daemon=True, name="scanner").start()
    threading.Thread(target=run_alerts, daemon=True, name="alertes").start()

    if port:
        url = f"http://localhost:{port}/dashboard.html"
        log(f"Dashboard: {url}")
        time.sleep(3)             # laisse le premier scan ecrire market.json
        webbrowser.open(url)
    else:
        log("Impossible d'ouvrir le dashboard (ports 8765-8775 occupes). Le scanner et les alertes tournent quand meme.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("Arret.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
        input("\nErreur inattendue. Appuie sur Entree pour fermer...")
