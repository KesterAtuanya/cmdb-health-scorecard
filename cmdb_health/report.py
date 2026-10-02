"""Writes the scorecard as a self-contained HTML page, a remediation CSV and
a JSON summary (handy for tracking the score over time or feeding a dashboard)."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from html import escape
from pathlib import Path

from . import __version__
from .models import Dataset
from .scoring import Scorecard, class_label

SEV_ORDER = {"high": 0, "medium": 1, "low": 2}
SEV_POINTS = {"high": 3, "medium": 2, "low": 1}
MAX_HTML_ROWS = 2000


def top_fixes(card: Scorecard, limit: int = 6) -> list[dict]:
    """Group findings that share a fix, and rank groups by impact."""
    groups: dict[tuple, dict] = {}
    for check in card.checks:
        for f in check.findings:
            label = f.message
            if f.message.startswith("Missing "):
                label = f.message  # keep the exact field list, it's the actionable part
            elif f.message.startswith("Last discovered"):
                label = "Not seen by discovery recently"
            elif f.message.startswith("Same serial"):
                label = "Duplicate serial numbers"
            elif f.message.startswith("Same host name"):
                label = "Duplicate host names"
            elif f.message.startswith("Depends on retired"):
                label = "Live CIs depending on retired CIs"
            elif f.message.startswith("'"):
                label = "Names outside the naming standard"
            key = (check.key, label)
            g = groups.setdefault(key, {"check": check.title, "label": label, "fix": f.fix, "count": 0, "points": 0, "severity": f.severity})
            g["count"] += 1
            g["points"] += SEV_POINTS[f.severity]
            if SEV_ORDER[f.severity] < SEV_ORDER[g["severity"]]:
                g["severity"] = f.severity
    ranked = sorted(groups.values(), key=lambda g: (-g["points"], g["label"]))
    return ranked[:limit]


def write_csv(card: Scorecard, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["severity", "check", "ci_name", "ci_class", "ci_sys_id", "problem", "suggested_fix"])
        rows = [(f, c.title) for c in card.checks for f in c.findings]
        rows.sort(key=lambda x: (SEV_ORDER[x[0].severity], x[1], x[0].ci_name.lower()))
        for f, title in rows:
            w.writerow([f.severity, title, f.ci_name, f.ci_class, f.ci_sys_id, f.message, f.fix])


def write_json(card: Scorecard, data: Dataset, path: Path) -> None:
    summary = {
        "tool": "cmdb-health-scorecard",
        "version": __version__,
        "source": data.source,
        "collected_at": data.collected_at.isoformat(),
        "score": card.score,
        "grade": card.grade,
        "total_cis": card.total_cis,
        "active_cis": card.active_cis,
        "affected_cis": card.affected_cis,
        "severity_counts": card.severity_counts,
        "checks": [
            {
                "key": c.key,
                "title": c.title,
                "weight": c.weight,
                "applicable": c.applicable,
                "affected": c.affected,
                "pass_rate": round(c.pass_rate, 4),
            }
            for c in card.checks
        ],
        "classes": card.class_rows,
    }
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _ring(score: float, grade: str) -> str:
    r, circ = 54, 2 * 3.14159265 * 54
    filled = circ * score / 100
    return f"""<svg class="ring" viewBox="0 0 140 140" role="img" aria-label="Health score {score} out of 100, grade {grade}">
  <circle cx="70" cy="70" r="{r}" fill="none" stroke="var(--track)" stroke-width="12"/>
  <circle cx="70" cy="70" r="{r}" fill="none" stroke="var(--grade-{grade})" stroke-width="12" stroke-linecap="round"
    stroke-dasharray="{filled:.1f} {circ:.1f}" transform="rotate(-90 70 70)"/>
  <text x="70" y="68" text-anchor="middle" class="ring-score">{score:.0f}</text>
  <text x="70" y="90" text-anchor="middle" class="ring-sub">GRADE {grade}</text>
</svg>"""


def write_html(card: Scorecard, data: Dataset, path: Path) -> None:
    e = escape
    fixes = top_fixes(card)
    check_cards = []
    for c in card.checks:
        pct = c.pass_rate * 100
        tone = "good" if pct >= 95 else "warn" if pct >= 85 else "bad"
        check_cards.append(f"""
      <article class="check">
        <header><h3>{e(c.title)}</h3><span class="pct {tone}">{pct:.1f}%</span></header>
        <div class="bar"><i class="{tone}" style="width:{pct:.1f}%"></i></div>
        <p>{e(c.description)}</p>
        <p class="meta"><b>{c.affected}</b> of {c.applicable} CIs affected · weight {c.weight:g}</p>
      </article>""")

    fix_rows = "".join(
        f"""<li><span class="sev {f['severity']}">{f['severity']}</span><div><b>{e(f['label'])}</b>
        <span class="count">{f['count']} CI{'s' if f['count'] != 1 else ''}</span><p>{e(f['fix'])}</p></div></li>"""
        for f in fixes
    )

    class_rows = "".join(
        f"""<tr><td>{e(r['label'])}<span class="code">{e(r['class'])}</span></td><td class="num">{r['total']}</td>
        <td class="num">{r['affected']}</td><td><div class="mini"><i style="width:{r['healthy_pct']}%"></i></div></td>
        <td class="num">{r['healthy_pct']:.1f}%</td></tr>"""
        for r in card.class_rows
    )

    findings = [(f, c.title) for c in card.checks for f in c.findings]
    findings.sort(key=lambda x: (SEV_ORDER[x[0].severity], x[1], x[0].ci_name.lower()))
    shown = findings[:MAX_HTML_ROWS]
    finding_rows = "".join(
        f"""<tr data-sev="{f.severity}" data-check="{e(title)}"><td><span class="sev {f.severity}">{f.severity}</span></td>
        <td>{e(title)}</td><td class="ci">{e(f.ci_name)}<span class="code">{e(class_label(f.ci_class))}</span></td>
        <td>{e(f.message)}</td></tr>"""
        for f, title in shown
    )
    more = (f'<p class="meta">Showing the first {MAX_HTML_ROWS} of {len(findings)} findings. The CSV has all of them.</p>'
            if len(findings) > MAX_HTML_ROWS else "")
    check_options = "".join(f'<option>{e(c.title)}</option>' for c in card.checks if c.findings)
    sev = card.severity_counts
    generated = datetime.now().strftime("%b %d, %Y %I:%M %p")

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CMDB Health Scorecard · {e(data.source)}</title>
<style>
:root{{--bg:#f4f6f9;--panel:#fff;--ink:#141a26;--soft:#5a6475;--line:#dfe3ea;--track:#e4e8ef;--accent:#2457d6;
--good:#1a8f5a;--warn:#c27a00;--bad:#c9314b;--grade-A:#1a8f5a;--grade-B:#3b8e2f;--grade-C:#c27a00;--grade-D:#d35a1d;--grade-F:#c9314b;
--mono:ui-monospace,"Cascadia Code",Consolas,Menlo,monospace;--sans:"Segoe UI",system-ui,-apple-system,Roboto,sans-serif}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f131b;--panel:#171d28;--ink:#e8ecf3;--soft:#9aa5b8;--line:#2a3242;--track:#252d3b;
--accent:#6d97ff;--good:#3fc285;--warn:#f0a93a;--bad:#ff6b82;--grade-A:#3fc285;--grade-B:#7bc96b;--grade-C:#f0a93a;--grade-D:#ff8a4c;--grade-F:#ff6b82;color-scheme:dark}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 var(--sans);padding:28px 16px 60px}}
.wrap{{max-width:1100px;margin:0 auto;display:flex;flex-direction:column;gap:22px}}
h1{{font-size:26px;margin:0;letter-spacing:-.01em}} h2{{font-size:17px;margin:0 0 12px}} h3{{font-size:15px;margin:0}}
.eyebrow{{font:600 11px/1 var(--sans);letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}}
.meta,.code{{color:var(--soft);font-size:13px}} .code{{display:block;font-family:var(--mono);font-size:11.5px}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px}}
.hero{{display:grid;grid-template-columns:180px 1fr;gap:28px;align-items:center}}
.ring{{width:170px;height:170px}} .ring-score{{font:700 40px var(--sans);fill:var(--ink)}} .ring-sub{{font:600 11px var(--sans);letter-spacing:.12em;fill:var(--soft)}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:16px}}
.stat b{{display:block;font-size:24px;font-variant-numeric:tabular-nums}} .stat span{{color:var(--soft);font-size:13px}}
.checks{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}}
.check header{{display:flex;justify-content:space-between;align-items:baseline;gap:10px}}
.check p{{margin:8px 0 0;font-size:14px}}
.pct{{font:700 15px var(--mono)}} .pct.good{{color:var(--good)}} .pct.warn{{color:var(--warn)}} .pct.bad{{color:var(--bad)}}
.bar{{height:6px;background:var(--track);border-radius:9px;margin-top:10px;overflow:hidden}}
.bar i{{display:block;height:100%;border-radius:9px}} .bar i.good{{background:var(--good)}} .bar i.warn{{background:var(--warn)}} .bar i.bad{{background:var(--bad)}}
.fixes{{list-style:none;margin:0;padding:0;display:grid;gap:12px}}
.fixes li{{display:grid;grid-template-columns:70px 1fr;gap:12px;align-items:start;padding-bottom:12px;border-bottom:1px solid var(--line)}}
.fixes li:last-child{{border:0;padding:0}} .fixes p{{margin:4px 0 0;font-size:14px;color:var(--soft)}}
.count{{margin-left:8px;font:600 12px var(--mono);color:var(--soft)}}
.sev{{display:inline-block;font:700 10.5px var(--sans);letter-spacing:.08em;text-transform:uppercase;padding:3px 7px;border-radius:5px;text-align:center}}
.sev.high{{background:color-mix(in srgb,var(--bad) 16%,transparent);color:var(--bad)}}
.sev.medium{{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}}
.sev.low{{background:var(--track);color:var(--soft)}}
.tablewrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th{{text-align:left;font:600 11px var(--sans);letter-spacing:.1em;text-transform:uppercase;color:var(--soft);padding:8px 10px;border-bottom:1px solid var(--line)}}
td{{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}} td.num{{text-align:right;font-variant-numeric:tabular-nums}}
.mini{{height:6px;background:var(--track);border-radius:9px;min-width:80px}} .mini i{{display:block;height:100%;background:var(--accent);border-radius:9px}}
.filters{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}}
.filters input,.filters select{{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}}
.filters input{{flex:1 1 220px}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:22px}} .two>*{{min-width:0}}
footer{{color:var(--soft);font-size:12.5px;text-align:center}}
@media (max-width:760px){{.hero,.two{{grid-template-columns:1fr}} .ring{{margin:0 auto;display:block}}}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <span class="eyebrow">CMDB Health Scorecard</span>
    <h1>{e(data.source)}</h1>
    <p class="meta">Data collected {data.collected_at.strftime("%b %d, %Y %H:%M")} · report generated {generated}</p>
  </header>

  <section class="panel hero">
    {_ring(card.score, card.grade)}
    <div>
      <h2>{card.affected_cis} of {card.active_cis} active CIs need attention</h2>
      <p class="meta" style="margin:0">The score is a weighted average of the checks below. Retired CIs are only checked for status conflicts.</p>
      <div class="stats">
        <div class="stat"><b>{card.total_cis}</b><span>CIs scanned</span></div>
        <div class="stat"><b style="color:var(--bad)">{sev['high']}</b><span>high severity findings</span></div>
        <div class="stat"><b style="color:var(--warn)">{sev['medium']}</b><span>medium severity</span></div>
        <div class="stat"><b>{sev['low']}</b><span>low severity</span></div>
      </div>
    </div>
  </section>

  <section class="checks">{''.join(check_cards)}</section>

  <section class="two">
    <div class="panel"><h2>Fix these first</h2><ul class="fixes">{fix_rows or '<li>No problems found.</li>'}</ul></div>
    <div class="panel"><h2>Health by class</h2><div class="tablewrap"><table>
      <thead><tr><th>Class</th><th class="num">CIs</th><th class="num">With issues</th><th></th><th class="num">Healthy</th></tr></thead>
      <tbody>{class_rows}</tbody></table></div></div>
  </section>

  <section class="panel">
    <h2>All findings ({len(findings)})</h2>
    <div class="filters">
      <input id="q" type="search" placeholder="Search by CI name or problem" aria-label="Search findings">
      <select id="sev" aria-label="Severity"><option value="">All severities</option><option>high</option><option>medium</option><option>low</option></select>
      <select id="chk" aria-label="Check"><option value="">All checks</option>{check_options}</select>
    </div>
    <div class="tablewrap"><table id="findings">
      <thead><tr><th>Severity</th><th>Check</th><th>CI</th><th>Problem</th></tr></thead>
      <tbody>{finding_rows}</tbody></table></div>
    {more}
  </section>

  <footer>cmdb-health-scorecard v{__version__} · read-only analysis, nothing was changed in the CMDB</footer>
</div>
<script>
(function(){{
  var q=document.getElementById('q'),s=document.getElementById('sev'),c=document.getElementById('chk');
  var rows=[].slice.call(document.querySelectorAll('#findings tbody tr'));
  function apply(){{var t=q.value.toLowerCase();rows.forEach(function(r){{
    var ok=(!s.value||r.dataset.sev===s.value)&&(!c.value||r.dataset.check===c.value)&&(!t||r.textContent.toLowerCase().indexOf(t)>-1);
    r.style.display=ok?'':'none';}});}}
  [q,s,c].forEach(function(el){{el.addEventListener('input',apply)}});
}})();
</script>
</body>
</html>"""
    path.write_text(html, encoding="utf-8")


def write_all(card: Scorecard, data: Dataset, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "html": out_dir / "cmdb_health_report.html",
        "csv": out_dir / "cmdb_findings.csv",
        "json": out_dir / "cmdb_health_summary.json",
    }
    write_html(card, data, paths["html"])
    write_csv(card, paths["csv"])
    write_json(card, data, paths["json"])
    return paths
