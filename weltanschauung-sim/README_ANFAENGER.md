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

- Regeln: `configs/rules/rules_v0_3.yaml`
- Karten: `configs/cards/cards_v0_3.yaml`
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

## Hinweis zu Karten

Der aktuelle Kartensatz fuer Version 0.3 liegt in:

```text
configs/cards/cards_v0_3.yaml
```

Diese Datei enthaelt die 240 aktuellen Fraktionskarten. Die echten
Rechercheauftraege liegen in:

```text
configs/cards/research_assignments_v0_3.yaml
```

Sie werden automatisch mitgeladen. Rechercheauftraege geben je nach Auftrag
1, 2 oder 3 Quellen. In Version 0.3 ist das Quellenlimit nicht mehr 3; das
Programm erlaubt aktuell bis zu 99 Quellen pro Spieler, damit Spieler mehr
Quellen sammeln und einsetzen koennen.

In den Karten-Dateien kann bei einer Karte `count` stehen. Das bedeutet:
Diese Kartenart liegt mehrfach im Deck. Das Programm behandelt jede einzelne
Kopie getrennt, damit Nachziehstapel, Ablagestapel, Handkarten und
Propagandaleiste korrekt nachvollziehbar bleiben.

Wenn der Nachziehstapel leer ist, wird der Ablagestapel automatisch gemischt
und weiterverwendet. Karten, die aus dem Spiel entfernt wurden oder gerade auf
der Hand, auf der Propagandaleiste, als Quelle oder als Research Order liegen,
kommen dabei nicht zurueck.

## Schritt 7: Analysepaket exportieren

Im Tab `Export fuer ChatGPT` klicken Sie auf:

```text
Analysepaket fuer ChatGPT exportieren
```

Dadurch entstehen im Run-Ordner:

- `analysis_package/`
- `analysis_package.zip`

Diese ZIP-Datei koennen Sie an ChatGPT geben. ChatGPT soll zuerst die Datei `README_FUER_CHATGPT.md` und danach `report.md` lesen.
