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

## Struktur

- `configs/`: Regeln, Karten, Bot-Profile und Experimente
- `src/wsim/`: Python-Paket im src-layout
- `outputs/runs/`: spaetere Simulationsergebnisse
- `tests/`: automatisierte Tests

