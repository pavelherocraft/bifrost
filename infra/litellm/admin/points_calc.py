#!/usr/bin/env python3
"""Points-cost calculator: points = tokens x multiplier; 280M points = $99."""
import subprocess, yaml

KEY_ALIAS, MODEL, DAY = 'localizer-webplate', 'qwen/qwen3.8-max', '2026-09-16'
MULT = {'in': 3.96, 'out': 11.88, 'read': 0.5, 'write': 4.95}
PPU = 99.0 / 280_000_000  # $ per point
sql = f"""SELECT sum(s.prompt_tokens), sum(s.completion_tokens),
(SELECT sum(d.cache_read_input_tokens) FROM "LiteLLM_DailyUserSpend" d
 WHERE d.api_key=(SELECT token FROM "LiteLLM_VerificationToken" WHERE key_alias='{KEY_ALIAS}')
   AND d.model='{MODEL}' AND d.date='{DAY}'),
(SELECT sum(d.cache_creation_input_tokens) FROM "LiteLLM_DailyUserSpend" d
 WHERE d.api_key=(SELECT token FROM "LiteLLM_VerificationToken" WHERE key_alias='{KEY_ALIAS}')
   AND d.model='{MODEL}' AND d.date='{DAY}')
FROM "LiteLLM_SpendLogs" s WHERE s.api_key=(SELECT token FROM "LiteLLM_VerificationToken" WHERE key_alias='{KEY_ALIAS}')
AND s.model='{MODEL}' AND s."startTime"::date='{DAY}';"""
out = subprocess.run(['docker','exec','-i','litellm-pg','psql','-U','litellm','-d','litellm','-At','-F','|','-c',sql],
                     capture_output=True, text=True, input='')
p_tok, c_tok, cr_tok, cw_tok = [int(float(x or 0)) for x in out.stdout.strip().split('|')]
fresh_in = p_tok - cr_tok
rows = [('input (fresh)', fresh_in, MULT['in']),
        ('output', c_tok, MULT['out']),
        ('cache read', cr_tok, MULT['read']),
        ('cache write', cw_tok, MULT['write'])]
total = 0
print(f'{MODEL} | key={KEY_ALIAS} | {DAY}')
print(f'{"category":14s} {"tokens":>10s} {"mult(pts/tok)":>13s} {"points":>14s}')
for name, n, m in rows:
    pts = n * m
    total += pts
    print(f'{name:14s} {n:>10,d} {m:>13.3f} {pts:>14,.2f}')
print(f'{"TOTAL":14s} {"":>10s} {"":>13s} {total:>14,.2f} points')
print(f'USD = {total:,.2f} x 99/280M = ${total*PPU:,.2f}')
