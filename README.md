# OSRS GE Toolkit

Un bouton pour avoir, pendant que tu joues à Old School RuneScape :

- **Un dashboard live** dans ton navigateur : ton cash (banque + inventaire), tes 8 offres du Grand Exchange
  avec le prix du marché, tes diaries et combat achievements.
- **Un scanner GE** toutes les ~5 min : les meilleurs flips (filtrés pour éviter les faux bons plans à 1 gp de marge)
  et les meilleurs items à **High Alch** avec le prix d'achat maximum et le profit par heure.
- **Des alertes Windows** : offre GE remplie (par palier 25/50/75/100 %), offre bloquée, vrais flips,
  crash/pic de prix, bond sous sa moyenne 7 jours, news officielles, matériaux de skilling pas chers.

Les prix viennent de l'API publique du [OSRS Wiki](https://prices.runescape.wiki). Tes données de jeu restent
sur ton PC : rien n'est envoyé nulle part.

## Installation (Windows 10/11)

1. **Télécharge** `OSRS-GE-Toolkit-Setup-x.y.z.exe` dans les [Releases](https://github.com/Gorsokk/osrs-ge-toolkit/releases/latest)
   et lance-le. Pas besoin de droits admin, ni d'installer Python.
   > Windows peut afficher « Windows a protégé votre ordinateur » (l'installateur n'est pas signé) :
   > clique sur **Informations complémentaires → Exécuter quand même**.
2. **Dans RuneLite**, ouvre la configuration (clé à molette) → **Plugin Hub**, et installe :
   - **Character Export** (par DZWNK) : stats, banque, inventaire, quêtes…
   - **Position Exporter** : offres du Grand Exchange + position.
3. Connecte-toi en jeu et **ouvre ta banque une fois**.
4. Double-clique sur **OSRS GE Toolkit** sur ton bureau. Le dashboard s'ouvre tout seul.

Laisse la fenêtre ouverte pendant que tu joues ; ferme-la pour tout arrêter.

## Réglages

Les seuils des alertes sont dans `%APPDATA%\OSRS GE Toolkit\alerts_config.json`
(raccourci « Dossier de configuration » dans le menu Démarrer). Les changements sont pris en compte à chaud.

## Pour les développeurs

```
pip install -r requirements.txt
python toolkit/launcher.py
```

L'installateur est construit par GitHub Actions (`.github/workflows/release.yml`) à chaque tag `v*` :
PyInstaller → Inno Setup → Release GitHub.

## Licence

BSD 2-Clause. Projet non affilié à Jagex ni à RuneLite.
