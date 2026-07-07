# Gap-Analyse: Regelstand v0.3

Stand dieser Analyse: Abgleich des aktuellen Engine-Stands mit dem neuen verbindlichen
Rundenablauf v0.3. Karteninhalte werden dabei bewusst nicht spezifiziert.

## Neuer verbindlicher Rundenablauf v0.3

1. Rundenbeginn
2. Journalistenpruefung
3. Medienmogulwahl
4. Draftphase
5. Journalist- und Medienmogulphase
6. Diskussion / Intrige
7. Urnenphase
8. Weltgeschichte und Kampf
9. Siegpruefung
10. Rechercheauftraege
11. Rundenende

## Aktueller technischer Stand

Die aktuelle Engine ist bereits phasengetrieben: `round_flow.phases` kommt aus YAML
und `GameEngine.run_phase()` ruft passende `_phase_<name>`-Handler auf. Unbekannte
Phasen laufen aktuell als Stub weiter. Das ist eine gute Grundlage fuer v0.3.

Der aktuelle Default-Ablauf in `configs/rules/base_rules.yaml` lautet:

1. `start_round`
2. `draft`
3. `journalist_phase`
4. `media_mogul_phase`
5. `planning`
6. `reveal`
7. `action_resolution`
8. `cleanup`
9. `victory_check`

Dieser Ablauf bildet den alten MVP ab. Besonders `planning`, `reveal` und
`action_resolution` sind auf verdecktes Committen von Handkarten und danach
`support`/`attack` ausgelegt. Das passt nur noch teilweise zu v0.3.

## Bereits passende Mechaniken

- YAML-basierte Regelkonfiguration ueber `GameConfig` und `round_flow.phases`.
- Reproduzierbarer Seed/RNG pro Spiel.
- Spieler, Fraktionen, geheime Fraktionsziele, optionaler Saboteur.
- Kartenmodell mit physischen Karteninstanzen, `count`, Nachziehstapel,
  Ablagestapel, Entfernt-aus-dem-Spiel-Zone und Reshuffle-Regel.
- Eventlog mit geordneten Events und JSONL-Export.
- Draft-Grundstruktur mit draw/pick/discard und Draft-Events.
- Propagandaleiste mit konfigurierbarer Slotzahl und Overflow `remove_oldest`.
- Journalist- und Medienmogul-Grundaktionen.
- Bot-Schnittstellen mit legalen Optionen, Public View und geheimen Eigeninfos.
- Siegpruefung als eigenes Modul mit konfigurierbaren Timing-Eintraegen.
- Batch Runner, Reports, Charts, Inspector, Dashboard und Analysepaket.
- Invariant Checks fuer Populationen, Kartenorte, Propagandaslots und Eventindices.

## Welche bisherigen Phasen noch passen

| v0.3 Phase | Aktueller Stand | Bewertung |
| --- | --- | --- |
| Rundenbeginn | `start_round` | Passt als Hook, ist aber noch Stub. |
| Draftphase | `draft` | Passt teilweise. Reihenfolge und Details muessen v0.3-spezifisch werden. |
| Journalist- und Medienmogulphase | `journalist_phase`, `media_mogul_phase` | Passt teilweise, sollte als kombinierte Phase oder als Unterphasen modelliert werden. |
| Siegpruefung | `victory_check` | Passt technisch, Siegbedingungen muessen v0.3 konkretisiert werden. |
| Rundenende | `cleanup` plus implizites Round-End | Muss zu explizitem `round_end` werden. |

## Welche Phasen ersetzt oder umgebaut werden muessen

- `planning` sollte nicht unveraendert weitergefuehrt werden. Es ist auf
  alte Support/Attack-Planung mit Kartencommit ausgelegt.
- `reveal` sollte nicht als zentrale Aktionsenthuellung bestehen bleiben, falls v0.3
  Aktionen ueber Diskussion/Intrige, Urnenphase und Weltgeschichte/Kampf aufloest.
- `action_resolution` ist aktuell eine konkrete Support/Attack-Aufloesung mit
  Bevoelkerungsaenderung. Diese Phase muss durch v0.3-spezifische Module ersetzt
  oder nur noch als Untermechanik im Kampf verwendet werden.
- `cleanup` ist zu generisch. v0.3 braucht eine explizite `round_end`-Phase und
  ggf. separate Aufraeumregeln fuer Intrige, Urnen, Weltgeschichte und Recherche.

## Fehlende v0.3-Mechaniken

### Journalistenpruefung

Aktuell wird der erste Journalist im Setup bestimmt. Es fehlt eine eigene
Phase, die pro Runde prueft, wer Journalist ist, ob die Rolle wechselt und
welche Bedingungen dafuer gelten.

Fehlt:
- `journalist_check` Phase.
- Status, Historie und Wechselregel fuer Journalist.
- Events wie `journalist_checked`, `journalist_changed`, `journalist_retained`.
- Bot-Entscheidung nur falls v0.3 dort aktive Wahlmoeglichkeiten vorsieht.

### Medienmogulwahl

Aktuell wird der erste Medienmogul im Setup bestimmt; `normal_rules` ist als
TODO-Platzhalter auf Startspieler gelegt. v0.3 verlangt eine eigene
Medienmogulwahl vor Draft und Rollenphase.

Fehlt:
- `media_mogul_selection` Phase.
- Regelmodell fuer Kandidaten, Wahlrecht, Gleichstand, Amtsdauer und Wechsel.
- State fuer aktuelle Wahl, Stimmen oder Auswahlgrund.
- Events wie `media_mogul_selection_started`, `media_mogul_candidate_selected`,
  `media_mogul_changed`.

### Diskussion / Intrige

Im aktuellen Code gibt es keine eigenstaendige soziale, verdeckte oder
verhandlungsnahe Intrigenphase. Die alte Planning-Phase kann hoechstens als
technischer Vorlaeufer dienen.

Fehlt:
- `discussion_intrigue` Phase.
- State fuer Intrigenkarten, Zusagen, verdeckte Marker, oeffentliche/private
  Informationen und ggf. temporaere Effekte.
- Legale Bot-Aktionen fuer Intrige.
- Eventtypen fuer Intrigenentscheidung, Enthuellung und Aufloesung.

### Urnenphase

Es gibt aktuell keine Urnen-, Abstimmungs- oder Wahlmechanik ausser
Medienmogul/Journlist-Platzhaltern.

Fehlt:
- `ballot_phase` oder `urn_phase` Modul.
- State fuer Urneninhalt, Stimmen, geheime/oeffentliche Stimmabgabe,
  Stimmberechtigung und Auszaehlung.
- Bot-Entscheidungspunkte fuer Stimmabgabe und ggf. Manipulation.
- Events wie `ballot_started`, `vote_cast`, `ballot_revealed`,
  `ballot_resolved`.

### Weltgeschichte und Kampf

Aktuell existiert nur Support/Attack als abstrakte Bevoelkerungsaenderung.
v0.3 nennt explizit Weltgeschichte und Kampf. Dafuer fehlen eigene State- und
Regelmodelle.

Fehlt:
- `world_history_and_combat` Phase.
- State fuer Weltgeschichte-Ereignisse, Kampfkontexte, beteiligte Fraktionen,
  Angriff/Verteidigung, Modifikatoren, Verluste und globale Effekte.
- Trennung von Weltgeschichte-Effekten und Kampfaufloesung.
- Effect-Trigger fuer v0.3, z.B. `on_world_history`, `before_combat`,
  `after_combat`, `before_vote_count`, `after_vote_count`.

### Rechercheauftraege

Research Orders existieren als verdeckte Karten im Setup, aber sie werden noch
nicht als eigene Rundenphase bearbeitet. Analytics kennt nur Platzhaltermetriken.

Fehlt:
- `research_orders` Phase.
- State fuer Fortschritt, Erfuellung, Belohnung, Scheitern und ggf. Abwurf.
- Bot-Entscheidung fuer Priorisierung, Fortschritt oder Einloesen.
- Events wie `research_order_progressed`, `research_order_completed`,
  `research_order_failed`, `research_order_rewarded`.

## Neue State-Objekte, die fehlen

- `RoleCycleState`: aktuelle und vorherige Rolleninhaber, Amtsdauer,
  Wechselgrund, ggf. Kandidaten.
- `JournalistCheckState`: Pruefergebnis, Ausloeser, naechster Journalist.
- `MediaMogulElectionState`: Kandidaten, Stimmen/Auswahl, Tie-Breaker,
  gewaehlter Spieler.
- `IntrigueState`: verdeckte Intrigen, oeffentliche Intrigen, temporaere Deals,
  ausstehende Aufloesungen.
- `BallotState`: Urne, Stimmen, Wahloptionen, Stimmberechtigte, Auszaehlstatus.
- `WorldHistoryState`: aktives Ereignis, Ereignisdeck oder Tabelle,
  globale Modifikatoren, Dauer.
- `CombatState`: Beteiligte, Ziel, Staerke, Verteidigung, Verluste,
  angewendete Modifikatoren.
- `ResearchOrderState`: Auftrag, Besitzer, Fortschritt, Status, Belohnung.
- `RoundEndState`: auslaufende Effekte, Cleanup-Aktionen, naechster Startspieler.

## Neue Bot-Entscheidungspunkte, die fehlen

- `choose_journalist_check_response`, falls die Pruefung interaktiv ist.
- `choose_media_mogul_candidate` oder `choose_media_mogul_vote`.
- `choose_intrigue_action`.
- `choose_intrigue_target`.
- `choose_vote` fuer die Urnenphase.
- `choose_ballot_manipulation`, falls v0.3 Manipulation erlaubt.
- `choose_world_history_response`.
- `choose_combat_action`.
- `choose_combat_target`.
- `choose_research_order_action`.
- `choose_research_order_to_complete`.
- `choose_round_end_discard_or_cleanup`, falls Handlimit oder Ablaufregeln kommen.

Die Bot-Public-View muss dafuer erweitert werden, ohne fremde Geheimnisse
freizugeben.

## Welche Analytics angepasst werden muessen

Aktuell sind viele Kennzahlen auf `action_revealed`, `action_resolved`,
`support`, `attack` und `population_changed` ausgelegt. Das betrifft besonders:

- Round Summaries: `attacks_this_round`, `supports_this_round`,
  `cards_played_this_round`.
- Bot-Metriken: Attack/Support Ratio, Schaden/Nutzen je Bot.
- Kartenmetriken: Spielquote und Effektmetriken ueber alte Action-Events.
- Inspector: Rundenverlauf zeigt committed/revealed Actions.
- Charts und Reporttexte: Fraktionsbalance bleibt nutzbar, Aktionsmetriken
  muessen v0.3-Begriffe bekommen.

Neue Metriken sollten eingefuehrt werden:

- Journalistenwechsel pro Spiel.
- Medienmogulwechsel und Amtsdauer.
- Draft-Picks nach Kartentyp/Faktion.
- Intrigenaktionen pro Runde und Erfolgsquote.
- Urnenbeteiligung, Stimmverteilungen, manipulierte Stimmen.
- Weltgeschichte-Ereignisse und durchschnittlicher Effekt.
- Kampfanzahl, Kampfverluste, Angreifer-/Verteidigererfolg.
- Rechercheauftrag-Fortschritt und Abschlussquote.
- Population-Swing getrennt nach Intrige, Urne, Weltgeschichte, Kampf und Karten.

## Welche Regeln noch nicht technisch abbildbar sind

Ohne weitere v0.3-Regeldetails sind folgende Bereiche noch nicht eindeutig
implementierbar:

- Exakte Bedingung fuer Journalistenpruefung und Rollenwechsel.
- Exakte Regel fuer Medienmogulwahl.
- Bedeutung und legaler Aktionsraum von Diskussion / Intrige.
- Ob Urnenstimmen geheim, offen, gewichtet, manipulierbar oder fraktionsbezogen sind.
- Ob Weltgeschichte aus Karten, Tabellen, Ereignisdeck oder Config-Triggern kommt.
- Kampfmodell: Beteiligte, Initiative, Zielwahl, Schaden, Schutz, Gleichstaende.
- Rechercheauftrag-Regeln: Fortschritt, Abschluss, Belohnung, Geheimhaltung.
- Siegbedingungen im Zusammenhang mit Urnen, Weltgeschichte, Kampf und Recherche.
- Timing von Karteneffekten innerhalb der neuen Phasen.
- Welche alten Kartenarten weiter existieren: `action`, `propaganda`, `hybrid`,
  `source`, `research_order` reichen vermutlich nicht fuer alle v0.3-Zonen.

Diese Punkte sollten in `rules_v0_3.yaml` als explizite Config-Optionen
modelliert werden oder bis zur Klaerung mit `ConfigError` blockieren.

## Notwendige neue Config-Felder

Neue oder zu erweiternde Modelle:

- `rules_version: "0.3"`
- `round_flow.phases` mit v0.3-Phasen:
  - `round_start`
  - `journalist_check`
  - `media_mogul_selection`
  - `draft`
  - `journalist_media_mogul_phase`
  - `discussion_intrigue`
  - `ballot_phase`
  - `world_history_and_combat`
  - `victory_check`
  - `research_orders`
  - `round_end`
- `roles.journalist_check`
- `roles.media_mogul_selection`
- `intrigue`
- `ballot`
- `world_history`
- `combat`
- `research_orders`
- `round_end`
- `effects.triggers` oder Erweiterung der Card-Effect-Trigger.
- `analytics.v0_3_metrics_enabled` oder automatische Aktivierung ueber
  `rules_version`.

Erste Datei:

- `configs/rules/rules_v0_3.yaml` sollte zunaechst den v0.3-Rundenablauf,
  vorhandene Startregeln und TODO/placeholder Configs enthalten.
- Noch unklare Pflichtregeln sollten nicht still hart codiert werden.

## Notwendige neue Engine-Module

Empfohlene Struktur:

- `src/wsim/engine/phases/round_start.py`
- `src/wsim/engine/phases/journalist_check.py`
- `src/wsim/engine/phases/media_mogul_selection.py`
- `src/wsim/engine/phases/draft.py`
- `src/wsim/engine/phases/role_phase.py`
- `src/wsim/engine/phases/intrigue.py`
- `src/wsim/engine/phases/ballot.py`
- `src/wsim/engine/phases/world_history.py`
- `src/wsim/engine/phases/combat.py`
- `src/wsim/engine/phases/research_orders.py`
- `src/wsim/engine/phases/round_end.py`

Mittelfristig sollte `GameEngine` Phasenhandler ueber ein Registry-System
aufrufen, statt immer mehr `_phase_*`-Methoden direkt in einer grossen Datei zu
halten.

## Notwendige neue Eventtypen

- `journalist_checked`
- `journalist_changed`
- `media_mogul_selection_started`
- `media_mogul_changed`
- `intrigue_committed`
- `intrigue_revealed`
- `intrigue_resolved`
- `ballot_started`
- `vote_cast`
- `ballot_revealed`
- `ballot_resolved`
- `world_history_drawn`
- `world_history_resolved`
- `combat_started`
- `combat_modifier_applied`
- `combat_resolved`
- `research_order_progressed`
- `research_order_completed`
- `research_order_failed`
- `round_ended`

## Notwendige neue Tests

- `rules_v0_3.yaml` laedt und validiert.
- v0.3-Rundenflow wird exakt in der konfigurierten Reihenfolge ausgefuehrt.
- Alte Phasen `planning`, `reveal`, `action_resolution` sind in v0.3 nicht
  erforderlich.
- Journalistenpruefung erzeugt passende Events.
- Medienmogulwahl erzeugt passenden State und Events.
- Draft bleibt reproduzierbar unter v0.3.
- Kombinierte Journalist-/Medienmogulphase bleibt legal.
- Intrigenphase erzeugt nur legale Bot-Aktionen.
- Urnenphase speichert Stimmen korrekt und verletzt Geheimhaltung nicht.
- Weltgeschichte wird reproduzierbar gezogen oder ausgewaehlt.
- Kampf veraendert Populationen nur nach Config-Regeln.
- Rechercheauftraege koennen fortschreiten und abgeschlossen werden.
- Invariants decken neue Kartenorte und neue State-Zonen ab.
- Analytics erzeugt v0.3-Round-Summaries ohne alte Support/Attack-Pflichtfelder.
- Inspector und Export zeigen v0.3-Rundenverlauf.

## Migrationsplan von aktuellem Engine-Stand zu rules_v0_3

1. `rules_version` und `configs/rules/rules_v0_3.yaml` einfuehren.
2. Phasennamen normalisieren:
   - `start_round` -> `round_start`
   - `journalist_phase` + `media_mogul_phase` -> `journalist_media_mogul_phase`
   - `cleanup` -> `round_end`
3. `GameEngine` auf eine Phase-Registry vorbereiten, aber alte Handler
   zunaechst weiter unterstuetzen.
4. Neue v0.3-Phasen als Stubs mit klaren Events und `ConfigError` fuer unklare
   Pflichtregeln einbauen.
5. State-Modelle fuer Rollenwechsel, Urne, Intrige, Weltgeschichte, Kampf und
   Rechercheauftraege einfuehren.
6. Bot-Schnittstellen um v0.3-Entscheidungen erweitern. RandomBot zuerst, danach
   Heuristic/Spezialbots.
7. Alte Support/Attack-Mechanik isolieren:
   - als Legacy-Modul fuer `base_rules.yaml` behalten,
   - nicht mehr als Standard fuer `rules_v0_3.yaml` verwenden.
8. Effect Engine um v0.3-Trigger erweitern.
9. Analytics zweigleisig machen:
   - Legacy-Metriken fuer alte Runs,
   - v0.3-Metriken fuer neue Runs.
10. Dashboard und Inspector auf `rules_version` reagieren lassen.
11. Nach jeder Phase Invariants fuer neue State-Zonen ausfuehren.
12. Erst danach konkrete Karteninhalte und Balancing fuer v0.3 ergaenzen.

## Empfehlung

Der naechste technische Schritt sollte nicht die direkte Ueberschreibung der
alten Action-Resolution sein. Besser ist eine parallele v0.3-Regeldatei mit
neuem Phasenflow und Stub-Handlern. So bleiben bestehende CLI-Befehle, Reports,
Dashboard-Funktionen und Tests stabil, waehrend die neue Engine-Schicht
kontrolliert aufgebaut wird.
