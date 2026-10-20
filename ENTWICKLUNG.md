# Entwicklung der Windows-Ausgabe

Diese Ausgabe verwendet eine mitgelieferte Python-Laufzeit unter `runtime`.
Der Anwendungscode bleibt lesbar und bearbeitbar. Eine Änderung an installiertem
Programmcode ist eine lokale Anpassung; ein späteres Setup kann sie ersetzen.
Eigene Recherchedaten gehören in die vorgesehenen Sammlungsdaten, nicht in Code.

Das separate Paket `Mein-Recherchearchiv-Windows-Installer-Quellcode.zip` enthält
zusätzlich `setup.iss`, `build_setup.py`, die mit Prüfsummen fixierte Bibliotheksliste,
Installer-Prüfungen und `ERSTELLEN.txt` mit reproduzierbaren Build-Schritten.
Das ältere `paket_erstellen.py` liefert hier nur die gemeinsame Quelldateiliste.
Quellcode-ZIPs dieser Ausgabe werden mit `build_setup.py --source-zip` erstellt.

## Programmteile

- BIP: `BIP/crawler.py` und `BIP/recherche/`.
- Gemeinsamer Server: `Recherche/research_portal/server.py`.
- ICC/ICJ: `Recherche/research_portal/courts.py`, `update_courts.py`, `index_courts.py`.
- UN: `Recherche/research_portal/un_library.py`, `update_un.py`, `index_un.py`, `un_*.py`.
- Oberflächen: `BIP/recherche/web/` und `Recherche/web/`.
- Windows-Start: `archiv_start.py`; sichere Setup-Hilfen: `installer_support.py`.
- Öffentliche UN-Startquellen: `UN/sources-start.json`.

Die drei Sammlungsordner und Recherche bilden zusammen eine Installation.
Der eingebettete Interpreter enthält explizite Suchpfade für BIP und Recherche,
auch für Aktualisierungsprozesse und Windows-Spawn-Arbeitsprozesse.
Benutzerkonfiguration und Archive dürfen beim Installer-Build nicht eingepackt
oder bei Aktualisierung/Deinstallation gelöscht werden.

`PRUEFBERICHT.txt` benennt die durchgeführten Prüfungen und ihre Grenzen.
`INSTALLER-INFO.txt`, `LICENSE` und `DRITTQUELLEN.md` dokumentieren Herkunft und Rechte.
