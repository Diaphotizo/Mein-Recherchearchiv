# Freigabe ohne kostenpflichtige Dienste vorbereiten

Dieser Ablauf richtet sich an den Maintainer. Er beschreibt die späteren Schritte nach ausdrücklicher Freigabe. **Die vorliegenden Dateien allein erstellen kein GitHub-Konto, veröffentlichen kein Repository und reichen keinen SignPath-Antrag ein.**

## Kosten vor dem ersten Lauf begrenzen

Vorgesehen ist ein persönliches **GitHub-Free-Konto** mit einem **öffentlichen Repository**. Kein kostenpflichtiges Abonnement, kein Codespaces, kein größerer Runner und kein privater Actions-Build gehören zu diesem Ablauf. Standard-GitHub-Runner sind für öffentliche Repositorys kostenlos; gespeicherte Actions-Artefakte haben jedoch ein eigenes, mit anderen Angeboten geteiltes Freikontingent. GitHub Free nennt dafür 500 MB. Siehe [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

Vor dem Start sicherstellen, dass **keine kostenpflichtige Nutzung möglich ist**: keine Zahlungsmethode hinterlegen oder bei einer bereits vorhandenen Zahlungsmethode für alle betroffenen verbrauchsabhängigen Dienste ein wirksames **0-Dollar-Limit mit Nutzungssperre** einrichten. Eine reine Budgetwarnung reicht nicht. Wenn eine kostenfreie Grenze erreicht ist, wird angehalten oder alter nicht mehr benötigter Buildspeicher freigegeben; es wird nichts kostenpflichtig freigeschaltet.

Der vorbereitete Workflow verwendet einen Standard-Windows-Runner, einen manuellen Start und eine eintägige Artefaktaufbewahrung. Keine Caches anlegen. Nur wenige Builds gleichzeitig aufbewahren. Nach dem Herunterladen und Sichern für die Freigabe können die nicht mehr benötigten Actions-Buildartefakte gelöscht werden. Bei laufenden Signieranfragen oder Nachweisbedarf erst deren Abschluss abwarten.

## 1. Öffentliches Repository anlegen

Nach Zustimmung unter dem Konto **Diaphotizo** ein **öffentliches** Repository mit dem Namen **Mein-Recherchearchiv** anlegen und nur den vorbereiteten Quellordner übernehmen. GitHub Desktop kann das Hochladen der vollständigen Ordnerstruktur erleichtern. Die enthaltene `.github`-Struktur muss mit übernommen werden. Installationsordner, persönliche Archive, Runtime, fertige EXE, Zugangsdaten und interne Antragsunterlagen gehören nicht in den Quellcode-Commit.

Vor Veröffentlichung die Git-Identität prüfen: Der Anzeigename und die gewählte Commit-E-Mail werden Teil öffentlicher Commits. GitHub bietet eine private No-reply-Adresse an. Die eigene Zwei-Faktor-Anmeldung einrichten; später ist auch SignPath-MFA Voraussetzung für die Signierung. Private Schwachstellenmeldungen und die verfügbaren Branch-/Review-Schutzfunktionen einrichten.

## 2. Erste unsignierte Windows-Ausgabe bauen

Im GitHub-Reiter **Actions** den Workflow aus `.github/workflows/windows-unsigned.yml` bewusst starten. Die Bestätigung `acknowledge_free_account` darf erst nach der Kostenprüfung aktiviert werden. Der Lauf benötigt keine SignPath-Geheimnisse und veröffentlicht keine Release automatisch.

Den erfolgreichen Lauf, die Prüfberichte, die verwendete Quellrevision und die erzeugten Dateien kontrollieren. Den Installer auf einem Windows-11-Testkonto prüfen: leere Sammlung starten, alle drei Reiter, Quellenaktualisierung, Programmupdate und Deinstallation mit Datenerhalt. Ein erfolgreicher Build allein ist kein vollständiger Funktionstest. Die [Installer-Anleitung für Entwickler](../installer/README.md) beschreibt den lokalen Build.

## 3. Erste öffentliche Freigabe erstellen

Erst nach ausdrücklicher Veröffentlichungserlaubnis einen Versions-Tag und eine GitHub-Release für die geprüfte Revision erstellen. Setup-Datei, SHA256-Prüfsumme und Quellpaket dieser Revision hinzufügen. Die Release klar als **unsigned / unsigniert** kennzeichnen. Inhaltlich kann [RELEASE-HINWEISE.md](RELEASE-HINWEISE.md) als Vorlage dienen; den tatsächlichen Prüfstand angeben.

README und Versionshinweise erst dann von „vorbereitet“ auf „veröffentlicht“ umstellen. Den echten Release-Link für den SignPath-Antrag aufbewahren. Nicht einen alten Installer hochladen, der aus einer anderen Revision oder mit anderen Symbolen entstanden ist.

## 4. Kostenloses SignPath-Angebot beantragen

Nach der ersten veröffentlichten Ausgabe können die Angaben über [SignPath Apply](https://signpath.org/apply) eingereicht werden. Der Antrag liegt getrennt vom öffentlichen Repository. Vorher Repository-URL, Release-URL, Ansprechpartner und Kontoschutz vollständig bestätigen.

Die Foundation prüft unter anderem den öffentlichen Projektstand und die nachvollziehbare Reputation; gerade ein neues Projekt hat keinen Aufnahmeanspruch. Es ist nur das kostenlose Open-Source-Angebot vorgesehen. **Bei Ablehnung wird kein bezahltes Zertifikat bestellt.** Die unsignierte Ausgabe kann unter transparenter Kennzeichnung weiter angeboten werden. Grundlage: [SignPath-Bedingungen](https://signpath.org/terms).

## 5. Signierung erst nach Annahme aktivieren

Die [Code signing policy](../CODE_SIGNING.md) und [Integrationsvorbereitung](../installer/signpath/README.md) beschreiben den Plan. Organisation, Projekt, Richtlinien und Secret erst nach tatsächlicher Zulassung konfigurieren. Sowohl der eigene Deinstaller als auch das äußere Setup brauchen den passenden Signierablauf. Die notwendigen manuellen Genehmigungen dürfen nicht durch einen automatischen Freigabeschritt ersetzt werden.

Erst ein erfolgreich signiertes und geprüftes Release darf als signiert bezeichnet werden. Der Zertifikatsherausgeber wäre bei diesem Modell SignPath Foundation. Auch eine gültige Signatur garantiert nicht, dass Windows jede Reputationswarnung unterdrückt.
