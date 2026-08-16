"""Tailor stage tests -- entirely mocked (Anthropic client, pdflatex
subprocess, PDF page-counting). No real compile, no real API calls.

Fixture (tests/fixtures/sample_resume.tex) has:
  - block_0: Test Corp, 2 bullets ("Built a thing...", "Shipped another...")
  - block_1: Cool Project, 1 bullet ("Trained a model...")
  - Skills: Languages [Python, Go, Rust (coursework: Systems Programming)],
    AI/ML [PyTorch, RAG]
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.tailor.resume_tailor as rt

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SAMPLE_RESUME = FIXTURES / "sample_resume.tex"
SAMPLE_TEX = SAMPLE_RESUME.read_text()


# --- find_base_resume ---


def test_find_base_resume_prefers_company_specific_file(tmp_path):
    (tmp_path / "Baseline Resume.tex").write_text("baseline")
    (tmp_path / "Acme Resume.tex").write_text("acme-specific")
    assert rt.find_base_resume("Acme", resumes_dir=tmp_path).name == "Acme Resume.tex"


def test_find_base_resume_falls_back_to_baseline_when_no_company_file(tmp_path):
    (tmp_path / "Baseline Resume.tex").write_text("baseline")
    assert rt.find_base_resume("NoResumeCo", resumes_dir=tmp_path).name == "Baseline Resume.tex"


# --- extract_blocks ---


def test_extract_blocks_groups_bullets_under_correct_heading():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    assert len(blocks) == 2
    assert blocks[0]["heading_type"] == "resumeSubheading"
    assert blocks[0]["heading_args"][0] == "Test Corp"
    assert [b[2] for b in blocks[0]["bullets"]] == [
        "Built a thing that did stuff, cutting latency by 50\\%.",
        "Shipped another thing using Kafka and \\$100K+ budget.",
    ]
    assert blocks[1]["heading_type"] == "resumeProjectHeading"
    assert [b[2] for b in blocks[1]["bullets"]] == ["Trained a model that did something 90\\% of the time."]


def test_extract_blocks_skips_headings_with_no_bullets():
    # Education's \resumeSubheading has no \resumeItem bullets following it
    blocks = rt.extract_blocks(SAMPLE_TEX)
    orgs = [b["heading_args"][0] for b in blocks]
    assert "Test University" not in orgs


# --- extract_skills_block ---


def test_extract_skills_block_parses_categories_and_items():
    skills = rt.extract_skills_block(SAMPLE_TEX)
    labels = [c["label"] for c in skills["categories"]]
    assert labels == ["Languages", "AI/ML"]
    assert skills["categories"][0]["items"] == ["Python", "Go", "Rust (coursework: Systems Programming)"]
    assert skills["categories"][1]["items"] == ["PyTorch", "RAG"]


def test_extract_skills_block_returns_none_when_no_technical_skills_section():
    tex = "\\section{Education}\\resumeSubheading{A}{B}{C}{D}"
    assert rt.extract_skills_block(tex) is None


# --- known-skill-token building + fabrication validation ---


def test_build_known_skill_tokens_includes_skills_and_bullet_text():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    skills = rt.extract_skills_block(SAMPLE_TEX)
    tokens, bullet_text = rt.build_known_skill_tokens(skills["categories"], blocks)

    assert "python" in tokens
    assert "rust (coursework: systems programming)" not in tokens  # stored without the annotation
    assert "rust" in tokens
    assert "kafka" in bullet_text  # appears in a bullet, not the skills list


def test_validate_no_fabricated_skills_passes_for_known_items():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    skills = rt.extract_skills_block(SAMPLE_TEX)
    tokens, bullet_text = rt.build_known_skill_tokens(skills["categories"], blocks)

    # "Kafka" only appears in a bullet, not the skills list -- still valid, not fabricated
    new_categories = [{"label": "Tools", "items": ["Kafka", "Python"]}]
    rt.validate_no_fabricated_skills(new_categories, tokens, bullet_text)  # should not raise


def test_validate_no_fabricated_skills_rejects_unknown_item():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    skills = rt.extract_skills_block(SAMPLE_TEX)
    tokens, bullet_text = rt.build_known_skill_tokens(skills["categories"], blocks)

    new_categories = [{"label": "Languages", "items": ["Python", "Kubernetes"]}]
    with pytest.raises(ValueError, match="Kubernetes.*fabricated"):
        rt.validate_no_fabricated_skills(new_categories, tokens, bullet_text)


# --- coursework annotation enforcement ---


def test_enforce_coursework_annotations_restores_dropped_annotation():
    original = [{"label": "Languages", "items": ["Python", "Go", "Rust (coursework: Systems Programming)"]}]
    new = [{"label": "Languages", "items": ["Rust", "Python"]}]  # LLM dropped the annotation

    fixed = rt.enforce_coursework_annotations(new, original)

    items = fixed[0]["items"]
    assert "Rust (coursework: Systems Programming)" in items
    assert "Python" in items


def test_enforce_coursework_annotations_leaves_correctly_annotated_items_alone():
    original = [{"label": "Languages", "items": ["Rust (coursework: Systems Programming)"]}]
    new = [{"label": "Languages", "items": ["Rust (coursework: Systems Programming)"]}]

    fixed = rt.enforce_coursework_annotations(new, original)

    assert fixed[0]["items"] == ["Rust (coursework: Systems Programming)"]


def test_enforce_coursework_annotations_does_not_affect_non_coursework_items():
    original = [{"label": "Languages", "items": ["Python", "Rust (coursework: Systems Programming)"]}]
    new = [{"label": "Languages", "items": ["Python"]}]

    fixed = rt.enforce_coursework_annotations(new, original)

    assert fixed[0]["items"] == ["Python"]  # Python never had an annotation to restore


# --- build_tailor_prompt ---


def test_prompt_includes_job_fields_blocks_and_skills():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    skills = rt.extract_skills_block(SAMPLE_TEX)["categories"]
    prompt = rt.build_tailor_prompt("Acme", "SWE", "NYC", "Build things", blocks, skills)

    assert "Acme" in prompt
    assert "Build things" in prompt
    assert "block_0" in prompt
    assert "Test Corp" in prompt
    assert "Built a thing that did stuff" in prompt
    assert "block_1" in prompt
    assert "Languages: Python, Go, Rust (coursework: Systems Programming)" in prompt


def test_prompt_includes_feedback_when_retrying():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    skills = rt.extract_skills_block(SAMPLE_TEX)["categories"]
    prompt = rt.build_tailor_prompt("Acme", "SWE", "NYC", "desc", blocks, skills, feedback="still 2 pages")
    assert "still 2 pages" in prompt
    assert "previous attempt failed" in prompt


# --- parse_tailor_response ---


def _tool_response(input_data):
    block = SimpleNamespace(type="tool_use", name="submit_tailored_content", input=input_data)
    return SimpleNamespace(content=[block])


def _valid_payload():
    return {
        "blocks": [
            {"block_id": "block_0", "bullets": ["New bullet A", "New bullet B"]},
            {"block_id": "block_1", "bullets": ["New bullet C"]},
        ],
        "skills": [{"label": "Languages", "items": ["Python"]}],
        "tailoring_summary": ["Reworded bullet A for relevance."],
        "outreach_draft": "Hi, ...",
    }


def test_parse_tailor_response_extracts_everything():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    response = _tool_response(_valid_payload())

    result = rt.parse_tailor_response(response, blocks)

    assert result["blocks"] == {"block_0": ["New bullet A", "New bullet B"], "block_1": ["New bullet C"]}
    assert result["skills"] == [{"label": "Languages", "items": ["Python"]}]
    assert result["tailoring_summary"] == ["Reworded bullet A for relevance."]
    assert result["outreach_draft"] == "Hi, ..."


def test_parse_tailor_response_rejects_wrong_block_count():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    payload = _valid_payload()
    payload["blocks"] = payload["blocks"][:1]  # missing block_1
    with pytest.raises(ValueError, match="expected 2 blocks"):
        rt.parse_tailor_response(_tool_response(payload), blocks)


def test_parse_tailor_response_rejects_wrong_bullet_count_by_default():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    payload = _valid_payload()
    payload["blocks"][0]["bullets"] = ["Only one bullet now"]  # block_0 originally had 2
    with pytest.raises(ValueError, match="block_0.*exactly 2"):
        rt.parse_tailor_response(_tool_response(payload), blocks, allow_fewer_bullets=False)


def test_parse_tailor_response_allows_fewer_bullets_when_flagged():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    payload = _valid_payload()
    payload["blocks"][0]["bullets"] = ["Only one bullet now"]
    result = rt.parse_tailor_response(_tool_response(payload), blocks, allow_fewer_bullets=True)
    assert result["blocks"]["block_0"] == ["Only one bullet now"]


def test_parse_tailor_response_rejects_more_bullets_even_when_fewer_allowed():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    payload = _valid_payload()
    payload["blocks"][1]["bullets"] = ["extra 1", "extra 2"]  # block_1 originally had 1
    with pytest.raises(ValueError, match="block_1.*at most 1"):
        rt.parse_tailor_response(_tool_response(payload), blocks, allow_fewer_bullets=True)


def test_parse_tailor_response_rejects_missing_summary():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    payload = _valid_payload()
    payload["tailoring_summary"] = []
    with pytest.raises(ValueError, match="tailoring_summary"):
        rt.parse_tailor_response(_tool_response(payload), blocks)


def test_parse_tailor_response_raises_when_no_tool_call():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="nope")])
    with pytest.raises(ValueError, match="no submit_tailored_content"):
        rt.parse_tailor_response(response, blocks)


# --- splice_tailored_content ---


def test_splice_replaces_bullets_and_skills_preserving_the_rest():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    skills_span = rt.extract_skills_block(SAMPLE_TEX)
    new_blocks = {"block_0": ["Rewritten A", "Rewritten B"], "block_1": ["Rewritten C"]}
    new_skills = [{"label": "Languages", "items": ["Python", "Go"]}]

    result = rt.splice_tailored_content(SAMPLE_TEX, blocks, skills_span, new_blocks, new_skills)

    assert "Rewritten A" in result
    assert "Rewritten C" in result
    assert "Built a thing that did stuff" not in result
    assert "\\textbf{Languages:} Python, Go" in result
    assert "RAG" not in result  # AI/ML category was replaced wholesale, not merged
    assert "\\section{Professional Experience}" in result
    assert "Test Corp" in result
    assert "\\end{document}" in result


def test_splice_escapes_unescaped_specials():
    blocks = rt.extract_blocks(SAMPLE_TEX)
    new_blocks = {"block_0": ["Cut cost by 30% and $50K saved", "b"], "block_1": ["c"]}
    result = rt.splice_tailored_content(SAMPLE_TEX, blocks, None, new_blocks, [])
    assert "30\\%" in result
    assert "\\$50K" in result


# --- extract_latex_error / _slugify ---


def test_extract_latex_error_finds_bang_line():
    stdout = "Some preamble\n! Undefined control sequence.\nl.42 \\foo\n"
    assert rt.extract_latex_error(stdout) == "! Undefined control sequence."


def test_slugify_sanitizes_special_characters():
    assert rt._slugify("Acme Corp! - SWE, Backend (NYC)") == "Acme_Corp_SWE_Backend_NYC"


# --- tailor_and_compile (fully mocked orchestration) ---


def _fake_compile_factory(returncode=0, stdout="", create_pdf=True):
    def fake_compile(tex_path, timeout=30):
        if create_pdf:
            (tex_path.parent / (tex_path.stem + ".pdf")).write_bytes(b"%PDF-fake")
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")

    return fake_compile


class FakeMessages:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.prompts_sent = []

    def create(self, **kwargs):
        self.prompts_sent.append(kwargs["messages"][0]["content"])
        item = next(self._responses)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


@pytest.fixture
def patch_base_resume(monkeypatch):
    monkeypatch.setattr(rt, "find_base_resume", lambda company, resumes_dir=None: SAMPLE_RESUME)


def test_tailor_and_compile_success_on_first_attempt(monkeypatch, tmp_path, patch_base_resume):
    response = _tool_response(_valid_payload())
    client = FakeClient([response])
    monkeypatch.setattr(rt, "compile_tex_to_pdf", _fake_compile_factory(returncode=0))
    monkeypatch.setattr(rt, "get_pdf_page_count", lambda path: 1)

    result = rt.tailor_and_compile(client, 1, "Acme", "SWE", "NYC", {}, output_dir=tmp_path)

    assert result["status"] == "tailored"
    assert result["attempts"] == 1
    assert Path(result["pdf_path"]).exists()
    assert result["tailoring_summary"] == ["Reworded bullet A for relevance."]
    assert "New bullet A" in Path(result["tex_path"]).read_text()


def test_tailor_and_compile_retries_when_llm_proposes_fabricated_skill(monkeypatch, tmp_path, patch_base_resume):
    bad_payload = _valid_payload()
    bad_payload["skills"] = [{"label": "Languages", "items": ["Python", "Kubernetes"]}]  # fabricated
    good_payload = _valid_payload()  # only "Python" -- genuinely known
    client = FakeClient([_tool_response(bad_payload), _tool_response(good_payload)])
    monkeypatch.setattr(rt, "compile_tex_to_pdf", _fake_compile_factory(returncode=0))
    monkeypatch.setattr(rt, "get_pdf_page_count", lambda path: 1)

    result = rt.tailor_and_compile(client, 1, "Acme", "SWE", "NYC", {}, output_dir=tmp_path)

    assert result["status"] == "tailored"
    assert result["attempts"] == 2
    assert "Kubernetes" in client.messages.prompts_sent[1]  # retry feedback names the offending skill


def test_tailor_and_compile_preserves_coursework_annotation_end_to_end(monkeypatch, tmp_path, patch_base_resume):
    payload = _valid_payload()
    payload["skills"] = [{"label": "Languages", "items": ["Rust"]}]  # LLM dropped the annotation
    client = FakeClient([_tool_response(payload)])
    monkeypatch.setattr(rt, "compile_tex_to_pdf", _fake_compile_factory(returncode=0))
    monkeypatch.setattr(rt, "get_pdf_page_count", lambda path: 1)

    result = rt.tailor_and_compile(client, 1, "Acme", "SWE", "NYC", {}, output_dir=tmp_path)

    assert result["status"] == "tailored"
    tex_content = Path(result["tex_path"]).read_text()
    assert "Rust (coursework: Systems Programming)" in tex_content


def test_tailor_and_compile_retries_on_page_overflow_and_allows_fewer_bullets(monkeypatch, tmp_path, patch_base_resume):
    full_payload = _valid_payload()
    shorter_payload = _valid_payload()
    shorter_payload["blocks"][0]["bullets"] = ["Just one now"]  # block_0 originally had 2
    client = FakeClient([_tool_response(full_payload), _tool_response(shorter_payload)])
    monkeypatch.setattr(rt, "compile_tex_to_pdf", _fake_compile_factory(returncode=0))

    page_counts = iter([2, 1])
    monkeypatch.setattr(rt, "get_pdf_page_count", lambda path: next(page_counts))

    result = rt.tailor_and_compile(client, 1, "Acme", "SWE", "NYC", {}, output_dir=tmp_path)

    assert result["status"] == "tailored"
    assert result["attempts"] == 2
    assert "drop the lowest-priority bullet" in client.messages.prompts_sent[1]


def test_tailor_and_compile_fails_after_exhausting_all_attempts(monkeypatch, tmp_path, patch_base_resume):
    response = _tool_response(_valid_payload())
    client = FakeClient([response, response, response])
    monkeypatch.setattr(rt, "compile_tex_to_pdf", _fake_compile_factory(returncode=0))
    monkeypatch.setattr(rt, "get_pdf_page_count", lambda path: 2)  # always overflowing

    result = rt.tailor_and_compile(client, 1, "Acme", "SWE", "NYC", {}, output_dir=tmp_path)

    assert result["status"] == "failed"
    assert result["attempts"] == 3
    assert "pages" in result["reason"]


def test_tailor_and_compile_fails_immediately_when_no_blocks_found(monkeypatch, tmp_path):
    monkeypatch.setattr(rt, "find_base_resume", lambda company, resumes_dir=None: tmp_path / "empty.tex")
    (tmp_path / "empty.tex").write_text("\\documentclass{article}\\begin{document}\\end{document}")
    client = FakeClient([])  # no API call should happen at all

    result = rt.tailor_and_compile(client, 1, "Acme", "SWE", "NYC", {}, output_dir=tmp_path)

    assert result["status"] == "failed"
    assert result["attempts"] == 0
    assert "no resume blocks" in result["reason"]
