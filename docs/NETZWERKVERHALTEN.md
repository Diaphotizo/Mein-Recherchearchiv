# Technische Grundlage der Datenschutzerklärung

Diese Notiz bezieht sich auf den zur Veröffentlichung vorbereiteten Quellstand vom 20. Oktober 2026. Es handelt sich um eine Codeprüfung der aufgeführten Funktionen, nicht um eine Messung sämtlicher Verbindungen von Windows, Browsern oder Fremdbibliotheken.

| Funktion | Prüfstelle | Beobachtetes Verhalten |
| --- | --- | --- |
| Gemeinsamer Server | `Recherche/research_portal/server.py`, `BIP/recherche/server.py` | Bindet an `127.0.0.1`; Suche und eigene Angaben über lokale API; normale HTTP-Access-Logs deaktiviert. |
| Browseroberfläche | `BIP/recherche/web/app.js`, `Recherche/web/courts.js`, `Recherche/web/un.js` | Relative lokale API-Aufrufe; Suchzustand wird in die lokale URL geschrieben. |
| Wechsel zwischen Sammlungen | `Recherche/web/collections.js` | Letzte Adresse pro Sammlung in `sessionStorage`; keine externe Adresse als Speicherziel. |
| BIP-Abruf | `BIP/crawler.py` | Öffentliche GET-Abrufe, Kennung des Crawlers, API/Sitemap und Seiten; lokale Ausgabe. |
| ICC-/ICJ-Abruf | `Recherche/research_portal/update_courts.py` | GET an freigegebene Hosts; feste Verfahrensfilter; Servervalidatoren für bedingte Abrufe. |
| UN-Abruf | `Recherche/research_portal/update_un.py` und `un_*.py` | GET für konfigurierte und gefundene Quellen; Host- und Weiterleitungsprüfung, robots-Regeln, Abrufpausen. |
| Start/Einrichtung | `archiv_start.py`, `installer_support.py` | Lokale Erreichbarkeitsprüfung, freie lokale Adresse, Öffnen des lokalen Appfensters; keine Telemetrie-Abfrage. |

In den Anwendungsskripten und lokalen HTML-/CSS-/JavaScript-Dateien wurden keine externen Analytics-, Anzeigen- oder Crash-Reporting-Endpunkte gefunden. UI-Schriftarten und Symbole werden nicht über einen externen CDN-Dienst angefordert. Diese Aussage gilt für den geprüften Stand; neue Abhängigkeiten oder Netzwerkfunktionen müssen erneut geprüft werden.

## Zugelassene Quellen

ICC / ICJ erlaubt `www.icj-cij.org`, `icj-cij.org`, `api.icj-cij.org`, `icj-web.leman.un-icc.cloud`, `legal-tools.org`, `www.legal-tools.org`, `ltd-docs.s3.nl-ams.scw.cloud`, `www.icc-cpi.int` und `icc-cpi.int`.

Die UN-Hostfreigabe umfasst die folgenden Domains einschließlich ihrer Unterdomains: `un.org`, `unmissions.org`, `ohchr.org`, `unrwa.org`, `ochaopt.org`, `who.int`, `unicef.org`, `wfp.org`, `unctad.org`, `ilo.org`, `unesco.org`, `undp.org`, `unhcr.org`, `refworld.org`, `unwomen.org`, `unfpa.org`, `unops.org`, `unhabitat.org`, `unece.org`, `unep.org`, `fao.org`. Eine Freigabe allein löst keine Anfrage aus.

Die gelieferte [UN-Startkonfiguration](../UN/sources-start.json) nennt konkret `digitallibrary.un.org`, `docs.un.org`, `docstore.ohchr.org`, `spcommreports.ohchr.org`, `unispal.un.org`, `unsco.unmissions.org`, `www.ochaopt.org` und `www.un.org`. Gefundene Dokumente und zulässige Weiterleitungen können weitere freigegebene Ziele hinzufügen. BIP beginnt bei `bip-jetzt.de`.

BIP prüft auch die endgültige Antwortdomain; sein Standard-HTTP-Client kann einer Weiterleitung folgen, bevor diese Prüfung greift. Deshalb wird in der Datenschutzerklärung keine absolute Zusicherung gemacht, dass niemals ein weiterer Host kontaktiert wird.

## Was nicht aus dem Quellstand gefolgert werden darf

Die Anwendung ist kein vollständiger Schutz vor Browserchronik, Erweiterungen, Cloud-Synchronisierung oder Betriebssystemdiensten. Die Loopback-Bindung ist keine Anmeldung oder Verschlüsselung gegenüber anderen lokalen Prozessen. Protokolle und Kataloge können lokale Pfade enthalten. Die App stellt keine Löschzusage für Browserdaten oder Sicherungskopien aus.

[Datenschutzerklärung](../PRIVACY.md) · [Sicherheit](../SECURITY.md)
