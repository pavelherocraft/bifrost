"""Patch convert_dict_to_response.py: normalize message.audio for Xiaomi TTS.

Xiaomi returns audio={id, data} without expires_at/transcript; LiteLLM pydantic
ChatCompletionAudio requires expires_at:int and transcript:str -> ValidationError.
Idempotent; survives docker restart (container FS) and re-applied by entrypoint
after image recreation.
"""
import os

TARGET = "/app/.venv/lib/python3.13/site-packages/litellm/litellm_core_utils/llm_response_utils/convert_dict_to_response.py"
MARKER = "_PATCH_XIAOMI_AUDIO_"
NEEDLE = "audio=choice[\"message\"].get(\"audio\", None),"
REPLACEMENT = (
    "audio=(lambda _a: ({**_a, "
    "\"expires_at\": (_a.get(\"expires_at\") if isinstance(_a.get(\"expires_at\"), int) else 0), "
    "\"transcript\": (_a.get(\"transcript\") if isinstance(_a.get(\"transcript\"), str) else \"\")} "
    "if isinstance(_a, dict) else _a))(choice[\"message\"].get(\"audio\", None)),"
    "  # " + MARKER
)

with open(TARGET) as f:
    src = f.read()

if MARKER in src:
    print("[patch_xiaomi_audio] already patched")
elif NEEDLE not in src:
    print("[patch_xiaomi_audio] WARN: needle not found")
else:
    if not os.path.exists(TARGET + ".bak.xiaomi-audio"):
        with open(TARGET + ".bak.xiaomi-audio", "w") as f:
            f.write(src)
    src = src.replace(NEEDLE, REPLACEMENT, 1)
    with open(TARGET, "w") as f:
        f.write(src)
    # bust pycache
    d = os.path.dirname(TARGET) + "/__pycache__"
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.endswith(".pyc"):
                os.remove(os.path.join(d, fn))
    print("[patch_xiaomi_audio] patched convert_dict_to_response.py")
