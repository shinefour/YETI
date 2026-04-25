---
name: Image extraction fallback to manual review
description: When OCR/vision fails, save image and create action item for manual data entry via dashboard
type: project
originSessionId: 64022ffc-735e-4cf5-8b51-2c9f6b05c915
---
When image extraction (Tesseract+Ollama or LLaVA) fails or produces low-confidence results, the system should:

1. Save the original image to persistent storage
2. Create an action item with status `pending_review` linked to the saved image
3. The dashboard should show the image alongside empty structured fields (business card / receipt template)
4. Daniel fills in the data manually via the dashboard
5. The manually entered data gets stored in MemPalace like any successful extraction would

**Why:** OCR and vision models are unreliable on rotated, low-quality, or unusual images. Daniel shouldn't lose the data just because the AI couldn't read it.

**How to apply:** Implement as a future enhancement to the image extraction pipeline. Requires: image storage (volume), action item linking to images, dashboard form for manual data entry.
