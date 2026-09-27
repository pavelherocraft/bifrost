#!/usr/bin/env python3
"""OR reference prices for ALL model_cost entries (v2: no skips, custom deployments included)."""
import json, yaml, urllib.request, time, sys, collections, shutil

OR_MODELS = {m['id']: m for m in json.load(open('/opt/litellm/admin/or_models.json'))['data']}
CFG_PATH = '/opt/litellm/config.yaml'
DRY = '--apply' not in sys.argv

ALIAS = {
    'tencent/DeepSeek-V4.1-Flash': 'deepseek/deepseek-v4.1-flash',
    'deepseek/deepseek-flash': 'deepseek/deepseek-v4.1-flash',
    'qwen/qwen3.8-max': 'qwen/qwen3.8-max-0902',
    'qwen3.8-max': 'qwen/qwen3.8-max-0902',
    'openai/qwen3.8-max': 'qwen/qwen3.8-max-0902',
    'atlas/qwen3.8-max': 'qwen/qwen3.8-max-0902',
    'qwen3.8-max-preview': 'qwen/qwen3.8-max-0902',
    'tencent/glm5-3flash (reserved - use when main is exhausted)': 'z-ai/glm-5.3-flash',
    'atlas_glm-5.1': 'z-ai/glm-5.1',
    'atlas_glm-5.2': 'z-ai/glm-5.2',
    'glm-5-2': 'z-ai/glm-5.2',
    'glm-5-3': 'z-ai/glm-5.3',
    'weblate-judge-deepseek-v4-pro': 'deepseek/deepseek-v4-pro',
    'cx/gpt-5.6-sol': 'openai/gpt-5.6-sol',
    'sex/gpt-5.6-sol': 'openai/gpt-5.6-sol',
    'k3-256k': 'moonshotai/kimi-k3-256k',
    'Kimi K3-256K': 'moonshotai/kimi-k3-256k',
    'custom_openai/k3-256k': 'moonshotai/kimi-k3-256k',
    'Kimi K2.8': 'moonshotai/kimi-k2.8',
}

def find_slug(key):
    if key in ALIAS:
        return ALIAS[key] if ALIAS[key] in OR_MODELS else None
    k = key
    for pref in ('openai/', 'AlibabaTokenPlan/', 'dashscope/', 'custom_openai/', 'tencent/', 'atlas/'):
        if k.startswith(pref):
            k = k[len(pref):]
    for org_from, org_to in (('deepseek-ai/', 'deepseek/'), ('zai-org/', 'z-ai/'), ('moonshot/', 'moonshotai/')):
        if k.startswith(org_from):
            k = org_to + k[len(org_from):]
    if k in OR_MODELS:
        return k
    if '/' not in k:
        tail = k.lower().replace(' (res)', '').replace(' ', '-')
        for cand in (tail, tail.replace('-highspeed', '')):
            hits = [s for s in OR_MODELS if s.split('/')[-1].lower() == cand]
            if len(hits) == 1:
                return hits[0]
    return None

def get_endpoints(slug):
    url = f'https://openrouter.ai/api/v1/models/{slug}/endpoints'
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)['data']['endpoints']
    except Exception as e:
        print(f'  !! endpoints {slug}: {e}')
        return []

def full_price(slug):
    eps = get_endpoints(slug)
    ins, outs, reads, maxdisc = [], [], [], 0.0
    for e in eps:
        pr = e.get('pricing') or {}
        d = float(pr.get('discount') or 0)
        if d > maxdisc:
            maxdisc = d
        def adj(fld):
            return float(pr[fld]) / (1 - d) if d else float(pr[fld])
        if pr.get('prompt'):
            ins.append(adj('prompt'))
        if pr.get('completion'):
            outs.append(adj('completion'))
        if pr.get('input_cache_read'):
            reads.append(adj('input_cache_read'))
    def pick(vals):
        if not vals:
            return None
        c = collections.Counter(round(v, 8) for v in vals)
        best, n = c.most_common(1)[0]
        return best if n >= 2 else sorted(vals)[len(vals)//2]
    return pick(ins), pick(outs), pick(reads), maxdisc, len(eps)

cfg = yaml.safe_load(open(CFG_PATH))
mc = cfg['litellm_settings']['model_cost']
mapped, unmatched = {}, []
for key in mc:
    slug = find_slug(key)
    if slug:
        mapped.setdefault(slug, []).append(key)
    else:
        unmatched.append(key)

print(f'slugs: {len(mapped)}, keys: {sum(len(v) for v in mapped.values())}, unmatched: {len(unmatched)}')
if unmatched:
    print('unmatched:', ', '.join(unmatched))
updates = []
for slug in sorted(mapped):
    fi, fo, fr, md, ne = full_price(slug)
    time.sleep(0.25)
    if fi is None or fo is None:
        print(f'{slug}: no token prices, skip')
        continue
    for key in mapped[slug]:
        old = mc[key]
        oi = old.get('input_cost_per_token')
        oo = old.get('output_cost_per_token')
        orr = old.get('cache_read_input_token_cost')
        if oi is None or oo is None:
            continue
        chg = abs(oi - fi) > 1e-12 or abs(oo - fo) > 1e-12 or (fr is not None and orr is not None and abs(orr - fr) > 1e-12)
        updates.append((key, slug, oi, oo, orr, fi, fo, fr, md, chg))

changed = [u for u in updates if u[-1]]
print(f'\nentries total: {len(updates)}, will change: {len(changed)}\n')
print(f'{"key":46s} {"or-slug":34s} {"old in/out":22s} {"NEW full in/out":22s} read    disc')
for key, slug, oi, oo, orr, fi, fo, fr, md, chg in sorted(changed, key=lambda u: u[0]):
    print(f'{key:46s} {slug:34s} {oi*1e6:<11.4g} {oo*1e6:<11.4g} {fi*1e6:<11.4g} {fo*1e6:<11.4g} {(fr or 0)*1e6:<7.4g} {md*100:.0f}%')

if not DRY:
    shutil.copy(CFG_PATH, CFG_PATH + '.bak.orfull2-20260917')
    n = 0
    for key, slug, oi, oo, orr, fi, fo, fr, md, chg in updates:
        if not chg:
            continue
        mc[key]['input_cost_per_token'] = float('%.6g' % fi)
        mc[key]['output_cost_per_token'] = float('%.6g' % fo)
        if fr is not None:
            mc[key]['cache_read_input_token_cost'] = float('%.6g' % fr)
        n += 1
    with open(CFG_PATH, 'w') as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True, width=120)
    print(f'\nupdated: {n}, backup: config.yaml.bak.orfull2-20260917')
else:
    print('\n(dry-run, use --apply to write)')
