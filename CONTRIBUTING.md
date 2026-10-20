# Mitwirken

Verbesserungen an Suche, Zugänglichkeit, Quellenerkennung und Dokumentation sind willkommen. Maintainer ist **Detlef Koch / Diaphotizo**. Beiträge zum eigenen Programmcode werden unter der [MIT-Lizenz](LICENSE) angenommen; mit einem Beitrag müssen die dafür nötigen Rechte vorliegen.

## Einen Fehler beschreiben

Im veröffentlichten Repository kann ein Issue mit Version, Windows-Version, betroffener Sammlung, erwartetem und beobachtetem Verhalten angelegt werden. Ein kleines Beispiel und die genaue Fehlermeldung helfen. Persönliche Dokumente, eigene Notizen, Zugangsdaten und unbereinigte lokale Dateipfade nicht öffentlich hochladen. Sicherheitsprobleme über den in [SECURITY.md](SECURITY.md) beschriebenen Weg melden.

## Änderungen vorbereiten

1. Mit einer sauberen Quellcodekopie arbeiten, niemals mit dem eigenen gefüllten Archiv.
2. Einen eigenen Branch erstellen und die Änderung auf ein klar beschriebenes Problem begrenzen.
3. Passende Tests ergänzen, wenn Verhalten, Datenhaltung, Quellenzugriff oder Installer geändert werden. Synthetische Beispieldaten verwenden; öffentliche PDFs und eigene Sammlungen nicht in Tests aufnehmen.
4. Den vorhandenen Prüfablauf und die betroffenen Tests ausführen. Einstieg: [ENTWICKLUNG.md](ENTWICKLUNG.md), [pruefen.py](pruefen.py), [Installer-Build](installer/README.md).
5. Im Pull Request Zweck, Auswirkung, Prüfung und verbleibende Grenzen erklären. Fremde Beiträge werden vor der Übernahme durch den Maintainer geprüft.

Die gemeinsame Programmbasis liegt in `Recherche`; der BIP-Crawler in `BIP`. Ein eigenes Python für die Entwicklung ist vom eingebetteten Python des fertigen Installers zu unterscheiden. Die Buildanleitung nennt die passenden Versionen und Befehle.

## Eigenschaften, die erhalten bleiben müssen

- Quellen nur lesend abrufen; Sperren und Abruflimits nicht umgehen.
- Bestehende Dokumente, frühere Fassungen und eigene Angaben bei Fehlern, Aktualisierungen und Deinstallation bewahren.
- Den lokalen Server auf Loopback beschränken; Host- und Schreibzugriffsschutz erhalten.
- Keine Telemetrie, externen Analysedienste oder Uploads persönlicher Archivdaten hinzufügen, ohne das Verhalten ausdrücklich neu zu entwerfen und zu dokumentieren.
- Neue Quellen oder Netzwerkanfragen in [PRIVACY.md](PRIVACY.md) und den Quellenhinweisen erfassen.
- Buildskripte, Abhängigkeitsdateien und Workflows genauso sorgfältig prüfen wie Anwendungscode. Kostenpflichtige Dienste gehören nicht zum Freigabeablauf.

Für Veröffentlichungen gilt der [Freigabeablauf](docs/FREIGABE.md). Signieranfragen erfordern nach einer möglichen Zulassung eine eigene manuelle Entscheidung nach der [Code signing policy](CODE_SIGNING.md).
