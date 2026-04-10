"""Image extraction — Tesseract OCR + Ollama structuring.

Primary: Tesseract (OCR) → Ollama (structure into JSON)
Fallback: LLaVA vision model (when Tesseract gets nothing)
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
{context}
For a business card, extract:
{{"type": "business_card", "name": "", "company": "", "title": "", \
"phone": "", "email": "", "address": "", "website": "", "notes": ""}}

For a receipt, extract:
{{"type": "receipt", "vendor": "", "date": "", "total": "", \
"currency": "", "items": [{{"description": "", "amount": ""}}], \
"payment_method": "", "notes": ""}}

For anything else:
{{"type": "document", "summary": "", "key_info": {{}}}}

Only include data present in the text. Leave fields empty if unknown.
Return ONLY valid JSON, no other text.

Text to extract from:
{text}"""

LLAVA_PROMPT = """\
Read ALL text visible in this image.

IMPORTANT: Only extract information you can actually read in the \
image. Never invent or guess data. Leave fields empty ("") if you \
cannot read them.
{context}
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
    """Run Tesseract with multiple preprocessing strategies."""
    import pytesseract
    from PIL import ImageEnhance, ImageFilter, ImageOps

    gray = image.convert("L")
    best = ""

    def _try(img, config="--psm 6"):
        nonlocal best
        text = pytesseract.image_to_string(
            img, lang="eng+deu", config=config
        ).strip()
        if len(text) > len(best):
            best = text

    # Strategy 1: binary threshold (white backgrounds)
    _try(gray.point(lambda p: 255 if p > 128 else 0))

    # Strategy 2: low threshold (dark backgrounds)
    _try(gray.point(lambda p: 255 if p > 70 else 0))

    # Strategy 3: inverted + high threshold (light text on dark)
    inverted = ImageOps.invert(gray)
    _try(inverted.point(lambda p: 255 if p > 180 else 0))

    # Strategy 4: contrast + sharpen, PSM 6
    enhanced = ImageEnhance.Contrast(gray).enhance(2.0)
    sharp = enhanced.filter(ImageFilter.SHARPEN)
    _try(sharp)

    # Strategy 5: contrast + sharpen, PSM 3
    _try(sharp, config="--psm 3")

    return best


def _find_best_orientation(
    image: Image.Image,
) -> Image.Image:
    """Try 0°, 90° CW, 90° CCW — return best orientation."""
    import pytesseract

    best_len = 0
    best_img = image

    for angle in [0, 90, 270]:
        rotated = (
            image.rotate(angle, expand=True) if angle else image
        )
        gray = rotated.convert("L")
        threshold = gray.point(lambda p: 255 if p > 128 else 0)
        text = pytesseract.image_to_string(
            threshold, config="--psm 6"
        ).strip()
        if len(text) > best_len:
            best_len = len(text)
            best_img = rotated
            if angle:
                logger.info(
                    "Best orientation: %d° (%d chars)",
                    angle,
                    len(text),
                )

    return best_img


async def extract_tesseract_ollama(
    image: Image.Image,
    caption: str = "",
) -> dict:
    """Primary: Tesseract OCR → Ollama structuring."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return {"error": "pytesseract not installed"}

    try:
        raw_text = _ocr_with_preprocessing(image)
    except Exception as e:
        return {"error": f"OCR failed: {e}"}

    if not raw_text.strip():
        return {"error": "No text detected in image"}

    context = ""
    if caption:
        context = f"\nAdditional context: {caption}\n"

    prompt = STRUCTURE_PROMPT.format(
        text=raw_text, context=context
    )

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
                logger.error(
                    "Ollama error %s: %s", r.status_code, body
                )
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
    image: Image.Image,
    caption: str = "",
) -> dict:
    """Fallback: LLaVA vision model (when Tesseract gets nothing)."""
    buf = BytesIO()
    image.save(buf, format="JPEG")
    image_b64 = base64.b64encode(buf.getvalue()).decode()

    context = ""
    if caption:
        context = f"\nAdditional context: {caption}\n"

    prompt = LLAVA_PROMPT.format(context=context)

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": "llava",
                    "prompt": prompt,
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


async def extract(
    image_bytes: bytes,
    caption: str = "",
) -> dict:
    """Extract data from an image.

    1. Detect best rotation
    2. Try Tesseract + Ollama (primary)
    3. Fall back to LLaVA if Tesseract gets nothing

    The result includes a `confidence` field (0..1) and
    `needs_review` (bool) so the caller can route low-confidence
    results to manual review.
    """
    image = Image.open(BytesIO(image_bytes))

    # Find best orientation
    try:
        oriented = _find_best_orientation(image)
    except Exception:
        logger.exception("Rotation detection failed")
        oriented = image

    # Primary: Tesseract + Ollama
    result = await extract_tesseract_ollama(
        oriented, caption
    )

    # If Tesseract got nothing, fall back to LLaVA
    if not result.get("raw_text"):
        logger.info(
            "Tesseract found nothing, falling back to LLaVA"
        )
        result = await extract_llava(oriented, caption)

    confidence = _score_confidence(result)
    result["confidence"] = confidence
    result["needs_review"] = confidence < 0.5
    return result


def _score_confidence(result: dict) -> float:
    """Heuristic confidence score for an extraction result."""
    if "error" in result and not result.get("structured"):
        return 0.0

    structured = result.get("structured")
    if not structured:
        # Got raw text but no structure
        raw = result.get("raw_text", "")
        if len(raw) > 50:
            return 0.3
        return 0.1

    doc_type = structured.get("type", "")
    if doc_type == "business_card":
        fields = ("name", "email", "phone", "company", "title")
    elif doc_type == "receipt":
        fields = ("vendor", "total", "date")
    else:
        fields = ()

    if not fields:
        return 0.5

    populated = sum(
        1
        for f in fields
        if structured.get(f) and str(structured[f]).strip()
    )
    score = populated / len(fields)
    return round(score, 2)


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
