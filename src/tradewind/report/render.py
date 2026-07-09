"""Render report and diff models to self-contained HTML.

Contract: HTML is produced with an autoescaping Jinja2 environment, so
arbitrary trace text (agent messages, veto reasons, LLM output) is escaped and
cannot inject markup. Output is a single file with inline CSS and an inline SVG
chart — no scripts, no external resources — so it opens anywhere offline.
"""

from jinja2 import Environment, select_autoescape

from tradewind.report.diff import DiffResult
from tradewind.report.model import ReportModel
from tradewind.report.svg import equity_svg

_env = Environment(autoescape=select_autoescape(default=True), trim_blocks=True, lstrip_blocks=True)

_CSS = """
:root { color-scheme: light dark; }
body { font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       margin: 0; padding: 2rem; max-width: 1000px; margin: 0 auto;
       color: #1a1a1a; background: #fff; }
@media (prefers-color-scheme: dark) { body { color: #e8e8e8; background: #161616; } }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 2rem 0 .5rem; border-bottom: 1px solid #8884; padding-bottom: .25rem; }
.sub { color: #8a8a8a; margin: 0 0 1.5rem; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #8883; vertical-align: top; }
th { font-weight: 600; }
code, .mono { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; }
.pill { display: inline-block; padding: .05rem .5rem; border-radius: 999px; font-size: 12px; font-weight: 600; }
.ok { background: #1a7f3722; color: #1a7f37; }
.bad { background: #d1242f22; color: #d1242f; }
.muted { color: #8a8a8a; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .75rem; margin: 1rem 0; }
.card { border: 1px solid #8883; border-radius: 8px; padding: .75rem 1rem; }
.card .k { font-size: 12px; color: #8a8a8a; } .card .v { font-size: 1.1rem; font-weight: 600; }
.chart { width: 100%; height: auto; border: 1px solid #8883; border-radius: 8px; margin: 1rem 0; }
.chart-line { stroke: #3b82f6; stroke-width: 2; }
.chart-dot { fill: #3b82f6; } .chart-zero { stroke: #8886; stroke-dasharray: 4 3; }
.chart-title { font-size: 13px; font-weight: 600; fill: currentColor; }
.chart-axis, .chart-empty { font-size: 11px; fill: #8a8a8a; }
.hash { word-break: break-all; }
"""

_REPORT_TEMPLATE = """
<h1>Tradewind evaluation report</h1>
<p class="sub">code {{ h.code_version }} &middot; models {{ h.model_ids | join(", ") or "—" }}
  &middot; seed {{ h.rng_seed }}{% if h.submodule_sha %} &middot; submodule {{ h.submodule_sha[:12] }}{% endif %}</p>

<div class="grid">
  <div class="card"><div class="k">Events</div><div class="v">{{ m.event_count }}</div></div>
  <div class="card"><div class="k">Proposed</div><div class="v">{{ m.proposed }}</div></div>
  <div class="card"><div class="k">Admitted (fills)</div><div class="v">{{ m.admitted }}</div></div>
  <div class="card"><div class="k">Violations</div><div class="v">{{ m.violation_count }}</div></div>
</div>

<h2>Determinism attestation</h2>
<div class="grid">
  <div class="card"><div class="k">Replay verified</div><div class="v">
    {% if m.replay_verified %}<span class="pill ok">byte-identical</span>
    {% else %}<span class="pill bad">NOT verified</span>{% endif %}</div></div>
  <div class="card" style="grid-column: span 2;"><div class="k">Config hash</div>
    <div class="v mono hash">{{ h.config_hash }}</div></div>
</div>
<p class="muted mono hash">chain hash {{ m.chain_hash }}</p>

<h2>Equity curve</h2>
{{ chart_svg | safe }}

<h2>Decisions</h2>
<table>
  <thead><tr><th>Seq</th><th>Action</th><th>Outcome</th><th>Fill</th><th>Provenance</th></tr></thead>
  <tbody>
  {% for d in m.decisions %}
    <tr>
      <td class="mono">{{ d.action_seq }}</td>
      <td>{{ d.action.side | default("") }} {{ d.action.quantity | default("") }}
          {{ d.action.symbol | default("") }}
          {% if d.action.executed_on %}<div class="muted">{{ d.action.executed_on }}</div>{% endif %}</td>
      <td>
        {% if d.blocked %}<span class="pill bad">BLOCKED</span>
        {% else %}<span class="pill ok">admitted</span>{% endif %}
        {% for v in d.violations %}<div class="muted mono">{{ v.invariant }}: {{ v.reason }}</div>{% endfor %}
      </td>
      <td class="mono">{% if d.fill %}{{ d.fill.quantity }} @ {{ d.fill.price }}
          <div class="muted">cash {{ d.fill.cash_delta }}</div>{% else %}—{% endif %}</td>
      <td>{% for p in d.provenance %}<div class="muted mono">#{{ p.seq }} {{ p.event_type }}
          {% if p.role %}[{{ p.role }}]{% endif %}</div>{% else %}<span class="muted">—</span>{% endfor %}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>

<h2>Violations</h2>
{% if m.violations %}
<table>
  <thead><tr><th>Invariant</th><th>Reason</th><th>Evidence</th></tr></thead>
  <tbody>
  {% for v in m.violations %}
    <tr><td class="mono">{{ v.invariant }}</td><td>{{ v.reason }}</td>
        <td class="mono">{{ v.evidence }}</td></tr>
  {% endfor %}
  </tbody>
</table>
{% else %}<p class="muted">None.</p>{% endif %}

<h2>Token &amp; cost by role</h2>
{% if m.usage %}
<table>
  <thead><tr><th>Role</th><th>Calls</th><th>Prompt tok</th><th>Completion tok</th><th>Cost</th></tr></thead>
  <tbody>
  {% for u in m.usage %}
    <tr><td>{{ u.role }}</td><td class="mono">{{ u.calls }}</td>
        <td class="mono">{{ u.prompt_tokens }}</td><td class="mono">{{ u.completion_tokens }}</td>
        <td class="mono">{{ u.cost }}</td></tr>
  {% endfor %}
  </tbody>
</table>
{% else %}<p class="muted">No LLM calls in this trace.</p>{% endif %}
"""

_DIFF_TEMPLATE = """
<h1>Tradewind trace diff</h1>
<p class="sub"><span class="mono">{{ d.left_path }}</span> vs <span class="mono">{{ d.right_path }}</span></p>
<div class="grid">
  <div class="card"><div class="k">Diverged</div><div class="v">
    {% if d.diverged %}<span class="pill bad">at seq {{ d.first_divergence_seq }}</span>
    {% else %}<span class="pill ok">identical</span>{% endif %}</div></div>
  <div class="card"><div class="k">Left events</div><div class="v">{{ d.left_count }}</div></div>
  <div class="card"><div class="k">Right events</div><div class="v">{{ d.right_count }}</div></div>
</div>
<h2>Aligned events</h2>
<table>
  <thead><tr><th>Seq</th><th></th><th>Left</th><th>Right</th></tr></thead>
  <tbody>
  {% for r in d.rows %}
    <tr>
      <td class="mono">{{ r.seq }}</td>
      <td>{% if r.same %}<span class="pill ok">=</span>{% else %}<span class="pill bad">≠</span>{% endif %}</td>
      <td class="mono">{{ r.left | default("—", true) }}</td>
      <td class="mono">{{ r.right | default("—", true) }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
"""


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html lang=en><head><meta charset=utf-8>"
        f'<meta name=viewport content="width=device-width, initial-scale=1">'
        f"<title>{title}</title><style>{_CSS}</style></head><body>{body}</body></html>"
    )


def render_report_html(model: ReportModel) -> str:
    """Render a full evaluation report to a self-contained HTML string."""
    body = _env.from_string(_REPORT_TEMPLATE).render(
        m=model,
        h=model.header,
        chart_svg=equity_svg(model.equity_curve, model.is_pnl),
    )
    return _page("Tradewind evaluation report", body)


def render_diff_html(result: DiffResult) -> str:
    """Render a trace diff to a self-contained HTML string."""
    body = _env.from_string(_DIFF_TEMPLATE).render(d=result)
    return _page("Tradewind trace diff", body)
