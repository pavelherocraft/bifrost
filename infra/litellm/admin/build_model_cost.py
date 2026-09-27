#!/usr/bin/env python3
"""Build LiteLLM model_cost dict from OpenRouter prices + manual overrides.

Outputs:
  1. Review table to stdout (model -> openrouter_id -> prices -> status).
  2. YAML snippet for config.yaml litellm_settings.model_cost.

Usage:
  python3 build_model_cost.py [--emit-yaml]   # default: only review
"""
import json
import re
import sys

OPENROUTER_PATH = "/opt/litellm/openrouter_models.json"

# --- Manual price overrides (not on OpenRouter, searched separately) ---
MANUAL = {
    "__DOUBao_PRO__":    {"prompt": 0.85,  "completion": 4.15},
    "__DOUBao_TURBO__":  {"prompt": 0.30,  "completion": 1.20},
}

# --- our_model -> openrouter_id (None = explicitly no price, leave $0) ---
OVERRIDES = {
    # === z-ai GLM family ===
    "GLM-4.7":                "z-ai/glm-4.7",
    "GLM-5.1":                "z-ai/glm-5.1",
    "GLM-5.1 (res)":          "z-ai/glm-5.1",
    "GLM-5.2":                "z-ai/glm-5.2",
    "GLM-5.2 (res)":          "z-ai/glm-5.2",
    "GLM-5.3":                "z-ai/glm-5.3",
    "GLM-5.3-Flash":          "z-ai/glm-5.3-flash",
    "glm-5.3-flash":          "z-ai/glm-5.3-flash",
    "glm-4.5-air":            "z-ai/glm-4.5-air",
    "zai-org/glm-5.1":        "z-ai/glm-5.1",
    "zai-org/glm-5.2":        "z-ai/glm-5.2",
    "openai/glm-4.6":         "z-ai/glm-4.6",
    "openai/glm-4.5-air":     "z-ai/glm-4.5-air",
    "atlas_glm-5.1":          "z-ai/glm-5.1",
    "atlas_glm-5.2":          "z-ai/glm-5.2",

    # === MiniMax (OpenRouter namespace: minimax) ===
    "MiniMax-M2.1-highspeed": "minimax/minimax-m2.1",
    "MiniMax-M2.5":           "minimax/minimax-m2.5",
    "MiniMax-M2.5-highspeed": "minimax/minimax-m2.5",
    "MiniMax-M2.7":           "minimax/minimax-m2.7",
    "MiniMax-M2.7 (res)":     "minimax/minimax-m2.7",
    "MiniMax-M2.7-highspeed": "minimax/minimax-m2.7",
    "MiniMax-M3":             "minimax/minimax-m3",
    "Minimax-M3":             "minimax/minimax-m3",

    # === Xiaomi MiMo (mimo) ===
    "mimo-v2.5":              "xiaomi/mimo-v2.5",
    "mimo-v2.5-pro":          "xiaomi/mimo-v2.5-pro",

    # === Qwen ===
    "QWEN3.7-plus":           "qwen/qwen3.7-plus",
    "Qwen3.5-plus":           "qwen/qwen3.5-plus-02-15",
    "qwen3.5-plus":           "qwen/qwen3.5-plus-02-15",
    "qwen3.6-flash":          "qwen/qwen3.6-flash",
    "qwen3.7-max":            "qwen/qwen3.7-max",
    "qwen3.7-plus":           "qwen/qwen3.7-plus",
    "qwen3.8-flash":          "qwen/qwen3.8-flash",
    "qwen3.8-max":            "qwen/qwen3.8-max",
    "qwen3.8-max-preview":    "qwen/qwen3.8-max",  # fuzzy: -preview not on OR
    "dashscope/qwen3.5-plus": "qwen/qwen3.5-plus-02-15",
    "dashscope/qwen3.7-plus": "qwen/qwen3.7-plus",
    "dashscope/qwen3.8-flash":"qwen/qwen3.8-flash",
    "openai/qwen3-max":       "qwen/qwen3-max",
    "openai/qwen3.6-flash":   "qwen/qwen3.6-flash",
    "openai/qwen3.7-max":     "qwen/qwen3.7-max",
    "openai/qwen3.8-flash":   "qwen/qwen3.8-flash",
    "openai/qwen3.8-max":     "qwen/qwen3.8-max",
    "openai/qwen3.8-max-preview": "qwen/qwen3.8-max",  # fuzzy
    "qwen/qwen3.8-flash":     "qwen/qwen3.8-flash",
    "qwen/qwen3.8-max":       "qwen/qwen3.8-max",
    "Kimi K2.6":              "moonshotai/kimi-k2.6",
    "Kimi K2.7":              "moonshotai/kimi-k2.7-code",
    "Kimi K3":                "moonshotai/kimi-k3",
    "kimi-k2.6":              "moonshotai/kimi-k2.6",
    "kimi-k2.7":              "moonshotai/kimi-k2.7-code",
    "kimi-k3":                "moonshotai/kimi-k3",
    "moonshot/kimi-k2.6":     "moonshotai/kimi-k2.6",
    "moonshot/kimi-k2.7":     "moonshotai/kimi-k2.7-code",
    "moonshot/kimi-k3":       "moonshotai/kimi-k3",
    "openai/kimi-k2-0905-preview": "moonshotai/kimi-k2-0905",

    # === DeepSeek ===
    "deepseek-ai/deepseek-v3.2":             "deepseek/deepseek-v3.2",
    "deepseek-ai/deepseek-v4-flash":         "deepseek/deepseek-v4-flash",
    "deepseek-ai/deepseek-v4-flash-0731":    "deepseek/deepseek-v4-flash-0731",
    "deepseek-ai/deepseek-v4-pro":           "deepseek/deepseek-v4-pro",
    "deepseek-ai/deepseek-v4-pro-0813":      "deepseek/deepseek-v4-pro-0813",
    "openai/deepseek-ai/deepseek-v4-pro":    "deepseek/deepseek-v4-pro",
    "openai/deepseek-v4-flash-0731":         "deepseek/deepseek-v4-flash-0731",
    "openai/deepseek-v4-pro-0813":           "deepseek/deepseek-v4-pro-0813",
    "AlibabaTokenPlan/deepseek-v4-flash-0731": "deepseek/deepseek-v4-flash-0731",

    # === Bytedance Doubao (manual, OpenRouter doesn't list) ===
    "bytedance/doubao-seed-2.1-pro-260628":    "__DOUBao_PRO__",
    "bytedance/doubao-seed-2.1-turbo-260628":  "__DOUBao_TURBO__",
    "openai/bytedance/doubao-seed-2.1-pro-260628": "__DOUBao_PRO__",

    # === OpenAI GPT ===
    "gpt-3.5-turbo":   "openai/gpt-3.5-turbo",
    "gpt-3.5-turbo-instruct": "openai/gpt-3.5-turbo-instruct",
    "gpt-4o":          "openai/gpt-4o",
    "gpt-4o-mini":     "openai/gpt-4o-mini",
    "gpt-5.2":         "openai/gpt-5.2",
    "gpt-5.5":         "openai/gpt-5.5",
    "gpt-5.6-sol":     "openai/gpt-5.6-sol",
    "gpt-5.6-luna":    "openai/gpt-5.6-luna",
    "gpt-image-1.5":   "openai/gpt-4o",                  # closest text-price proxy
    "gpt-image-2":     "openai/gpt-5-image",            # newer image model

    # === Anthropic Claude ===
    "claude-3-haiku-20240307": "anthropic/claude-3-haiku",
    "claude-haiku-4-5-20251001": "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-5": "anthropic/claude-sonnet-5",
    "claude-opus-5":  "anthropic/claude-opus-5",
    "claude-fable-5": "anthropic/claude-fable-5",
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4.6",

    # === Gemini image (no exact OpenRouter match) ===
    "gemini-2.5-flash-image":       None,
    "gemini-3-pro-image":           None,
    "gemini/gemini-3-pro-image":    None,
    "gemini/gemini-3.1-flash-image": None,

    # === Custom proxies (price = underlying model) ===
    "cx/gpt-5.6-sol":               "openai/gpt-5.6-sol",
    "sex/gpt-5.6-sol":              "openai/gpt-5.6-sol",
    "atlas/qwen3.8-max":            "qwen/qwen3.8-max",
    "weblate-judge-deepseek-v4-pro": "deepseek/deepseek-v4-pro",

    # === Explicit no-price ===
    "x":                             None,
}


def load_openrouter():
    with open(OPENROUTER_PATH, encoding="utf-8") as f:
        return {m["id"]: m for m in json.load(f)["data"]}


# OpenRouter pricing.prompt / .completion are USD **per token**.
# LiteLLM model_cost expects USD per token too. Values used as-is.
#


def main():
    or_models = load_openrouter()
    review_rows = []  # (our_model, or_id, prompt_usd/m, completion_usd/m, status)
    cost_dict = {}     # our_model -> {"input_cost_per_token": x, "output_cost_per_token": y}

    for our, or_id in OVERRIDES.items():
        if or_id is None:
            review_rows.append((our, "—", "—", "—", "explicit $0"))
            continue
        # Manual price? (per-token values entered as dollars-per-million for readability)
        if or_id.startswith("__") and or_id.endswith("__"):
            key = or_id
            if key in MANUAL:
                p_million, c_million = MANUAL[key]["prompt"], MANUAL[key]["completion"]
                cost_dict[our] = {
                    "input_cost_per_token": p_million / 1_000_000,
                    "output_cost_per_token": c_million / 1_000_000,
                }
                review_rows.append((our, f"manual({key})", p_million / 1_000_000, c_million / 1_000_000, "manual"))
            else:
                review_rows.append((our, or_id, "?", "?", "MISSING MANUAL"))
            continue
        # OpenRouter lookup (already per-token)
        if or_id not in or_models:
            review_rows.append((our, or_id, "—", "—", "OR-MISSING"))
            continue
        m = or_models[or_id]
        p_per_token = float(m["pricing"]["prompt"])
        c_per_token = float(m["pricing"]["completion"])
        cost_dict[our] = {
            "input_cost_per_token": p_per_token,
            "output_cost_per_token": c_per_token,
        }
        review_rows.append((our, or_id, p_per_token, c_per_token, "OK"))

    # Print review table (skip if --emit-yaml)
    if "--emit-yaml" not in sys.argv:
        print(f"\n{'OUR MODEL':<48s} {'OPENROUTER ID':<38s} {'PROMPT $/M':>11s} {'COMP $/M':>11s} STATUS")
        print("-" * 120)
        ok = miss = manual = explicit = or_missing = 0
        for our, oid, p, c, status in review_rows:
            if status == "OK":
                ps, cs = f"${p * 1_000_000:.4f}", f"${c * 1_000_000:.4f}"
                ok += 1
            elif status == "manual":
                ps, cs = f"${p * 1_000_000:.4f}", f"${c * 1_000_000:.4f}"
                manual += 1
            elif status == "explicit $0":
                ps, cs = "—", "—"
                explicit += 1
            elif status == "OR-MISSING":
                ps, cs = "—", "—"
                or_missing += 1
            else:
                ps, cs = "—", "—"
                miss += 1
            print(f"{our:<48s} {oid:<38s} {ps:>11s} {cs:>11s} {status}")
        print("-" * 120)
        print(f"OK={ok}  manual={manual}  explicit=$0={explicit}  OR-MISSING={or_missing}  BROKEN={miss}")
        print("\n(run with --emit-yaml to dump YAML snippet)")

    # Emit YAML snippet if asked
    if "--emit-yaml" in sys.argv:
        print()
        print("# ===== model_cost: merge into EXISTING litellm_settings section =====")
        print("# values are USD per token (LiteLLM format)")
        print("# NOTE: do NOT emit a 'litellm_settings:' wrapper line here —")
        print("# a duplicate top-level key silently wipes drop_params etc. (2026-08-30 incident)")
        print("  model_cost:")
        for our, cost in cost_dict.items():
            inp = cost["input_cost_per_token"]
            out = cost["output_cost_per_token"]
            def fmt(x):
                return f"{x:.10e}".rstrip("0").rstrip(".") or "0"
            print(f"    {our!r}:")
            print(f"      input_cost_per_token: {fmt(inp)}")
            print(f"      output_cost_per_token: {fmt(out)}")
    else:
        print("\n(run with --emit-yaml to dump YAML snippet)")


if __name__ == "__main__":
    main()
