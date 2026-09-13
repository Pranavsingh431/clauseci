"""
Presentation helpers for the Evidence Console.

Pure formatting. No product logic, no provider access, and nothing here decides
anything. Streamlit runs markdown over these strings, so every block of HTML is
emitted on one line: leading indentation would be read as a code block and the
card would come out empty.
"""

from __future__ import annotations

CSS = """
<style>
  .block-container {padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1220px;}
  /* Scoped to Streamlit's own test ids. A bare `footer` selector would reach
     into undocumented DOM that changes between versions, and hiding the wrong
     node is how a page ends up looking blank. */
  [data-testid="stMainMenu"] {visibility: hidden;}
  header [data-testid="stToolbar"] {right: 1rem;}

  .cci-eyebrow {font-size:.70rem; letter-spacing:.16em; text-transform:uppercase;
                color:#8A93A6; font-weight:700;}
  .cci-h1 {font-size:2.9rem; font-weight:800; letter-spacing:-.025em;
           line-height:1.05; margin:.15rem 0 .35rem 0;}
  .cci-tag {font-size:1.12rem; font-weight:600; color:#C7CEDB; margin-bottom:.55rem;}
  .cci-lede {font-size:.96rem; color:#98A1B3; max-width:66ch; line-height:1.6;}

  .cci-pill {display:inline-block; padding:4px 11px; margin-right:7px;
             border:1px solid #2A3040; border-radius:999px; font-size:.76rem;
             color:#C7CEDB; background:#141924;}

  .cci-card {border:1px solid #222836; border-radius:12px; padding:16px 18px;
             background:#12161F; height:100%;}
  .cci-card.good {border-color:#1F4536;}
  .cci-card.bad {border-color:#4A2430;}
  .cci-card.pref {border-color:#2E4A7A; background:#131A28;}

  .cci-label {font-size:.68rem; letter-spacing:.11em; text-transform:uppercase;
              color:#7B8496; font-weight:700; margin-bottom:3px;}
  .cci-value {font-size:1.02rem; font-weight:650; color:#E6E9EF;}
  .cci-mono {font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
             font-size:.95rem; font-weight:650; color:#E6E9EF;}
  .cci-note {font-size:.78rem; color:#7B8496; line-height:1.5;}

  .cci-badge {display:inline-block; padding:3px 10px; border-radius:6px;
              font-size:.71rem; font-weight:800; letter-spacing:.05em;}
  .b-bad {background:#3A1C25; color:#F08A9B;}
  .b-good {background:#14332A; color:#5FD3A3;}
  .b-flat {background:#1C2130; color:#9AA3B4;}
  .b-info {background:#1A2740; color:#8FB4F5;}

  .cci-step {border:1px solid #222836; border-left:3px solid #2E3747; border-radius:10px;
             padding:13px 16px; background:#11151E; margin-bottom:9px;}
  .cci-step.bad {border-left-color:#A33B50;}
  .cci-step.good {border-left-color:#2E8B65;}
  .cci-step.dev {border-left-color:#5B8DEF;}
  .cci-step-h {font-size:.70rem; letter-spacing:.13em; text-transform:uppercase;
               color:#7B8496; font-weight:800;}

  .cci-metric {font-size:2.0rem; font-weight:800; letter-spacing:-.02em; line-height:1.1;}
  .cci-metric-sub {font-size:.75rem; color:#7B8496; line-height:1.45; margin-top:2px;}

  .cci-flow {border:1px solid #222836; border-radius:10px; padding:11px 14px;
             background:#12161F; text-align:center; font-size:.83rem; color:#C7CEDB;}
  .cci-flow.ai {border-color:#3A3050; background:#171325;}
  .cci-flow.det {border-color:#223340;}
  .cci-arrowdn {text-align:center; color:#3C4456; font-size:1.0rem; line-height:1.2;}

  div[data-testid="stDataFrame"] {border:1px solid #222836; border-radius:10px;}
  .stTabs [data-baseweb="tab-list"] {gap:2px;}
  .stTabs [data-baseweb="tab"] {padding:9px 18px; font-weight:650; font-size:.9rem;}
</style>
"""


def pill(text: str) -> str:
    return f'<span class="cci-pill">{text}</span>'


def badge(text: str, kind: str = "flat") -> str:
    return f'<span class="cci-badge b-{kind}">{text}</span>'


def card(*parts: str, kind: str = "") -> str:
    klass = f"cci-card {kind}".strip()
    return f'<div class="{klass}">' + "".join(parts) + "</div>"


def field(label: str, value: str, mono: bool = False) -> str:
    css = "cci-mono" if mono else "cci-value"
    return f'<div class="cci-label">{label}</div><div class="{css}">{value}</div>'


def spacer(height: int = 10) -> str:
    return f'<div style="height:{height}px"></div>'


def step(heading: str, body: str, kind: str = "") -> str:
    klass = f"cci-step {kind}".strip()
    return (f'<div class="{klass}"><div class="cci-step-h">{heading}</div>'
            f'<div style="margin-top:5px">{body}</div></div>')


def metric(value: str, caption: str) -> str:
    return (f'<div class="cci-metric">{value}</div>'
            f'<div class="cci-metric-sub">{caption}</div>')


def flow(text: str, kind: str = "det") -> str:
    return f'<div class="cci-flow {kind}">{text}</div>'


ARROW_DOWN = '<div class="cci-arrowdn">&#8595;</div>'
