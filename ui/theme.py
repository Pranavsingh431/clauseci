"""
Presentation helpers for the Evidence Console.

Pure formatting. No product logic, no provider access, and nothing here decides
anything. Streamlit runs markdown over these strings, so every block of HTML is
emitted on one line: leading indentation would be read as a code block and the
card would come out empty.

One light visual system. Every colour is a token defined once in `:root`, so a
component cannot drift away from the rest of the page. The palette is pinned
rather than inherited, because the page has to look the same on a judge's
laptop whether or not their browser is set to dark mode.
"""

from __future__ import annotations

CSS = """
<style>
  :root {
    --cci-bg:        #FFFFFF;
    --cci-panel:     #F6F8FA;
    --cci-card:      #FFFFFF;
    --cci-border:    #E1E6EC;
    --cci-border-2:  #CBD3DD;
    --cci-text:      #15181E;
    --cci-text-2:    #47515F;
    --cci-text-3:    #6B7583;
    --cci-blue:      #2A5FC8;
    --cci-blue-bg:   #EDF3FD;
    --cci-blue-bd:   #C4D7F5;
    --cci-green:     #17714A;
    --cci-green-bg:  #E8F5EE;
    --cci-green-bd:  #BADFCA;
    --cci-red:       #A81F2D;
    --cci-red-bg:    #FCEDEF;
    --cci-red-bd:    #F1C5CB;
  }

  /* The palette is pinned on purpose. The page must not follow the viewer's
     system dark mode during a screen recording. */
  .stApp, body {background: var(--cci-bg); color: var(--cci-text);}
  .block-container {padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1220px;}
  a {color: var(--cci-blue);}

  /* Scoped to Streamlit's own test ids. A bare `footer` selector would reach
     into undocumented DOM that changes between versions, and hiding the wrong
     node is how a page ends up looking blank. */
  [data-testid="stMainMenu"] {visibility: hidden;}
  header [data-testid="stToolbar"] {right: 1rem;}

  .cci-eyebrow {font-size:.70rem; letter-spacing:.16em; text-transform:uppercase;
                color:var(--cci-blue); font-weight:700;}
  .cci-h1 {font-size:2.9rem; font-weight:800; letter-spacing:-.025em;
           line-height:1.05; margin:.15rem 0 .35rem 0; color:var(--cci-text);}
  .cci-tag {font-size:1.12rem; font-weight:600; color:var(--cci-text-2);
            margin-bottom:.55rem;}
  .cci-lede {font-size:.96rem; color:var(--cci-text-2); max-width:66ch; line-height:1.6;}

  .cci-pill {display:inline-block; padding:4px 11px; margin-right:7px;
             border:1px solid var(--cci-border-2); border-radius:999px;
             font-size:.76rem; color:var(--cci-text-2); background:var(--cci-panel);}

  .cci-card {border:1px solid var(--cci-border); border-radius:12px; padding:16px 18px;
             background:var(--cci-card); height:100%;
             box-shadow:0 1px 2px rgba(16,24,40,.04);}
  .cci-card.good {border-color:var(--cci-green-bd); background:var(--cci-green-bg);}
  .cci-card.bad {border-color:var(--cci-red-bd); background:var(--cci-red-bg);}
  .cci-card.pref {border-color:var(--cci-blue-bd); background:var(--cci-blue-bg);}
  .cci-card.plain {background:var(--cci-panel);}

  .cci-label {font-size:.68rem; letter-spacing:.11em; text-transform:uppercase;
              color:var(--cci-text-3); font-weight:700; margin-bottom:3px;}
  .cci-value {font-size:1.02rem; font-weight:650; color:var(--cci-text);}
  .cci-mono {font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
             font-size:.95rem; font-weight:650; color:var(--cci-text);}
  .cci-note {font-size:.78rem; color:var(--cci-text-3); line-height:1.5;}

  .cci-badge {display:inline-block; padding:3px 10px; border-radius:6px;
              font-size:.71rem; font-weight:800; letter-spacing:.05em;}
  .b-bad {background:var(--cci-red-bg); color:var(--cci-red);
          border:1px solid var(--cci-red-bd);}
  .b-good {background:var(--cci-green-bg); color:var(--cci-green);
           border:1px solid var(--cci-green-bd);}
  .b-flat {background:var(--cci-panel); color:var(--cci-text-2);
           border:1px solid var(--cci-border-2);}
  .b-info {background:var(--cci-blue-bg); color:var(--cci-blue);
           border:1px solid var(--cci-blue-bd);}

  .cci-step {border:1px solid var(--cci-border); border-left:3px solid var(--cci-border-2);
             border-radius:10px; padding:13px 16px; background:var(--cci-card);
             margin-bottom:9px; box-shadow:0 1px 2px rgba(16,24,40,.04);}
  .cci-step.bad {border-left-color:var(--cci-red);}
  .cci-step.good {border-left-color:var(--cci-green);}
  .cci-step.dev {border-left-color:var(--cci-blue);}
  .cci-step-h {font-size:.70rem; letter-spacing:.13em; text-transform:uppercase;
               color:var(--cci-text-3); font-weight:800;}

  .cci-metric {font-size:2.0rem; font-weight:800; letter-spacing:-.02em;
               line-height:1.1; color:var(--cci-text);}
  .cci-metric-sub {font-size:.75rem; color:var(--cci-text-3); line-height:1.45;
                   margin-top:2px;}

  .cci-flow {border:1px solid var(--cci-border); border-radius:10px; padding:11px 14px;
             background:var(--cci-card); text-align:center; font-size:.83rem;
             color:var(--cci-text-2);}
  .cci-flow.ai {border-color:var(--cci-blue-bd); background:var(--cci-blue-bg);}
  .cci-flow.det {border-color:var(--cci-border);}
  .cci-arrowdn {text-align:center; color:var(--cci-border-2); font-size:1.0rem;
                line-height:1.2;}

  /* The capability chain. One compact wrapping row, so the whole workflow is
     readable in the first viewport instead of a column of boxes. */
  .cci-chain {display:flex; flex-wrap:wrap; align-items:center; gap:6px;
              padding:14px 16px; border:1px solid var(--cci-border);
              border-radius:12px; background:var(--cci-panel);}
  .cci-link {display:inline-block; padding:6px 11px; border-radius:8px;
             background:var(--cci-card); border:1px solid var(--cci-border-2);
             font-size:.80rem; font-weight:600; color:var(--cci-text);
             white-space:nowrap;}
  .cci-link.start {border-color:var(--cci-blue-bd); background:var(--cci-blue-bg);
                   color:var(--cci-blue);}
  .cci-link.act {border-color:var(--cci-blue-bd);}
  .cci-link.end {border-color:var(--cci-green-bd); background:var(--cci-green-bg);
                 color:var(--cci-green);}
  .cci-chev {color:var(--cci-text-3); font-size:.82rem; font-weight:700;}

  div[data-testid="stDataFrame"] {border:1px solid var(--cci-border); border-radius:10px;}
  .stTabs [data-baseweb="tab-list"] {gap:2px; border-bottom:1px solid var(--cci-border);}
  .stTabs [data-baseweb="tab"] {padding:9px 18px; font-weight:650; font-size:.9rem;
                                color:var(--cci-text-2);}
  .stTabs [aria-selected="true"] {color:var(--cci-blue);}
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


def chain(links: list[tuple[str, str]]) -> str:
    """One wrapping row of steps, separated by chevrons.

    Each link is (text, kind) where kind is "", "start", "act" or "end".
    """
    parts = []
    for index, (text, kind) in enumerate(links):
        if index:
            parts.append('<span class="cci-chev">&rsaquo;</span>')
        klass = f"cci-link {kind}".strip()
        parts.append(f'<span class="{klass}">{text}</span>')
    return '<div class="cci-chain">' + "".join(parts) + "</div>"


ARROW_DOWN = '<div class="cci-arrowdn">&#8595;</div>'
