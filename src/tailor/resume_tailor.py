"""Tailor stage: rewrite a resume for score>=8 postings using the real
resume-tailor skill's methodology, compile to a one-page PDF via pdflatex
with up to 3 retries on overflow/compile errors (architecture doc section 5).

No skill existed anywhere obviously discoverable on this machine at first
look -- checked ~/.claude/skills (doesn't exist) and got a false negative
from a first-pass filename search. A slower background search later turned
up the real one: ~/Downloads/resume-tailor.yaml, which despite its
extension is actually a zip archive containing resume-tailor/SKILL.md -- a
keyword-categorization methodology from a career-services skill (RED =
technical skills, BLUE = soft skills, YELLOW = tools/frameworks, GREEN =
hard requirements), instructing bullet reordering within each experience
(most relevant first), Technical Skills updates with genuinely-known tools,
strong action verbs, no fabrication ever, and a required "Tailoring
Summary" of changes made. This module implements that methodology --
extended per explicit direction to go for full fidelity rather than a
simplified version.

Architecture: the LLM never touches raw LaTeX syntax, only structured
content (bullet text per block, skill categories, a summary). Positions of
every \\resumeSubheading / \\resumeProjectHeading / \\resumeItem /
Technical-Skills-\\item block are found via src/common/latex.py's
brace-matcher, and the LLM's structured response is spliced back into the
*original* document at those exact positions. This was a deliberate choice
over having the LLM regenerate the whole .tex file -- one wrong brace or a
"helpful" structural change would silently corrupt the document, with no
clean way to test that deterministically. Three hard guardrails enforce
rules programmatically, not just via prompt instruction: any skill item
the LLM proposes that doesn't already appear somewhere in the original
resume's skills or bullets is rejected outright (triggering a retry, not
a silent fabrication); any item that carried a "(coursework: ...)"
annotation in the original keeps that annotation even if the LLM's
response drops it; and any tailoring_summary line claiming a block/
section-level reorder is rejected, since the splice architecture only
ever reorders bullets within a fixed block and never blocks/sections
relative to each other -- found live (Point72's "NLP / AI Engineer"
summary claimed "reordered project blocks" when the actual document's
block order never changed) via a manual comparison-report audit across
29 postings, not caught by the original test suite.
"""

import re
import shutil
import subprocess
from pathlib import Path

import anthropic
from pypdf import PdfReader

from src.common.latex import (
    escape_latex_specials,
    extract_command_args,
    split_top_level_commas,
    unescape_latex_specials,
)
from src.common.text import extract_description

REPO_ROOT = Path(__file__).resolve().parents[2]
RESUMES_DIR = REPO_ROOT / "resumes"
BASELINE_RESUME = RESUMES_DIR / "Baseline Resume.tex"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "tailored"

MODEL = "claude-sonnet-5"  # architecture doc: Sonnet for tailoring (higher quality writing)
MAX_TOKENS = 4096
MAX_COMPILE_ATTEMPTS = 3
PDFLATEX_TIMEOUT_SECONDS = 30

COURSEWORK_RE = re.compile(r"^(.*?)\s*(\(coursework:[^()]*\))\s*$", re.IGNORECASE)


def find_base_resume(company: str, resumes_dir: Path = RESUMES_DIR) -> Path:
    """Prefer an existing company-tailored resume if one exists (it's
    already closer to right for that company), else fall back to Baseline.
    """
    candidate = resumes_dir / f"{company} Resume.tex"
    if candidate.exists():
        return candidate
    return resumes_dir / "Baseline Resume.tex"


# --- extraction -------------------------------------------------------


def extract_blocks(tex: str) -> list[dict]:
    """Group each \\resumeSubheading / \\resumeProjectHeading with the
    \\resumeItem bullets that immediately follow it, in document order.
    Each block: {"heading_type", "heading_args", "bullets": [(start,end,text),...]}.
    Bullets belong to whichever heading precedes them and before the next one.
    """
    headings = []
    for pos, end, args in extract_command_args(tex, "resumeSubheading", 4):
        headings.append((pos, end, "resumeSubheading", args))
    for pos, end, args in extract_command_args(tex, "resumeProjectHeading", 2):
        headings.append((pos, end, "resumeProjectHeading", args))
    headings.sort(key=lambda h: h[0])

    all_bullets = extract_command_args(tex, "resumeItem", 1)

    blocks = []
    for i, (h_start, h_end, h_type, h_args) in enumerate(headings):
        block_end = headings[i + 1][0] if i + 1 < len(headings) else len(tex)
        block_bullets = [(s, e, a[0]) for s, e, a in all_bullets if h_end <= s < block_end]
        if not block_bullets:
            continue
        blocks.append({"heading_type": h_type, "heading_args": h_args, "bullets": block_bullets})
    return blocks


def extract_skills_block(tex: str) -> dict | None:
    """Find the Technical Skills section's \\item{...} content and parse it
    into categories. Returns {"start", "end", "categories": [{"label","items"}]}
    (start/end span the \\item{...}'s content, not including the braces
    themselves) or None if no Technical Skills section/item is found.
    """
    sections = extract_command_args(tex, "section", 1)
    skills_idx = next((i for i, (_, _, (name,)) in enumerate(sections) if name.strip() == "Technical Skills"), None)
    if skills_idx is None:
        return None

    body_start = sections[skills_idx][1]
    body_end = sections[skills_idx + 1][0] if skills_idx + 1 < len(sections) else len(tex)
    body = tex[body_start:body_end]

    items = extract_command_args(body, "item", 1)
    if not items:
        return None
    item_start, item_end, (content,) = items[0]

    categories = []
    for line in content.split("\\\\"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"\\textbf\{([^{}]*)\}\s*(.*)", line, re.DOTALL)
        if not m:
            continue
        label = unescape_latex_specials(m.group(1)).rstrip(":").strip()
        raw_items = split_top_level_commas(m.group(2).strip())
        categories.append({"label": label, "items": [unescape_latex_specials(it) for it in raw_items]})

    return {"start": body_start + item_start, "end": body_start + item_end, "categories": categories}


def _split_coursework(item: str) -> tuple[str, str | None]:
    m = COURSEWORK_RE.match(item)
    if m:
        return m.group(1).strip(), m.group(2)
    return item.strip(), None


# --- validation (the "never fabricate" guardrails) ---------------------


def build_known_skill_tokens(original_categories: list[dict], blocks: list[dict]) -> tuple[set[str], str]:
    """Every skill/tool token the candidate's actual resume already
    establishes -- from the current skills list, and from bullet text
    (catches tools mentioned in a bullet but not listed in Skills, e.g.
    "Kafka" appearing in an Experience bullet).
    """
    tokens = set()
    for cat in original_categories:
        for item in cat["items"]:
            base, _ = _split_coursework(item)
            tokens.add(base.lower())
    bullet_text = " ".join(b[2] for block in blocks for b in block["bullets"]).lower()
    return tokens, bullet_text


def validate_no_fabricated_skills(new_categories: list[dict], known_tokens: set[str], known_bullet_text: str) -> None:
    for cat in new_categories:
        for item in cat["items"]:
            base, _ = _split_coursework(item)
            base_lower = base.lower().strip()
            if not base_lower:
                continue
            if base_lower in known_tokens or base_lower in known_bullet_text:
                continue
            raise ValueError(
                f"proposed skill '{item}' does not appear anywhere in the original resume "
                "(skills list or bullets) -- this looks fabricated and was rejected"
            )


# Found live: Point72's "NLP / AI Engineer" tailoring_summary claimed
# "Reordered project blocks so AI Portfolio Agent... leads over ML Trading
# Strategy block" -- the splice architecture only ever reorders bullets
# WITHIN a block, never blocks/sections relative to each other, so this
# described something structurally impossible. Tested against all 210 real
# summary lines from a live 29-posting run before wiring in: exactly this
# one line matched, zero false positives against legitimate phrasing like
# "Reordered block_0 to lead with..." (a specific block ID, not a claim
# about relative block/section position).
_BLOCK_REORDER_CLAIM_PATTERNS = [
    re.compile(r"reorder(?:ed|ing)?\s+(?:the\s+)?(?:project\s+)?blocks\b", re.IGNORECASE),
    re.compile(r"reorder(?:ed|ing)?\s+(?:the\s+)?sections\b", re.IGNORECASE),
    re.compile(r"\bblocks?\s+order\b", re.IGNORECASE),
    re.compile(r"\bsections?\s+order\b", re.IGNORECASE),
    re.compile(r"mov(?:ed|ing)\s+[\w\s]{2,40}\bblocks?\b\s+(?:before|after|above|below|ahead of)", re.IGNORECASE),
    re.compile(r"swap(?:ped|ping)?\s+(?:the\s+)?(?:order of\s+)?[\w\s]{2,40}\b(?:blocks?|sections?)\b", re.IGNORECASE),
    re.compile(r"leads?\s+over\s+[\w\s]{2,40}\b(?:blocks?|sections?|projects?)\b", re.IGNORECASE),
]


def validate_no_block_reorder_claims(tailoring_summary: list[str]) -> None:
    """Reject a summary line claiming a block/section-level reorder -- the
    splice architecture can't do that, only reorder bullets within a fixed
    block, so a claim like this is either wrong or describes a change that
    didn't actually happen in the document.
    """
    for line in tailoring_summary:
        for pattern in _BLOCK_REORDER_CLAIM_PATTERNS:
            if pattern.search(line):
                raise ValueError(
                    "tailoring_summary claims a block/section-level reorder, which this "
                    f"architecture cannot do (only bullets within a block are ever reordered, "
                    f"never blocks or sections relative to each other): {line!r}"
                )


def enforce_coursework_annotations(new_categories: list[dict], original_categories: list[dict]) -> list[dict]:
    """A skill originally flagged '(coursework: ...)' must keep that
    annotation even if the LLM's response dropped it -- auto-corrected
    rather than just rejected, since it's a small, deterministic fix.
    """
    coursework_by_token: dict[str, str] = {}
    for cat in original_categories:
        for item in cat["items"]:
            base, annotation = _split_coursework(item)
            if annotation:
                coursework_by_token[base.lower().strip()] = annotation

    fixed = []
    for cat in new_categories:
        fixed_items = []
        for item in cat["items"]:
            base, annotation = _split_coursework(item)
            required = coursework_by_token.get(base.lower().strip())
            if required and not annotation:
                item = f"{base} {required}"
            fixed_items.append(item)
        fixed.append({"label": cat["label"], "items": fixed_items})
    return fixed


# --- prompt / tool schema ------------------------------------------------

TAILOR_TOOL = {
    "name": "submit_tailored_content",
    "description": "Submit the tailored resume content and outreach draft for this posting.",
    "input_schema": {
        "type": "object",
        "properties": {
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "block_id": {"type": "string"},
                        "bullets": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["block_id", "bullets"],
                },
                "description": "One entry per experience/project block, same block_id as given",
            },
            "skills": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "items": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["label", "items"],
                },
                "description": "Full replacement Technical Skills categories",
            },
            "tailoring_summary": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Bullet list of the key changes made and why",
            },
            "outreach_draft": {
                "type": "string",
                "description": "Short (3-5 sentence) generic cold-outreach message draft",
            },
        },
        "required": ["blocks", "skills", "tailoring_summary", "outreach_draft"],
    },
}

SYSTEM_PROMPT = """You are tailoring a candidate's resume for a specific job posting, following \
this methodology (from a career-services resume-tailoring skill):

CORE PHILOSOPHY
- Recruiters are not technical -- they scan for keywords matching what the posting asked for.
- ATS systems filter candidates before any human sees them. Keyword density and relevance matter.
- It's not about what's impressive to the candidate -- it's about what the posting actually asked for.
- Soft skills matter as much as technical ones.

STEP 1 -- Annotate the job posting (internally): categorize meaningful phrases into RED \
(technical skills / transferable technical experience), BLUE (soft skills), YELLOW (specific \
tools/languages/frameworks), GREEN (hard requirements/must-haves). Don't skip the "About Us" / \
"About the Role" intro paragraphs -- they carry keywords too.

STEP 2 -- Map those keywords to the candidate's existing experience (given below, grouped into \
blocks). Not every experience will make the cut -- surface what's most relevant to THIS posting.

STEP 3 -- Rewrite bullets, per block:
- Lead with strong action verbs.
- Incorporate RED and BLUE keywords naturally -- don't just append them awkwardly.
- Use quantified results wherever the original had them -- NEVER invent numbers.
- Reorder bullets WITHIN each block so the most relevant to this posting come first.
- NEVER fabricate skills, technologies, or achievements not present in the original bullet.
- NEVER copy job description language verbatim as if it's the candidate's own accomplishment.
- Keep each bullet roughly the same length as the original -- this resume must fit exactly one page.
- Return the SAME number of bullets per block as given (reordered/reworded, not added or dropped) \
unless you are told otherwise below because a previous attempt overflowed one page.
- IMPORTANT: the blocks themselves (each Experience or Project entry) stay in the exact order \
given -- you can only reorder the bullets inside a block, never move a whole block/project/section \
ahead of or behind another one. If a project block feels less relevant than another, express that \
by how much you reword its bullets toward or away from the posting, not by claiming to have moved it.

STEP 4 -- Update Technical Skills: reorder/recategorize the given categories to surface what's \
most relevant to this posting first. You may ONLY include a skill/tool/language that ALREADY \
appears somewhere in the resume content given to you (the current skills list or some bullet) -- \
do not add anything not already established. If a given skill item carries a \
"(coursework: ...)" annotation, any output that includes that same skill must keep that exact \
annotation -- never let a reorder imply production experience where the original only claimed \
coursework.

STEP 5 -- Produce a short "Tailoring Summary": a list of the key changes made and why (e.g. \
"Reordered the Kafka bullet to lead in the C. Mack Solutions block -- posting emphasizes \
distributed systems"). Only describe changes you actually made: bullet reordering/rewording \
within a block, and Technical Skills changes. NEVER describe reordering blocks, sections, or \
projects relative to each other -- that's not something this process does, so don't claim it \
happened even if it would have made sense to do.

Also draft a short, generic cold-outreach message (not addressed to a specific named person -- \
no contact was looked up for this posting; address it generically, e.g. "Hi,").

Call submit_tailored_content with your blocks (same block_id as given), skills (full replacement \
category list), tailoring_summary, and outreach_draft. Preserve the exact LaTeX escaping style \
used in the originals (\\% for percent, \\$ for dollar signs, \\& for ampersands)."""


def _format_block_for_prompt(i: int, block: dict) -> str:
    args = block["heading_args"]
    if block["heading_type"] == "resumeSubheading":
        org, dates, title, _location = [unescape_latex_specials(a) for a in args]
        label = f"{org} ({dates}) - {title}" if title else f"{org} ({dates})"
    else:
        title_and_tech, dates = [unescape_latex_specials(a) for a in args]
        label = f"{title_and_tech} ({dates})" if dates else title_and_tech
    bullets = "\n".join(f"  {j + 1}. {unescape_latex_specials(b[2])}" for j, b in enumerate(block["bullets"]))
    return f"block_{i} -- {label}\n{bullets}"


def build_tailor_prompt(
    company: str,
    title: str,
    location: str,
    description: str | None,
    blocks: list[dict],
    skills_categories: list[dict],
    feedback: str | None = None,
) -> str:
    description_block = description[:3000] if description else "(no description available for this posting)"
    blocks_block = "\n\n".join(_format_block_for_prompt(i, b) for i, b in enumerate(blocks))
    skills_lines = "\n".join(f"  {c['label']}: {', '.join(c['items'])}" for c in skills_categories)
    feedback_block = f"\n\nIMPORTANT -- the previous attempt failed: {feedback}\n" if feedback else ""
    return f"""## Job posting

Company: {company}
Title: {title}
Location: {location}

Description:
{description_block}

## Candidate's current resume, by block (rewrite bullets within each block, same block_id, same \
bullet count unless told otherwise below)

{blocks_block}

## Candidate's current Technical Skills

{skills_lines}
{feedback_block}"""


def parse_tailor_response(response, blocks: list[dict], allow_fewer_bullets: bool = False) -> dict:
    for content_block in response.content:
        if getattr(content_block, "type", None) == "tool_use" and content_block.name == "submit_tailored_content":
            data = content_block.input
            new_blocks = data.get("blocks")
            new_skills = data.get("skills")
            summary = data.get("tailoring_summary")
            outreach = data.get("outreach_draft")

            if not isinstance(new_blocks, list) or len(new_blocks) != len(blocks):
                got = len(new_blocks) if isinstance(new_blocks, list) else type(new_blocks).__name__
                raise ValueError(f"expected {len(blocks)} blocks, got {got}")

            by_id = {b.get("block_id"): b.get("bullets") for b in new_blocks}
            for i, block in enumerate(blocks):
                block_id = f"block_{i}"
                bullets = by_id.get(block_id)
                original_count = len(block["bullets"])
                if not isinstance(bullets, list) or not bullets:
                    raise ValueError(f"{block_id}: missing or empty bullets list")
                if not all(isinstance(b, str) and b.strip() for b in bullets):
                    raise ValueError(f"{block_id}: one or more bullets is empty or not a string")
                if allow_fewer_bullets:
                    if len(bullets) > original_count:
                        raise ValueError(f"{block_id}: expected at most {original_count} bullets, got {len(bullets)}")
                elif len(bullets) != original_count:
                    raise ValueError(f"{block_id}: expected exactly {original_count} bullets, got {len(bullets)}")

            if not isinstance(new_skills, list) or not new_skills:
                raise ValueError("missing/empty skills in tool response")
            for cat in new_skills:
                if not isinstance(cat, dict) or not cat.get("label") or not cat.get("items"):
                    raise ValueError(f"malformed skills category: {cat!r}")

            if not isinstance(summary, list) or not summary:
                raise ValueError("missing/empty tailoring_summary")
            if not outreach or not isinstance(outreach, str):
                raise ValueError("missing/invalid outreach_draft")

            return {
                "blocks": {f"block_{i}": by_id[f"block_{i}"] for i in range(len(blocks))},
                "skills": new_skills,
                "tailoring_summary": summary,
                "outreach_draft": outreach.strip(),
            }
    raise ValueError("no submit_tailored_content tool call found in response")


# --- splicing ------------------------------------------------------------


def splice_tailored_content(
    tex: str,
    blocks: list[dict],
    skills_span: dict | None,
    new_blocks: dict[str, list[str]],
    new_skills: list[dict],
) -> str:
    """Replace each block's bullet span and the skills \\item{...} content
    in the original document, working back-to-front by position so earlier
    replacements don't shift positions still to come.
    """
    edits: list[tuple[int, int, str]] = []

    for i, block in enumerate(blocks):
        new_bullets = new_blocks.get(f"block_{i}")
        if not new_bullets:
            continue
        span_start = block["bullets"][0][0]
        span_end = block["bullets"][-1][1]
        rendered = "\n  ".join(f"\\resumeItem{{{escape_latex_specials(b.strip())}}}" for b in new_bullets)
        edits.append((span_start, span_end, rendered))

    if skills_span and new_skills:
        rendered_lines = []
        for cat in new_skills:
            label = escape_latex_specials(cat["label"])
            items = ", ".join(escape_latex_specials(it) for it in cat["items"])
            rendered_lines.append(f"\\textbf{{{label}:}} {items}")
        # skills_span covers the WHOLE \item{...} call (extract_skills_block
        # returns extract_command_args' span, which starts at "\item" itself,
        # not just its inner content) -- the replacement must re-wrap in
        # \item{...} or the itemize environment ends up with no \item command
        # in it at all. Found live: every single tailored posting failed to
        # compile with "missing \item" before this fix.
        rendered = "\\item{" + " \\\\\n     ".join(rendered_lines) + "}"
        edits.append((skills_span["start"], skills_span["end"], rendered))

    edits.sort(key=lambda e: e[0], reverse=True)
    result = tex
    for start, end, replacement in edits:
        result = result[:start] + replacement + result[end:]
    return result


# --- compile ------------------------------------------------------------

# Common macOS TeX install locations, checked when a bare "pdflatex" isn't
# on PATH -- e.g. a fresh BasicTeX/MacTeX install whose path_helper update
# hasn't propagated to an already-running shell (this harness doesn't
# persist env vars between tool calls at all, so relying on the caller's
# PATH being right is fragile regardless).
_PDFLATEX_FALLBACK_PATHS = [
    "/Library/TeX/texbin/pdflatex",
    "/usr/local/texlive/2026basic/bin/universal-darwin/pdflatex",
    "/usr/local/bin/pdflatex",
]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")
    return slug[:120] or "untitled"


def find_pdflatex() -> str:
    """Resolve the pdflatex binary: PATH first, then known install
    locations. Raises FileNotFoundError with an actionable message if
    neither turns anything up.
    """
    on_path = shutil.which("pdflatex")
    if on_path:
        return on_path
    for candidate in _PDFLATEX_FALLBACK_PATHS:
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError(
        "pdflatex not found on PATH or in known install locations -- "
        "install a LaTeX distribution (e.g. `brew install --cask basictex`)"
    )


def compile_tex_to_pdf(tex_path: Path, timeout: int = PDFLATEX_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            find_pdflatex(),
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


# --- orchestration --------------------------------------------------------


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
    """Full-fidelity tailor: per-block bullet reorder/reword, Technical
    Skills update (validated against fabrication, coursework-annotation
    preserved), tailoring summary (validated against claiming an impossible
    block-level reorder), splice into the original .tex, compile to a
    one-page PDF -- retrying up to `max_attempts` times with the real
    failure fed back each time (compile error, current page count, a named
    fabricated-skill rejection, or a named block-reorder-claim rejection).

    Returns {"status": "tailored", "pdf_path", "tex_path", "outreach_draft",
    "tailoring_summary", "attempts"} on success, or {"status": "failed",
    "reason", "attempts"} once max_attempts is exhausted.
    """
    base_path = find_base_resume(company)
    original_tex = base_path.read_text()

    blocks = extract_blocks(original_tex)
    if not blocks:
        return {"status": "failed", "reason": f"no resume blocks found in {base_path.name}", "attempts": 0}

    skills_span = extract_skills_block(original_tex)
    original_categories = skills_span["categories"] if skills_span else []
    known_tokens, known_bullet_text = build_known_skill_tokens(original_categories, blocks)

    description = extract_description(raw_json)

    output_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(f"{company}_{title}_{posting_id}")
    tex_out_path = output_dir / f"{slug}.tex"
    pdf_out_path = output_dir / f"{slug}.pdf"

    feedback = None
    last_error = "unknown failure"
    saw_overflow = False

    for attempt in range(1, max_attempts + 1):
        prompt = build_tailor_prompt(company, title, location, description, blocks, original_categories, feedback)
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
            result = parse_tailor_response(response, blocks, allow_fewer_bullets=saw_overflow)
            new_categories = enforce_coursework_annotations(result["skills"], original_categories)
            validate_no_fabricated_skills(new_categories, known_tokens, known_bullet_text)
            validate_no_block_reorder_claims(result["tailoring_summary"])
        except ValueError as e:
            last_error = f"invalid tool response: {e}"
            feedback = str(e)
            continue

        new_tex = splice_tailored_content(original_tex, blocks, skills_span, result["blocks"], new_categories)
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
                "Check special-character escaping."
            )
            continue

        page_count = get_pdf_page_count(pdf_out_path)
        if page_count > 1:
            saw_overflow = True
            last_error = f"resume is {page_count} pages, must be 1"
            feedback = (
                f"The resume compiled but is currently {page_count} pages -- it must fit on exactly "
                "1 page. You may now drop the lowest-priority bullet(s) within a block (never below "
                "1 per block) in addition to shortening bullets, prioritizing what's most relevant to "
                "this posting."
            )
            continue

        return {
            "status": "tailored",
            "pdf_path": str(pdf_out_path),
            "tex_path": str(tex_out_path),
            "outreach_draft": result["outreach_draft"],
            "tailoring_summary": result["tailoring_summary"],
            "attempts": attempt,
        }

    return {"status": "failed", "reason": last_error, "attempts": max_attempts}
