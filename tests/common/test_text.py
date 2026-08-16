from src.common.text import extract_description


def test_extract_description_prefers_content_field():
    assert extract_description({"content": "<p>Hello</p>"}) == "Hello"


def test_extract_description_falls_back_to_description_text():
    assert extract_description({"descriptionText": "Plain text here"}) == "Plain text here"


def test_extract_description_falls_back_to_description_html():
    assert extract_description({"descriptionHtml": "<p>HTML &amp; stuff</p>"}) == "HTML & stuff"


def test_extract_description_returns_none_when_missing():
    # Workday's cxs/jobs list endpoint never includes a description at all.
    assert extract_description({"title": "Foo", "externalPath": "/job/x"}) is None


def test_extract_description_strips_tags_and_collapses_whitespace():
    result = extract_description({"content": "<p>Line one</p>\n\n<p>Line   two</p>"})
    assert result == "Line one Line two"
