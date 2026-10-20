# Sicherheit

Mein Recherchearchiv ist eine lokale Einzelplatzanwendung. Der Server bindet an `127.0.0.1`; er ist nicht als öffentlich erreichbarer oder gemeinsam genutzter Netzwerkdienst vorgesehen. Schreibende Browseranfragen werden auf Host, Ursprung und Sitzungstoken geprüft. Das ersetzt keine Trennung gegenüber anderen Programmen im gleichen Windows-Benutzerkonto.

## Ein Sicherheitsproblem melden

Bitte keine ausnutzbare Schwachstelle und keine privaten Archivdateien in ein öffentliches Issue stellen. Im veröffentlichten Repository kann **Security → Report a vulnerability** verwendet werden, sofern der Maintainer diese Funktion eingerichtet hat. Solange der private GitHub-Meldeweg nicht verfügbar ist, über [Diaphotizo kontaktieren](https://diaphotizo.com/kontakt/) zunächst einen vertraulichen Austausch anfragen; im Erstkontakt keine Zugangsdaten, vollständigen Archive oder vertraulichen Dokumente senden.

Hilfreich sind betroffene Version, nachvollziehbare Schritte mit künstlichen Beispieldaten, erwartete Wirkung und mögliche Auswirkungen. Eine Antwortfrist oder durchgehende Betreuung kann für dieses kleine Projekt nicht zugesagt werden.

## Unterstützte Versionen

Sicherheitskorrekturen sind für die jeweils aktuelle veröffentlichte Version vorgesehen. Es gibt noch keine Zusage zur Pflege älterer Versionslinien. Vor einer neuen Programmversion das Archiv vollständig sichern. Ein Dokumentabruf über „Jetzt aktualisieren“ ersetzt kein Programmupdate.

## Dateien und Vertrauensgrenzen

Quellen werden aus dem Internet abgerufen. PDF-Parser und das zum Öffnen verwendete PDF-Programm verarbeiten fremde Inhalte; sie sollen aktuell gehalten werden. Die Anwendung umgeht keine Zugriffs- oder Authentifizierungssperren. Der eigene Archivordner wird von der App nicht zusätzlich verschlüsselt. Windows-Berechtigungen, Datenträgerverschlüsselung und Sicherungen liegen beim Betreiber.

Die erste Ausgabe ist als unsigniert vorbereitet. Eine SHA256-Prüfsumme erkennt Veränderungen gegenüber einem vertrauenswürdig bezogenen Vergleichswert; sie ist allein kein Herkunftsnachweis. Künftige Signierung und Verantwortlichkeiten: [Code signing policy](CODE_SIGNING.md). Keine Windows-Schutzfunktion muss für den vorgesehenen Betrieb deaktiviert werden.
