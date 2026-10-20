# Datenschutz / Privacy policy

Stand: 20. Oktober 2026. Diese Erklärung beschreibt den hier vorliegenden Programmcode und den vorgesehenen Windows-Installer. Projektpflege: **Diaphotizo – Detlef Koch**. Sie beschreibt keinen gehosteten Archivdienst: Jede Person betreibt ihre eigene lokale Sammlung.

## Suche und eigene Angaben

Die Suche verarbeitet Begriffe, Filter und Dokumenttexte über eine lokale API auf `127.0.0.1`. Die Anwendung übermittelt Suchbegriffe, Notizen, eigene Schlagwörter oder Personenangaben nicht an den Maintainer oder einen Analysedienst. Im geprüften Quellstand sind keine Telemetrie-, Werbe-, Crash-Reporting- oder externen Analytics-Module eingebunden. Die Oberfläche lädt ihre Skripte, Styles und Sammlungssymbole lokal.

Suchbegriffe und Filter stehen in der lokalen Browseradresse. Der Browser merkt sich je Reiter die zuletzt verwendete Adresse im `sessionStorage`. Browserchronik, Wiederherstellung, Erweiterungen oder eine vom Nutzer aktivierte Browsersynchronisierung können solche Adressen zusätzlich speichern oder verarbeiten. Dafür gelten die Einstellungen und Datenschutzregeln des verwendeten Browsers; die App steuert diese nicht.

## Wann Verbindungen nach außen entstehen

Ein Klick auf **„Jetzt aktualisieren“** oder ein ausdrücklich gestarteter Kommandozeilenabruf ruft öffentliche Quellen ab. Es werden Dokument-URLs, Quellenlisten und konfigurierte Discovery-Filter übertragen. Das sind die im Programm oder in der Quellenkonfiguration festgelegten Auswahlkriterien, nicht die Eingaben im lokalen Suchfeld. Die Gegenstelle erhält dabei übliche Verbindungsdaten wie IP-Adresse, angeforderte URL, Zeitpunkt und die Kennung des Downloaders. Bedingte Abrufe können zuletzt bekannte Serverkennungen wie ETag und Last-Modified mitsenden. Auf Quellwebsites wird nicht geschrieben; es werden keine Kontozugangsdaten benötigt.

| Sammlung | Externe Ziele im vorgesehenen Abruf |
| --- | --- |
| BIP | `bip-jetzt.de` für öffentliche API, Sitemap, Regeln und Seiten. |
| ICC / ICJ | ICJ-Websites und deren freigegebener Dokumenthost; `legal-tools.org` und der zugehörige Dokumenthost `ltd-docs.s3.nl-ams.scw.cloud`; gegebenenfalls verlinkte ICC-Originale. |
| UN | Konfigurierte UN- und UN-Einrichtungsquellen, darunter `un.org`, `ohchr.org`, `ochaopt.org` und `unmissions.org`. Weitere zugelassene Einrichtungen werden nur angesprochen, wenn konfigurierte oder gefundene Dokumentziele dorthin führen. |

Die konkret angesprochenen Quellen ergeben sich aus den Original-Links in der App, der [UN-Startkonfiguration](UN/sources-start.json) und den Downloadern. Die gesamte technische Hostfreigabe und eine Liste der dafür geprüften Programmstellen stehen in [Netzwerkverhalten](docs/NETZWERKVERHALTEN.md).

Wer einen **Original-Link** oder einen Link in einem PDF öffnet, besucht die betreffende externe Website mit dem eigenen Browser bzw. PDF-Programm. Dort können weitere Websitefunktionen, Cookies oder Analysedienste aktiv sein. Die Downloader führen keine JavaScript-Programme der abgerufenen Websites aus. Externe Weiterleitungen können zusätzliche Verbindungsdaten erzeugen; Sperren und Hostprüfungen begrenzen die automatischen Abrufe.

Hinweise betroffener Anbieter: [BIP Datenschutz](https://bip-jetzt.de/impressum-datenschutz/), [UN Privacy Notice](https://www.un.org/en/about-us/privacy-notice), [ICJ-Websitehinweise](https://www.icj-cij.org/disclaimer), [Legal Tools – Kontakt und Verweis auf Datenschutz](https://legal-tools.org/training-and-contact/). Für eigenständige UN-Einrichtungen gelten außerdem die jeweils auf der aufgerufenen Website verlinkten Datenschutzregeln.

## Lokale Speicherung und Protokolle

Dokumente, extrahierte Texte, Kataloge, Suchindizes, Abrufstände, frühere Fassungen und eigene Angaben werden im Installationsordner gespeichert. Dieser liegt standardmäßig im persönlichen App-Verzeichnis. Lauf- und Fehlerprotokolle können Quellen-URLs, technische Fehler und lokale Pfade enthalten. Die üblichen HTTP-Zugriffsprotokolle des lokalen Servers sind deaktiviert; das ist keine Zusage, dass Browser oder Windows keine eigenen Protokolle führen.

Archivdateien werden von der App nicht automatisch hochgeladen. Eine selbst eingerichtete Cloud-Synchronisierung, ein Backupdienst oder das Teilen des Ordners kann sie dennoch übertragen. Dateien mit eigenen Angaben sind nicht zusätzlich durch die App verschlüsselt. Wer Protokolle zur Fehlersuche teilt, sollte sie vorher auf persönliche Daten prüfen.

## Installation, Programmbezug und sonstige Dienste

Der fertige Windows-Installer enthält Python und die Bibliotheken. Die Installation lädt keine weiteren Python-Pakete und ruft noch keine Dokumentarchive ab. Das Programm hat keine automatische Programmupdate-Abfrage. Neue Programmversionen werden bewusst über eine neue Setup-Datei installiert.

Ein Download oder eine Meldung auf GitHub unterliegt der [GitHub Privacy Statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement). Windows und Edge können unabhängig von der App eigene Verbindungen herstellen, etwa für Schutzprüfungen; siehe [Microsoft Privacy Statement](https://www.microsoft.com/en-us/privacy/privacystatement). Für einen anderen Browser gelten dessen eigene Hinweise. SignPath ist als Dienst für eine künftige Freigabesignierung vorgesehen und wird von der installierten Anwendung nicht aufgerufen.

Wer den Quellcode selbst baut, lädt Entwicklungsabhängigkeiten von den in den Buildskripten benannten Anbietern. Das gehört zum Build und nicht zum normalen Betrieb des fertigen Installers.

## Kontrolle und Entfernen

Ohne gestarteten Quellenabruf und ohne Öffnen externer Links kann im gespeicherten Bestand offline recherchiert werden. Die Deinstallation lässt Dokumente, frühere Fassungen und eigene Angaben bewusst im Archivordner zurück. Zum endgültigen Entfernen der eigenen Sammlung zuerst eine gewünschte Sicherung erstellen, das Archiv beenden und die verbleibenden Archivdaten gezielt löschen. Browserdaten müssen bei Bedarf separat in den Browsereinstellungen gelöscht werden. Der Maintainer hat keinen Fernzugriff auf diese lokale Sammlung.
