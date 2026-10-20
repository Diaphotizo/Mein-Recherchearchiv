# SignPath-Vorbereitung – noch keine aktive Signierung

Dieses Verzeichnis ist ein technischer Entwurf für den kostenlosen Foundation-
Antrag. Es enthält keine Zugangsdaten und keinen ausführbaren Signierworkflow.
Die Aufnahme, das Zertifikat und die unten beschriebene Pipeline müssen erst
von SignPath bestätigt werden. Der aktuelle GitHub-Build bleibt unsigniert.

## Vor Aktivierung zu klären

- Zulassung des öffentlichen Projekts und Bestätigung, dass das Foundation-
  Angebot kostenlos genutzt werden kann.
- Organisation-ID, Projekt-Slug sowie zwei passende Signier-/Artefaktrichtlinien:
  eigenes Deinstallationsprogramm und fertiger Installer.
- Repository-Bindung an `Diaphotizo/Mein-Recherchearchiv`, erlaubte Commits/Tags,
  Produktname und Versionswerte. Nur Änderungen aus geprüftem Quellcode signieren.
- SHA-256, vertrauenswürdiger Zeitstempel und der tatsächlich zugelassene
  Zertifikatsinhaber. Unter dem Foundation-Modell ist das **SignPath Foundation**.
- Eine befugte Person muss jede Release-Signierung bewusst freigeben; MFA ist
  für Repository und SignPath erforderlich.

Nur nach Zulassung würde `SIGNPATH_API_TOKEN` als geschütztes GitHub-Secret
hinzukommen. Es gehört weder in Dateien noch in den Antrag oder in Logs.
IDs und Richtliniennamen sind später aus der tatsächlichen Einrichtung zu
übernehmen; dieser Entwurf erfindet dafür keine Werte.

## Inno Setup: innen und außen signieren

Der Installer ist eine Inno-Setup-EXE, kein MSI. Die SignPath-Referenz beschreibt
Authenticode für PE-Dateien; daraus folgt keine automatische Signierung eines
im Inno-Datenstrom eingebetteten Uninstallers. Nur die fertige äußere EXE zu
signieren wäre deshalb unvollständig.

Inno unterstützt einen vorab signierten Uninstaller über `SignedUninstaller=yes`
und `SignedUninstallerDir`. Vorgeschlagen ist ein zweistufiger Ablauf im selben
GitHub-Workflow: zuerst den von Inno erzeugten Uninstaller als GitHub-Artefakt
signieren lassen, ihn geprüft wieder einsetzen, dann das fertige Setup als
zweites GitHub-Artefakt signieren lassen. Einzelheiten und Freigabepunkte stehen
in [workflow-plan.txt](workflow-plan.txt). Dieser Ablauf ist **nicht cloudgetestet**
und muss mit SignPath abgestimmt werden.

Unveränderte Python- und Bibliotheksbinärdateien bleiben im Installer enthalten.
Sie werden nicht einzeln unter dem Projektzertifikat neu signiert.

## Prüfkriterien für die erste signierte Ausgabe

1. Beide Artefakte stammen nachvollziehbar aus demselben freigegebenen Commit
   und GitHub-gehosteten Build; kein lokal hochgeladener Ersatz.
2. Windows bestätigt Setup und den tatsächlich installierten `unins000.exe`:
   gültige Authenticode-Signatur, erwarteter Herausgeber und gültiger Zeitstempel.
3. Installation, Start aller drei Bereiche, Upgrade, aktive Aktualisierungssperre
   und Deinstallation erhalten alle eigenen Daten.
4. Endgültige signierte Dateien erhalten neue SHA-256-Prüfsummen. Bestehende
   Ausgaben bleiben erhalten; keine automatische Veröffentlichung.

Quellen:
[Foundation-Bedingungen](https://signpath.org/terms),
[GitHub-Herkunftsprüfung](https://docs.signpath.io/trusted-build-systems/github),
[PE-Signierung](https://docs.signpath.io/artifact-configuration/reference),
[Inno: SignedUninstaller](https://jrsoftware.org/ishelp/topic_setup_signeduninstaller.htm),
[Inno: SignedUninstallerDir](https://jrsoftware.org/ishelp/topic_setup_signeduninstallerdir.htm).
