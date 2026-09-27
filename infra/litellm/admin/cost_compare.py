#!/usr/bin/env python3
"""Full cost breakdown: Atlas (points, 280M=$99) vs Alibaba direct.
Usage: python3 cost_compare.py [key_alias] [spendlogs_model] [YYYY-MM-DD]"""
import subprocess, sys

KEY_ALIAS = sys.argv[1] if len(sys.argv) > 1 else 'localizer-webplate'
MODEL    = sys.argv[2] if len(sys.argv) > 2 else 'qwen/qwen3.8-max'
DAY      = sys.argv[3] if len(sys.argv) > 3 else '2026-09-16'

# --- тарифы ---
PTS = {'in': 3.96, 'out': 11.88, 'read': 0.5, 'write': 4.95}     # поинтов за токен
ALI = {'in': 2.00, 'read': 0.25, 'out': 2.50, 'out_cache': 0.17}  # $ за 1M токенов
PACK_POINTS, PACK_USD = 280_000_000, 99.0
PPU = PACK_USD / PACK_POINTS

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
fresh = p_tok - cr_tok

print(f'=== {MODEL} | key={KEY_ALIAS} | {DAY} ===')
print(f'Шаг 0. Токены: промпт={p_tok:,} (кэш-хиты={cr_tok:,} -> свежих={fresh:,}), output={c_tok:,}, cache write={cw_tok:,}')
print()
print(f'=== ATLAS: поинты -> доллары (280M pts = $99, 1 поинт = ${PPU:.9f}) ===')
tp = 0
for name, n, m in (('input(fresh)', fresh, PTS['in']), ('output', c_tok, PTS['out']),
                   ('cache read', cr_tok, PTS['read']), ('cache write', cw_tok, PTS['write'])):
    pts = n * m; tp += pts
    usd = pts * PPU
    print(f'  {name:12s} {n:>10,} tok x {m:>5.2f} pts/tok = {pts:>14,.2f} pts = ${usd:>7.4f}')
print(f'  {"ИТОГО":12s} {"":>10s}    {"":>5s}    {tp:>14,.2f} pts = ${tp*PPU:>7.2f}')
print(f'  формула: {tp:,.2f} / 280,000,000 x 99 = ${tp*PPU:.4f}')
print()
print(f'=== ALIBABA DIRECT: list-цены за 1M токенов ===')
ta = 0
for name, n, pr in (('input(fresh)', fresh, ALI['in']), ('cache read', cr_tok, ALI['read']),
                    ('output', c_tok, ALI['out']), ('output cache', 0, ALI['out_cache'])):
    usd = n / 1e6 * pr; ta += usd
    print(f'  {name:12s} {n:>10,} tok x ${pr:.2f}/1M = ${usd:>7.4f}')
print(f'  {"ИТОГО":12s} {"":>10s}                = ${ta:>7.2f}')
print()
print(f'=== СРАВНЕНИЕ: Atlas ${tp*PPU:.2f} | Alibaba ${ta:.2f} | (Atlas-выгода ${(ta-tp*PPU):.2f}, '
      f'Alibaba дороже на {((ta/(tp*PPU))-1)*100:.1f}%)')
