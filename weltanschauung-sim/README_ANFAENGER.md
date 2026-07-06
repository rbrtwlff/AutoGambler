# Einfache Anleitung

Dieses Programm simuliert lokale Testspiele und zeigt die Ergebnisse im Browser an. Sie muessen dafuer nicht programmieren.

## Schritt 1: Python installieren

Installieren Sie Python 3.12 oder neuer von:

```text
https://www.python.org/downloads/
```

Wichtig: Beim Installieren die Option `Add Python to PATH` aktivieren.

## Schritt 2: Projektordner oeffnen

Oeffnen Sie den Ordner:

```text
weltanschauung-sim
```

In diesem Ordner liegen die Dateien `install.bat` und `start_dashboard.bat`.

## Schritt 3: install.bat doppelklicken

Doppelklicken Sie auf:

```text
install.bat
```

Das Fenster installiert alles Noetige und fuehrt einen kurzen Funktionstest aus. Am Ende sollte dort stehen:

```text
Installation erfolgreich.
```

Wenn eine Fehlermeldung erscheint, bleibt das Fenster offen, damit Sie die Meldung lesen koennen.

## Schritt 4: start_dashboard.bat doppelklicken

Doppelklicken Sie auf:

```text
start_dashboard.bat
```

Das Dashboard startet lokal. Normalerweise oeffnet sich automatisch ein Browserfenster.

## Schritt 5: Simulation starten

Im Tab `Start / Simulation starten` sind einfache Default-Dateien vorausgewaehlt:

- Regeln: `configs/rules/base_rules.yaml`
- Karten: `configs/cards/base_cards.yaml`
- Bots: `configs/bots/bot_profiles.yaml`

Fuer den ersten Beispiel-Run sind 10 Spiele und Seed 123 voreingestellt. Klicken Sie auf `Simulation starten`.

## Schritt 6: Report finden

Alle Ergebnisse werden automatisch gespeichert unter:

```text
outputs/runs/
```

Jeder Run hat einen eigenen Ordner. Darin finden Sie unter anderem:

- `report.md`
- `metrics.json`
- `game_summaries.csv`
- `round_summaries.csv`
- `charts/`

Im Dashboard koennen Sie einen vorhandenen Run links in der Seitenleiste laden.

## Schritt 7: Analysepaket exportieren

Im Tab `Export fuer ChatGPT` klicken Sie auf:

```text
Analysepaket fuer ChatGPT exportieren
```

Dadurch entstehen im Run-Ordner:

- `analysis_package/`
- `analysis_package.zip`

Diese ZIP-Datei koennen Sie an ChatGPT geben. ChatGPT soll zuerst die Datei `README_FUER_CHATGPT.md` und danach `report.md` lesen.
