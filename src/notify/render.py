"""Renders the dashboard digest's markdown into real HTML for an email
body -- proper headers/bullets/bold/links, not a wall of markdown source
text. Uses Python-Markdown (a real, well-tested library) rather than a
hand-rolled parser: unlike src/common/latex.py (deliberately narrow
because no library exists for this project's specific LaTeX macro set),
dashboard.md is plain, standard markdown, exactly what a real markdown
library is for.
"""

import markdown

# `extra` bundles fenced_code (the ```...``` accept/reject command blocks
# in section 1), tables, and a few other GFM-ish features; sane_lists
# tightens ordered/unordered list nesting behavior -- the digest nests a
# nested "Other roles at X:" bullet list under a top-level "-" item.
_MARKDOWN_EXTENSIONS = ["extra", "sane_lists"]

# Minimal, email-safe styling -- inline-friendly tags only, no external
# assets or webfonts (most email clients strip <link>/@import). Gmail
# specifically -- the actual delivery target here -- does support a
# <style> block in <head>, so this isn't fighting the client.
_EMAIL_CSS = """
body { font-family: -apple-system, Helvetica, Arial, sans-serif; color: #1a1a1a;
       line-height: 1.5; max-width: 700px; margin: 0 auto; padding: 16px; }
h1 { font-size: 22px; border-bottom: 2px solid #ddd; padding-bottom: 8px; }
h2 { font-size: 18px; margin-top: 28px; border-bottom: 1px solid #eee; padding-bottom: 4px; }
h3 { font-size: 15px; margin-top: 20px; }
ul { padding-left: 22px; }
li { margin-bottom: 4px; }
code { background: #f4f4f4; padding: 1px 5px; border-radius: 3px; font-size: 13px; }
pre { background: #f4f4f4; padding: 10px; border-radius: 4px; overflow-x: auto; }
pre code { background: none; padding: 0; }
a { color: #1a56db; }
hr { border: none; border-top: 1px solid #ddd; margin: 24px 0; }
blockquote { margin: 4px 0; padding-left: 12px; border-left: 3px solid #ddd; color: #555; }
"""


def render_digest_html(markdown_text: str) -> str:
    """The whole point: this returns a real HTML document, not the
    markdown source text -- headers become <h1>/<h2>/<h3>, "- item"
    becomes <li>, "**bold**" becomes <strong>, "[text](url)" becomes a
    real clickable <a href>. That's what gets sent as the email body
    itself.
    """
    body_html = markdown.markdown(markdown_text, extensions=_MARKDOWN_EXTENSIONS)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>{_EMAIL_CSS}</style>
</head>
<body>
{body_html}
</body>
</html>"""
