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
Look at this image carefully. Determine if it shows a business card, \
receipt, or other document.

For a business card, extract:
{{"type": "business_card", "name": "", "company": "", "title": "", \
"phone": "", "email": "", "address": "", "website": "", "notes": ""}}

For a receipt, extract:
{{"type": "receipt", "vendor": "", "date": "", "total": "", \
"currency": "", "items": [{{"description": "", "amount": ""}}], \
"payment_method": "", "notes": ""}}

For anything else:
{{"type": "document", "summary": "", "key_info": {{}}}}

Return ONLY valid JSON, no other text."""


async def extract_tesseract_ollama(
    image_bytes: bytes,
) -> dict:
    """Approach 1: Tesseract OCR → Ollama structuring."""
    try:
        import pytesseract
    except ImportError:
        return {"error": "pytesseract not installed"}

    try:
        image = Image.open(BytesIO(image_bytes))
        raw_text = pytesseract.image_to_string(
            image, lang="eng+deu"
        )
    except Exception as e:
        return {"error": f"OCR failed: {e}"}

    if not raw_text.strip():
        return {"error": "No text detected in image"}

    prompt = STRUCTURE_PROMPT.format(text=raw_text)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": "llama3",
                    "prompt": prompt,
                    "stream": False,
                },
            )
            r.raise_for_status()
            response_text = r.json()["response"]

        structured = _parse_json_response(response_text)
        return {
            "method": "tesseract+ollama",
            "raw_text": raw_text,
            "structured": structured,
        }
    except Exception as e:
        return {
            "method": "tesseract+ollama",
            "raw_text": raw_text,
            "structured": None,
            "error": f"Ollama structuring failed: {e}",
        }


async def extract_llava(image_bytes: bytes) -> dict:
    """Approach 2: Ollama LLaVA vision model (OCR + structure)."""
    image_b64 = base64.b64encode(image_bytes).decode()

    try:
        async with httpx.AsyncClient(timeout=60) as client:
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


async def extract_both(
    image_bytes: bytes,
) -> dict:
    """Run both approaches and return results for comparison."""
    import asyncio

    results = await asyncio.gather(
        extract_tesseract_ollama(image_bytes),
        extract_llava(image_bytes),
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
