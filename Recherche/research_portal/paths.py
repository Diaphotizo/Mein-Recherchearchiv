from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
BASE = PORTAL.parent
BIP = BASE / 'BIP'
COURTS = BASE / 'ICC-ICJ'
COURT_DATA = COURTS / 'data'
COURT_SOURCE = COURTS / 'Bestand'
