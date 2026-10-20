"""Offline regression tests. Uses synthetic data, never the real collections."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


def main():
    sys.path[:0] = [str(ROOT), str(ROOT / "BIP"), str(ROOT / "Recherche")]
    suite = unittest.TestSuite()
    modules = ["test_crawler", "recherche.test_recherche"]
    modules.extend("research_portal." + path.stem for path in
                   sorted((ROOT / "Recherche/research_portal").glob("test_*.py")))
    modules.extend(path.stem for path in sorted(ROOT.glob("test_*.py")))
    for name in dict.fromkeys(modules):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
