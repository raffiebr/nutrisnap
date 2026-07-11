"""LLM layer: turns a food photo (or text description) into structured nutrition JSON.

Swap providers by setting LLM_PROVIDER in .env. Both implementations share the
same prompt and return the same dict shape, so the rest of the bot never cares
which model is behind it.
"""
import base64
import json
import os
import re

PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()

SYSTEM_PROMPT = """You are a nutrition estimation engine. You receive either:
1. A photo of a meal (possibly multiple items, e.g. pizza + soup + a drink), or
2. A photo of a packaged food's nutrition label, or
3. A plain-text description of food eaten.

Your job: identify each food item, estimate the portion, and estimate nutrition.

Rules:
- If it's a nutrition label, READ the label values directly (per serving) and
  note the serving size. If the photo suggests the whole package was consumed,
  say so in notes but report per-serving unless told otherwise.
- If it's a cooked meal, itemize each visible component with portion estimates.
- Local/Asian dishes (e.g. Singaporean hawker food) are common: chicken rice,
  laksa, mee goreng, kopi/teh drinks. Use realistic hawker portions and note
  that these are often high in sodium.
- Estimates should be realistic, not optimistic. When unsure, give a middle
  estimate and lower the confidence.

Respond with ONLY a JSON object, no markdown fences, in exactly this shape:
{
  "meal_name": "short human-friendly name, e.g. 'Chicken Rice + Teh Bing'",
  "items": [
    {"name": "...", "portion": "e.g. 1 plate / 250g / 1 bowl",
     "calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0,
     "sodium_mg": 0, "sugar_g": 0}
  ],
  "total": {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0,
            "sodium_mg": 0, "sugar_g": 0},
  "confidence": "high|medium|low",
  "source": "meal_photo|nutrition_label|text",
  "notes": "one short sentence of caveats or observations"
}
"""

CORRECTION_PROMPT = """The user corrected your previous estimate.

Previous estimate JSON:
{previous}

User's correction: "{correction}"

Re-estimate taking the correction into account. Respond with ONLY the same
JSON shape as before, no markdown fences."""


def _extract_json(text: str) -> dict:
    """Be forgiving: strip fences, find the outermost JSON object."""
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON found in LLM response: {text[:200]}")
    return json.loads(text[start : end + 1])


# ---------------------------------------------------------------- Anthropic
def _analyze_anthropic(image_bytes: bytes | None, text: str) -> dict:
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    content = []
    if image_bytes:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(image_bytes).decode(),
            },
        })
    content.append({"type": "text", "text": text})

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    return _extract_json(resp.content[0].text)


# ------------------------------------------------------------------ Gemini
def _analyze_gemini(image_bytes: bytes | None, text: str) -> dict:
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(
        "gemini-2.5-flash", system_instruction=SYSTEM_PROMPT
    )
    parts = []
    if image_bytes:
        parts.append({"mime_type": "image/jpeg", "data": image_bytes})
    parts.append(text)
    resp = model.generate_content(parts)
    return _extract_json(resp.text)


# ------------------------------------------------------------------- Public
def analyze_food(image_bytes: bytes | None = None,
                 description: str | None = None) -> dict:
    """Analyze a food photo and/or text description. Returns nutrition dict."""
    prompt = ("Analyze this food and estimate nutrition."
              if image_bytes else
              f"Estimate nutrition for this food description: {description}")
    if image_bytes and description:
        prompt += f" User's caption/context: {description}"

    fn = _analyze_anthropic if PROVIDER == "anthropic" else _analyze_gemini
    return fn(image_bytes, prompt)


def refine_estimate(previous: dict, correction: str) -> dict:
    """Re-run estimation with a user correction like 'that was 2 slices'."""
    prompt = CORRECTION_PROMPT.format(
        previous=json.dumps(previous, indent=2), correction=correction
    )
    fn = _analyze_anthropic if PROVIDER == "anthropic" else _analyze_gemini
    return fn(None, prompt)
