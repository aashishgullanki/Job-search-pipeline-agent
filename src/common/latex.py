"""Shared LaTeX parsing primitives for this resume template's macros.

Not a general LaTeX parser -- scoped to the specific custom commands used
across all 8 files in resumes/ (\\resumeSubheading, \\resumeItem,
\\resumeProjectHeading, \\section). Originally lived only in
src/score/profile.py; pulled out here once src/tailor/ needed the same
brace-matching extraction to find and replace individual bullet contents
without touching the rest of the document.
"""

import re


def strip_comments(text: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", text)


def extract_command_args(text: str, command: str, num_args: int) -> list[tuple[int, int, list[str]]]:
    """Find every `\\command{arg1}{arg2}...` occurrence (brace-matched, so
    nested braces in an argument don't break extraction) and return each
    match's (start_pos, end_pos, args) -- start_pos is where `\\command`
    itself begins, end_pos is right after the last argument's closing brace.
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


def escape_latex_specials(text: str) -> str:
    """Defensively escape %, $, &, # if not already escaped.

    Used on LLM-authored replacement text before splicing it into a .tex
    file -- a model asked to "preserve the escaping style" mostly will, but
    this is a safety net against a compile break if it forgets, not a
    substitute for the instruction.
    """
    for ch in ["%", "$", "&", "#"]:
        text = re.sub(r"(?<!\\)" + re.escape(ch), "\\" + ch, text)
    return text


def unescape_latex_specials(text: str) -> str:
    """Inverse of escape_latex_specials -- for showing existing .tex content
    to an LLM in readable form before it gets re-escaped on the way back out.
    """
    return text.replace("\\%", "%").replace("\\$", "$").replace("\\&", "&").replace("\\#", "#")


def split_top_level_commas(text: str) -> list[str]:
    """Comma-split that doesn't split inside parentheses.

    Needed for skill lists like "LLM Integration (OpenAI, Anthropic), RAG"
    -- a naive text.split(",") would wrongly break "(OpenAI" and " Anthropic)"
    into two separate items.
    """
    parts = []
    depth = 0
    current = []
    for ch in text:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]
