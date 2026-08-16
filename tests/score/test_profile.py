from pathlib import Path

from src.score.profile import build_profile_summary

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_resume.tex"


def test_extracts_all_relevant_section_headers():
    summary = build_profile_summary(FIXTURE)
    assert "## Education" in summary
    assert "## Technical Skills" in summary
    assert "## Professional Experience" in summary
    assert "## AI/ML Projects" in summary


def test_excludes_header_contact_info():
    # Name/email live outside any \section{} -- must not leak into the profile.
    summary = build_profile_summary(FIXTURE)
    assert "Test Candidate" not in summary
    assert "test@example.com" not in summary


def test_extracts_education_subheading():
    summary = build_profile_summary(FIXTURE)
    assert "Test University" in summary
    assert "Bachelor of Science in Computer Science" in summary
    assert "GPA: 3.9/4.0" in summary


def test_technical_skills_renders_each_category_on_its_own_line():
    summary = build_profile_summary(FIXTURE)
    skills_block = summary.split("## Technical Skills")[1].split("## Professional Experience")[0]
    lines = [l for l in skills_block.strip().split("\n") if l]
    assert any(l.startswith("Languages:") for l in lines)
    assert any(l.startswith("AI/ML:") for l in lines)
    assert "Python, Go" in skills_block
    assert "PyTorch, RAG" in skills_block


def test_professional_experience_interleaves_subheading_and_bullets_in_order():
    summary = build_profile_summary(FIXTURE)
    exp_block = summary.split("## Professional Experience")[1].split("## AI/ML Projects")[0]
    assert "Test Corp (Jan 2024 - Present) - Software Engineer" in exp_block
    # bullets follow their subheading, not grouped separately
    idx_heading = exp_block.index("Test Corp")
    idx_bullet = exp_block.index("Built a thing")
    assert idx_bullet > idx_heading


def test_strips_latex_escapes():
    summary = build_profile_summary(FIXTURE)
    assert "50%" in summary  # \% unescaped
    assert "$100K+" in summary  # \$ unescaped
    assert "\\%" not in summary
    assert "\\$" not in summary


def test_project_heading_renders_with_tech_stack():
    summary = build_profile_summary(FIXTURE)
    assert "Cool Project" in summary
    assert "Python, PyTorch" in summary
