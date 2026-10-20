# Mein Recherchearchiv

**BIP, ICC / ICJ und UN-Dokumente lokal sammeln und durchsuchen.** Eine gemeinsame Windows-App mit drei getrennten Sammlungen, Volltextsuche, Filtern, eigenen Angaben und einem Aktualisierungsbutton je Bereich.

Das Projekt wird von **Diaphotizo – Detlef Koch** für die eigene Recherche und die Weitergabe als freie Software entwickelt. Hintergrund: [Diaphotizo](https://diaphotizo.com/) und [öffentliche Anbieterangaben](https://diaphotizo.com/impressum/).

## Drei Sammlungen

| Bereich | Quellen und Umfang |
| --- | --- |
| **BIP** | Öffentliche Artikel und Seiten von bip-jetzt.de, lokal als TXT und PDF. |
| **ICC / ICJ** | Die eingerichteten englischsprachigen Gerichtsquellen: zwölf ICJ-Verfahren sowie öffentliche Legal-Tools-Register zu ICC-01/18 und ICC-01/13. |
| **UN** | Eingerichtete Quellen zu Deutschland, Israel, Palästina, Gaza, Westbank und UNRWA; englische und vorhandene deutsche Fassungen. |

**Die Sammlungen beginnen leer.** Programmcode und öffentliche Quellenkonfigurationen sind enthalten, persönliche Dokumente und Angaben nicht. „Jetzt aktualisieren“ lädt die erreichbaren Quellen des geöffneten Reiters, erstellt den Suchindex und zeigt verbleibende Lücken. Es werden keine vollständigen historischen Bestände und keine automatische Übersetzung zugesichert. Frühere Fassungen und eigene Angaben bleiben bei Quellenaktualisierungen erhalten.

## Installation unter Windows 11

Vorgesehen ist **Windows 11 x64 auf Intel-/AMD-Rechnern**. Der Installer bringt Python und die benötigten Bibliotheken mit. Nutzer benötigen keine eigene Python-Installation, kein Codex, kein Dropbox-Konto und keinen GitHub-Account für die spätere Nutzung. Auf ARM-Geräten ist die Funktion nicht zugesichert.

Nach einer veröffentlichten Freigabe wird die Setup-Datei zusammen mit ihrer SHA256-Prüfsumme im GitHub-Bereich **Releases** angeboten. Die Anleitung befindet sich bereits in [INSTALLATION.html](INSTALLATION.html), als [Text](INSTALLATION.txt) und im [Kurzstart](docs/KURZSTART.md).

Die erste öffentliche Ausgabe ist als **unsigniert** vorbereitet. Eine öffentliche Freigabe oder SignPath-Zulassung wird mit diesem vorbereiteten Quellstand noch nicht behauptet. Windows kann bei unsignierten Dateien warnen. Nur Dateien aus der erwarteten Quelle verwenden; Schutzfunktionen nicht abschalten.

## Lokal recherchieren

Suchbegriffe und eigene Angaben werden durch die App lokal verarbeitet. Ein Quellenabruf verbindet den Rechner mit den eingerichteten Websites. Bereits gespeicherte Dokumente sind offline nutzbar. Details zu lokalen Dateien, Browserdaten und externen Verbindungen stehen in der [Datenschutzerklärung](PRIVACY.md).

Der Installer richtet eine Installation für das eigene Windows-Konto ein. Sie enthält Programm und Archivdaten. Zum Sichern die Aktualisierungen abschließen lassen, **Archiv beenden** wählen und den gesamten Installationsordner einschließlich `Recherche`, `BIP`, `ICC-ICJ` und `UN` kopieren. Den installierten Ordner nicht verschieben; Kataloge können absolute Dokumentpfade enthalten. Die Deinstallation lässt Archivdaten erhalten.

**„Jetzt aktualisieren“ lädt Dokumente; neue Programmversionen werden über ein neues Setup installiert.**

## Quellcode und Mitwirkung

| Ordner | Aufgabe |
| --- | --- |
| `BIP` | BIP-Crawler, BIP-Suche und Oberfläche. |
| `Recherche` | Gemeinsamer Server, ICC-/ICJ- und UN-Programmteile sowie gemeinsame Oberfläche. |
| `ICC-ICJ`, `UN` | Einstieg, öffentliche Konfiguration und später lokale Sammlungsdaten. |
| `installer` | Buildskripte, Abhängigkeitsnachweise und Windows-Setup. |
| `docs` | Nutzung, Freigabeablauf und technische Datenschutznotizen. |

[Mitwirken](CONTRIBUTING.md) · [Installer bauen](installer/README.md) · [Sicherheitsmeldungen](SECURITY.md) · [Freigabeablauf](docs/FREIGABE.md)

## Code signing policy

[Code signing policy](CODE_SIGNING.md): Die kostenlose Signierung über SignPath Foundation ist **geplant und von einer Annahme abhängig**. Derzeit wird keine SignPath-Signatur oder Förderung behauptet. Eine Signatur wäre keine Garantie für einen warnungsfreien Start auf jedem Windows-Rechner.

## Lizenz und Quellenrechte

Der eigene Programmcode und die eigenen Sammlungssymbole stehen unter der [MIT-Lizenz](LICENSE). Für Bibliotheken gelten deren eigene Lizenzen; siehe [Drittquellen](DRITTQUELLEN.md). **Heruntergeladene Artikel, Gerichts- und UN-Dokumente fallen nicht dadurch unter MIT.** Ihre Herkunft und jeweiligen Nutzungsbedingungen bleiben maßgeblich.

Das ist ein unabhängiges Recherchewerkzeug. Die Sammlungsnamen und eigenen Symbole bezeichnen Quellenbereiche; sie behaupten keine Beauftragung, Partnerschaft oder Billigung durch BIP, ICC, ICJ oder die Vereinten Nationen.
