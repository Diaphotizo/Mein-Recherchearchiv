# Windows-Installer bauen

Die Anwendung liegt in der Repository-Wurzel; dieser Ordner enthält nur
Installer-Quellen und Build-Anweisungen. Die normale Ausgabe ist **unsigniert**.
Die SignPath-Anbindung ist ein vorbereiteter Plan, noch keine bestätigte Signatur.

## Lokal

Benötigt werden Windows x64 und reguläres Python 3.13 mit pip. Inno Setup 6.7.3
wird ausschließlich für den Build gebraucht. Endnutzer benötigen diese Werkzeuge
nicht; der fertige Installer enthält Python 3.13.16 und die Bibliotheken.

In einem neuen, leeren Checkout, aus dessen Wurzel:

```powershell
python installer/build_setup.py --prepare-runtime
./installer/install_inno.ps1
./runtime/python.exe -B pruefen.py
./runtime/python.exe -B installer/test_runtime.py
python -m unittest discover -s installer -p "test_installer_support.py"
python -m unittest discover -s installer -p "test_build_setup.py"
python installer/build_setup.py --compiler installer/tools/InnoSetup/ISCC.exe --isolated-test
python installer/test_setup_integration.py
python installer/build_setup.py --compiler installer/tools/InnoSetup/ISCC.exe --source-zip
```

Die Befehle müssen jeweils erfolgreich sein, bevor der nächste ausgeführt wird.
`install_inno.ps1` lädt die feste Inno-Version vom offiziellen Projekt, prüft
SHA-256 und die Windows-Signatur des Herstellers und installiert den Compiler
innerhalb von `installer/tools`. Dabei entstehen lokale Einträge des Compiler-
Installers; deshalb einen Build-Rechner oder eine Wegwerf-Testumgebung verwenden.
Bereits vorhandene Compiler-, Laufzeit- und Ausgabeordner werden nicht überschrieben.

`installer/output` enthält die Windows-11-EXE und das Quellcode-ZIP.
Die Ausgabe `Installer-Pruefung.exe` ist ausschließlich für Tests bestimmt und
darf nicht verteilt werden. Sie erlaubt zum Testen zusätzlich Windows Server 2022,
erzeugt keinen Windows-Deinstallationseintrag und wird ohne Verknüpfungen installiert.
Die normale EXE verlangt weiterhin mindestens Windows 11 Build 22000.
Ein vollständiger manueller Windows-11-Test einschließlich Startmenü und
Desktopverknüpfung bleibt vor jeder Veröffentlichung erforderlich.

Nur Quellcode verpacken, ohne Downloads oder Laufzeit:

```powershell
python installer/build_setup.py --source-zip
```

Das ZIP behält die Repository-Struktur und enthält ein SHA-256-Dateiverzeichnis.
Archivdaten, PDFs, eigene Angaben, Zugangsdaten, heruntergeladene Laufzeit und
Build-Werkzeuge gehören nicht dazu.

## Manuell auf GitHub

Der Workflow `.github/workflows/windows-unsigned.yml` im Quellcodepaket
startet ausschließlich per „Run workflow“, nur im öffentlichen Repository
`Diaphotizo/Mein-Recherchearchiv` und nach bewusster Bestätigung der Kostenprüfung.
Er benutzt einen normalen `windows-2022`-Runner, keinen kostenpflichtigen
Spezialrunner, keine Caches und keine Signiergeheimnisse. Pushes und Pull Requests
lösen ihn nicht aus. Es wird kein GitHub-Release veröffentlicht.

Standardrunner-Minuten in öffentlichen Repositories sind kostenlos; Speicher für
Artefakte unterliegt trotzdem dem kontoweiten Freikontingent. Vor dem Start muss
das Konto ohne Zahlungsmethode oder mit wirksamer harter Ausgabensperre eingerichtet
und freier Speicher vorhanden sein. Die Workflow-Bestätigung prüft diese
Kontoeinstellungen nicht automatisch. Pro Lauf sind höchstens 100 MiB Ausgabe und
ein Tag Aufbewahrung vorgesehen; alte Ergebnisse nach dem Herunterladen entfernen.
Bei ausgeschöpftem Freikontingent nicht kostenpflichtig erweitern.

Der Workflow wurde lokal vorbereitet; ein erfolgreicher Lauf auf GitHub ist erst
nach dem tatsächlichen manuellen Start belegbar. Alle Cloud-Signierschritte bleiben
deaktiviert; siehe [SignPath-Vorbereitung](signpath/README.md).

## Fixierte Eingaben

- Python-Download und dessen SHA-256: `build_setup.py`.
- Bibliotheken mit exakten Versionen und SHA-256: `wheels.lock.txt`.
- Inno Setup 6.7.3: `install_inno.ps1`.
- GitHub-Actions sind auf vollständige, auf den offiziellen Release-Seiten
  nachgeprüfte Commit-IDs fixiert:
  [checkout v7.0.1](https://github.com/actions/checkout/commit/3d3c42e5aac5ba805825da76410c181273ba90b1),
  [setup-python v7.0.0](https://github.com/actions/setup-python/commit/5fda3b95a4ea91299a34e894583c3862153e4b97),
  [upload-artifact v7.0.1](https://github.com/actions/upload-artifact/commit/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a).

Aktualisierungen dieser Eingaben erfordern neue Prüfung und Tests.

Siehe auch [GitHub-Kostenregeln](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
und [Inno-Setup-Release](https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3).
