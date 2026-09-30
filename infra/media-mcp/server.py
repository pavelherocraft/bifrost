#!/usr/bin/env python3
"""media-mcp — FastMCP server exposing media generation tools through LiteLLM.

Runs on 0.0.0.0:9101 (streamable HTTP at /mcp). Registered in LiteLLM
mcp_servers; LiteLLM forwards the caller's `authorization` header (VK) —
all upstream inference calls are made with that VK (per-user attribution
and per-key/team model permissions apply).

Generated media is written to FILES_DIR and served publicly by nginx under
https://hcbifrost.herocraft.com/media-files/ (TTL 24h via cron cleanup).

Tools: list_media_models, generate_image, edit_image, generate_video,
       video_status, synthesize_speech, clone_speech,
       register_voice_clone, transcribe_audio
Extra route: POST /upload (Bearer VK required) -> files/uploads/.
"""

import base64
import binascii
import os
import re
import uuid
from typing import Annotated, Optional

import httpx
from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

LITELLM = "http://127.0.0.1:4001"
# LiteLLM managed routes claim /minimax/* and /mmx/* before generic
# passthrough — files/retrieve is called directly with the PAYG key.
MINIMAX_API = "https://api.minimax.io"
MINIMAX_KEY = os.environ.get("MINIMAX_PAYG_KEY", "")
PUBLIC_BASE = "https://hcbifrost.herocraft.com"
FILES_DIR = "/opt/media-mcp/files"
UPLOADS_DIR = os.path.join(FILES_DIR, "uploads")
MAX_UPLOAD = 25 * 1024 * 1024          # nginx client_max_body_size 25M
MAX_SAMPLE = 10 * 1024 * 1024          # xiaomi voiceclone base64 limit

MIMO_VOICES = ["mimo_default", "冰糖", "茉莉", "苏打", "白桦",
               "Mia", "Chloe", "Milo", "Dean"]
MINIMAX_VOICES = ["female-shaonv", "female-yujie", "male-qn-qingse",
                  "male-qn-jingying", "presenter_male", "presenter_female",
                  "audiobook_male_1", "audiobook_female_1"]

IMAGE_MODELS = {
    "gemini/gemini-3.1-flash-image": "fast, cheap, good default",
    "gemini/gemini-3-pro-image": "higher quality, slower",
    "gpt-image-1.5": "OpenAI image gen",
    "gpt-image-2": "OpenAI image gen",
    "gpt-image-2.5-sunburst": "OpenAI image gen variant",
    "gpt-image-2.5-flare": "OpenAI image gen variant",
    "minimax/image-01": "MiniMax t2i (aspect_ratio via size)",
}
IMAGE_EDIT_MODELS = {
    "gemini/gemini-3.1-flash-image": "fast edit (default)",
    "gemini/gemini-3-pro-image": "higher quality edit",
    "gpt-image-1.5": "OpenAI image edits",
    "gpt-image-2": "OpenAI image edits",
    "gpt-image-2.5-sunburst": "OpenAI image edits variant",
    "gpt-image-2.5-flare": "OpenAI image edits variant",
    "minimax/image-01": "MiniMax i2i via subject_reference (character/style)",
}
VIDEO_MODELS = {
    "MiniMax-H3": "newest H3 (v2 API, up to 2K, first/last frame + reference inputs, PAYG billing)",
    "MiniMax-Hailuo-2.3": "v1 flagship, good quality (TokenPlan)",
    "MiniMax-Hailuo-02": "v1 previous generation",
    "T2V-01": "v1 legacy text-to-video",
}
VIDEO_V2_MODELS = {"MiniMax-H3", "MiniMax-H3-Max"}
VOICE_CLONE_MODELS = {
    "voice/xiaomi/mimo-v2.5-tts-voiceclone": "zero-shot clone: sample passed inline with each request (clone_speech)",
    "minimax/speech-2.8-hd (persistent clone)": "register_voice_clone creates a reusable voice_id -> synthesize_speech(model='minimax/speech-2.8-hd', voice=<voice_id>)",
}
TTS_MODELS = {
    "voice/xiaomi/mimo-v2.5-tts": "preset mimo voices + style instructions; singing via (唱歌) tag in text",
    "voice/xiaomi/mimo-v2.5-tts-voicedesign": "free-form voice from `style` description (style REQUIRED)",
    "minimax/speech-2.8-hd": "system voices (TokenPlan) or cloned voice_id from register_voice_clone (auto-routed to PAYG)",
}

mcp = FastMCP("media", host="0.0.0.0", port=9101,
              streamable_http_path="/mcp")


# ---------- helpers ----------

def _vk(ctx: Context) -> str:
    """Extract caller's LiteLLM VK forwarded by the gateway."""
    req = getattr(ctx.request_context, "request", None)
    headers = getattr(req, "headers", None) or {}
    auth = headers.get("authorization") or headers.get("x-litellm-api-key") or ""
    tok = auth.replace("Bearer ", "").strip() if auth.lower().startswith("bearer") else auth
    if not tok:
        raise ValueError("No LiteLLM virtual key forwarded — call this server through LiteLLM (/litellm/media/mcp) with a Bearer VK.")
    return tok


async def _llm(vk: str, method: str, path: str, timeout: float = 120, **kw):
    """Call LiteLLM on localhost with the caller's VK; raise readable errors."""
    async with httpx.AsyncClient(base_url=LITELLM, timeout=timeout) as c:
        r = await c.request(method, path,
                            headers={"Authorization": f"Bearer {vk}"}, **kw)
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:
            msg = r.text
        raise ValueError(f"LiteLLM {r.status_code}: {msg[:400]}")
    return r


def _ext_for(raw: bytes, hint: str = "") -> str:
    if raw[:4] == b"RIFF":
        return ".wav"
    if raw[:3] == b"ID3" or raw[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if raw[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if raw[4:8] == b"ftyp":
        return ".mp4"
    if "pcm" in hint:
        return ".pcm"
    return ".bin"


def _mime_for(raw: bytes) -> str:
    e = _ext_for(raw)
    return {".wav": "audio/wav", ".mp3": "audio/mpeg", ".png": "image/png",
            ".jpg": "image/jpeg", ".mp4": "video/mp4"}.get(e, "application/octet-stream")


def _save(raw: bytes, ext: str, subdir: str = "") -> str:
    d = os.path.join(FILES_DIR, subdir) if subdir else FILES_DIR
    os.makedirs(d, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(d, name), "wb") as f:
        f.write(raw)
    return f"{PUBLIC_BASE}/media-files/{subdir + '/' if subdir else ''}{name}"


async def _resolve_source(source: str, client: httpx.AsyncClient) -> bytes:
    """source: http(s) URL | data URI | raw base64 | 'upload:<name>'."""
    if source.startswith("data:"):
        return base64.b64decode(source.split(",", 1)[1])
    if source.startswith("http://") or source.startswith("https://"):
        r = await client.get(source)
        r.raise_for_status()
        return r.content
    if source.startswith("upload:") or ("/" not in source and " " not in source):
        name = source.removeprefix("upload:").removeprefix("/media-files/uploads/")
        name = os.path.basename(name)  # no traversal
        p = os.path.join(UPLOADS_DIR, name)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return f.read()
        if source.startswith("upload:"):
            raise ValueError(f"upload not found: {name} (POST /media-upload first)")
    try:
        return base64.b64decode(source)
    except binascii.Error:
        raise ValueError("source must be http(s) URL, data-URI, 'upload:<name>' or base64")


async def _img_data_uri(source: str, client: httpx.AsyncClient) -> str:
    """Resolve any image source -> data: URI. MiniMax often fails to fetch
    plain HTTP(S) URLs (e.g. our /media-files/ links) -> task ends with
    status=Fail; inline base64 always works."""
    raw = await _resolve_source(source, client)
    return f"data:{_mime_for(raw)};base64,{base64.b64encode(raw).decode()}"


# ---------- tools ----------

@mcp.tool()
def list_media_models() -> dict:
    """Full catalog of media capabilities: model names, voices, formats, limits.
    Call this FIRST before any generation — one call returns everything.
    Typical pipelines: generate_image -> generate_video(first_frame_url=<url>);
    synthesize_speech / clone_speech -> transcribe_audio(<url>)."""
    return {
        "image_models": IMAGE_MODELS,
        "image_edit_models": IMAGE_EDIT_MODELS,
        "video_models": VIDEO_MODELS,
        "tts_models": TTS_MODELS,
        "voice_clone_models": VOICE_CLONE_MODELS,
        "asr_model": "voice/xiaomi/mimo-v2.5-asr",
        "mimo_voices": MIMO_VOICES,
        "minimax_voices": MINIMAX_VOICES,
        "audio_formats": ["wav", "mp3"],
        "video_durations_s": [5, 6, 10],
        "video_resolutions": ["768P", "1080P", "2K (MiniMax-H3 only)"],
        "video_ratios": ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9",
                         "adaptive (i2v only)"],
        "notes": [
            "ALL tools return hosted URLs (files live 24h) — never base64 in context",
            "input audio for clone/ASR: http(s) URL | data-URI | base64 | 'upload:<name>'",
            "to upload a local file the user must POST it to https://hcbifrost.herocraft.com/media-upload?name=<file> with Bearer <their LiteLLM key> — response gives url and ref ('upload:<name>')",
            "video is async: generate_video -> task_id -> poll video_status until Success",
            "edit_image modifies an existing image (i2i); its output URL can feed generate_video.first_frame_url too",
            "style param = natural-language voice/tone instruction (emotion, pace, accent); audio tags like (laughs)/(sighs) go inside the text",
            "voiceclone sample: wav/mp3, <=10MB, clean speech a few seconds long",
            "two clone modes: xiaomi zero-shot (clone_speech, per-request) vs minimax persistent voice_id (register_voice_clone once -> reuse in synthesize_speech)",
            "MiniMax-H3 video also supports last_frame_url and reference media (see tool params); H3/H3-Max and voice_clone bill to the MiniMax PAYG account",
            "voicedesign: voice comes purely from `style` description; voice param not supported",
        ],
    }


@mcp.tool()
async def generate_image(
    ctx: Context,
    prompt: Annotated[str, Field(description="Text description of the image to generate")],
    model: Annotated[str, Field(description="Image model id — see list_media_models().image_models")] = "gemini/gemini-3.1-flash-image",
    size: Annotated[Optional[str], Field(description="Optional: 'WxH' (e.g. 1024x1024) for openai models, aspect ratio like '16:9' for minimax/image-01")] = None,
) -> dict:
    """Generate an image from text. Returns {url} — a hosted public URL (TTL 24h).
    The URL can be passed to generate_video.first_frame_url for image-to-video."""
    vk = _vk(ctx)
    if model == "minimax/image-01":
        body = {"model": "image-01", "prompt": prompt}
        if size:
            body["aspect_ratio"] = size
        r = await _llm(vk, "POST", "/minimax/v1/image_generation", timeout=180, json=body)
        d = r.json()
        if (d.get("base_resp") or {}).get("status_code", 0) != 0:
            raise ValueError(f"minimax: {(d.get('base_resp') or {}).get('status_msg')}")
        urls = (d.get("data") or {}).get("image_urls") or d.get("image_urls") or []
        if urls:
            async with httpx.AsyncClient(timeout=60) as c:
                img = (await c.get(urls[0])).content
        else:
            b64 = (d.get("data") or {}).get("image_base64") or d.get("image_base64")
            img = base64.b64decode(b64)
        return {"url": _save(img, _ext_for(img)), "model": model, "bytes": len(img)}

    body = {"model": model, "prompt": prompt, "response_format": "b64_json"}
    if size:
        body["size"] = size
    r = await _llm(vk, "POST", "/v1/images/generations", timeout=180, json=body)
    item = (r.json().get("data") or [{}])[0]
    if item.get("b64_json"):
        img = base64.b64decode(item["b64_json"])
    elif item.get("url"):
        async with httpx.AsyncClient(timeout=60) as c:
            img = (await c.get(item["url"])).content
    else:
        raise ValueError("no image data in response")
    return {"url": _save(img, _ext_for(img)), "model": model, "bytes": len(img)}


@mcp.tool()
async def edit_image(
    ctx: Context,
    prompt: Annotated[str, Field(description="What to change in the image (e.g. 'replace background with sunset', 'make it watercolor style')")],
    image: Annotated[str, Field(description="Source image: http(s) URL (e.g. from generate_image) | data-URI | 'upload:<name>' | base64. png/jpg")],
    model: Annotated[str, Field(description="Edit-capable model — see list_media_models().image_edit_models")] = "gemini/gemini-3.1-flash-image",
    size: Annotated[Optional[str], Field(description="Optional output size 'WxH' for openai models")] = None,
) -> dict:
    """Edit an existing image (image-to-image / inpainting by instruction).
    Returns {url} — hosted public URL (TTL 24h); usable as
    generate_video.first_frame_url."""
    vk = _vk(ctx)
    async with httpx.AsyncClient(timeout=120) as c:
        raw = await _resolve_source(image, c)
    mime = _mime_for(raw)
    if mime not in ("image/png", "image/jpeg"):
        raise ValueError(f"image must be png/jpg, got {mime}")

    if model == "minimax/image-01":
        body = {"model": "image-01", "prompt": prompt,
                "subject_reference": [{"type": "character",
                                       "image_file": f"data:{mime};base64,{base64.b64encode(raw).decode()}"}]}
        if size:
            body["aspect_ratio"] = size
        r = await _llm(vk, "POST", "/minimax/v1/image_generation", timeout=180, json=body)
        d = r.json()
        if (d.get("base_resp") or {}).get("status_code", 0) != 0:
            raise ValueError(f"minimax: {(d.get('base_resp') or {}).get('status_msg')}")
        urls = (d.get("data") or {}).get("image_urls") or d.get("image_urls") or []
        if urls:
            async with httpx.AsyncClient(timeout=60) as c:
                img = (await c.get(urls[0])).content
        else:
            b64 = (d.get("data") or {}).get("image_base64") or d.get("image_base64")
            img = base64.b64decode(b64)
        return {"url": _save(img, _ext_for(img)), "model": model, "bytes": len(img)}

    data = {"model": model, "prompt": prompt}
    if size:
        data["size"] = size
    files = {"image": (f"source{_ext_for(raw)}", raw, mime)}
    r = await _llm(vk, "POST", "/v1/images/edits", timeout=300,
                   data=data, files=files)
    item = (r.json().get("data") or [{}])[0]
    if item.get("b64_json"):
        img = base64.b64decode(item["b64_json"])
    elif item.get("url"):
        async with httpx.AsyncClient(timeout=60) as c:
            img = (await c.get(item["url"])).content
    else:
        raise ValueError("no image data in response")
    return {"url": _save(img, _ext_for(img)), "model": model, "bytes": len(img)}


@mcp.tool()
async def generate_video(
    ctx: Context,
    prompt: Annotated[str, Field(description="Scene description: subject, motion, camera")],
    model: Annotated[str, Field(description="Video model — see list_media_models().video_models. MiniMax-H3 = newest v2 (up to 2K, first+last frame, reference media); Hailuo-2.3/02, T2V-01 = v1")] = "MiniMax-H3",
    duration_s: Annotated[Optional[int], Field(description="Clip length in seconds (H3: 5-10; v1: 5-10); omit for provider default")] = None,
    resolution: Annotated[str, Field(description="768P (default) | 1080P | 2K (H3 only)")] = "768P",
    ratio: Annotated[Optional[str], Field(description="Aspect ratio 16:9|4:3|1:1|3:4|9:16|21:9 — REQUIRED for H3 text-to-video; 'adaptive' allowed when a frame image is given")] = None,
    first_frame_url: Annotated[Optional[str], Field(description="Optional image (URL from generate_image/edit_image, upload:<name>, data URI or base64) -> image-to-video first frame; converted to inline data URI server-side (MiniMax can't fetch hosted URLs)")] = None,
    last_frame_url: Annotated[Optional[str], Field(description="H3 only: image for the last frame (first+last frame mode); same source formats as first_frame_url")] = None,
) -> dict:
    """Submit an async video generation job (MiniMax). Returns {task_id} —
    generation takes minutes; poll video_status(task_id) until status=Success.
    H3 models use the v2 multimodal API; others use v1."""
    vk = _vk(ctx)
    async with httpx.AsyncClient(timeout=60) as c:
        if first_frame_url:
            first_frame_url = await _img_data_uri(first_frame_url, c)
        if last_frame_url:
            last_frame_url = await _img_data_uri(last_frame_url, c)
    if model in VIDEO_V2_MODELS:
        content = [{"type": "text", "text": prompt}]
        if first_frame_url:
            content.append({"type": "image_url",
                            "image_url": {"url": first_frame_url},
                            "role": "first_frame"})
        if last_frame_url:
            content.append({"type": "image_url",
                            "image_url": {"url": last_frame_url},
                            "role": "last_frame"})
        has_frame = bool(first_frame_url or last_frame_url)
        body = {"model": model, "content": content,
                "resolution": resolution,
                "duration": duration_s or 5,
                "ratio": ratio or ("adaptive" if has_frame else "16:9")}
        r = await _llm(vk, "POST", "/minimax/v2/video_generation",
                       timeout=90, json=body)
    else:
        body = {"model": model, "prompt": prompt, "resolution": resolution}
        if duration_s:
            body["duration"] = duration_s
        if first_frame_url:
            body["first_frame_image"] = first_frame_url
        r = await _llm(vk, "POST", "/minimax/v1/video_generation",
                       timeout=90, json=body)
    d = r.json()
    tid = d.get("task_id")
    if not tid:
        raise ValueError(f"no task_id: {d!r}"[:300])
    return {"task_id": tid, "status": "submitted", "model": model}


@mcp.tool()
async def video_status(
    ctx: Context,
    task_id: Annotated[str, Field(description="task_id returned by generate_video")],
) -> dict:
    """Poll a video job. Returns {status: Queueing|Processing|Success|Fail};
    on Success also {url} — the video re-hosted locally (provider links expire).
    Works for both v1 and v2 (H3) task_ids — the right API is probed."""
    vk = _vk(ctx)
    d = None
    for ver in ("v2", "v1"):
        try:
            r = await _llm(vk, "GET",
                           f"/minimax/{ver}/query/video_generation?task_id={task_id}",
                           timeout=60)
            cand = r.json()
            items = cand.get("items")          # v2 wraps tasks in items[]
            if items is not None:              # v2 ignores task_id — filter
                cand = next((i for i in items
                             if str(i.get("id")) == str(task_id)), {})
                if not cand:
                    continue                   # task not on v2 -> try v1
            if cand.get("status") or cand.get("file_id") or cand.get("content"):
                d = cand
                break
            d = cand
        except ValueError:
            continue
    if d is None:
        raise ValueError("task not found on v1/v2 query endpoints")
    status = (d.get("status") or "unknown")
    status = {"succeeded": "Success", "failed": "Fail"}.get(status.lower(), status)
    out = {"task_id": task_id, "status": status}
    if status == "Success":
        # v2: content.url / file list; v1: file_id -> files/retrieve
        dl = ((d.get("content") or {}).get("url") if isinstance(d.get("content"), dict) else None)
        fid = d.get("file_id")
        if not dl and fid:
            if not MINIMAX_KEY:
                raise ValueError("MINIMAX_PAYG_KEY not set on media-mcp")
            async with httpx.AsyncClient(timeout=60) as c:
                fr = await c.get(
                    f"{MINIMAX_API}/v1/files/retrieve?file_id={fid}",
                    headers={"Authorization": f"Bearer {MINIMAX_KEY}"})
                fr.raise_for_status()
            dl = ((fr.json().get("file") or {}).get("download_url")
                  or fr.json().get("download_url"))
        if not dl and isinstance(d.get("content"), list):
            for item in d["content"]:
                if isinstance(item, dict) and item.get("url"):
                    dl = item["url"]; break
        if dl:
            async with httpx.AsyncClient(timeout=300) as c:
                vid = (await c.get(dl)).content
            out["url"] = _save(vid, ".mp4")
            out["bytes"] = len(vid)
        else:
            out["note"] = "no downloadable URL in response — inspect raw"
    return out


@mcp.tool()
async def synthesize_speech(
    ctx: Context,
    text: Annotated[str, Field(description="Text to speak. Audio tags like (laughs),(sighs),(唱歌) for singing are allowed inside")],
    model: Annotated[str, Field(description="TTS model — see list_media_models().tts_models")] = "voice/xiaomi/mimo-v2.5-tts",
    voice: Annotated[Optional[str], Field(description="Voice id: mimo presets (mimo_default, Mia, Chloe, Milo, Dean, 冰糖, 茉莉, 苏打, 白桦), minimax system voices (female-shaonv, presenter_male, ...) or a cloned voice_id from register_voice_clone")] = None,
    style: Annotated[Optional[str], Field(description="Natural-language style instruction: emotion, pace, accent. REQUIRED for voicedesign model")] = None,
    format: Annotated[str, Field(description="Output audio format: wav | mp3")] = "wav",
) -> dict:
    """Text-to-speech; returns hosted audio URL.
    Voice selection: mimo preset voices (xiaomi) or minimax system voices —
    full lists in list_media_models()."""
    vk = _vk(ctx)
    if model.startswith("minimax/"):
        vid = voice or "female-shaonv"
        # cloned voice_ids live in the PAYG account; system voices go
        # through TokenPlan billing
        path = ("/minimax/v1/t2a_v2" if vid in MINIMAX_VOICES
                else "/minimaxpayg/v1/t2a_v2")
        body = {"model": "speech-2.8-hd", "text": text,
                "voice_setting": {"voice_id": vid},
                "audio_setting": {"format": "mp3"}}
        r = await _llm(vk, "POST", path, timeout=180, json=body)
        d = r.json()
        if (d.get("base_resp") or {}).get("status_code", 0) != 0:
            raise ValueError(f"minimax: {(d.get('base_resp') or {}).get('status_msg')}")
        raw = binascii.unhexlify(d["data"]["audio"])
        return {"url": _save(raw, ".mp3"), "model": model, "bytes": len(raw)}

    messages = []
    if style:
        messages.append({"role": "user", "content": style})
    elif model.endswith("voicedesign"):
        raise ValueError("voicedesign requires `style` (voice description)")
    messages.append({"role": "assistant", "content": text})
    audio = {"format": format}
    if voice:
        audio["voice"] = voice
    body = {"model": model, "messages": messages, "audio": audio}
    r = await _llm(vk, "POST", "/v1/chat/completions", timeout=300, json=body)
    a = (r.json()["choices"][0]["message"].get("audio") or {})
    raw = base64.b64decode(a.get("data") or "")
    return {"url": _save(raw, _ext_for(raw, format)), "model": model,
            "bytes": len(raw)}


@mcp.tool()
async def clone_speech(
    ctx: Context,
    text: Annotated[str, Field(description="Text to speak in the cloned voice")],
    sample: Annotated[str, Field(description="Voice sample to clone: http(s) URL | data-URI | 'upload:<name>' (user uploads via POST /media-upload) | base64. wav/mp3, <=10MB, a few seconds of clean speech")],
    style: Annotated[Optional[str], Field(description="Optional style instruction (emotion, pace)")] = None,
    format: Annotated[str, Field(description="Output format: wav | mp3")] = "wav",
) -> dict:
    """TTS in a cloned voice (xiaomi mimo-v2.5-tts-voiceclone, zero-shot —
    sample travels with the request). For a reusable clone use
    register_voice_clone + synthesize_speech(minimax). Returns audio URL."""
    vk = _vk(ctx)
    async with httpx.AsyncClient(timeout=120) as c:
        raw = await _resolve_source(sample, c)
    if len(base64.b64encode(raw)) > MAX_SAMPLE * 4 // 3:
        raise ValueError("sample too large (max ~10MB audio)")
    mime = _mime_for(raw)
    if mime not in ("audio/wav", "audio/mpeg"):
        raise ValueError(f"sample must be wav or mp3, got {mime}")
    data_uri = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
    messages = [{"role": "user", "content": style or ""},
                {"role": "assistant", "content": text}]
    body = {"model": "voice/xiaomi/mimo-v2.5-tts-voiceclone",
            "messages": messages,
            "audio": {"voice": data_uri, "format": format}}
    r = await _llm(vk, "POST", "/v1/chat/completions", timeout=300, json=body)
    a = (r.json()["choices"][0]["message"].get("audio") or {})
    out = base64.b64decode(a.get("data") or "")
    return {"url": _save(out, _ext_for(out, format)), "bytes": len(out)}


@mcp.tool()
async def register_voice_clone(
    ctx: Context,
    sample: Annotated[str, Field(description="Voice sample: http(s) URL | data-URI | 'upload:<name>' | base64. wav/mp3, clean speech (>=10s recommended)")],
    voice_id: Annotated[Optional[str], Field(description="Custom name for the cloned voice (letters/digits/-_, starts with a letter, >=8 chars). Auto-generated if omitted")] = None,
    noise_reduction: Annotated[bool, Field(description="Strip background noise from the sample")] = False,
    volume_normalization: Annotated[bool, Field(description="Normalize sample loudness")] = False,
) -> dict:
    """Register a PERSISTENT cloned voice at MiniMax — returns {voice_id}.
    Reuse it in synthesize_speech(model='minimax/speech-2.8-hd', voice=<voice_id>).
    Unlike clone_speech (xiaomi zero-shot, per-request), the voice stays
    registered in the MiniMax account and can be reused across calls.
    Billed to the MiniMax PAYG account."""
    vk = _vk(ctx)
    vid = voice_id or f"mcp-{uuid.uuid4().hex[:8]}"
    if not re.match(r"^[A-Za-z][A-Za-z0-9_-]{7,}$", vid):
        raise ValueError("voice_id must start with a letter, >=8 chars, letters/digits/-/_")
    async with httpx.AsyncClient(timeout=120) as c:
        raw = await _resolve_source(sample, c)
    mime = _mime_for(raw)
    if mime not in ("audio/wav", "audio/mpeg"):
        raise ValueError(f"sample must be wav or mp3, got {mime}")
    if len(raw) > MAX_SAMPLE:
        raise ValueError("sample too large (max ~10MB)")

    ext = _ext_for(raw)
    r = await _llm(vk, "POST", "/minimax/v1/files/upload", timeout=180,
                   files={"file": (f"sample{ext}", raw, mime)},
                   data={"purpose": "voice_clone"})
    d = r.json()
    file_id = (d.get("file") or {}).get("file_id") or d.get("file_id")
    if not file_id:
        raise ValueError(f"no file_id in upload response: {str(d)[:200]}")

    body = {"file_id": file_id, "voice_id": vid,
            "need_noise_reduction": noise_reduction,
            "need_volume_normalization": volume_normalization}
    r = await _llm(vk, "POST", "/minimax/v1/voice_clone", timeout=180, json=body)
    d = r.json()
    br = d.get("base_resp") or {}
    if br.get("status_code", 0) != 0:
        raise ValueError(f"minimax voice_clone: {br.get('status_msg')}")
    return {"voice_id": vid, "file_id": file_id,
            "usage": f"synthesize_speech(text=..., model='minimax/speech-2.8-hd', voice='{vid}')"}


@mcp.tool()
async def transcribe_audio(
    ctx: Context,
    source: Annotated[str, Field(description="Audio to transcribe: http(s) URL | data-URI | 'upload:<name>' | base64. wav/mp3, <=10MB")],
    language: Annotated[str, Field(description="auto | zh | en")] = "auto",
) -> dict:
    """Speech-to-text (xiaomi mimo-v2.5-asr); returns {text, seconds}."""
    vk = _vk(ctx)
    async with httpx.AsyncClient(timeout=120) as c:
        raw = await _resolve_source(source, c)
    ext = _ext_for(raw)
    if ext not in (".wav", ".mp3"):
        raise ValueError(f"unsupported audio (need wav/mp3), detected {ext or 'unknown'}")
    data_url = f"data:{_mime_for(raw)};base64,{base64.b64encode(raw).decode()}"
    body = {"model": "voice/xiaomi/mimo-v2.5-asr",
            "messages": [{"role": "user", "content": [
                {"type": "input_audio",
                 "input_audio": {"data": data_url,
                                 "format": ext.lstrip(".")}}]}],
            "extra_body": {"asr_options": {"language": language}}}
    r = await _llm(vk, "POST", "/v1/chat/completions", timeout=300, json=body)
    d = r.json()
    text = d["choices"][0]["message"].get("content") or ""
    usage = d.get("usage") or {}
    return {"text": text, "seconds": usage.get("seconds")}


# ---------- upload route ----------

@mcp.custom_route("/upload", methods=["POST"])
async def upload(request: Request):
    auth = request.headers.get("authorization", "")
    tok = auth.replace("Bearer ", "").strip()
    if not tok:
        return JSONResponse({"error": "missing Bearer VK"}, status_code=401)
    async with httpx.AsyncClient(base_url=LITELLM, timeout=20) as c:
        vr = await c.get("/v1/models", headers={"Authorization": f"Bearer {tok}"})
    if vr.status_code != 200:
        return JSONResponse({"error": "invalid VK"}, status_code=401)
    body = await request.body()
    if not body:
        return JSONResponse({"error": "empty body"}, status_code=400)
    if len(body) > MAX_UPLOAD:
        return JSONResponse({"error": "too large (>25MB)"}, status_code=413)
    fname = request.query_params.get("name", "file")
    fname = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(fname))[:80]
    name = f"{uuid.uuid4().hex}-{fname}"
    with open(os.path.join(UPLOADS_DIR, name), "wb") as f:
        f.write(body)
    url = f"{PUBLIC_BASE}/media-files/uploads/{name}"
    return JSONResponse({"url": url, "ref": f"upload:{name}",
                         "bytes": len(body)})


if __name__ == "__main__":
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    mcp.run(transport="streamable-http")
