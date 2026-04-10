"""Tests for image fallback (storage + confidence + manual review)."""

from yeti.vision import storage
from yeti.vision.extract import _score_confidence


def test_save_and_get_image(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "IMAGE_DIR", tmp_path)
    image_id = storage.save_image(b"fake image data")
    assert image_id

    path = storage.get_image_path(image_id)
    assert path is not None
    assert path.exists()
    assert path.read_bytes() == b"fake image data"


def test_get_image_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "IMAGE_DIR", tmp_path)
    image_id = storage.save_image(b"hello")
    assert storage.get_image_bytes(image_id) == b"hello"


def test_get_missing_image(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "IMAGE_DIR", tmp_path)
    assert storage.get_image_path("nonexistent") is None


def test_confidence_complete_business_card():
    result = {
        "structured": {
            "type": "business_card",
            "name": "Sam Allen",
            "email": "sam@example.com",
            "phone": "+1 555 1234",
            "company": "Example Inc",
            "title": "CEO",
        }
    }
    assert _score_confidence(result) == 1.0


def test_confidence_partial_business_card():
    result = {
        "structured": {
            "type": "business_card",
            "name": "Sam",
            "email": "sam@example.com",
            "phone": "",
            "company": "",
            "title": "",
        }
    }
    score = _score_confidence(result)
    assert 0.3 < score < 0.5  # 2/5


def test_confidence_complete_receipt():
    result = {
        "structured": {
            "type": "receipt",
            "vendor": "Coffee Shop",
            "total": "5.50",
            "date": "2026-04-10",
        }
    }
    assert _score_confidence(result) == 1.0


def test_confidence_no_structure_with_text():
    result = {"raw_text": "x" * 100}
    assert _score_confidence(result) == 0.3


def test_confidence_error_no_data():
    result = {"error": "OCR failed"}
    assert _score_confidence(result) == 0.0


def test_confidence_threshold_triggers_review():
    """Sparse extraction triggers review."""
    sparse = _score_confidence(
        {
            "structured": {
                "type": "business_card",
                "name": "Y",
                "email": "",
                "phone": "",
                "company": "",
                "title": "",
            }
        }
    )
    assert sparse < 0.5

    rich = _score_confidence(
        {
            "structured": {
                "type": "business_card",
                "name": "Y",
                "email": "y@z.com",
                "phone": "1",
                "company": "Acme",
                "title": "",
            }
        }
    )
    assert rich >= 0.5
