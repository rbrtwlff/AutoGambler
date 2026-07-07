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

Aktuellen Regel- und Kartensatz validieren:

```powershell
wsim validate-config --rules configs/rules/rules_v0_3.yaml
wsim validate-cards --rules configs/rules/rules_v0_3.yaml --cards configs/cards/cards_v0_3.yaml
```

Tests ausfuehren:

```powershell
python -m pytest
```

Experiment mit Regelvarianten ausfuehren:

```powershell
wsim experiment --base-rules configs/rules/rules_v0_3.yaml --variants configs/experiments --cards configs/cards/cards_v0_3.yaml --bots configs/bots/bot_profiles.yaml --games-per-variant 10000 --seed 123 --output outputs/runs/experiment_001
```

Der Experiment-Runner erzeugt pro Variante einen eigenen Run-Ordner sowie `comparison_metrics.json` und `comparison_report.md` im Experiment-Ordner.

## Struktur

- `configs/`: Regeln, Karten, Bot-Profile und Experimente
- `src/wsim/`: Python-Paket im src-layout
- `outputs/runs/`: spaetere Simulationsergebnisse
- `tests/`: automatisierte Tests

## Karten und Decks

Der aktuelle V0.3-Kartensatz liegt in `configs/cards/cards_v0_3.yaml`.
Er enthaelt die 240 gelieferten Fraktionskarten als Datenkonfiguration.
Die Felder `timing`, `archetype` und `effect_text` bewahren den verbindlichen
Kartentext, ohne ihn im Code hart zu verdrahten.

Die Rechercheauftraege liegen getrennt in
`configs/cards/research_assignments_v0_3.yaml` und werden von
`cards_v0_3.yaml` eingebunden. Jeder Auftrag hat `points` mit dem Wert 1, 2
oder 3; dieser Wert ist der Quellen-Ertrag, wenn der Auftrag erfuellt wird.
V0.3 begrenzt Quellen nicht mehr auf 3, sondern nutzt aktuell ein hohes
praktisches Limit von 99 Quellen pro Spieler, damit Spieler mehr Quellen
sammeln und in der Journalistenpruefung einsetzen koennen.

Kartenarten werden in YAML als `CardConfig` beschrieben. Das Feld `count`
bestimmt, wie oft diese Kartenart physisch im Deck vorkommt. Jede physische
Kopie erhaelt eine eindeutige `instance_id`, waehrend die logische `card_id`
fuer alle Kopien gleich bleibt. Dadurch koennen mehrere Exemplare derselben
Karte gespielt, abgelegt und in Auswertungen trotzdem als dieselbe Kartenart
zusammengefasst werden.

Der Nachziehstapel wird aus allen aktivierten Karten (`enabled: true`) und
ihrem jeweiligen `count` aufgebaut. Deaktivierte Karten kommen nicht ins Deck.
Wenn der Nachziehstapel leer ist, regelt der Abschnitt `deck:` in der
Regeldatei, was passiert:

- `reshuffle_discard_when_empty`: mischt den Ablagestapel reproduzierbar in den Nachziehstapel zurueck.
- `when_not_enough_cards: draw_less`: zieht nur die verfuegbaren Karten.
- `when_not_enough_cards: error`: bricht mit einer klaren Regel-Fehlermeldung ab.
- `log_reshuffle_events`: schreibt Ereignisse fuer leeren Nachziehstapel und Neumischen.

Karten, die aus dem Spiel entfernt wurden, sowie Karten auf Hand,
Propagandaleiste, als committed Action, Quelle oder Research Order werden
nicht zurueckgemischt.

## MVP-Annahmen

- Die Medienmogul-Phase waehlt aktuell eine geeignete `propaganda`- oder `hybrid`-Karte aus der Hand des Medienmoguls und legt sie auf die Propagandaleiste.
- Die Propagandaleiste ist von links nach rechts geordnet: links liegt die aelteste Karte, rechts die neueste. Bei voller Leiste entfernt `remove_oldest` die linke Karte.
