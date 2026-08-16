"""Build a plain-text candidate profile from resumes/Baseline Resume.tex for
the fit-scorer prompt.

Baseline.tex is the right source, not one of the company-tailored resumes:
diffing Baseline against a couple of company variants (Virtu, Optiver) shows
they're just re-worded/re-emphasized versions of the same jobs and bullets,
not different underlying experience -- Baseline is the neutral one.

This is a small custom parser for this resume template's specific macros
(\\resumeSubheading, \\resumeItem, \\resumeProjectHeading, \\section), not a
general LaTeX-to-text converter -- that's deliberately narrower in scope
than a real LaTeX parser, but it's grounded in the actual file structure
(all 8 resumes share the same command definitions) rather than a fully
generic solution this task doesn't need.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RESUMES_DIR = REPO_ROOT / "resumes"
BASELINE_RESUME = RESUMES_DIR / "Baseline Resume.tex"

# Sections relevant to a fit judgment. Contact info / header intentionally
# excluded -- not relevant to whether a posting is a good fit.
RELEVANT_SECTIONS = ["Education", "Technical Skills", "Professional Experience", "AI/ML Projects"]


def _strip_comments(text: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", text)


def _clean_inline(text: str) -> str:
    # Unwrap simple one-argument formatting commands, repeatedly in case
    # they're nested (e.g. \textbf{\emph{x}}).
    for _ in range(3):
        text = re.sub(r"\\(textbf|textit|emph|small|scshape)\{([^{}]*)\}", r"\2", text)
    text = re.sub(r"\\href\{[^{}]*\}\{([^{}]*)\}", r"\1", text)
    text = text.replace("\\$", "$").replace("\\%", "%").replace("\\&", "&").replace("\\_", "_")
    text = text.replace("--", "-").replace("\\\\", " ").replace("$|$", "|")
    text = re.sub(r"\\vspace\{[^{}]*\}", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_command_args(text: str, command: str, num_args: int) -> list[tuple[int, int, list[str]]]:
    """Find every `\\command{arg1}{arg2}...` occurrence (brace-matched, so
    nested braces in an argument don't break extraction) and return each
    match's (start_pos, end_pos, args) -- end_pos is right after the last
    argument's closing brace.
    """
    results = []
    for m in re.finditer(r"\\" + re.escape(command) + r"\b", text):
        pos = m.end()
        args = []
        for _ in range(num_args):
            while pos < len(text) and text[pos] in " \t\n":
                pos += 1
            if pos >= len(text) or text[pos] != "{":
                break
            depth = 0
            start = pos
            while pos < len(text):
                if text[pos] == "{":
                    depth += 1
                elif text[pos] == "}":
                    depth -= 1
                    if depth == 0:
                        pos += 1
                        break
                pos += 1
            args.append(text[start + 1 : pos - 1])
        if len(args) == num_args:
            results.append((m.start(), pos, args))
    return results


def _section_slices(text: str) -> list[tuple[str, str]]:
    """Split the document into (section_name, section_body_text) pairs, in order."""
    headers = _extract_command_args(text, "section", 1)
    slices = []
    for i, (_, end_pos, (name,)) in enumerate(headers):
        # body runs from just after this \section{...} call to the start of the next one
        body_start = end_pos
        body_end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
        slices.append((_clean_inline(name), text[body_start:body_end]))
    return slices


def _render_section_body(name: str, body: str) -> str:
    if name == "Technical Skills":
        # This section doesn't use the resume{Item,Subheading} macros --
        # it's raw `\small{\item{ \textbf{Label:} text \\ ... }}`. Pull the
        # \item{...} content out with the same brace-matcher (handles the
        # \textbf{...} nested inside it), then render each `\\`-separated
        # skill line on its own line instead of one clean() pass jamming
        # them together.
        items = _extract_command_args(body, "item", 1)
        content = items[0][2][0] if items else body
        lines = [_clean_inline(line) for line in content.split("\\\\")]
        return "\n".join(line for line in lines if line)

    # Interleave resumeSubheading / resumeItem / resumeProjectHeading in the
    # document order they actually appear in, not grouped by command type.
    commands = {"resumeSubheading": 4, "resumeItem": 1, "resumeProjectHeading": 2}
    found = []
    for cmd, nargs in commands.items():
        for pos, _, args in _extract_command_args(body, cmd, nargs):
            found.append((pos, cmd, args))
    found.sort(key=lambda x: x[0])

    lines = []
    for _, cmd, args in found:
        args = [_clean_inline(a) for a in args]
        if cmd == "resumeSubheading":
            org, dates, title, location = args
            header = f"{org} ({dates})"
            if title:
                header += f" - {title}"
            if location:
                header += f", {location}"
            lines.append(header)
        elif cmd == "resumeProjectHeading":
            title_and_tech, dates = args
            lines.append(title_and_tech + (f" ({dates})" if dates else ""))
        elif cmd == "resumeItem":
            lines.append(f"  - {args[0]}")
    return "\n".join(lines)


def build_profile_summary(tex_path: Path = BASELINE_RESUME) -> str:
    """Return a plain-text candidate profile assembled from the resume's
    Education / Technical Skills / Professional Experience / AI-ML Projects
    sections, for use in the fit-scorer prompt.
    """
    raw = tex_path.read_text()
    raw = _strip_comments(raw)

    parts = []
    for name, body in _section_slices(raw):
        if name not in RELEVANT_SECTIONS:
            continue
        rendered = _render_section_body(name, body)
        if rendered:
            parts.append(f"## {name}\n{rendered}")

    return "\n\n".join(parts)
