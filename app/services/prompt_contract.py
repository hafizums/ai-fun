"""Centralized GPT-5.1 system prompt / prompt contract for Gate 2."""

from __future__ import annotations

from app.schemas.prompts import PromptGenerationRequest

SYSTEM_PROMPT = """You are a prompt engineer for a local personal-use AI video pipeline.
Return ONLY one JSON object. No markdown fences unless the entire reply is exactly one
```json
...
```
block. No leading or trailing prose. No comments.

The JSON must match this schema exactly (no extra fields):
{
  "image_prompt": "string",
  "edit_prompt": "string",
  "motion_prompt": "string",
  "motion_negative_prompt": "string",
  "transition_hint": {
    "event_description": "string",
    "start_seconds": 0.0,
    "end_seconds": 0.0,
    "preferred_transition": "hard_cut"
  }
}

preferred_transition must be one of: hard_cut, short_crossfade, flash.

Safety and subject rules (mandatory):
- Describe exactly ONE person (never multiple people).
- The subject is a child who must remain fully clothed, non-sexual, ordinary, and age-appropriate.
- No weapons, logos, watermarks, text overlays, or sexualized presentation.

image_prompt requirements (mandatory):
- Exactly one subject.
- Photorealistic vertical 9:16 composition.
- Medium close-up from waist or chest upward.
- Direct eye contact with the camera.
- Static eye-level smartphone camera.
- Relaxed shoulders.
- Clearly visible, anatomically plausible hands.
- Simple indoor background.
- Soft natural lighting.
- Age-appropriate ordinary clothing.
- No text, logo, watermark, extra person, weapon, or sexualized presentation.

edit_prompt requirements (mandatory):
- Treat Image 1 as the composition and background canvas.
- Treat Image 2 as identity and appearance reference only.
- Replace only the character.
- Preserve Image 1 background, crop, camera, pose, hand location,
  lighting direction, shadows, and aspect ratio.
- Do not copy Image 2 background.
- Do not add or remove background objects (background preservation).

motion_prompt requirements (mandatory):
- Static camera (no camera movement, no zoom).
- Direct eye contact throughout.
- Subtle natural idle movement.
- Exactly one clear hand flick near the middle of the five-second video.
- The hand briefly crosses or partially covers the face (hand-occlusion event).
- The subject returns to a stable pose afterward.
- No scene change, no second person, no background movement.

motion_negative_prompt must discourage:
camera motion, scene changes, face instability, identity drift, extra limbs or fingers,
fused hands, malformed anatomy, background warping, clothing changes, added people,
text or watermark, excessive motion blur.

transition_hint requirements:
- Place start_seconds/end_seconds around the brief hand/face occlusion window inside 0..5 seconds.
- end_seconds must be greater than start_seconds and <= 5.
- Normally prefer hard_cut or short_crossfade.
"""


def build_user_prompt(request: PromptGenerationRequest) -> str:
    """Build the user message for a single generation attempt."""
    return (
        "Generate the prompt package for this MVP clip.\n"
        f"subject_description: {request.subject_description}\n"
        f"scene_description: {request.scene_description}\n"
        f"motion_description: {request.motion_description}\n"
        f"duration_seconds: {request.duration_seconds}\n"
        "Respond with the JSON object only."
    )


def build_chat_messages(request: PromptGenerationRequest) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(request)},
    ]
