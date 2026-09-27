#!/usr/bin/env python3
"""Atlas Cloud consumption for a day: tokens -> points (official multipliers) -> USD (280M=$99)."""
import subprocess, sys
DAY = sys.argv[1] if len(sys.argv) > 1 else '2026-09-16'
PPU = 99.0 / 280_000_000

# upstream-имя в SpendLogs -> (public, in_mult, out_mult, write_mult, read_mult)
# multipliers: atlascloud.ai/coding-plan "Pay-per-Use Packs"
MULT = {
 'qwen/qwen3.8-max':                          ('atlas/qwen3.8-max',            3.96, 11.88, 4.95, 0.500),
 'deepseek-ai/deepseek-v4-pro-0813':          ('atlas/deepseek-v4-pro-0813',   2.18,  6.53, 0.0,  0.220),
 'deepseek-ai/deepseek-v4-flash-0731':        ('atlas/deepseek-v4-flash-0731', 0.73,  2.18, 0.0,  0.070),
 'openai/deepseek-ai/deepseek-v4-pro':        ('deepseek-v4-pro',              2.87,  5.75, 0.0,  0.231),
 'deepseek-ai/deepseek-v4-flash':             ('deepseek-ai/deepseek-v4-flash',0.73,  2.18, 0.0,  0.070),
 'deepseek-ai/deepseek-v3.2':                 ('deepseek-ai/deepseek-v3.2',    0.42,  0.62, 0.0,  0.193),
 'bytedance/doubao-seed-2.1-turbo-260628':    ('doubao-seed-2.1-turbo',        1.19,  5.94, 0.0,  0.238),
 'openai/bytedance/doubao-seed-2.1-pro-260628':('seed-2.1',                     2.38, 11.88, 0.0,  0.475),
 'zai-org/glm-5.2':                           ('atlas_glm-5.2',                3.70, 11.62, 0.0,  0.686),
 'zai-org/glm-5.1':                           ('atlas_glm-5.1',                2.54,  7.99, 0.0,  0.472),
}
models_sql = ','.join(f"'{m}'" for m in MULT)

def q(sql):
    r = subprocess.run(['docker','exec','-i','litellm-pg','psql','-U','litellm','-d','litellm','-At','-F','|','-c',sql],
                       capture_output=True, text=True, input='')
    return [l.split('|') for l in r.stdout.strip().split('\n') if l]

spend = q(f"""SELECT model, count(*), sum(prompt_tokens), sum(completion_tokens), round(sum(spend)::numeric,4)
FROM \"LiteLLM_SpendLogs\" WHERE \"startTime\"::date='{DAY}' AND model IN ({models_sql}) GROUP BY model;""")
cache = dict()
for m, cr, cw in q(f"""SELECT model, sum(cache_read_input_tokens), sum(cache_creation_input_tokens)
FROM \"LiteLLM_DailyUserSpend\" WHERE date='{DAY}' AND model IN ({models_sql}) GROUP BY model;"""):
    cache[m] = (int(float(cr or 0)), int(float(cw or 0)))

TP, TD, TL = 0.0, 0.0, 0.0
print(f'=== ATLAS: всё потребление за {DAY} (все ключи/команды) ===')
print(f'{"модель":28s} {"вызовы":>6s} {"fresh_in":>10s} {"c_read":>10s} {"c_write":>8s} {"output":>9s} | {"поинты":>14s} | {"доллары":>8s} | {"литл.лог":>8s}')
rows = sorted(spend, key=lambda r: -float(r[4] or 0))
for m, cnt, p, c, sp in rows:
    pub, mi, mo, mw, mr = MULT[m]
    p, c = int(p), int(c)
    cr, cw = cache.get(m, (0, 0))
    fresh = p - cr
    pts = fresh*mi + c*mo + cr*mr + cw*mw
    TP += pts; TD += pts*PPU; TL += float(sp or 0)
    print(f'{pub:28s} {cnt:>6s} {fresh:>10,} {cr:>10,} {cw:>8,} {c:>9,} | {pts:>14,.0f} | {pts*PPU:>8.2f} | {float(sp or 0):>8.2f}')
print('-'*118)
print(f'{"ИТОГО":28s} {"":>6s} {"":>10s} {"":>10s} {"":>8s} {"":>9s} | {TP:>14,.0f} | {TD:>8.2f} | {TL:>8.2f}')
print(f'\nПоинты: {TP:,.0f}   Доллары: ${TD:.2f}   (LiteLLM заллогжено по OR-тарифам: ${TL:.2f}, '
      f'поинты дешевле на {(1-TD/TL)*100:.0f}%)')
