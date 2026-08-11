import urllib.request, json, re, openpyxl, os

url = 'http://localhost:5174/api/results/latest?target=qa'
with urllib.request.urlopen(url) as resp:
    payload = json.loads(resp.read().decode('utf-8'))

items = payload.get('items') or []

def result_channel(op, pe=''):
    op = str(op or '').strip()
    if re.search(r'\(app\)$', op, re.I): return 'app'
    if re.search(r'\(web\)$', op, re.I): return 'web'
    pe = str(pe or '').strip().lower()
    if pe == 'app': return 'app'
    if pe == 'web': return 'web'
    if pe == 'plugin': return 'cron'
    if pe and pe not in ('web', 'app'): return 'cron'
    base = op.split('(')[0]
    if re.match(r'^[a-z0-9]+(-[a-z0-9]+)+$', base, re.I): return 'cron'
    return 'web'

app_items = []
for item in items:
    op = item.get('operation') or ''
    pe = item.get('platformEnvironment') or ''
    if result_channel(op, pe) == 'app':
        app_items.append(item)

app_items.sort(key=lambda x: str(x.get('operation')).lower())

art_path = '/Users/sachinkoirala/.gemini/antigravity-ide/brain/f9b00beb-b699-4d2c-b1fb-ae345610419a/app_covered_143_events_184_scenarios.md'

lines = []
lines.append('# App Covered Events & Scenarios (143 Events / 184 Scenarios)\n')
lines.append('This document lists all **143 unique events** and **184 scenarios** currently covered for the **App** platform in QA.\n')
lines.append('| # | Base Event Name | Full Operation Key | Scenario | Pass | Fail | Skip | Total Fields |')
lines.append('| :---: | :--- | :--- | :--- | :---: | :---: | :---: | :---: |')

row_num = 1
for item in app_items:
    full_op = item.get('operation') or ''
    base_evt = full_op.split('(')[0].strip()
    parts = re.findall(r'\(([^)]+)\)', full_op)
    if not parts:
        scen_label = 'default'
    elif len(parts) == 1:
        scen_label = parts[0] if parts[0].lower() != 'app' else 'default · APP'
    else:
        scen_label = ' · '.join(p for p in parts if p.lower() != 'app') + (' · APP' if '(app)' in full_op.lower() else '')

    summ = item.get('summary') or {}
    p_cnt = int(summ.get('passed', 0))
    f_cnt = int(summ.get('failed', 0))
    s_cnt = int(summ.get('skipped', 0))
    tot = p_cnt + f_cnt + s_cnt
    
    lines.append(f'| {row_num} | `{base_evt}` | `{full_op}` | `{scen_label}` | **{p_cnt}** | **{f_cnt}** | **{s_cnt}** | {tot} |')
    row_num += 1

with open(art_path, 'w') as f:
    f.write('\n'.join(lines))

print(f'Written artifact to {art_path}')
