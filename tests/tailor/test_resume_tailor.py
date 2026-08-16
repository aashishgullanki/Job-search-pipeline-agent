"""Tailor stage tests -- entirely mocked (Anthropic client, pdflatex
subprocess, PDF page-counting). No real compile, no real API calls.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.tailor.resume_tailor as rt

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SAMPLE_RESUME = FIXTURES / "sample_resume.tex"


# --- find_base_resume ---


def test_find_base_resume_prefers_company_specific_file(tmp_path):
    (tmp_path / "Baseline Resume.tex").write_text("baseline")
    (tmp_path / "Acme Resume.tex").write_text("acme-specific")

    result = rt.find_base_resume("Acme", resumes_dir=tmp_path)

    assert result.name == "Acme Resume.tex"


def test_find_base_resume_falls_back_to_baseline_when_no_company_file(tmp_path):
    (tmp_path / "Baseline Resume.tex").write_text("baseline")

    result = rt.find_base_resume("SomeCompanyWithNoResume", resumes_dir=tmp_path)

    assert result.name == "Baseline Resume.tex"


# --- extract_bullets / splice_bullets ---


def test_extract_bullets_finds_all_three_in_order():
    bullets = rt.extract_bullets(SAMPLE_RESUME.read_text())
    texts = [b[2] for b in bullets]
    assert texts == [
        "Built a thing that did stuff, cutting latency by 50\\%.",
        "Shipped another thing using Kafka and \\$100K+ budget.",
        "Trained a model that did something 90\\% of the time.",
    ]


def test_splice_bullets_replaces_content_and_preserves_surrounding_document():
    tex = SAMPLE_RESUME.read_text()
    bullets = rt.extract_bullets(tex)
    new_bullets = ["New bullet one.", "New bullet two.", "New bullet three."]

    result = rt.splice_bullets(tex, bullets, new_bullets)

    assert "New bullet one." in result
    assert "New bullet two." in result
    assert "New bullet three." in result
    assert "Built a thing that did stuff" not in result
    # surrounding structure (section headers, subheadings) untouched
    assert "\\section{Professional Experience}" in result
    assert "Test Corp" in result
    assert "\\end{document}" in result


def test_splice_bullets_escapes_unescaped_specials_in_new_text():
    tex = SAMPLE_RESUME.read_text()
    bullets = rt.extract_bullets(tex)
    new_bullets = ["Cut cost by 30% and $50K saved", "b", "c"]

    result = rt.splice_bullets(tex, bullets, new_bullets)

    assert "30\\%" in result
    assert "\\$50K" in result


def test_splice_bullets_raises_on_count_mismatch():
    tex = SAMPLE_RESUME.read_text()
    bullets = rt.extract_bullets(tex)
    with pytest.raises(ValueError, match="bullet count mismatch"):
        rt.splice_bullets(tex, bullets, ["only one"])


# --- build_tailor_prompt ---


def test_prompt_includes_job_fields_and_numbered_bullets():
    prompt = rt.build_tailor_prompt("Acme", "Software Engineer", "NYC", "Build things", ["Bullet A", "Bullet B"])
    assert "Acme" in prompt
    assert "Software Engineer" in prompt
    assert "Build things" in prompt
    assert "1. Bullet A" in prompt
    assert "2. Bullet B" in prompt


def test_prompt_includes_feedback_when_retrying():
    prompt = rt.build_tailor_prompt("Acme", "SWE", "NYC", "desc", ["A"], feedback="still 2 pages")
    assert "still 2 pages" in prompt
    assert "previous attempt failed" in prompt


def test_prompt_omits_feedback_block_on_first_attempt():
    prompt = rt.build_tailor_prompt("Acme", "SWE", "NYC", "desc", ["A"], feedback=None)
    assert "previous attempt failed" not in prompt


# --- parse_tailor_response ---


def _tool_response(input_data):
    block = SimpleNamespace(type="tool_use", name="submit_tailored_content", input=input_data)
    return SimpleNamespace(content=[block])


def test_parse_tailor_response_extracts_bullets_and_outreach():
    response = _tool_response({"bullets": ["A", "B"], "outreach_draft": "Hi, ..."})
    result = rt.parse_tailor_response(response, expected_bullet_count=2)
    assert result == {"bullets": ["A", "B"], "outreach_draft": "Hi, ..."}


def test_parse_tailor_response_rejects_wrong_bullet_count():
    response = _tool_response({"bullets": ["A"], "outreach_draft": "Hi"})
    with pytest.raises(ValueError, match="expected 2 bullets"):
        rt.parse_tailor_response(response, expected_bullet_count=2)


def test_parse_tailor_response_rejects_empty_bullet():
    response = _tool_response({"bullets": ["A", "   "], "outreach_draft": "Hi"})
    with pytest.raises(ValueError, match="empty"):
        rt.parse_tailor_response(response, expected_bullet_count=2)


def test_parse_tailor_response_rejects_missing_outreach():
    response = _tool_response({"bullets": ["A"], "outreach_draft": ""})
    with pytest.raises(ValueError, match="outreach_draft"):
        rt.parse_tailor_response(response, expected_bullet_count=1)


def test_parse_tailor_response_raises_when_no_tool_call():
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="nope")])
    with pytest.raises(ValueError, match="no submit_tailored_content"):
        rt.parse_tailor_response(response, expected_bullet_count=1)


# --- extract_latex_error / _slugify ---


def test_extract_latex_error_finds_bang_line():
    stdout = "Some preamble\n! Undefined control sequence.\nl.42 \\foo\n"
    assert rt.extract_latex_error(stdout) == "! Undefined control sequence."


def test_extract_latex_error_falls_back_when_no_bang_line():
    assert "no '!' error line" in rt.extract_latex_error("nothing useful here")


def test_slugify_sanitizes_special_characters():
    assert rt._slugify("Acme Corp! - Software Engineer, Backend (NYC)") == "Acme_Corp_Software_Engineer_Backend_NYC"


def test_slugify_caps_length():
    assert len(rt._slugify("x" * 500)) <= 120


# --- tailor_and_compile (fully mocked orchestration) ---


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


def _fake_compile_factory(returncode=0, stdout="", create_pdf=True):
    def fake_compile(tex_path, timeout=30):
        if create_pdf:
            pdf_path = tex_path.parent / (tex_path.stem + ".pdf")
            pdf_path.write_bytes(b"%PDF-fake")
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")

    return fake_compile


@pytest.fixture
def patch_base_resume(monkeypatch):
    monkeypatch.setattr(rt, "find_base_resume", lambda company, resumes_dir=None: SAMPLE_RESUME)


def test_tailor_and_compile_success_on_first_attempt(monkeypatch, tmp_path, patch_base_resume):
    response = _tool_response({"bullets": ["A", "B", "C"], "outreach_draft": "Hi, interested in this role."})
    client = FakeClient([response])
    monkeypatch.setattr(rt, "compile_tex_to_pdf", _fake_compile_factory(returncode=0))
    monkeypatch.setattr(rt, "get_pdf_page_count", lambda path: 1)

    result = rt.tailor_and_compile(client, 1, "Acme", "SWE", "NYC", {}, output_dir=tmp_path)

    assert result["status"] == "tailored"
    assert result["attempts"] == 1
    assert Path(result["pdf_path"]).exists()
    assert Path(result["tex_path"]).exists()
    assert "A" in Path(result["tex_path"]).read_text()


def test_tailor_and_compile_retries_on_compile_error_then_succeeds(monkeypatch, tmp_path, patch_base_resume):
    bad_response = _tool_response({"bullets": ["A", "B", "C"], "outreach_draft": "Hi"})
    good_response = _tool_response({"bullets": ["A2", "B2", "C2"], "outreach_draft": "Hi"})
    client = FakeClient([bad_response, good_response])

    calls = {"n": 0}

    def fake_compile(tex_path, timeout=30):
        calls["n"] += 1
        if calls["n"] == 1:
            return subprocess.CompletedProcess(args=[], returncode=1, stdout="! Undefined control sequence.", stderr="")
        pdf_path = tex_path.parent / (tex_path.stem + ".pdf")
        pdf_path.write_bytes(b"%PDF-fake")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rt, "compile_tex_to_pdf", fake_compile)
    monkeypatch.setattr(rt, "get_pdf_page_count", lambda path: 1)

    result = rt.tailor_and_compile(client, 1, "Acme", "SWE", "NYC", {}, output_dir=tmp_path)

    assert result["status"] == "tailored"
    assert result["attempts"] == 2
    # the retry prompt must carry the real compile error forward
    assert "Undefined control sequence" in client.messages.prompts_sent[1]


def test_tailor_and_compile_retries_on_page_overflow_then_succeeds(monkeypatch, tmp_path, patch_base_resume):
    response = _tool_response({"bullets": ["A", "B", "C"], "outreach_draft": "Hi"})
    client = FakeClient([response, response])
    monkeypatch.setattr(rt, "compile_tex_to_pdf", _fake_compile_factory(returncode=0))

    page_counts = iter([2, 1])
    monkeypatch.setattr(rt, "get_pdf_page_count", lambda path: next(page_counts))

    result = rt.tailor_and_compile(client, 1, "Acme", "SWE", "NYC", {}, output_dir=tmp_path)

    assert result["status"] == "tailored"
    assert result["attempts"] == 2
    assert "2 pages" in client.messages.prompts_sent[1] or "must fit on exactly" in client.messages.prompts_sent[1]


def test_tailor_and_compile_fails_after_exhausting_all_attempts(monkeypatch, tmp_path, patch_base_resume):
    response = _tool_response({"bullets": ["A", "B", "C"], "outreach_draft": "Hi"})
    client = FakeClient([response, response, response])
    monkeypatch.setattr(rt, "compile_tex_to_pdf", _fake_compile_factory(returncode=0))
    monkeypatch.setattr(rt, "get_pdf_page_count", lambda path: 2)  # always overflowing

    result = rt.tailor_and_compile(client, 1, "Acme", "SWE", "NYC", {}, output_dir=tmp_path)

    assert result["status"] == "failed"
    assert result["attempts"] == 3
    assert "pages" in result["reason"]
    assert len(client.messages.prompts_sent) == 3  # never exceeds the attempt cap


def test_tailor_and_compile_fails_immediately_when_base_resume_has_no_bullets(monkeypatch, tmp_path):
    monkeypatch.setattr(rt, "find_base_resume", lambda company, resumes_dir=None: tmp_path / "empty.tex")
    (tmp_path / "empty.tex").write_text("\\documentclass{article}\\begin{document}\\end{document}")
    client = FakeClient([])  # no API call should happen at all

    result = rt.tailor_and_compile(client, 1, "Acme", "SWE", "NYC", {}, output_dir=tmp_path)

    assert result["status"] == "failed"
    assert result["attempts"] == 0
    assert "no \\resumeItem" in result["reason"]


def test_tailor_and_compile_retries_on_malformed_tool_response(monkeypatch, tmp_path, patch_base_resume):
    bad_response = _tool_response({"bullets": ["A"], "outreach_draft": "Hi"})  # wrong count (expects 3)
    good_response = _tool_response({"bullets": ["A", "B", "C"], "outreach_draft": "Hi"})
    client = FakeClient([bad_response, good_response])
    monkeypatch.setattr(rt, "compile_tex_to_pdf", _fake_compile_factory(returncode=0))
    monkeypatch.setattr(rt, "get_pdf_page_count", lambda path: 1)

    result = rt.tailor_and_compile(client, 1, "Acme", "SWE", "NYC", {}, output_dir=tmp_path)

    assert result["status"] == "tailored"
    assert result["attempts"] == 2
