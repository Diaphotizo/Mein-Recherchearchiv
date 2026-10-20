"""Editable, transparent keyword vocabulary. Patterns match normalized German text."""
import re
import unicodedata


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold().replace('ß', 'ss')) if not unicodedata.combining(c))


# label | synonyms/word stems, separated by commas; broad words require repeated use.
GROUPS = {
    'Orte und Regionen': [
        ('Gaza', 'gaza,gazastreifen'),
        ('Westbank / Westjordanland', 'westbank,westjordanland'),
        ('Ostjerusalem', 'ostjerusalem,ost-jerusalem,ost jerusalem'),
        ('Israel', 'israel'),
        ('Palästina', 'palastina,palastinens'),
        ('Deutschland', 'deutschland,deutsche,deutschen,bundesrepublik'),
        ('Europa', 'europa,europaisch'),
        ('USA', 'usa,vereinigten staaten,vereinigte staaten,us-regierung'),
        ('Libanon', 'libanon,libanes'),
        ('Iran', 'iran,iranisch'),
    ],
    'Besatzung und Land': [
        ('Besatzung', 'besatzung,besetzt,okkupation'),
        ('Siedlungspolitik', 'siedlung,siedler'),
        ('Landenteignung', 'landenteign,landraub,enteignung'),
        ('Annexion', 'annexion,annektier'),
        ('Vertreibung', 'vertreib,vertrieben,ethnische sauberung,ethnischen sauberung'),
        ('Rückkehrrecht', 'ruckkehrrecht,recht auf ruckkehr'),
        ('Blockade', 'blockade,abriegelung'),
    ],
    'Menschenrechte und Repression': [
        ('Folter', 'folter,torture'),
        ('Haftbedingungen', 'haftbeding,gefangnis,gefangene,gefangenen,haftling,inhaftiert'),
        ('Administrativhaft', 'administrativhaft,verwaltungshaft'),
        ('Kinderrechte', 'kinderrecht,kinder,minderjahrig'),
        ('Frauenrechte', 'frauenrecht,frauen,geschlechtsspezif'),
        ('Pressefreiheit', 'pressefreiheit,journalisten,journalistinnen'),
        ('Meinungsfreiheit', 'meinungsfreiheit,zensur,redeverbot'),
        ('Versammlungsfreiheit', 'versammlungsfreiheit,demonstrationsverbot,protestverbot'),
        ('Rassismus', 'rassismus,rassistisch'),
        ('Antisemitismus', 'antisemit'),
        ('Antipalästinensischer Rassismus', 'antipalastinens,anti-palastinens'),
    ],
    'Krieg und Lebensbedingungen': [
        ('Krieg und Militär', 'krieg,militar,armee'),
        ('Angriffe auf Zivilbevölkerung', 'zivilbevolkerung,zivilisten,zivile opfer'),
        ('Zerstörung ziviler Infrastruktur', 'infrastruktur,zerstorung,bombardier'),
        ('Humanitäre Hilfe', 'humanitare hilfe,humanitaren hilfe,hilfsliefer,hilfsguter,hilfsorganisation'),
        ('Hunger und Ernährung', 'hunger,hungersnot,aushungern,verhungern,ernahrung,nahrungsmittel'),
        ('Gesundheitsversorgung', 'gesundheit,krankenhaus,krankenhauser,medizin,arzt'),
        ('Wasser und Energie', 'wasserversorgung,stromversorgung,trinkwasser,energie,wasserknapp'),
        ('Flucht', 'flucht,fluchtling'),
    ],
    'Wirtschaft und Rüstung': [
        ('Wirtschaft', 'wirtschaft,okonomie,okonomisch'),
        ('Handel', '=handel,handels,assoziierungsabkommen,export,import'),
        ('Waffenexporte', 'waffenexport,waffenliefer,waffenhandel,waffenverkauf,rustungsexport'),
        ('Rüstungsindustrie', 'rustungsindustrie,rustungskonzern,waffenhersteller,elbit,rheinmetall'),
        ('Investitionen und Finanzierung', 'investition,finanzier,anleihen,staatsanleihen,kriegsanleihen'),
        ('Arbeitsbedingungen', 'arbeitsbeding,arbeiter,arbeitnehmer,gewerkschaft'),
        ('Landwirtschaft', 'landwirtschaft,bauern,oliven,acker'),
        ('Sanktionen', 'sanktion,embargo'),
        ('Boykott / BDS', 'boykott,bds,desinvestition'),
    ],
    'Recht und internationale Verfahren': [
        ('Völkerrecht', 'volkerrecht'),
        ('Menschenrechte', 'menschenrecht'),
        ('Kriegsverbrechen', 'kriegsverbrech'),
        ('Völkermord', 'volkermord,genozid,genocide'),
        ('Apartheid', 'apartheid'),
        ('Internationale Gerichtsverfahren', 'internationalen gerichtshof,internationale gerichtshof,internationalen strafgerichtshof,internationale strafgerichtshof,icj,icc,istgh'),
        ('Haftbefehle', 'haftbefehl'),
        ('Staatliche Verantwortung', 'staatliche verantwortung,verantwortung deutschlands,verantwortung der bundesregierung,beihilfe'),
    ],
    'Politik und Gesellschaft': [
        ('Deutsche Politik', 'bundesregierung,bundestag,bundeskanzler,auswartige amt,deutsche politik,deutschen politik'),
        ('Staatsräson', 'staatsrason'),
        ('Diplomatie', 'diplomatie,diplomatisch,botschafter,verhandlung'),
        ('Friedensinitiativen', 'friedensinitiative,friedensbewegung,friedensplan,friedensprozess,zwei-staaten,ein-staat'),
        ('Protestbewegungen', 'protest,demonstration,solidaritatsbewegung'),
        ('Medienberichterstattung', 'medien,berichterstattung,propaganda,desinformation'),
        ('Bildung und Wissenschaft', 'universitat,hochschule,wissenschaft,schule,bildung'),
        ('Geschichte und Erinnerung', 'geschichte,erinnerung,nakba,holocaust,shoah,kolonialismus'),
        ('Kultur', 'kultur,theater,kunstler,musik,literatur'),
    ],
    'Akteure und Institutionen': [
        ('Bundesregierung', 'bundesregierung,bundeskanzler,auswartige amt'),
        ('EU', 'eu,europaische union,europaischen union,eu-kommission'),
        ('UNO', 'uno,vereinte nationen,vereinten nationen,un-sicherheitsrat,un-generalversammlung'),
        ('UNRWA', 'unrwa,unwra'),
        ('ICC / Internationaler Strafgerichtshof', 'icc,istgh,internationalen strafgerichtshof,internationale strafgerichtshof'),
        ('ICJ / Internationaler Gerichtshof', 'icj,igh,internationalen gerichtshof,internationale gerichtshof'),
        ('Israelische Regierung', 'israelische regierung,israelischen regierung,netanjahu,netanyahu,kabinett'),
        ('Israelische Armee', 'israelische armee,israelischen armee,idf,israelische militar,israelischen militar'),
        ('Palästinensische Autonomiebehörde', 'autonomiebehorde,palastinensische behorde'),
        ('Hamas', 'hamas'),
        ('Fatah', 'fatah'),
        ('Menschenrechtsorganisationen', 'menschenrechtsorganisation,amnesty,human rights watch,btselem,b’tselem,b\'tselem,adalah'),
    ],
}

TAGS = []
for group, values in GROUPS.items():
    for label, words in values:
        aliases = [normalize(v.strip()) for v in words.split(',')]
        # Short abbreviations must match full words; longer stems may match inflections.
        expression = r'\b(?:' + '|'.join(re.escape(v.lstrip('=')) + (r'\b' if len(v) <= 3 or v.startswith('=') else '') for v in aliases) + ')'
        TAGS.append({'label': label, 'group': group, 'aliases': aliases, 'pattern': re.compile(expression)})


def classify(title, paragraphs):
    title = normalize(title)
    # Editorial/footer repetitions are not evidence of an article's topic.
    useful = [(i, p) for i, p in enumerate(paragraphs) if not re.search(r'redaktionsteam|v\.\s*i\.\s*s\.\s*d\.\s*p|spendenkonto|fordermitglied', normalize(p['text']))]
    evidence = {}
    for tag in TAGS:
        pattern = tag['pattern']
        matches = [(i, len(pattern.findall(normalize(p['text'])))) for i, p in useful]
        matches = [(i, n) for i, n in matches if n]
        heading_hit = any(pattern.search(normalize(p['text'])) for _, p in useful if p['heading'])
        if pattern.search(title) or heading_hit or sum(n for _, n in matches) >= 3:
            evidence[tag['label']] = [i for i, _ in sorted(matches, key=lambda v: -v[1])[:5]]
    return evidence


def query_groups(query):
    """AND across words/quoted phrases, synonyms OR within each word."""
    chunks = re.findall(r'"([^"]+)"|(\S+)', query[:300])
    groups = []
    alias_sets = [
        ['westbank', 'westjordanland'],
        ['volkermord', 'genozid', 'genocide'],
        ['icc', 'istgh', 'internationaler strafgerichtshof', 'internationalen strafgerichtshof'],
        ['icj', 'igh', 'internationaler gerichtshof', 'internationalen gerichtshof'],
        ['ostjerusalem', 'ost-jerusalem'],
    ]
    for phrase, word in chunks[:20]:
        term = normalize(phrase or word)
        groups.append(next((a for a in alias_sets if term in a), [term]))
    return groups
