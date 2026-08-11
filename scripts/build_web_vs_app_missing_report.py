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

web_items = [i for i in items if result_channel(i.get('operation'), i.get('platformEnvironment')) == 'web']
app_items = [i for i in items if result_channel(i.get('operation'), i.get('platformEnvironment')) == 'app']

def base_op(op):
    return str(op).split('(')[0].strip()

web_scenarios_by_base = {}
for i in web_items:
    web_scenarios_by_base.setdefault(base_op(i.get('operation')), []).append(i)

app_scenarios_by_base = {}
for i in app_items:
    app_scenarios_by_base.setdefault(base_op(i.get('operation')), []).append(i)

web_base_set = set(web_scenarios_by_base.keys())
app_base_set = set(app_scenarios_by_base.keys())

# Helper to normalize scenario labels
def clean_scen(op):
    m = re.findall(r'\(([^)]+)\)', op)
    c = [p for p in m if p.lower() not in ('web', 'app')]
    return ' · '.join(c) if c else 'default'

# 1. Base events missing entirely in App
missing_events = []
for b in sorted(list(web_base_set - app_base_set), key=lambda x: x.lower()):
    w_items = web_scenarios_by_base[b]
    for wi in w_items:
        full_op = wi.get('operation')
        scen = clean_scen(full_op)
        summ = wi.get('summary') or {}
        missing_events.append({
            'base_event': b,
            'full_operation': full_op,
            'scenario': scen,
            'pass': int(summ.get('passed', 0)),
            'fail': int(summ.get('failed', 0)),
            'skip': int(summ.get('skipped', 0)),
            'status': 'COMPLETELY_MISSING_EVENT'
        })

# 2. Scenarios missing in App for shared events
missing_scenarios = []
for b in sorted(list(web_base_set & app_base_set), key=lambda x: x.lower()):
    w_items = web_scenarios_by_base[b]
    a_items = app_scenarios_by_base[b]
    
    w_map = {clean_scen(x.get('operation')): x for x in w_items}
    a_map = {clean_scen(x.get('operation')): x for x in a_items}
    
    for label, wi in w_map.items():
        if label not in a_map:
            full_op = wi.get('operation')
            summ = wi.get('summary') or {}
            missing_scenarios.append({
                'base_event': b,
                'full_operation': full_op,
                'scenario': label,
                'app_covered_scenarios': ', '.join(sorted(a_map.keys())),
                'pass': int(summ.get('passed', 0)),
                'fail': int(summ.get('failed', 0)),
                'skip': int(summ.get('skipped', 0)),
                'status': 'MISSING_SCENARIO'
            })

print(f'Completely Missing Events Count: {len(set(x["base_event"] for x in missing_events))} (total {len(missing_events)} scenarios)')
print(f'Missing Scenarios in Shared Events Count: {len(missing_scenarios)}')
print(f'Total Missing Scenarios (Web vs App): {len(missing_events) + len(missing_scenarios)}')

# Build Excel workbook
wb = openpyxl.Workbook()

# Sheet 1: Missing Events Entirely
ws1 = wb.active
ws1.title = "Events Missing in App"
ws1.append(["#", "Base Event Name", "Web Operation Key", "Scenario Label", "Web Pass", "Web Fail", "Web Skip"])
for idx, r in enumerate(missing_events, 1):
    ws1.append([idx, r['base_event'], r['full_operation'], r['scenario'], r['pass'], r['fail'], r['skip']])

# Sheet 2: Missing Scenarios in Shared Events
ws2 = wb.create_sheet(title="Missing Scenarios in App")
ws2.append(["#", "Base Event Name", "Web Operation Key", "Missing Scenario", "App Covered Scenarios", "Web Pass", "Web Fail", "Web Skip"])
for idx, r in enumerate(missing_scenarios, 1):
    ws2.append([idx, r['base_event'], r['full_operation'], r['scenario'], r['app_covered_scenarios'], r['pass'], r['fail'], r['skip']])

excel_path = 'reports/web_vs_app_missing_coverage_report.xlsx'
wb.save(excel_path)
print(f'Saved Excel to {excel_path}')

# Build Markdown Artifact
art_path = '/Users/sachinkoirala/.gemini/antigravity-ide/brain/f9b00beb-b699-4d2c-b1fb-ae345610419a/web_vs_app_missing_coverage_report.md'
lines = []
lines.append('# Web vs App Audit Coverage Delta Report\n')
lines.append('This report details all **Events** and **Scenarios** that are covered in **Web** (`Platform: Web (209)`), but are **missing in App** (`Platform: App (186)`).\n')

lines.append('## 1. Base Events Covered in Web but COMPLETELY MISSING in App\n')
lines.append(f'There are **{len(set(x["base_event"] for x in missing_events))} unique events** ({len(missing_events)} scenarios) covered in Web that do not exist in App:\n')
lines.append('| # | Base Event Name | Web Operation Key | Scenario | Web Pass | Web Fail | Web Skip |')
lines.append('| :---: | :--- | :--- | :--- | :---: | :---: | :---: |')

for idx, r in enumerate(missing_events, 1):
    lines.append(f'| {idx} | `{r["base_event"]}` | `{r["full_operation"]}` | `{r["scenario"]}` | **{r["pass"]}** | **{r["fail"]}** | **{r["skip"]}** |')

lines.append('\n## 2. Scenarios Covered in Web but MISSING in App (for Shared Events)\n')
lines.append(f'There are **{len(missing_scenarios)} additional scenarios** where the event is covered in App, but specific Web scenario variants are missing:\n')
lines.append('| # | Base Event Name | Web Operation Key | Missing Scenario | App Currently Covers | Web Pass | Web Fail | Web Skip |')
lines.append('| :---: | :--- | :--- | :--- | :--- | :---: | :---: | :---: |')

for idx, r in enumerate(missing_scenarios, 1):
    lines.append(f'| {idx} | `{r["base_event"]}` | `{r["full_operation"]}` | `{r["scenario"]}` | `{r["app_covered_scenarios"]}` | **{r["pass"]}** | **{r["fail"]}** | **{r["skip"]}** |')

with open(art_path, 'w') as f:
    f.write('\n'.join(lines))

print(f'Saved Markdown Artifact to {art_path}')
