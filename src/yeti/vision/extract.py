"""Image extraction — Tesseract OCR + Ollama structuring.

Two approaches tested side by side:
1. Tesseract (OCR) → Ollama (structure into JSON)
2. Ollama LLaVA (vision model does both OCR + structure)
"""

import base64
import json
import logging
from io import BytesIO

import httpx
from PIL import Image

from yeti.config import settings

logger = logging.getLogger(__name__)

STRUCTURE_PROMPT = """\
Extract structured data from the following text.
Determine if this is a business card, receipt, or other document.

For a business card, extract:
{{"type": "business_card", "name": "", "company": "", "title": "", \
"phone": "", "email": "", "address": "", "website": "", "notes": ""}}

For a receipt, extract:
{{"type": "receipt", "vendor": "", "date": "", "total": "", \
"currency": "", "items": [{{"description": "", "amount": ""}}], \
"payment_method": "", "notes": ""}}

For anything else:
{{"type": "document", "summary": "", "key_info": {{}}}}

Return ONLY valid JSON, no other text.

Text to extract from:
{text}"""

LLAVA_PROMPT = """\
Read ALL text visible in this image. The image may be rotated — \
read it in whatever orientation makes the text readable.

IMPORTANT: Only extract information you can actually read in the \
image. Never invent or guess data. Leave fields empty ("") if you \
cannot read them.

First determine what this is: a business card, receipt, or other \
document.

For a business card, return:
{{"type": "business_card", "name": "", "company": "", "title": "", \
"phone": "", "email": "", "address": "", "website": ""}}

For a receipt, return:
{{"type": "receipt", "vendor": "", "date": "", "total": "", \
"currency": "", "items": [{{"description": "", "amount": ""}}]}}

For anything else, return:
{{"type": "document", "summary": "", "raw_text": ""}}

Return ONLY valid JSON. Do not invent any data."""


def _ocr_with_preprocessing(image: Image.Image) -> str:
    """Run Tesseract with multiple preprocessing and rotation strategies."""
    import pytesseract
    from PIL import ImageEnhance, ImageFilter

    best = ""

    # Try 0° and 90° rotations
    rotations = [image, image.rotate(90, expand=True)]

    for rotated in rotations:
        gray = rotated.convert("L")

        # Strategy 1: binary threshold
        threshold = gray.point(lambda p: 255 if p > 128 else 0)
        text = pytesseract.image_to_string(
            threshold, lang="eng+deu", config="--psm 6"
        ).strip()
        if len(text) > len(best):
            best = text

        # Strategy 2: contrast + sharpen, PSM 6
        enhanced = ImageEnhance.Contrast(gray).enhance(2.0)
        sharp = enhanced.filter(ImageFilter.SHARPEN)
        text = pytesseract.image_to_string(
            sharp, lang="eng+deu", config="--psm 6"
        ).strip()
        if len(text) > len(best):
            best = text

        # Strategy 3: default PSM 3
        text = pytesseract.image_to_string(
            sharp, lang="eng+deu", config="--psm 3"
        ).strip()
        if len(text) > len(best):
            best = text

    return best


async def extract_tesseract_ollama(
    image_bytes: bytes,
) -> dict:
    """Approach 1: Tesseract OCR → Ollama structuring."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return {"error": "pytesseract not installed"}

    try:
        image = Image.open(BytesIO(image_bytes))
        raw_text = _ocr_with_preprocessing(image)
    except Exception as e:
        return {"error": f"OCR failed: {e}"}

    if not raw_text.strip():
        return {"error": "No text detected in image"}

    prompt = STRUCTURE_PROMPT.format(text=raw_text)

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": "llama3",
                    "prompt": prompt,
                    "stream": False,
                },
            )
            if not r.is_success:
                body = r.text[:200]
                logger.error("Ollama error %s: %s", r.status_code, body)
                return {
                    "method": "tesseract+ollama",
                    "raw_text": raw_text,
                    "structured": None,
                    "error": f"Ollama returned {r.status_code}: {body}",
                }
            response_text = r.json()["response"]

        structured = _parse_json_response(response_text)
        return {
            "method": "tesseract+ollama",
            "raw_text": raw_text,
            "structured": structured,
        }
    except Exception as e:
        logger.exception("Ollama structuring failed")
        return {
            "method": "tesseract+ollama",
            "raw_text": raw_text,
            "structured": None,
            "error": f"Ollama structuring failed: {e}",
        }


async def extract_llava(
    image_bytes: bytes,
) -> dict:
    """Approach 2: Ollama LLaVA vision model (OCR + structure)."""
    image_b64 = base64.b64encode(image_bytes).decode()

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": "llava",
                    "prompt": LLAVA_PROMPT,
                    "images": [image_b64],
                    "stream": False,
                },
            )
            r.raise_for_status()
            response_text = r.json()["response"]

        structured = _parse_json_response(response_text)
        return {
            "method": "llava",
            "structured": structured,
        }
    except Exception as e:
        return {
            "method": "llava",
            "structured": None,
            "error": str(e),
        }


def _find_best_rotation(image_bytes: bytes) -> bytes:
    """Try 0° and 90° rotation, return whichever Tesseract reads better."""
    import pytesseract

    image = Image.open(BytesIO(image_bytes))
    best_len = 0
    best_angle = 0

    for angle in [0, 90]:
        rotated = image.rotate(angle, expand=True) if angle else image
        gray = rotated.convert("L")
        threshold = gray.point(lambda p: 255 if p > 128 else 0)
        text = pytesseract.image_to_string(
            threshold, config="--psm 6"
        ).strip()
        if len(text) > best_len:
            best_len = len(text)
            best_angle = angle

    if best_angle != 0:
        logger.info("Image rotated %d° for better OCR", best_angle)
        rotated = image.rotate(best_angle, expand=True)
        buf = BytesIO()
        rotated.save(buf, format="JPEG")
        return buf.getvalue()

    return image_bytes


async def extract_both(
    image_bytes: bytes,
) -> dict:
    """Detect best rotation, then run both approaches."""
    import asyncio

    # Find best rotation first (quick Tesseract check)
    try:
        oriented = _find_best_rotation(image_bytes)
    except Exception:
        logger.exception("Rotation detection failed")
        oriented = image_bytes

    results = await asyncio.gather(
        extract_tesseract_ollama(oriented),
        extract_llava(oriented),
        return_exceptions=True,
    )

    return {
        "tesseract_ollama": (
            results[0]
            if not isinstance(results[0], Exception)
            else {"error": str(results[0])}
        ),
        "llava": (
            results[1]
            if not isinstance(results[1], Exception)
            else {"error": str(results[1])}
        ),
    }


def _parse_json_response(text: str) -> dict | None:
    """Try to parse JSON from a model response."""
    text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from code block
    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                continue

    # Try finding first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return None
