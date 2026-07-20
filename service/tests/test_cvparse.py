"""Tests for cvparse — proving the pypdf 5->6 bump didn't silently break extraction.

`cvparse.py` is the code behind `POST /cv/parse`: a public, unauthenticated endpoint that
parses attacker-supplied PDF/DOCX files. It had zero test coverage before pypdf was bumped
from 5.1.0 to 6.14.2 (2026-07-20, closing 31 CVEs) — the changelog showed no breaking change
to `PdfReader`/`extract_text`, but "the changelog looked fine" is a claim, not a test.

Fixtures are built by hand rather than pulled from a binary test asset, so there is nothing
to keep in sync with a fixture file and no extra dependency (no reportlab) for one test. The
PDF builder computes real byte offsets rather than hand-typing an xref table, so a malformed
fixture fails at parse time instead of masking a real regression.
"""

from __future__ import annotations

import io

import pytest

from service import cvparse

SAMPLE_TEXT = "dbt snowflake python sql, 4 years experience, fintech"


def _make_pdf(text: bytes) -> bytes:
    """A minimal single-page PDF with a real Helvetica text stream."""
    content = b"BT /F1 12 Tf 10 50 Td (" + text + b") Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 200 100] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_start = len(out)
    n = len(objects) + 1
    out += f"xref\n0 {n}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF".encode()
    return bytes(out)


def _make_docx(text: str) -> bytes:
    import docx
    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------- extract_text --

def test_pdf_text_extraction():
    """Pins the pypdf 6.x behaviour actually used in production: PdfReader + extract_text
    on every page, joined with newlines (service/cvparse.py:_from_pdf)."""
    data = _make_pdf(SAMPLE_TEXT.encode())
    text = cvparse.extract_text(data, "resume.pdf")
    assert "dbt" in text
    assert "snowflake" in text
    assert "4 years" in text


def test_docx_text_extraction():
    data = _make_docx(SAMPLE_TEXT)
    text = cvparse.extract_text(data, "resume.docx")
    assert text.strip() == SAMPLE_TEXT


def test_txt_passthrough():
    text = cvparse.extract_text(SAMPLE_TEXT.encode("utf-8"), "resume.txt")
    assert text == SAMPLE_TEXT


def test_unsupported_extension_rejected():
    with pytest.raises(cvparse.CVParseError):
        cvparse.extract_text(b"whatever", "resume.exe")


def test_oversized_file_rejected():
    with pytest.raises(cvparse.CVParseError):
        cvparse.extract_text(b"x" * (cvparse.MAX_BYTES + 1), "resume.pdf")


def test_corrupt_pdf_raises_cvparse_error_not_a_raw_exception():
    """The API translates CVParseError to a 422; anything else would be a 500 leaking
    an internal traceback for a normal case (a scanned/garbled upload)."""
    with pytest.raises(cvparse.CVParseError):
        cvparse.extract_text(b"%PDF-1.4\nnot actually a valid pdf body", "resume.pdf")


def test_docx_that_is_not_a_zip_raises_cvparse_error():
    with pytest.raises(cvparse.CVParseError):
        cvparse.extract_text(b"not a zip file at all", "resume.docx")


# ---------------------------------------------------------------- end-to-end signals --

def test_end_to_end_pdf_to_signals():
    """The actual call sequence /cv/parse makes: extract_text then extract_signals."""
    data = _make_pdf(SAMPLE_TEXT.encode())
    text = cvparse.extract_text(data, "resume.pdf")
    signals = cvparse.extract_signals(text)

    assert "dbt" in signals["skills"]
    assert "snowflake" in signals["skills"]
    assert "python" in signals["skills"]
    assert signals["years_experience"] == 4
    assert "fintech" in signals["sectors"]
    assert signals["summary"].startswith("Detected:")
