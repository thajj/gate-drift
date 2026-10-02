"""Portable reports: escaped source snippets, no scripts or remote resources."""
from dataclasses import asdict
from html import escape
import json


def payload(findings, metadata):
    return {"tool": "gate-drift", "version": "0.1.0", "metadata": metadata,
            "findings": [asdict(f) for f in findings],
            "note": "Heuristic review signals. No findings does not prove the code or CI is correct."}


def text_report(findings, metadata):
    lines = ["GATE DRIFT · Review the checks behind green CI", "",
             f"{len(findings)} review signal(s) across {metadata['files_inspected']} changed text file(s)."]
    if metadata.get("demo"):
        lines.append("SYNTHETIC DEMO — no repository was inspected.")
    for f in findings:
        lines.extend(["", f"[{f.severity.upper()}] {f.rule_id} · {f.path}:{f.line} ({f.side})", f.title])
        if f.before:
            lines.append("  before: " + f.before.strip())
        if f.after:
            lines.append("  after:  " + f.after.strip())
        lines.append("  " + f.explanation)
    if metadata.get("excluded"):
        lines.extend(["", "Excluded: " + ", ".join(metadata["excluded"])])
    lines.extend(["", metadata.get("scope", ""), "Review signals, not a correctness verdict."])
    return "\n".join(lines) + "\n"


def markdown_report(findings, metadata):
    lines = ["## Gate Drift", "", f"**{len(findings)} review signal(s)** across {metadata['files_inspected']} changed text file(s).", ""]
    for f in findings:
        lines.extend([f"- **{escape(f.title)}** (`{f.rule_id}`), {escape(f.path)}:{f.line} ({f.side}). {escape(f.explanation)}"])
    lines.extend(["", "Heuristic signals for human review. No findings does not prove correctness.", "", escape(metadata.get("scope", ""))])
    if metadata.get("excluded"):
        lines.extend(["", "Excluded: " + escape(", ".join(metadata["excluded"]))])
    return "\n".join(lines) + "\n"


def json_report(findings, metadata):
    return json.dumps(payload(findings, metadata), indent=2, ensure_ascii=True) + "\n"


def html_report(findings, metadata):
    e = lambda value: escape(str(value), quote=True)
    cards = []
    for f in findings:
        cards.append(f'''<article class="finding"><div class="finding-top"><span class="pill {e(f.severity)}">{e(f.severity)}</span><code>{e(f.rule_id)}</code></div>
<h2>{e(f.title)}</h2><p class="location">{e(f.path)} <span>line {f.line} · {e(f.side)} side</span></p>
<div class="diff"><div><label>BEFORE</label><pre>{e(f.before) if f.before else '∅'}</pre></div><div><label>AFTER</label><pre>{e(f.after) if f.after else '∅'}</pre></div></div>
<p class="explanation">{e(f.explanation)}</p></article>''')
    empty = '<article class="finding"><h2>No supported rule matched this change</h2><p>Keep reviewing. Gate Drift does not inspect all ways to weaken a quality gate.</p></article>'
    excluded = '<p>Excluded: ' + e(", ".join(metadata["excluded"])) + '</p>' if metadata.get("excluded") else ''
    label = "SYNTHETIC DEMO" if metadata.get("demo") else "LOCAL REVIEW"
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; form-action 'none'"><title>Gate Drift · {len(findings)} review signals</title>
<style>
*{{box-sizing:border-box}}:root{{color-scheme:dark}}body{{margin:0;background:#101516;color:#edf2ed;font:16px/1.55 system-ui,-apple-system,sans-serif}}main{{max-width:1060px;margin:auto;padding:40px 28px 70px}}header{{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #2b3736;padding-bottom:22px;margin-bottom:38px}}.brand{{font-size:20px;font-weight:760;letter-spacing:-.7px}}.mark{{color:#bbf971;margin-right:10px}}.badge{{font-size:11px;letter-spacing:1.7px;color:#bbf971}}.eyebrow{{color:#a6b4aa;font-size:12px;letter-spacing:2px}}h1{{font-size:clamp(32px,5vw,54px);line-height:1.08;letter-spacing:-2px;max-width:730px;margin:14px 0 18px}}.lede{{color:#abb9b3;max-width:750px;font-size:18px}}.stats{{display:flex;gap:30px;padding:25px 0;margin:25px 0;border-top:1px solid #2b3736;border-bottom:1px solid #2b3736}}.stat strong{{display:block;font-size:30px;line-height:1.2;color:#bbf971}}.stat span{{font-size:12px;color:#abb9b3}}.finding{{border:1px solid #2c3938;background:#18201f;border-radius:12px;padding:24px;margin:18px 0}}.finding-top{{display:flex;gap:12px;align-items:center}}.finding-top code{{font-size:12px;color:#abb9b3}}.pill{{border-radius:30px;font-size:10px;text-transform:uppercase;letter-spacing:1px;padding:4px 9px;background:#48301e;color:#ffbf80}}.pill.medium{{background:#303c27;color:#cde3a9}}h2{{font-size:20px;line-height:1.3;margin:15px 0 10px;letter-spacing:-.4px}}.location{{font:13px ui-monospace,monospace;margin:0 0 20px;color:#d2ded5}}.location span{{color:#7e9487;margin-left:14px}}.diff{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.diff>div{{border:1px solid #32413e;border-radius:7px;overflow:hidden;background:#101817}}label{{display:block;font:10px ui-monospace,monospace;letter-spacing:1.7px;padding:9px 12px;border-bottom:1px solid #32413e;color:#91a799}}pre{{margin:0;padding:14px 12px;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.65 ui-monospace,SFMono-Regular,monospace}}.diff>div:last-child pre{{color:#ffce9e}}.explanation{{color:#aabbaf;font-size:14px;margin:16px 0 0}}footer{{color:#91a799;font-size:12px;padding-top:28px;border-top:1px solid #2b3736;margin-top:34px}}footer strong{{color:#c9d7cd}}@media(max-width:600px){{main{{padding:25px 16px}}.diff{{grid-template-columns:1fr}}.finding{{padding:18px}}.location span{{display:block;margin:5px 0}}h1{{letter-spacing:-1px}}}}
</style></head><body><main><header><div class="brand"><span class="mark">◈</span>gate drift</div><span class="badge">{label} / v0.1.0</span></header>
<div class="eyebrow">REVIEW THE CHECKS BEHIND GREEN CI</div><h1>Did the code improve,<br>or did the checks get weaker?</h1><p class="lede">Concrete changes to tests, coverage, workflows, and type checks. Every signal comes with the evidence a reviewer needs.</p>
<div class="stats"><div class="stat"><strong>{len(findings)}</strong><span>review signals</span></div><div class="stat"><strong>{metadata['files_inspected']}</strong><span>changed text files</span></div><div class="stat"><strong>0</strong><span>external requests</span></div></div>
{''.join(cards) or empty}<footer><strong>Signals, not a correctness verdict.</strong> A clean report does not prove a safe change. Intentional relaxations need review too.<p>{e(metadata.get('scope',''))}</p>{excluded}<p>Repository: {e(metadata.get('repository','demo'))} · Base: {e(metadata.get('base','synthetic'))} · Head: {e(metadata.get('head','synthetic'))}</p><p>This report contains source snippets. Review them before sharing. Gate Drift makes no network requests and runs no repository code.</p></footer></main></body></html>'''
