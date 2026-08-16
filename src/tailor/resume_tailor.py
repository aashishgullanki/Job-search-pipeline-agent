"""Tailor stage: rewrite resume bullets via Claude Sonnet for score>=8
postings, splice them back into the base .tex, compile to a one-page PDF
via pdflatex with up to 3 retries on overflow/compile errors (architecture
doc section 5).

No "resume-tailor skill" exists anywhere on this machine to reuse verbatim
(checked -- like the Portfolio Agent Apify code the architecture doc
referenced, this turned out to be aspirational, not present), so the
tailoring approach here is built from scratch:

- Bullets, not the whole file, are what an LLM touches. The base .tex is
  parsed with src/common/latex.py's brace-matcher to find every
  \\resumeItem{...} call's exact position; Sonnet only ever sees and
  returns bullet TEXT (never raw LaTeX syntax to write), which get spliced
  back into the *original* document at those exact positions. This avoids
  the far riskier "have the LLM regenerate the whole .tex file" approach,
  where one wrong brace or a "helpful" structural change silently breaks
  the document.
- escape_latex_specials() is a defensive second pass on whatever Sonnet
  returns, regardless of whether the system prompt's escaping instruction
  was followed -- a forgotten "\\%" shouldn't be able to break a compile.
- The compile-loop retries by giving Sonnet the ACTUAL failure back
  (the real pdflatex error line, or "still N pages, must be 1") rather
  than just re-asking blind -- each retry attempt is materially more
  informed than the last, not just a repeat roll.
"""

import re
import subprocess
from pathlib import Path

import anthropic
from pypdf import PdfReader

from src.common.latex import escape_latex_specials, extract_command_args
from src.common.text import extract_description

REPO_ROOT = Path(__file__).resolve().parents[2]
RESUMES_DIR = REPO_ROOT / "resumes"
BASELINE_RESUME = RESUMES_DIR / "Baseline Resume.tex"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "tailored"

MODEL = "claude-sonnet-5"  # architecture doc: Sonnet for tailoring (higher quality writing)
MAX_TOKENS = 2048
MAX_COMPILE_ATTEMPTS = 3
PDFLATEX_TIMEOUT_SECONDS = 30

TAILOR_TOOL = {
    "name": "submit_tailored_content",
    "description": "Submit the rewritten resume bullets and an outreach draft for this posting.",
    "input_schema": {
        "type": "object",
        "properties": {
            "bullets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Rewritten bullets, same count and order as the originals given",
            },
            "outreach_draft": {
                "type": "string",
                "description": "Short (3-5 sentence) cold-outreach message draft",
            },
        },
        "required": ["bullets", "outreach_draft"],
    },
}

SYSTEM_PROMPT = """You are tailoring a candidate's resume bullets for a specific job posting. \
Rewrite each bullet to emphasize the skills, technologies, and achievements most relevant to \
this posting -- but NEVER invent facts, numbers, technologies, or achievements not present in \
the original bullet. Only reword/re-emphasize what's already true. Keep each rewritten bullet \
roughly the same length as the original -- this resume must fit on exactly one page, so making \
bullets longer risks overflow. Preserve the exact LaTeX escaping style used in the originals \
(e.g. \\% for percent signs, \\$ for dollar signs, \\& for ampersands) in your rewritten text.

Also draft a short, generic cold-outreach message -- NOT addressed to any specific named \
person (no contact was looked up for this posting; address it generically, e.g. "Hi,") -- that \
the candidate could send to a recruiter or hiring manager they find themselves, referencing \
genuine fit for this specific role.

Call submit_tailored_content with your rewritten bullets (same count and order as given) and \
the outreach draft."""


def find_base_resume(company: str, resumes_dir: Path = RESUMES_DIR) -> Path:
    """Prefer an existing company-tailored resume if one exists (it's
    already closer to right for that company), else fall back to Baseline.
    """
    candidate = resumes_dir / f"{company} Resume.tex"
    if candidate.exists():
        return candidate
    return resumes_dir / "Baseline Resume.tex"


def extract_bullets(tex: str) -> list[tuple[int, int, str]]:
    """Every \\resumeItem{...} call across the whole document, in order, as
    (start_pos, end_pos, text) -- covers Experience and Projects sections
    alike, not just one.
    """
    return [(s, e, args[0]) for s, e, args in extract_command_args(tex, "resumeItem", 1)]


def splice_bullets(tex: str, bullet_positions: list[tuple[int, int, str]], new_bullets: list[str]) -> str:
    """Replace each \\resumeItem{...} call's content with the corresponding
    new bullet text. Works back-to-front so replacing one bullet doesn't
    shift the stored positions of the ones still to come.
    """
    if len(bullet_positions) != len(new_bullets):
        raise ValueError(
            f"bullet count mismatch: {len(bullet_positions)} positions, {len(new_bullets)} new bullets"
        )
    result = tex
    for (start, end, _), new_text in reversed(list(zip(bullet_positions, new_bullets))):
        escaped = escape_latex_specials(new_text.strip())
        result = result[:start] + f"\\resumeItem{{{escaped}}}" + result[end:]
    return result


def build_tailor_prompt(
    company: str, title: str, location: str, description: str | None, bullets: list[str], feedback: str | None = None
) -> str:
    numbered = "\n".join(f"{i + 1}. {b}" for i, b in enumerate(bullets))
    description_block = description[:3000] if description else "(no description available for this posting)"
    feedback_block = f"\n\nIMPORTANT -- the previous attempt failed: {feedback}\n" if feedback else ""
    return f"""## Job posting

Company: {company}
Title: {title}
Location: {location}

Description:
{description_block}

## Candidate's current resume bullets ({len(bullets)} total -- rewrite ALL of them, same order)

{numbered}
{feedback_block}"""


def parse_tailor_response(response, expected_bullet_count: int) -> dict:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_tailored_content":
            data = block.input
            bullets = data.get("bullets")
            outreach = data.get("outreach_draft")
            if not isinstance(bullets, list) or len(bullets) != expected_bullet_count:
                got = len(bullets) if isinstance(bullets, list) else type(bullets).__name__
                raise ValueError(f"expected {expected_bullet_count} bullets, got {got}")
            if not all(isinstance(b, str) and b.strip() for b in bullets):
                raise ValueError("one or more returned bullets is empty or not a string")
            if not outreach or not isinstance(outreach, str):
                raise ValueError("missing/invalid outreach_draft in tool response")
            return {"bullets": bullets, "outreach_draft": outreach.strip()}
    raise ValueError("no submit_tailored_content tool call found in response")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")
    return slug[:120] or "untitled"


def compile_tex_to_pdf(tex_path: Path, timeout: int = PDFLATEX_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "pdflatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-output-directory",
            str(tex_path.parent),
            str(tex_path),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def extract_latex_error(pdflatex_stdout: str) -> str:
    for line in pdflatex_stdout.splitlines():
        if line.startswith("!"):
            return line
    return "pdflatex failed with a non-zero exit and no '!' error line found in its output"


def get_pdf_page_count(pdf_path: Path) -> int:
    return len(PdfReader(str(pdf_path)).pages)


def tailor_and_compile(
    client: anthropic.Anthropic,
    posting_id: int,
    company: str,
    title: str,
    location: str,
    raw_json: dict,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    max_attempts: int = MAX_COMPILE_ATTEMPTS,
) -> dict:
    """Rewrite bullets, splice into the base resume, compile to a one-page
    PDF, retrying up to `max_attempts` times -- each retry is given the
    real failure reason (compile error or current page count) so it's an
    informed correction, not a blind re-roll.

    Returns {"status": "tailored", "pdf_path", "tex_path", "outreach_draft",
    "attempts"} on success, or {"status": "failed", "reason", "attempts"}
    once max_attempts is exhausted without a clean one-page compile.
    """
    base_path = find_base_resume(company)
    original_tex = base_path.read_text()
    bullet_positions = extract_bullets(original_tex)
    if not bullet_positions:
        return {"status": "failed", "reason": f"no \\resumeItem bullets found in {base_path.name}", "attempts": 0}

    original_bullets = [b[2] for b in bullet_positions]
    description = extract_description(raw_json)

    output_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(f"{company}_{title}_{posting_id}")
    tex_out_path = output_dir / f"{slug}.tex"
    pdf_out_path = output_dir / f"{slug}.pdf"

    feedback = None
    last_error = "unknown failure"

    for attempt in range(1, max_attempts + 1):
        prompt = build_tailor_prompt(company, title, location, description, original_bullets, feedback)
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=[TAILOR_TOOL],
                tool_choice={"type": "tool", "name": "submit_tailored_content"},
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError as e:
            last_error = f"Sonnet call failed: {e}"
            feedback = None
            continue

        try:
            result = parse_tailor_response(response, expected_bullet_count=len(original_bullets))
        except ValueError as e:
            last_error = f"invalid tool response: {e}"
            feedback = str(e)
            continue

        new_tex = splice_bullets(original_tex, bullet_positions, result["bullets"])
        tex_out_path.write_text(new_tex)

        try:
            compile_result = compile_tex_to_pdf(tex_out_path)
        except subprocess.TimeoutExpired:
            last_error = "pdflatex timed out"
            feedback = "The previous LaTeX took too long to compile -- simplify the content."
            continue

        if compile_result.returncode != 0 or not pdf_out_path.exists():
            error_line = extract_latex_error(compile_result.stdout)
            last_error = f"compile error: {error_line}"
            feedback = (
                f"The previous version failed to compile with this LaTeX error: {error_line}. "
                "Check special-character escaping in your bullets."
            )
            continue

        page_count = get_pdf_page_count(pdf_out_path)
        if page_count > 1:
            last_error = f"resume is {page_count} pages, must be 1"
            feedback = (
                f"The resume compiled but is currently {page_count} pages -- it must fit on exactly "
                "1 page. Shorten the bullets significantly, prioritizing the most relevant ones for "
                "this specific posting over less relevant ones."
            )
            continue

        return {
            "status": "tailored",
            "pdf_path": str(pdf_out_path),
            "tex_path": str(tex_out_path),
            "outreach_draft": result["outreach_draft"],
            "attempts": attempt,
        }

    return {"status": "failed", "reason": last_error, "attempts": max_attempts}
