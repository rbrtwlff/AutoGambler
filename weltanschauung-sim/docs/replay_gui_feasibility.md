# Spielbrett- und Replay-GUI: Machbarkeit

Stand: v0.3

## Ergebnis

Eine schlichte grafische Benutzeroberflaeche fuer Einzelspiel-Replays ist gut machbar. Die aktuelle Architektur passt grundsaetzlich dazu, weil Spiele bereits ueber Events, Round Summaries und Single-Game-Exports nachvollziehbar gespeichert werden.

Wichtig ist die Trennung zweier Modi:

- Analysemodus: viele Spiele so schnell wie moeglich simulieren, keine Animation, nur Summaries und optional Event-Samples.
- Replaymodus: ein einzelnes gespeichertes Spiel langsam oder schrittweise abspielen, mit Spielbrett, Karten, Bevoelkerung, Propaganda, Urne, Weltgeschichte und Kampf.

Diese Modi duerfen nicht dieselbe Laufzeitlogik erzwingen. Massensimulationen sollen weiterhin ohne grafische Darstellung laufen.

## Was heute schon vorhanden ist

- Eventlog mit Phasen, Entscheidungen, Kartenbewegungen, Propaganda, Weltgeschichte, Kampf und Siegpruefung.
- Round Summaries mit Bevoelkerung, Quellen, Handkartenzahlen, Journalist, Medienmogul, Propagandaleiste, Weltgeschichtsreihe und Machtwerten.
- Single Game Inspector, der ein Spiel aus einem Run laden kann.
- Dashboard auf Streamlit-Basis.
- Eindeutige Karteninstanzen ueber `instance_id`.

Damit kann ein erster Replay-Viewer gebaut werden, ohne die Engine neu zu schreiben.

## Was fuer ein gutes Spielbrett noch fehlt

1. Replay-State-Builder
   Aus Eventlog und Round Summaries muss fuer jeden Replay-Schritt ein sichtbarer Zustand gebaut werden.

2. Einheitliches Replay-Step-Modell
   Jeder Schritt sollte mindestens enthalten:
   - Runde
   - Phase
   - Titel
   - Beschreibung
   - sichtbare Kartenbereiche
   - Bevoelkerung
   - Quellen
   - Rollen
   - Propagandaleiste
   - Urne / Weltgeschichte
   - Kampfdetails
   - hervorgehobene Veraenderungen

3. Event-Granularitaet pruefen
   Fuer eine angenehme Animation muessen manche Ereignisse eventuell feiner gespeichert werden, z.B.:
   - Karte wandert von Hand in Urne
   - Urne wird gemischt
   - Weltgeschichte wird aufgedeckt
   - Propaganda verschiebt Slots
   - Bevoelkerung wird simultan veraendert

4. Dashboard-Replay-Controls
   - Zurueck
   - Weiter
   - Play / Pause
   - Geschwindigkeit: Schrittweise, langsam, normal, schnell
   - Sprung zu Runde / Phase

5. Simulationsgeschwindigkeit
   Fuer Massensimulationen ist keine Animation sinnvoll. Dort braucht es stattdessen:
   - Spielzahl
   - Seed
   - Eventlogging-Detailgrad
   - Fortschritt
   - Durchsatz Spiele/Sekunde

## Empfohlene Umsetzung

Phase 1: Replay-Datenmodell

- `src/wsim/dashboard/replay.py`
- Funktion `build_replay_steps(run_dir, game_id)`
- Tests fuer:
  - Schritte werden erzeugt
  - Runden und Phasen stimmen
  - Bevoelkerung/Propaganda/Weltgeschichte erscheinen
  - alte Runs crashen nicht

Phase 2: Schlichtes Spielbrett im Dashboard

- Neuer Tab `Replay`
- Vier Fraktionsbereiche mit Bevoelkerung und Macht
- Propagandaleiste als horizontale Slots
- Weltgeschichtsreihe als Kartenreihe
- Rollenleiste fuer Startspieler, Journalist, Medienmogul
- Textpanel fuer aktuelles Ereignis

Phase 3: Geschwindigkeit und Steuerung

- Slider fuer Geschwindigkeit
- Buttons fuer Schritt zurueck / weiter
- Autoplay fuer langsame Replays
- Kein Autoplay bei Massensimulationen

Phase 4: Optional spaeter bessere Optik

- Karten als kompakte visuelle Karten
- Farbcodierung fuer Fraktionen
- Hervorhebung von Angriffen und Bevoelkerungsbewegungen
- Export eines Replay-Protokolls

## Fazit

Ja, eine schlichte grafische Spielbrett-GUI ist realistisch. Der beste erste Schritt ist kein komplett neues Programmfenster, sondern ein Replay-Tab im bestehenden Streamlit-Dashboard. Das haelt die Installation einfach und passt zum Ziel, dass der Nutzer per Doppelklick arbeiten kann.
