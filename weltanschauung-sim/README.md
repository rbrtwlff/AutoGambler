# weltanschauung-sim

`weltanschauung-sim` ist das Grundgeruest fuer ein lokales Simulationsprogramm fuer ein asymmetrisches Brettspiel.

Das Projekt soll langfristig:

- vollstaendige Spiele reproduzierbar simulieren,
- Regeln, Karten, Bots und Experimente ueber Config-Dateien steuerbar machen,
- viele Simulationslaeufe schnell auswerten,
- einzelne Spiele Runde fuer Runde nachvollziehbar machen,
- spaeter ueber eine einfache lokale Browseroberflaeche bedienbar sein.

## Entwicklung

Installation im Projektordner:

```powershell
python -m pip install -e ".[dev]"
```

CLI testen:

```powershell
wsim --help
wsim version
```

Tests ausfuehren:

```powershell
python -m pytest
```

Experiment mit Regelvarianten ausfuehren:

```powershell
wsim experiment --base-rules configs/rules/base_rules.yaml --variants configs/experiments --cards configs/cards/base_cards.yaml --bots configs/bots/bot_profiles.yaml --games-per-variant 10000 --seed 123 --output outputs/runs/experiment_001
```

Der Experiment-Runner erzeugt pro Variante einen eigenen Run-Ordner sowie `comparison_metrics.json` und `comparison_report.md` im Experiment-Ordner.

## Struktur

- `configs/`: Regeln, Karten, Bot-Profile und Experimente
- `src/wsim/`: Python-Paket im src-layout
- `outputs/runs/`: spaetere Simulationsergebnisse
- `tests/`: automatisierte Tests

## MVP-Annahmen

- Die Medienmogul-Phase waehlt aktuell eine geeignete `propaganda`- oder `hybrid`-Karte aus der Hand des Medienmoguls und legt sie auf die Propagandaleiste.
- Die Propagandaleiste ist von links nach rechts geordnet: links liegt die aelteste Karte, rechts die neueste. Bei voller Leiste entfernt `remove_oldest` die linke Karte.
