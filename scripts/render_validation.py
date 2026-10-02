"""Render the published real-history evidence from recorded validator output."""
import argparse
from collections import Counter
from html import escape
import json
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]


def link(url, label):
    return f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'


def note_text(note):
    return note.get("reason", str(note)) if isinstance(note, dict) else str(note)


def notes_list(value):
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def findings_html(case):
    output = []
    reasons = {(e["path"], e["line"], e["rule_id"]): e["reason"] for e in case["expected"]}
    for f in case["findings"]:
        sha = case["head"] if f["side"] == "right" else case["base"]
        url = f'https://github.com/{case["repository"]}/blob/{sha}/{quote(f["path"], safe="/")}#L{f["line"]}'
        location = link(url, f'{f["path"]}:{f["line"]}')
        reason = reasons.get((f["path"], f["line"], f["rule_id"]), f["explanation"])
        output.append(f'<div class="signal"><div class="rule">{escape(f["rule_id"])}</div><p>{location}</p>'
                      f'<div class="diff"><div><label>BEFORE</label><pre>{escape(f["before"] or "∅")}</pre></div>'
                      f'<div><label>AFTER</label><pre>{escape(f["after"] or "∅")}</pre></div></div>'
                      f'<p class="muted">{escape(reason)}</p></div>')
    return "".join(output)


def case_html(case):
    title = f'{case["repository"]} · {case["head"][:10]}'
    notes = [note_text(n) for n in notes_list(case.get("notes", []))]
    unsupported = [note_text(n) for n in notes_list(case.get("unsupported", []))]
    detail = findings_html(case)
    if not case["findings"]:
        detail += '<p>No supported signal emitted. This does not establish that every check remained intact.</p>'
    detail += "".join(f'<p class="muted">{escape(n)}</p>' for n in notes)
    if unsupported:
        detail += '<div class="limit"><strong>Outside rule scope</strong>' + "".join(f'<p>{escape(n)}</p>' for n in unsupported) + '</div>'
    if not case["passed"]:
        detail += f'<pre>{escape(json.dumps({k: case.get(k) for k in ("missing", "unexpected", "invalid_addresses", "error")}, indent=2))}</pre>'
    scope = 'Selected configuration only: ' + ', '.join(case['paths']) if case.get('paths') else 'All changed tracked text files in this commit comparison.'
    status = 'Matched labels' if case['passed'] else 'Mismatch / error'
    return f'<details class="case"><summary><span>{escape(title)}</span><span class="status">{status} · {len(case["findings"])} signals</span></summary>' \
           f'<p>{link(case["url"], "Inspect upstream commit")} · {escape(scope)}</p><p class="hash">Base {case["base"]}<br>Head {case["head"]}</p>{detail}</details>'


def render(report, baseline):
    cases = report['results']
    frozen = [c for c in cases if c['selection'] == 'frozen-corpus']
    curated = [c for c in cases if c['selection'] != 'frozen-corpus']
    repos = len({c['repository'] for c in cases})
    groups = Counter(f['rule_id'] for c in cases for f in c['findings'])
    commit = report.get('tool_commit')
    revision = link(f'https://github.com/thajj/gate-drift/commit/{commit}', commit[:10]) if commit else 'unrecorded'
    if report.get('working_tree_dirty'):
        revision += ' + uncommitted changes'
    examples = [next(c for c in cases if c['repository'] == r and c['selection'] != 'frozen-corpus')
                for r in ('microsoft/playwright', 'eslint/eslint', 'groupthinking/EventRelay', 'Naykel98/ebg-protrack')]
    examples_html = ''.join(f'<article class="card"><p class="eyebrow">{escape(c["repository"])} · {"CONFIGURATION ONLY" if c.get("paths") else "WHOLE COMMIT"}</p>{findings_html(c)}<p>{link(c["url"], "Verify the upstream change →")}</p></article>' for c in examples)
    missed = ''.join(f'<li>{link(c["url"], c["repository"] + " · " + c["head"][:10])}: {escape(", ".join(m[1] + ":" + str(m[2]) for m in c["missing"]))}</li>' for c in baseline['results'] if c['missing'])
    unsupported_count = sum(bool(c.get('unsupported')) for c in cases)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Gate Drift · real repository evidence</title><style>
*{{box-sizing:border-box}}:root{{color-scheme:dark}}body{{margin:0;background:#101516;color:#edf2ed;font:16px/1.6 system-ui,-apple-system,sans-serif}}main{{max-width:1080px;margin:auto;padding:32px 28px 70px}}a{{color:#bbf971;text-underline-offset:4px}}header{{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;padding:12px 0 25px;border-bottom:1px solid #31413a}}.brand{{font-size:20px;font-weight:750}}.brand span{{color:#bbf971}}nav{{display:flex;gap:20px;font-size:13px}}.eyebrow{{font-size:11px;letter-spacing:1.6px;color:#a7bbae}}.hero{{padding-top:42px}}h1{{font-size:clamp(36px,6vw,62px);line-height:1.04;letter-spacing:-2.4px;max-width:780px;margin:12px 0 22px}}h2{{font-size:26px;letter-spacing:-.6px;line-height:1.2;margin-top:46px}}h3{{font-size:18px}}.lede{{font-size:18px;max-width:780px;color:#b6c5bd}}.stats{{display:flex;gap:48px;flex-wrap:wrap;border-top:1px solid #31413a;border-bottom:1px solid #31413a;padding:22px 0;margin:28px 0}}.stats strong{{display:block;color:#bbf971;font-size:32px;line-height:1.2}}.stats span{{font-size:12px;color:#b6c5bd}}.callout,.limit{{border-left:3px solid #bbf971;background:#18201f;padding:14px 20px;margin:22px 0}}.limit{{border-color:#e1b27e;font-size:14px}}.callout p,.limit p{{margin:7px 0}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}.card{{border:1px solid #31413a;background:#18201f;border-radius:12px;padding:20px}}.rule{{font:12px ui-monospace,monospace;color:#d7e8c6}}.signal p{{font-size:14px;overflow-wrap:anywhere}}.diff{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}.diff>div{{background:#101817;border:1px solid #31413a;border-radius:7px;overflow:hidden}}label{{display:block;padding:8px 12px;border-bottom:1px solid #31413a;font:10px ui-monospace,monospace;letter-spacing:1px;color:#9cb3a5}}pre{{margin:0;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 ui-monospace,monospace}}.diff>div:last-child pre{{color:#ffd2a7}}.muted{{color:#b6c5bd}}.case{{border:1px solid #31413a;border-radius:8px;margin:10px 0;padding:14px 18px}}summary{{cursor:pointer;display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;font-size:14px}}.status{{color:#b5db92;font-size:12px}}.hash{{font:11px/1.8 ui-monospace,monospace;color:#9cb3a5;overflow-wrap:anywhere}}footer{{border-top:1px solid #31413a;margin-top:40px;padding-top:20px;font-size:12px;color:#a7bbae}}code{{font-family:ui-monospace,monospace}}@media(max-width:700px){{main{{padding:22px 16px}}.grid,.diff{{grid-template-columns:1fr}}.stats{{gap:25px}}h1{{letter-spacing:-1.4px}}}}
</style></head><body><main><header><div class="brand"><span>◈</span> gate drift</div><nav>{link('https://github.com/thajj/gate-drift', 'Source & reproduction')} · {link('./', 'Synthetic demo')} · {link('validation.json', 'Raw results')}</nav></header>
<section class="hero"><div class="eyebrow">PINNED PUBLIC GIT HISTORY / {escape(report['dataset_date'])}</div><h1>Real commits.<br>Inspectable evidence.</h1><p class="lede">Gate Drift found changes to test execution, workflow failure handling, coverage thresholds, and TypeScript checking in actual repository history. Each example links to its source.</p>
<div class="stats"><div><strong>{report['total']}</strong><span>commit comparisons</span></div><div><strong>{repos}</strong><span>public repositories</span></div><div><strong>{report['emitted_sites']}</strong><span>source-addressed signals</span></div><div><strong>{report['errors']}</strong><span>inspection errors</span></div></div>
<div class="callout"><p><strong>{report['passed']}/{report['total']} comparisons matched the project’s labels exactly.</strong> Rule, path, side, and line were checked; emitted snippets were checked against Git source.</p><p>This is regression evidence. The rules were improved using these cases, so this is not a held-out accuracy estimate. The upstream changes below include intentional maintenance decisions.</p></div>
<p class="muted">Tool v{escape(report['tool_version'])} · source revision {revision}. No upstream tests or repository code were executed. Fetching the history is a separate network step; analysis runs offline.</p></section>
<h2>Four real changes, four rule families</h2><div class="grid">{examples_html}</div>
<h2>The first version missed real cases</h2><p>The baseline revision {link('https://github.com/thajj/gate-drift/commit/'+baseline['source_commit'], baseline['source_commit'][:10])} emitted {baseline['emitted_sites']} of the {report['expected_sites']} currently labeled sites. It missed three executable Vitest conditional skips:</p><ul>{missed}</ul><p>Named Vitest import aliases, <code>skipIf/runIf</code>, and limited arrow-callback context skips were added. Regression tests also guard against fixture strings and unrelated business methods. These same cases were then rechecked; they were not reserved as an unseen test set.</p>
<h2>How the sample was chosen</h2><p>The frozen corpus uses the latest 20 first-parent transitions at each of two pinned pytest and Vitest heads: <strong>{len(frozen)} comparisons</strong> chosen before running the detector. A separate AI review pass labeled the source diffs without seeing detector output. These are project-authored labels, not external validation by the upstream maintainers.</p><p>The additional <strong>{len(curated)} selected comparisons</strong> exercise specific patterns and negative controls. Eight inspect whole commit changes. EventRelay and ebg-protrack inspect only the named configuration file, so they do not claim complete-commit coverage. Four rule families are exercised: {escape(', '.join(f'{k} ({v})' for k,v in sorted(groups.items())))}. Other rules are covered by unit tests, not this history sample.</p><p><strong>{unsupported_count} comparisons contain separately recorded out-of-scope changes.</strong> Examples include Python coverage exclusion comments, warning filters, commented-out tox commands, and individual assertion changes. Quiet output means no supported pattern matched; it does not mean every quality gate was preserved.</p>
<h2>All frozen corpus comparisons</h2><p class="muted">Open a comparison to inspect scope, source links, findings, and limits.</p>{''.join(case_html(c) for c in frozen)}
<h2>All selected historical comparisons</h2>{''.join(case_html(c) for c in curated)}
<h2>Reproduce it</h2><pre>git clone https://github.com/thajj/gate-drift.git
cd gate-drift
python3 scripts/fetch_validation_history.py
python3 scripts/validate_history.py --repos-root .validation-repos --output validation-local.json</pre><p>Python 3.10+ and Git 2.29+ are sufficient. The first script downloads pinned public Git objects without checking out files. The second uses the committed manifest and exact expected addresses, reports missing or unexpected findings, and exits unsuccessfully on any mismatch or inspection error.</p>
<footer>Source snippets are attributed through immutable upstream links. Signals invite review; they do not prove a defect, intent, correctness, or test adequacy. {link('https://github.com/thajj/gate-drift/blob/main/validation/dataset.json', 'Pinned dataset')} · {link('baseline.json', 'Recorded baseline')}</footer></main></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=ROOT / 'docs/validation.json')
    parser.add_argument('--baseline', type=Path, default=ROOT / 'docs/baseline.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/validation.html')
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding='utf-8'))
    baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
    args.output.write_text(render(report, baseline), encoding='utf-8')


if __name__ == '__main__':
    main()
