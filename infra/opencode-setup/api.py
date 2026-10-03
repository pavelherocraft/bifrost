#!/usr/bin/env python3
"""opencode-setup API.

Resolves a LiteLLM user_id -> the model list + MCP server list that user
can actually access. Returns model objects with name, context-window limits
(max_input_tokens / max_tokens) and vision capability (supports_vision),
all sourced from LiteLLM's /model/info — single source of truth for both
config and opencode.json.

For MCP servers: source of truth is the user's Teams. We aggregate
object_permission.mcp_servers + mcp_access_groups across all teams the
user belongs to, then intersect with the set of registered MCP servers.
Per-tool filtering happens server-side at request time.

Endpoints
  GET /models?user=<UUID>      -> 200 {"models": [{"id","context","output","vision","image","image_edit"}]}
  GET /models?vk=sk-...        -> resolves VK -> user_id, then 200 (same shape)
  GET /models?vkh=<sha256>     -> resolves VK by key hash -> user_id, then 200
  GET /mcp?user=<UUID>         -> 200 {"servers": [{"name","url","description"}]}
  GET /mcp?vk=sk-...           -> resolves VK -> user_id, then 200 (same shape)
  GET /mcp?vkh=<sha256>        -> resolves VK by key hash -> user_id, then 200
  GET /vk-info?vk=sk-...       -> 200 {"user_id","key_alias","team_id"} or 404
  GET /vk-info?vkh=<sha256>    -> same shape, looked up by key hash
  GET /health                  -> 200 {"ok": true}
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LITELLM_BASE = os.environ.get("LITELLM_BASE", "http://127.0.0.1:4001")
ENV_FILE = "/opt/litellm/.env"
LISTEN = ("127.0.0.1", 9000)
EXTERNAL_BASE_URL = os.environ.get(
    "EXTERNAL_BASE_URL", "https://hcbifrost.herocraft.com/litellm"
)

_MASTER_KEY = None
_MODEL_INFO = None
_ALIASES = None

# Models that support reasoning/thinking output. Used to emit
# `reasoning: true` in /models response so the opencode.json generator
# (index.html buildCfg) can add `options.thinking` per model.
# Source: official docs (zhipu/bigmodel.cn for GLM, dashscope for Qwen,
# moonshot for Kimi, custom_openai passthrough for MiniMax-M3).
_REASONING_CAPABLE = {
    "glm-4.5", "glm-4.5-air", "glm-4.6",
    "GLM-4.7", "GLM-4.7 (res)",
    "GLM-5.3", "GLM-5.3 (res)",
    "GLM-5.3-Flash", "GLM-5.3-Flash (res)",
    "tencent/glm-5-2 (reserved - use when main is exhausted)", "tencent/glm-5-3 (reserved - use when main is exhausted)",
    "tencent/glm5-3flash (reserved - use when main is exhausted)",
    "tencent/DeepSeek-V4.1-Flash",
    "openrouter/deepseek-v4.1-flash",
    "xiaomi/mimo-v2.6-pro", "xiaomi/mimo-v2.6-flash",
    "stepfun/step-5-preview", "stepfun/step-3.5-flash-2603", "stepfun/step-3.7-flash",
    "tencent/Kimi K3 (reserved - use when main is exhausted)",
    "tencent/Hy4",
    "tencent/Hy3",
    "kimi-k2-0905-preview", "kimi-k2-turbo-preview",
    "Kimi K2.6", "Kimi K2.7", "Kimi K3",
    "Kimi K3-256K", "Kimi K2.8",
    "MiniMax-M2.5", "MiniMax-M3", "MiniMax-M3.1-Flash-Preview",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/deepseek-v4.1-flash (free)", "nvidia/glm-5.3 (free)",
    "nvidia/glm-5.3-flash (free)", "nvidia/kimi-k3 (free)",
    "Qwen3.5-plus", "QWEN3.7-plus",
    "qwen3.7-max", "qwen3.6-flash",
    "qwen3-coder-plus", "qwen3-coder-flash", "qwen3-max",
    "mimo-v2.5", "mimo-v2.5-pro",
    "deepseek-v4-pro", "deepseek-ai/deepseek-v3.2",
}

# Models that support `reasoningEffort` cycling via opencode `variants` cycle keybind.
# Cycles {low, high, max} for k3-256k (also kimi-for-coding would be drop-down-driven by
# its own Thinking always-ON flag, not selected here). All others inherit reasoning=True
# but no variants from LiteLLM (e.g. K2.6 was removed, Moonshot k2.x echoes reasoning
# in completion but not as a configurable effort parameter in /chat/completions).
_REASONING_VARIANTS = {
    "Kimi K3": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "Kimi K3-256K": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "MiniMax-M3": {
        "low":    {"reasoningEffort": "low"},
        "medium": {"reasoningEffort": "medium"},
        "high":   {"reasoningEffort": "high"},
        "xhigh":  {"reasoningEffort": "xhigh"},
        "max":    {"reasoningEffort": "max"},
    },
    "MiniMax-M3.1-Flash-Preview": {
        "low":    {"reasoningEffort": "low"},
        "medium": {"reasoningEffort": "medium"},
        "high":   {"reasoningEffort": "high"},
        "xhigh":  {"reasoningEffort": "xhigh"},
        "max":    {"reasoningEffort": "max"},
    },
    "GLM-5.3": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "tencent/DeepSeek-V4.1-Flash": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "openrouter/deepseek-v4.1-flash": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "xiaomi/mimo-v2.6-pro": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "xiaomi/mimo-v2.6-flash": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "stepfun/step-5-preview": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "stepfun/step-3.5-flash-2603": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "stepfun/step-3.7-flash": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "tencent/Kimi K3 (reserved - use when main is exhausted)": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "GLM-5.3 (res)": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "GLM-5.3-Flash": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "GLM-5.3-Flash (res)": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "tencent/glm-5-3 (reserved - use when main is exhausted)": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "tencent/glm5-3flash (reserved - use when main is exhausted)": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
}

# Models that support image generation (text-to-image) via /v1/images/generations.
# Litellm model_info.mode == 'image_generation' identifies these.
# All image models in DB currently have mode = 'image_generation' (set during
# model creation); the gate is enforced at LiteLLM proxy level.

# Models that ALSO support image-to-image editing via /v1/images/edits.
# - gpt-image-1.5 / gpt-image-2: OpenAI image edits endpoint, native support.
# - gemini/gemini-3.1-flash-image: routed to native Gemini :generateContent with
#   multimodal input (text + image parts), model must be configured with
#   custom_llm_provider='gemini' (NOT 'openai') so LiteLLM uses the Gemini
#   image_edit transformation instead of OpenAI's.
# - gemini/gemini-3-pro-image: multimodal Gemini 3 Pro with image generation,
#   supports both text-to-image AND image-to-image via native :generateContent.
#   Upstream gemini-3-pro-image (NOT imagen-4). Routed to native provider.
_IMAGE_EDIT_CAPABLE = {
    "gpt-image-1.5",
    "gpt-image-2",
    "gemini/gemini-3.1-flash-image",
    "gemini/gemini-3-pro-image",
    "gpt-image-2.5-sunburst", "gpt-image-2.5-flare",
}


def load_master_key():
    global _MASTER_KEY
    if _MASTER_KEY:
        return _MASTER_KEY
    if not os.path.exists(ENV_FILE):
        raise RuntimeError(f"missing {ENV_FILE}")
    with open(ENV_FILE) as f:
        for line in f:
            m = re.match(r'^LITELLM_MASTER_KEY\s*=\s*["\']?([^"\'#\s]+)', line)
            if m:
                _MASTER_KEY = m.group(1)
                return _MASTER_KEY
    raise RuntimeError(f"LITELLM_MASTER_KEY not found in {ENV_FILE}")


def litellm_get(path):
    key = load_master_key()
    req = urllib.request.Request(
        LITELLM_BASE + path,
        headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

# Virtual key prefixes supported by LiteLLM (see litellm.proxy.common_utils._keys).
# A VK lookup must start with one of these, otherwise it's clearly not a key token.
VK_PREFIX_RE = re.compile(r"^(sk-|hkg-)")

# Key hash: 64-char lowercase hex (SHA-256). LiteLLM stores keys internally
# as hashes (token_sha256_hash / token in VerificationToken). LiteLLM
# /key/info accepts ?key=<hash> as a secure lookup that doesn't expose the
# raw sk-/hkg- prefix.
VK_HASH_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)

def _to_int(v):
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


# Cache VK -> user_id lookups. Keys are rarely rotated, so a 5-minute cache
# dramatically reduces load when the opencode-setup page is open in many tabs.
_VK_CACHE = {}
_VK_CACHE_TTL_S = 300


def resolve_vk_to_user(vk_token, is_hash=False):
    """Resolve a virtual-key token (sk-.../hkg-...) or its SHA-256 hash to
    {user_id, key_alias, team_id, models, user_email, key_name} via LiteLLM
    /key/info. Returns None if the key is invalid/unknown.

    Result is cached in-memory for 5 minutes. Use force=True to bypass.

    LiteLLM's /key/info endpoint accepts both the raw token AND its hash.
    Using hashes is safer (no need to ship sk-... strings through the page)
    and is what the Admin UI itself uses internally.

    The `models` field is the per-key whitelist (VerificationToken.models).
    If empty, the key inherits models from user + teams.
    """
    if not vk_token:
        return None
    if not is_hash and not VK_PREFIX_RE.match(vk_token):
        return None
    if is_hash and not VK_HASH_RE.match(vk_token):
        return None
    now = __import__("time").time()
    cached = _VK_CACHE.get(vk_token)
    if cached and cached[0] > now - _VK_CACHE_TTL_S:
        return cached[1]
    try:
        doc = litellm_get("/key/info?key=" + urllib.parse.quote(vk_token, safe=""))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _VK_CACHE[vk_token] = (now, None)
            return None
        raise
    info = doc.get("info") or doc
    user_id = info.get("user_id") or None
    if isinstance(user_id, str) and not UUID_RE.match(user_id):
        # Some keys are scoped to a team without a user_id. Fall back to
        # the team owner's user_id by reading the team membership list.
        user_id = None
    key_alias = info.get("key_alias") or ""
    team_id = info.get("team_id") or None
    user_email = info.get("user_email") or None
    key_name = info.get("key_name") or None
    # Per-key model whitelist (VerificationToken.models). May be None / [].
    raw_models = info.get("models")
    if raw_models is None:
        key_models = []
    elif isinstance(raw_models, list):
        key_models = [m for m in raw_models if isinstance(m, str)]
    else:
        key_models = []
    result = {
        "user_id": user_id,
        "key_alias": key_alias,
        "team_id": team_id,
        "user_email": user_email,
        "key_name": key_name,
        "key_models": key_models,  # [] means "inherit from user + teams"
    }
    _VK_CACHE[vk_token] = (now, result)
    return result


def resolve_target(qs):
    """Return user_id (str) given parsed query-string from /models or /mcp.

    Accepts either ?user=<UUID> (or user_id=<UUID>), ?vk=sk-... (or hkg-...),
    or ?vkh=<64-hex SHA-256>. Raises ValueError with a human-readable
    message when none is supplied or all are bad.

    When called from /models or /mcp, also stashes the VK info in
    _VK_CONTEXT so resolve_models() can intersect with the key's own
    model whitelist. When the key has its own models list, that's the
    AUTHORITATIVE list — user/team models are ignored.
    """
    global _VK_CONTEXT
    vk = (qs.get("vk") or [""])[0]
    vkh = (qs.get("vkh") or [""])[0]
    uid = (qs.get("user") or qs.get("user_id") or [""])[0]
    # VK parameters take priority over plain user lookup. The setup-opencode
    # page forwards both ?user=<resolved-uid> and ?vkh=... (so a refresh
    # works even after the key row rotates). We must honor VK context so
    # resolve_models() applies team-only / whitelist rules — otherwise the
    # owner's other teams' models would leak into the answer.
    if vk or vkh:
        if vk:
            info = resolve_vk_to_user(vk)
            if not info or not info.get("user_id"):
                raise LookupError(f"virtual key not found or has no user_id: {vk[:12]}...")
        else:
            info = resolve_vk_to_user(vkh, is_hash=True)
            if not info or not info.get("user_id"):
                raise LookupError("virtual key (hash) not found or has no user_id")
        if not info.get("user_email"):
            try:
                udoc = litellm_get("/user/info?user_id=" + urllib.parse.quote(info["user_id"]))
                uinfo = udoc.get("user_info") or udoc
                info["user_email"] = uinfo.get("user_email")
            except Exception:
                pass
        _VK_CONTEXT = info
        via = "vk" if vk else "vkh"
        return info["user_id"], {"via": via, "user_id": info["user_id"],
                                  "vk": vk or None, "vkh": vkh or None,
                                  "key_alias": info.get("key_alias"),
                                  "user_email": info.get("user_email"),
                                  "key_name": info.get("key_name"),
                                  "key_models": info.get("key_models") or []}
    if uid:
        if not UUID_RE.match(uid):
            raise ValueError(
                f"user param must be a UUID, got: {uid!r}. " +
                "API keys / virtual keys (sk-...) are NOT user_ids."
            )
        _VK_CONTEXT = None  # No VK context for plain user lookup.
        return uid, {"via": "user", "user_id": uid,
                     "vk": None, "vkh": None, "key_alias": None,
                     "user_email": None, "key_name": None,
                     "key_models": []}
    raise ValueError("missing user, vk or vkh param")


# Set by resolve_target() when the request was scoped to a VK. Read by
# resolve_models() so it can intersect with the per-key model whitelist.
_VK_CONTEXT = None


def _extract_meta(m):
    """Read context/output limits, modality flags and image capability from a
    /model/info entry.

    Returns (ctx, out, vision, video, audio, image, image_edit, blocked) where
    the modality booleans come from model_info.supports_vision /
    supports_video_input / supports_audio_input (all optional, default False).

    `image` is True when mode='image_generation' (text-to-image via
    /v1/images/generations). `image_edit` is True when the model is in
    _IMAGE_EDIT_CAPABLE (image-to-image via /v1/images/edits). `blocked` is
    True when LiteLLM has flagged the model as blocked (admin-disabled).
    """
    lp = m.get("litellm_params") or {}
    mi = m.get("model_info") or {}
    ctx = _to_int(lp.get("max_input_tokens") or mi.get("max_input_tokens"))
    out = _to_int(lp.get("max_tokens") or mi.get("max_tokens"))
    vision = bool(mi.get("supports_vision"))
    video = bool(mi.get("supports_video_input"))
    audio = bool(mi.get("supports_audio_input"))
    image = (mi.get("mode") == "image_generation")
    mn = m.get("model_name") or ""
    image_edit = image and (mn in _IMAGE_EDIT_CAPABLE)
    blocked = bool(mi.get("blocked"))
    return ctx, out, vision, video, audio, image, image_edit, blocked


def build_model_info():
    global _MODEL_INFO, _ALIASES
    if _MODEL_INFO is not None:
        return _MODEL_INFO
    info = {}
    aliases = {}
    try:
        doc = litellm_get("/model/info")
        for m in doc.get("data", []):
            mn = m.get("model_name")
            if not mn:
                continue
            ctx, out, vision, video, audio, image, image_edit, blocked = _extract_meta(m)
            if blocked:
                continue
            info[mn] = {
                "context": ctx,
                "output": out,
                "vision": vision,
                "video": video,
                "audio": audio,
                "image": image,
                "image_edit": image_edit,
            }
            aliases[mn] = mn
            # Alias by upstream `model` field (LiteLLM may store the openai-style
            # name there for OpenAI-compat providers).
            lm = (m.get("litellm_params") or {}).get("model", "")
            if lm and lm not in aliases:
                aliases[lm] = mn
            # For model_name like "gemini/gemini-3.1-flash-image", alias the
            # bare "gemini-3.1-flash-image" too, because users see that name in
            # team.model lists (LiteLLM strips the provider prefix there).
            if "/" in mn:
                bare = mn.split("/", 1)[1]
                if bare not in aliases:
                    aliases[bare] = mn
    except Exception:
        pass
    _MODEL_INFO = info
    _ALIASES = aliases
    return info


def resolve_model_name(raw):
    build_model_info()
    return (_ALIASES or {}).get(raw, raw)


def resolve_user_teams(user_id):
    """Return list of team_info dicts the user belongs to."""
    teams = []
    try:
        udoc = litellm_get(f"/user/info?user_id={urllib.parse.quote(user_id)}")
    except urllib.error.HTTPError:
        return teams
    info = udoc.get("user_info") or udoc
    for team_id in info.get("teams") or []:
        try:
            tdoc = litellm_get(f"/team/info?team_id={urllib.parse.quote(team_id)}")
        except urllib.error.HTTPError:
            continue
        tinfo = tdoc.get("team_info") or tdoc
        if isinstance(tinfo, dict):
            teams.append(tinfo)
    return teams


def resolve_team(team_id):
    """Return a single team_info dict for the given team_id, or None on failure."""
    try:
        tdoc = litellm_get(f"/team/info?team_id={urllib.parse.quote(team_id)}")
    except urllib.error.HTTPError:
        return None
    tinfo = tdoc.get("team_info") or tdoc
    return tinfo if isinstance(tinfo, dict) else None


def resolve_models(user_id):
    """Aggregate model access according to LiteLLM's actual model resolution.

    Resolution priority (matches LiteLLM proxy behavior for VerificationToken
    auth, verified empirically):

      1. Per-key whitelist (VerificationToken.models) is AUTHORITATIVE when it
         contains REAL model names. Special values like "all-team-models" mean
         "inherit from team only" — that's a different case (handled below).
      2. If key_models == ["all-team-models"] (team-scoped key with sentinel):
         use ONLY the models of the team the VK is BOUND to (key.team_id).
         User.personal models are NOT applied. The user's other teams are NOT
         applied — only the one the VK is scoped to.
      3. If key_models is empty (None / []): no per-key whitelist. For a
         user-scoped VK, inherit user.models + all user's teams. For a
         team-scoped VK (team_id set), the model set is empty — those keys
         MUST have models=['all-team-models'] to be useful.
      4. Otherwise (real whitelist): use the whitelist verbatim.

    Returns a list of model dicts sorted by id. Side-channel info about the
    resolution path is stashed in _VK_CONTEXT so /models can report it back
    to the UI (e.g. "this VK inherits from team Agents only").
    """
    try:
        udoc = litellm_get(f"/user/info?user_id={urllib.parse.quote(user_id)}")
    except urllib.error.HTTPError:
        return []
    info = udoc.get("user_info") or udoc

    user_models = set(info.get("models") or [])

    # Teams the user belongs to (used for non-VK lookups).
    user_team_models = set()
    for team in resolve_user_teams(user_id):
        user_team_models.update(team.get("models") or [])

    raw_models = None
    resolution_mode = None  # one of: "user+team", "team-only", "explicit", "none"
    if _VK_CONTEXT:
        km = _VK_CONTEXT.get("key_models") or []
        vk_team_id = _VK_CONTEXT.get("team_id")
        if km == ["all-team-models"]:
            # Team-scoped key with sentinel. LiteLLM uses ONLY the team the
            # key is BOUND to (VerificationToken.team_id), NOT the user's
            # other teams or personal models.
            bound_team = resolve_team(vk_team_id) if vk_team_id else None
            raw_models = set((bound_team or {}).get("models") or [])
            resolution_mode = "team-only"
        elif km:
            # Explicit per-key whitelist — overrides everything.
            raw_models = set(km)
            resolution_mode = "explicit"
        else:
            # Empty key whitelist on a user-scoped VK: inherit user + all teams.
            if vk_team_id:
                # Team-scoped but no whitelist — LiteLLM allows nothing.
                # (This shouldn't happen in practice; team keys always have
                # 'all-team-models'. We surface this honestly.)
                bound_team = resolve_team(vk_team_id)
                raw_models = set((bound_team or {}).get("models") or [])
                resolution_mode = "team-only"
            else:
                raw_models = user_models | user_team_models
                resolution_mode = "user+team"
    else:
        # Not a VK request: just user + all teams.
        raw_models = user_models | user_team_models
        resolution_mode = "user+team"

    # Surface resolution info via the (mutable) _VK_CONTEXT so /models can
    # echo it back to the UI. Existing fields stay intact.
    if _VK_CONTEXT is not None:
        _VK_CONTEXT["resolution_mode"] = resolution_mode

    table = build_model_info()
    out = []
    for raw in raw_models:
        canonical = resolve_model_name(raw)
        limits = table.get(canonical) or {
            "context": 0, "output": 0,
            "vision": False, "video": False, "audio": False,
            "image": False, "image_edit": False,
        }
        out.append({
            "id": canonical,
            "context": limits["context"],
            "output": limits["output"],
            "vision": limits.get("vision", False),
            "video": limits.get("video", False),
            "audio": limits.get("audio", False),
            "image": limits.get("image", False),
            "image_edit": limits.get("image_edit", False),
            "reasoning": canonical in _REASONING_CAPABLE,
            "variants": _REASONING_VARIANTS.get(canonical),
        })
    out.sort(key=lambda x: x["id"])
    return out


def _collect_team_mcp_ids(teams):
    """Aggregate mcp_servers + mcp_access_groups across teams.

    Returns (server_id_set, access_group_set). server_ids may be raw
    server_id UUIDs OR alias names (LiteLLM stores both formats).
    """
    server_ids = set()
    access_groups = set()
    for team in teams:
        op = team.get("object_permission") or {}
        if not isinstance(op, dict):
            continue
        for sid in op.get("mcp_servers") or []:
            if sid:
                server_ids.add(sid)
        for ag in op.get("mcp_access_groups") or []:
            if ag:
                access_groups.add(ag)
    return server_ids, access_groups


def _list_mcp_servers():
    """Return the raw list of registered MCP server objects from LiteLLM."""
    try:
        doc = litellm_get("/v1/mcp/server")
    except (urllib.error.HTTPError, Exception):
        return []
    if isinstance(doc, dict):
        return doc.get("data") or []
    return doc or []


def resolve_user_mcp_servers(user_id):
    """Resolve MCP servers accessible to a user via Teams.

    Strategy:
      1. Resolve all teams the user belongs to (via /user/info).
      2. Aggregate object_permission.mcp_servers (LiteLLM stores these
         as server_name OR alias) and object_permission.mcp_access_groups
         across teams.
      3. List registered MCP servers via /v1/mcp/server.
      4. Keep only servers whose server_name (or alias) matches the
         team's allowed set, OR that belong to one of the allowed
         access_groups.
      5. URL-namespaced through LiteLLM proxy; per-tool filtering is
         applied server-side at request time.
    """
    teams = resolve_user_teams(user_id)
    allowed_ids, allowed_groups = _collect_team_mcp_ids(teams)

    if not allowed_ids and not allowed_groups:
        return []

    registered = _list_mcp_servers()
    matched = []
    for s in registered:
        if not isinstance(s, dict):
            continue
        sid = s.get("server_id")
        sname = s.get("server_name")
        alias = s.get("alias")
        server_groups = set(s.get("mcp_access_groups") or [])

        # Match by server_id (LiteLLM stores mcp_servers as UUIDs in
        # object_permission) OR by alias/server_name (legacy / manual config).
        if sid and sid in allowed_ids:
            matched.append(s); continue
        if sname and sname in allowed_ids:
            matched.append(s); continue
        if alias and alias in allowed_ids:
            matched.append(s); continue
        if server_groups and (server_groups & allowed_groups):
            matched.append(s); continue

    servers_out = []
    for s in matched:
        sname = s.get("alias") or s.get("server_name")
        if not sname:
            continue
        servers_out.append({
            "name": sname,
            "url": f"{EXTERNAL_BASE_URL}/{sname}/mcp",
            "description": s.get("description", ""),
        })
    return sorted(servers_out, key=lambda s: s["name"])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            u = urllib.parse.urlparse(self.path)
            if u.path == "/health":
                return self._send(200, {"ok": True})

            if u.path == "/vk-info":
                qs = urllib.parse.parse_qs(u.query)
                vk = (qs.get("vk") or [""])[0]
                vkh = (qs.get("vkh") or [""])[0]
                if not vk and not vkh:
                    return self._send(400, {"error": "missing vk or vkh param"})
                if vk:
                    info = resolve_vk_to_user(vk)
                else:
                    info = resolve_vk_to_user(vkh, is_hash=True)
                if not info:
                    return self._send(404, {"error": "virtual key not found",
                                            "vk_prefix": vk[:12] if vk else None,
                                            "vkh": vkh[:8] + "..." if vkh else None})
                # Enrich with user_email if missing.
                if info.get("user_id") and not info.get("user_email"):
                    try:
                        udoc = litellm_get("/user/info?user_id=" + urllib.parse.quote(info["user_id"]))
                        uinfo = udoc.get("user_info") or udoc
                        info["user_email"] = uinfo.get("user_email")
                    except Exception:
                        pass
                # Enrich with team_alias if team_id present (for the UI).
                if info.get("team_id") and not info.get("team_alias"):
                    try:
                        tdoc = litellm_get("/team/info?team_id=" + urllib.parse.quote(info["team_id"]))
                        tinfo = tdoc.get("team_info") or tdoc
                        info["team_alias"] = tinfo.get("team_alias")
                        info["team_models"] = tinfo.get("models") or []
                    except Exception:
                        pass
                # Explain the special 'all-team-models' sentinel so the UI
                # doesn't treat it as a literal model name.
                km = info.get("key_models") or []
                if km == ["all-team-models"]:
                    info["key_models_meaning"] = "all-team-models"
                elif km:
                    info["key_models_meaning"] = "explicit-whitelist"
                else:
                    info["key_models_meaning"] = "inherited"
                return self._send(200, info)

            if u.path == "/models":
                qs = urllib.parse.parse_qs(u.query)
                try:
                    uid, ctx = resolve_target(qs)
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                except LookupError as e:
                    return self._send(404, {"error": str(e)})
                try:
                    models = resolve_models(uid)
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        return self._send(404, {"error": "user not found"})
                    raise
                body = {"models": models, "resolved_via": ctx["via"]}
                if ctx["via"] in ("vk", "vkh"):
                    body["user_id"] = ctx["user_id"]
                    body["key_alias"] = ctx["key_alias"]
                    body["user_email"] = ctx.get("user_email")
                    body["key_name"] = ctx.get("key_name")
                    if _VK_CONTEXT:
                        body["team_id"] = _VK_CONTEXT.get("team_id")
                        # resolution_mode is set by resolve_models() AFTER
                        # reading _VK_CONTEXT. 'team-only' means key_models ==
                        # ['all-team-models'] — the most common team key pattern.
                        body["resolution_mode"] = _VK_CONTEXT.get("resolution_mode", "user+team")
                    # Pass the raw key_models only when it's an explicit
                    # whitelist (NOT ['all-team-models']) so the UI can show
                    # the actual restriction.
                    km = ctx.get("key_models") or []
                    if km and km != ["all-team-models"]:
                        body["vk_key_models"] = km
                return self._send(200, body)

            if u.path == "/mcp":
                qs = urllib.parse.parse_qs(u.query)
                try:
                    uid, ctx = resolve_target(qs)
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                except LookupError as e:
                    return self._send(404, {"error": str(e)})
                try:
                    servers = resolve_user_mcp_servers(uid)
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        return self._send(404, {"error": "user not found"})
                    raise
                return self._send(200, {"servers": servers,
                                        "resolved_via": ctx["via"]})

            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})


if __name__ == "__main__":
    print(f"opencode-setup api listening on {LISTEN[0]}:{LISTEN[1]}", flush=True)
    ThreadingHTTPServer(LISTEN, Handler).serve_forever()