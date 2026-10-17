"""Render the shortlist as a self-contained HTML page.

No external assets — it has to survive being emailed and opened on a phone.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

CSS = """
*{box-sizing:border-box}
body{margin:0;padding:24px 16px;background:#f6f7f9;color:#16181d;
 font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:720px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:#6b7280;font-size:14px;margin:0 0 24px}
.job{background:#fff;border:1px solid #e5e7eb;border-radius:12px;
 padding:18px;margin-bottom:14px}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.title{font-weight:650;font-size:17px;margin:0 0 2px;letter-spacing:-.01em}
.co{color:#6b7280;font-size:14px;margin:0}
.score{flex:none;font-weight:700;font-size:15px;padding:4px 11px;border-radius:999px}
.s-hi{background:#dcfce7;color:#14532d}
.s-mid{background:#fef3c7;color:#713f12}
.why{margin:12px 0 0;font-size:14.5px;color:#374151}
details{margin-top:12px;border-top:1px solid #f0f1f3;padding-top:12px}
summary{cursor:pointer;font-size:14px;font-weight:600;color:#1d4ed8}
pre{white-space:pre-wrap;font:inherit;background:#fafbfc;border:1px solid #eef0f2;
 border-radius:8px;padding:14px;margin:12px 0 0}
.go{display:inline-block;margin-top:12px;font-size:14px;font-weight:600;
 color:#1d4ed8;text-decoration:none}
.foot{color:#9ca3af;font-size:12.5px;text-align:center;margin-top:28px}
@media(prefers-color-scheme:dark){
 body{background:#0f1115;color:#e8eaed}
 .job{background:#171a1f;border-color:#2a2e35}
 .co,.sub{color:#9aa1ab}.why{color:#c3c8cf}
 pre{background:#0f1115;border-color:#2a2e35}
 details{border-top-color:#2a2e35}
 .s-hi{background:#14532d;color:#bbf7d0}.s-mid{background:#713f12;color:#fde68a}
 .go,summary{color:#7aa2ff}}
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job shortlist — {date}</title><style>{css}</style></head>
<body><div class="wrap">
<h1>Job shortlist</h1>
<p class="sub">{date} · {n} role{s} worth your time</p>
{jobs}
<p class="foot">Review, tweak the letter, submit yourself. Source: France Travail.</p>
</div></body></html>
"""

CARD = """<div class="job">
  <div class="top">
    <div>
      <p class="title">{title}</p>
      <p class="co">{company} · {url_host}</p>
    </div>
    <span class="score {cls}">{score}</span>
  </div>
  <p class="why">{why}</p>
  {letter}
  <a class="go" href="{url}">Open posting &rarr;</a>
</div>"""


def render_digest(shortlist: list[dict], out_path: Path | str) -> Path:
    cards = []
    for job in shortlist:
        score = job.get("score") or 0
        letter = job.get("cover_letter")
        letter_html = (
            f"<details><summary>Cover letter draft</summary>"
            f"<pre>{html.escape(letter)}</pre></details>"
            if letter
            else ""
        )
        cards.append(
            CARD.format(
                title=html.escape(job.get("title") or "Untitled"),
                company=html.escape(job.get("company") or "Company not stated"),
                url_host="France Travail",
                cls="s-hi" if score >= 80 else "s-mid",
                score=score,
                why=html.escape(job.get("reasoning") or ""),
                letter=letter_html,
                url=html.escape(job.get("url") or "#"),
            )
        )

    page = PAGE.format(
        css=CSS,
        date=datetime.now().strftime("%A %d %B %Y"),
        n=len(shortlist),
        s="" if len(shortlist) == 1 else "s",
        jobs="\n".join(cards),
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out
