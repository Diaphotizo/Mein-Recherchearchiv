"""Native PDF and Windows spawn integration test using synthetic documents."""
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
from pathlib import Path
import sqlite3
import sys
import tempfile


def inspect_pdf(filename):
    from pypdf import PdfReader
    import pdfplumber
    import pypdfium2 as pdfium
    assert "Recherche" in PdfReader(filename).pages[0].extract_text()
    with pdfplumber.open(filename) as pdf:
        assert "Recherche" in pdf.pages[0].extract_text()
    with pdfium.PdfDocument(filename) as pdf:
        page = pdf[0]
        bitmap = page.render(scale=.25)
        assert bitmap.width > 0 and bitmap.height > 0
        bitmap.close()
        page.close()
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE VIRTUAL TABLE documents USING fts5(text)")
        db.execute("INSERT INTO documents VALUES ('Recherche Gaza UNRWA')")
        assert db.execute("SELECT COUNT(*) FROM documents WHERE documents MATCH 'UNRWA'").fetchone()[0] == 1
    return True


def main():
    from reportlab.pdfgen import canvas
    import archiv_start
    with tempfile.TemporaryDirectory(prefix="runtime-pdf-") as temp:
        pdf = Path(temp) / "fixture.pdf"
        c = canvas.Canvas(str(pdf))
        c.drawString(50, 700, "Recherche: Gaza, Deutschland, Israel und UNRWA")
        c.save()
        with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
            assert list(pool.map(inspect_pdf, [str(pdf)] * 2)) == [True, True]
    print(json.dumps({"python": sys.version, "runtime": str(archiv_start.venv_python()),
                      "pdf_spawn_workers": 2, "fts5": "ok", "pdf_rendering": "ok"}))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
