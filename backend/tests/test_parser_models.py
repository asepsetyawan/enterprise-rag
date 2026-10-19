"""Local artifact checks; excluded from CI unit jobs via the live marker."""
import pytest

from rag_platform.config import settings
from rag_platform.parsing import parse_document, parser_configuration

pytestmark = pytest.mark.live


@pytest.fixture
def source_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "storage_root", tmp_path)
    return tmp_path


def test_digital_pdf_has_page_provenance(source_root):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 22 Tf 60 760 Td (Travel policy) Tj 0 -45 Td /F1 14 Tf (Domestic hotel allowance is Rp750.000 per night.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(source_root / "digital.pdf")
    result = parse_document("digital.pdf", parser_configuration())
    assert any("750" in c for c in result["chunks"])
    assert all(m["pages"] == [1] for m in result["chunk_metadata"].values())


def test_scanned_pdf_uses_local_ocr(source_root):
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (1240, 1754), "white")
    drawing = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=38)
    drawing.text((100, 150), "TRAVEL POLICY", font=font, fill="black")
    drawing.text((100, 240), "Domestic hotel allowance", font=font, fill="black")
    drawing.text((100, 310), "Rp750.000 per night.", font=font, fill="black")
    image.save(source_root / "scan.pdf", resolution=150)
    result = parse_document("scan.pdf", parser_configuration())
    assert "750" in result["text"] and "hotel" in result["text"].lower()
    assert any(m["pages"] == [1] for m in result["chunk_metadata"].values())
