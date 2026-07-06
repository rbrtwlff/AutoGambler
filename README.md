# AutoGambler

Lokale Simulationsengine fuer ein asymmetrisches Brettspiel in Entwicklung.

## Start

```powershell
python -m pip install -e ".[dev]"
python -m autogambler.cli simulate --games 10 --seed 42
```

Falls `python` auf den Windows-Store-Platzhalter zeigt, nutze:

```powershell
& "$env:LOCALAPPDATA\Python\bin\python.exe" -m autogambler.cli simulate --games 10 --seed 42
```

Das Dashboard ist vorbereitet:

```powershell
.\start_dashboard.bat
```

## Prinzipien

- Regeln, Karten, Bots und Analyseparameter liegen in `configs/`.
- Die Engine erzeugt reproduzierbare Simulationen ueber Seeds.
- Jede relevante Zustandsaenderung wird als Event geloggt.
- Bots erhalten nur eine Sicht auf den Zustand, keine geheimen Informationen anderer Spieler.
- Analytics liest Ergebnisse aus `outputs/`, ist aber von Engine und Bots getrennt.
