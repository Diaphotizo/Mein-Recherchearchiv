# Drittquellen und Symbole

Stand: 20. Oktober 2026.

Dieses Verzeichnis unterscheidet eigene Gestaltung, externe Software und später abgerufene Quellen. Die MIT-Lizenz des Projekts überträgt keine Rechte an fremden Programmen, Kennzeichnungen, Schriften oder Dokumenten. Für externe Bestandteile gelten die jeweils mitgelieferten Lizenztexte und Hinweise.

## Quellcode und Windows-Installer

Das GitHub-Quellcodepaket enthält den eigenen Programmcode, öffentliche Quellenkonfigurationen und die fertigen Navigationssymbole. Es enthält weder eine Python-Laufzeit noch eine eingerichtete `.venv`, heruntergeladene Dokumentbestände oder persönliche Angaben. Wer die Anwendung aus dem Quellcode einrichtet, installiert Python und die Pakete aus `requirements.txt` separat.

Der daraus erstellte Windows-Installer ist anders zusammengesetzt: Er liefert Python 3.13.16 und die festgelegten Python-Pakete mit. Die Installation der App benötigt deshalb keinen zusätzlichen Python- oder Paketdownload. Die verwendeten Paketversionen stehen in `requirements-getestet.txt`; `installer/wheels.lock.txt` legt die Wheel-Dateien mit Prüfsummen fest. Die Laufzeit und Pakete werden beim Installer-Build abgerufen, nicht im GitHub-Quellcode gespeichert.

Der Installer-Build übernimmt die Dateien der Python-Laufzeit und der installierten Pakete einschließlich vorhandener Lizenz- und Copyrightdateien. Diese Hinweise müssen bei einer Weitergabe des Installers oder seiner Laufzeit erhalten bleiben. Dazu zählen insbesondere die Python-Lizenz, Lizenzdateien in Paketverzeichnissen und in `*.dist-info` sowie zusätzliche Hinweise nativer Komponenten wie PDFium. Diese Übersicht ersetzt diese Texte nicht und ist keine Zusicherung, dass jede denkbare Weitergabeform alle jeweiligen Lizenzbedingungen erfüllt.

## Python und Bibliotheken

| Komponente | Verwendung | Projekt und Lizenzinformationen |
| --- | --- | --- |
| Python | Laufzeit und Standardbibliothek | [Python](https://www.python.org/), [Lizenztexte](https://docs.python.org/3/license.html) |
| lxml | Lesen und Auswerten von HTML/XML | [Projekt](https://lxml.de/), [Paket](https://pypi.org/project/lxml/) |
| ReportLab | Erstellung der lokalen BIP-PDFs | [Projekt](https://www.reportlab.com/), [Paket](https://pypi.org/project/reportlab/) |
| pypdf | Lesen und Prüfen von PDFs | [Projekt](https://pypdf.readthedocs.io/), [Paket](https://pypi.org/project/pypdf/) |
| pypdfium2 / PDFium | PDF-Prüfung und Textextraktion | [Projekt und Lizenzhinweise](https://github.com/pypdfium2-team/pypdfium2#licensing) |
| python-bidi | Text mit gemischter Schreibrichtung | [Projekt](https://github.com/MeirKriheli/python-bidi), [Paket](https://pypi.org/project/python-bidi/) |
| arabic-reshaper | Formung arabischer Schrift für PDF-Ausgabe | [Projekt](https://github.com/mpcabd/python-arabic-reshaper), [Paket](https://pypi.org/project/arabic-reshaper/) |
| pdfplumber | Prüfung auslesbarer PDF-Inhalte | [Projekt](https://github.com/jsvine/pdfplumber), [Paket](https://pypi.org/project/pdfplumber/) |
| Pillow | Bildverarbeitung | [Projekt](https://python-pillow.org/), [Lizenz](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| pdfminer.six | Textextraktion für pdfplumber | [Projekt](https://github.com/pdfminer/pdfminer.six), [Paket](https://pypi.org/project/pdfminer.six/) |
| charset-normalizer | Zeichencodierungserkennung | [Projekt](https://github.com/jawah/charset_normalizer), [Paket](https://pypi.org/project/charset-normalizer/) |
| cryptography | Kryptografische Funktionen für PDF-Verarbeitung | [Projekt](https://github.com/pyca/cryptography), [Paket](https://pypi.org/project/cryptography/) |
| cffi | Anbindung nativer Bibliotheken | [Projekt](https://cffi.readthedocs.io/), [Paket](https://pypi.org/project/cffi/) |
| pycparser | C-Parser als Paketabhängigkeit | [Projekt](https://github.com/eliben/pycparser), [Paket](https://pypi.org/project/pycparser/) |

Die Tabelle erfasst die Python-Pakete aus `requirements-getestet.txt`. Diese Pakete können ihrerseits native Komponenten, Schriftdateien oder weitere Bestandteile enthalten. Maßgeblich bleiben die Lizenztexte der tatsächlich ausgelieferten Versionen und ihrer eingebundenen Komponenten. Beim Aktualisieren der Abhängigkeiten sind auch deren Hinweise zu prüfen und mitzuliefern.

## Werkzeuge zur Erstellung

Die optionale Neuerstellung der Windows-Symbole verwendet Node.js und [sharp](https://sharp.pixelplumbing.com/), einschließlich dessen nativer Abhängigkeiten. Die fertigen Symbole liegen im Quellcodepaket; Node.js und sharp sind für die Nutzung der Recherche-App nicht erforderlich und werden vom Windows-Installer nicht mitgeliefert.

Der Windows-Installer wird mit [Inno Setup](https://jrsoftware.org/isinfo.php) erstellt. Für dieses Werkzeug und seine Installer-Komponenten gelten die [Lizenzbedingungen des Projekts](https://jrsoftware.org/files/is/license.txt). Eine digitale Signatur kennzeichnet die Herkunft einer ausführbaren Datei; sie verändert keine Urheber- oder Lizenzrechte der enthaltenen Bestandteile.

## Eigene Navigationssymbole und Kennzeichnungen

Die veröffentlichten Navigationssymbole wurden für dieses Projekt in Code gestaltet:

| Datei | Gestaltung und Herkunft |
| --- | --- |
| `Recherche/web/archive.svg` und `archive.ico` | Weltkugel aus Kreisen, Ellipse und Linien mit Lupe; eigene SVG-Geometrie. |
| `Recherche/web/courts.svg` und `courts.ico` | Stilisiertes Gerichtsgebäude aus Dreieck, Rechtecken und Säulen; eigene SVG-Geometrie. |
| `Recherche/web/recherche.ico` | Ältere Variante derselben selbst gezeichneten Säulengestaltung; Generator `Recherche/scripts/create_icon.py`. |
| `BIP/recherche/web/favicon.svg` und `bip.ico` | Selbst gesetzte Großbuchstaben „BIP“ auf grünem Grund. Das SVG nutzt eine lokal verfügbare Serifenschrift; eine Schriftdatei oder ein BIP-Bildlogo wird nicht mitgeliefert. |
| `Recherche/web/un.svg` und `un.ico` | Selbst gezeichnete Buchstaben „UN“ aus einfachen Linienpfaden; keine externe Schriftdatei oder Bildvorlage. |

Die eigenen Vektorzeichnungen und daraus erzeugten Symbole stehen zusammen mit dem eigenen Programmcode unter der Projektlizenz MIT. Die öffentliche Fassung enthält kein offizielles UN-Emblem und keine daraus übernommene Weltkarten- oder Olivenzweiggeometrie. Die Datei `Recherche/web/un-emblem-source.json` beschreibt das neue eigene Buchstabensymbol; ihr bisheriger Dateiname bleibt für die Paketierung erhalten.

Die Bezeichnungen BIP, ICC, ICJ und UN benennen die jeweiligen Quellensammlungen. Rechte an Namen und Kennzeichen der jeweiligen Organisationen werden nicht übertragen. Das Recherchearchiv ist eine unabhängige Anwendung und wird nicht als offizielle, empfohlene oder freigegebene App dieser Organisationen angeboten.

## Schriften

Die Oberfläche nutzt Systemschriften wie Segoe UI, Georgia und Arial. Diese Systemschriftdateien werden nicht mitgeliefert. Für die PDF-Erzeugung werden verfügbare Systemschriften oder die mit ReportLab gelieferten Vera-Schriften verwendet. Die Vera-Schriften und ihre Lizenzhinweise gehören beim Windows-Installer zur gebündelten ReportLab-Installation.

## Abgerufene Inhalte

Texte, PDFs, Bilder und Metadaten aus den eingerichteten Quellen behalten ihre jeweiligen Urheber- und Nutzungsrechte. Sie werden bei Bedarf von den öffentlichen Quellenseiten abgerufen und gehören nicht zur MIT-Lizenz des eigenen Programmcodes. Die Veröffentlichung enthält keine persönlichen Dokumentbestände des Erstellers.

Die Quellen werden nur gelesen. Für eine Weitergabe oder Veröffentlichung später heruntergeladener Dokumente sind die Bedingungen der jeweiligen Quelle maßgeblich. Die lokale Speicherung macht aus fremden Inhalten keine frei lizenzierten eigenen Werke.
