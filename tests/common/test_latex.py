from src.common.latex import escape_latex_specials, extract_command_args, strip_comments


def test_strip_comments_removes_comment_to_end_of_line():
    text = "some text % this is a comment\nmore text"
    assert strip_comments(text) == "some text \nmore text"


def test_strip_comments_preserves_escaped_percent():
    text = "50\\% done % real comment"
    result = strip_comments(text)
    assert "50\\%" in result
    assert "real comment" not in result


def test_extract_command_args_single_arg():
    text = "\\resumeItem{Did a thing.}"
    results = extract_command_args(text, "resumeItem", 1)
    assert len(results) == 1
    start, end, args = results[0]
    assert args == ["Did a thing."]
    assert text[start:end] == "\\resumeItem{Did a thing.}"


def test_extract_command_args_multi_arg():
    text = "\\resumeSubheading{Org}{Dates}{Title}{Location}"
    results = extract_command_args(text, "resumeSubheading", 4)
    assert results[0][2] == ["Org", "Dates", "Title", "Location"]


def test_extract_command_args_handles_nested_braces():
    text = "\\resumeProjectHeading{\\textbf{Cool Project} $|$ \\emph{Python}}{2024}"
    results = extract_command_args(text, "resumeProjectHeading", 2)
    assert results[0][2][0] == "\\textbf{Cool Project} $|$ \\emph{Python}"
    assert results[0][2][1] == "2024"


def test_extract_command_args_finds_multiple_occurrences_in_order():
    text = "\\resumeItem{First} \\resumeItem{Second} \\resumeItem{Third}"
    results = extract_command_args(text, "resumeItem", 1)
    assert [r[2][0] for r in results] == ["First", "Second", "Third"]


def test_extract_command_args_ignores_incomplete_command():
    # Missing the second brace group entirely -- should not be counted as a match.
    text = "\\resumeSubheading{Org}{Dates}"
    results = extract_command_args(text, "resumeSubheading", 4)
    assert results == []


def test_extract_command_args_returns_empty_for_no_match():
    assert extract_command_args("no commands here", "resumeItem", 1) == []


def test_escape_latex_specials_escapes_unescaped_chars():
    assert escape_latex_specials("50% done") == "50\\% done"
    assert escape_latex_specials("$100K budget") == "\\$100K budget"
    assert escape_latex_specials("R&D team") == "R\\&D team"
    assert escape_latex_specials("#1 priority") == "\\#1 priority"


def test_escape_latex_specials_does_not_double_escape():
    assert escape_latex_specials("50\\% done") == "50\\% done"
    assert escape_latex_specials("\\$100K") == "\\$100K"


def test_escape_latex_specials_handles_mixed_escaped_and_unescaped():
    text = "Cut cost by 50\\% and grew revenue $10K in Q1 for R&D"
    result = escape_latex_specials(text)
    assert "50\\%" in result  # already escaped, untouched
    assert "\\$10K" in result  # was unescaped, now escaped
    assert "R\\&D" in result  # was unescaped, now escaped
