# Einfache Anleitung

Dieses Projekt ist ein lokales Simulationsprogramm in Vorbereitung.

## Einmal installieren

1. Den Ordner `weltanschauung-sim` oeffnen.
2. Doppelklick auf `install.bat`.
3. Warten, bis das Fenster fertig ist.

## Testen, ob alles funktioniert

In PowerShell im Projektordner:

```powershell
wsim version
```

Wenn eine Versionsnummer erscheint, ist die Installation gelungen.

## Dashboard

Das Dashboard ist die einfache Bedienoberflaeche im Browser.

Start:

```text
start_dashboard.bat
```

Danach oeffnet sich der Browser automatisch. Dort kannst du:

- Regeln, Karten und Bots auswaehlen
- Anzahl Spiele und Seed einstellen
- Simulationen starten
- vorhandene Runs laden
- Reports, Grafiken und einzelne Spiele ansehen
- ein Analysepaket fuer ChatGPT erzeugen

Alle Ergebnisse werden automatisch unter `outputs/runs/` gespeichert.
