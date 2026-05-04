"""Unit tests for gemini_save.py"""
import pytest
from gemini_save import (
    _clean,
    _GeminiConverter,
    _has_content,
    _normalize_share_url,
    format_markdown,
    format_html,
    format_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _convert(html: str) -> str:
    return _GeminiConverter(
        heading_style="ATX", bullets="-", strip=["script", "style"]
    ).convert(html).strip()


def _make_msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


# ---------------------------------------------------------------------------
# _normalize_share_url
# ---------------------------------------------------------------------------

def test_normalize_continue_url():
    result = _normalize_share_url("https://gemini.google.com/share/continue/abc123xyz")
    assert result == "https://gemini.google.com/share/abc123xyz"

def test_normalize_passthrough_public_url():
    url = "https://gemini.google.com/share/abc123xyz"
    assert _normalize_share_url(url) == url


# ---------------------------------------------------------------------------
# _clean
# ---------------------------------------------------------------------------

def test_clean_strips_you_said():
    assert _clean("You said\nActual message") == "Actual message"

def test_clean_strips_gemini_said():
    assert _clean("Gemini said\nResponse") == "Response"

def test_clean_case_insensitive():
    assert _clean("YOU SAID\nMessage") == "Message"

def test_clean_collapses_blank_lines():
    result = _clean("line1\n\n\n\nline2")
    assert "\n\n\n" not in result

def test_clean_passthrough():
    assert _clean("Normal content") == "Normal content"

def test_clean_strips_you_said_after_image():
    content = "![img](http://example.com/x.png)\n\nYou said\n\nSome text"
    result = _clean(content)
    assert "You said" not in result
    assert "Some text" in result
    assert "example.com" in result

def test_clean_strips_you_said_midline():
    content = "Line 1\n\nYou said\n\nLine 2"
    result = _clean(content)
    assert "You said" not in result


# ---------------------------------------------------------------------------
# _has_content
# ---------------------------------------------------------------------------

def test_has_content_with_text():
    from bs4 import BeautifulSoup
    el = BeautifulSoup("<div>hello</div>", "html.parser").find("div")
    assert _has_content(el) is True

def test_has_content_image_only():
    from bs4 import BeautifulSoup
    el = BeautifulSoup('<div><img src="https://example.com/x.png"></div>', "html.parser").find("div")
    assert _has_content(el) is True

def test_has_content_empty():
    from bs4 import BeautifulSoup
    el = BeautifulSoup("<div><!----></div>", "html.parser").find("div")
    assert _has_content(el) is False


# ---------------------------------------------------------------------------
# _GeminiConverter — formatting
# ---------------------------------------------------------------------------

def test_converter_bold():
    assert "**important**" in _convert("<p><strong>important</strong></p>")

def test_converter_italic():
    result = _convert("<p><em>slanted</em></p>")
    assert "*slanted*" in result or "_slanted_" in result

def test_converter_heading():
    result = _convert("<h2>Section</h2>")
    assert "## Section" in result

def test_converter_unordered_list():
    result = _convert("<ul><li>Apple</li><li>Banana</li></ul>")
    assert "- Apple" in result
    assert "- Banana" in result

def test_converter_ordered_list():
    result = _convert("<ol><li>First</li><li>Second</li></ol>")
    assert "First" in result
    assert "Second" in result


# ---------------------------------------------------------------------------
# _GeminiConverter — table
# ---------------------------------------------------------------------------

def test_converter_table_headers():
    html = "<table><tr><th>Col A</th><th>Col B</th></tr><tr><td>1</td><td>2</td></tr></table>"
    result = _convert(html)
    assert "Col A" in result
    assert "Col B" in result
    assert "|" in result

def test_converter_table_separator_row():
    html = "<table><tr><th>X</th></tr><tr><td>Y</td></tr></table>"
    result = _convert(html)
    assert "---" in result or "---" in result


# ---------------------------------------------------------------------------
# _GeminiConverter — formula (sub / sup / KaTeX)
# ---------------------------------------------------------------------------

def test_converter_preserves_sub():
    result = _convert("<p>A<sub>0</sub> is a constant</p>")
    assert "<sub>0</sub>" in result

def test_converter_preserves_sup():
    result = _convert("<p>e<sup>iπ</sup> + 1 = 0</p>")
    assert "<sup>iπ</sup>" in result

def test_converter_preserves_katex_span():
    result = _convert('<p>Formula: <span class="katex"><span class="katex-html">f(x)</span></span></p>')
    assert 'class="katex"' in result

def test_converter_strips_plain_span():
    result = _convert('<p><span class="other">plain</span></p>')
    assert "<span" not in result
    assert "plain" in result


# ---------------------------------------------------------------------------
# _GeminiConverter — images
# ---------------------------------------------------------------------------

def test_converter_image_src():
    result = _convert('<img src="https://cdn.example.com/photo.png" alt="diagram">')
    assert "https://cdn.example.com/photo.png" in result

def test_converter_image_alt():
    result = _convert('<img src="https://cdn.example.com/photo.png" alt="my diagram">')
    assert "my diagram" in result

def test_converter_image_html_tag():
    result = _convert('<img src="https://example.com/x.png" alt="x">')
    assert "<img" in result


# ---------------------------------------------------------------------------
# _GeminiConverter — code
# ---------------------------------------------------------------------------

def test_converter_inline_code():
    result = _convert("<p>Call <code>print()</code> here.</p>")
    assert "`print()`" in result

def test_converter_fenced_code_block():
    result = _convert("<pre><code>def hello():\n    pass\n</code></pre>")
    assert "def hello():" in result
    assert "```" in result

def test_converter_code_language_hint():
    result = _convert('<pre><code class="language-python">x = 1\n</code></pre>')
    assert "x = 1" in result


# ---------------------------------------------------------------------------
# format_markdown
# ---------------------------------------------------------------------------

MSGS = [
    _make_msg("user",  "Hello there"),
    _make_msg("model", "Hi back"),
]

def test_format_markdown_you_heading():
    assert "### You" in format_markdown("http://x.com", "T", MSGS)

def test_format_markdown_gemini_heading():
    assert "### Gemini" in format_markdown("http://x.com", "T", MSGS)

def test_format_markdown_source_url():
    out = format_markdown("http://x.com/share/abc", "T", MSGS)
    assert "http://x.com/share/abc" in out

def test_format_markdown_title():
    out = format_markdown("http://x.com", "My Title", MSGS)
    assert "My Title" in out

def test_format_markdown_table_preserved():
    msgs = [_make_msg("model", "| A | B |\n|---|---|\n| 1 | 2 |")]
    assert "| A | B |" in format_markdown("http://x.com", "T", msgs)

def test_format_markdown_sub_preserved():
    msgs = [_make_msg("model", "A<sub>0</sub>e<sup>-αx</sup>")]
    out = format_markdown("http://x.com", "T", msgs)
    assert "<sub>0</sub>" in out

def test_format_markdown_code_preserved():
    msgs = [_make_msg("model", "```python\nprint('hi')\n```")]
    out = format_markdown("http://x.com", "T", msgs)
    assert "print('hi')" in out

def test_format_markdown_skips_empty():
    msgs = [_make_msg("user", ""), _make_msg("model", "hello")]
    out = format_markdown("http://x.com", "T", msgs)
    assert out.count("### You") == 0
    assert out.count("### Gemini") == 1


# ---------------------------------------------------------------------------
# format_html
# ---------------------------------------------------------------------------

def test_format_html_user_bubble_class():
    msgs = [_make_msg("user", "Hi")]
    assert 'bubble-wrap user' in format_html("http://x.com", "T", msgs)

def test_format_html_model_bubble_class():
    msgs = [_make_msg("model", "Hello")]
    assert 'bubble-wrap model' in format_html("http://x.com", "T", msgs)

def test_format_html_table_rendered():
    msgs = [_make_msg("model", "| A | B |\n|---|---|\n| 1 | 2 |")]
    out = format_html("http://x.com", "T", msgs)
    assert "<table" in out
    assert "<th>" in out

def test_format_html_katex_css_linked():
    out = format_html("http://x.com", "T", MSGS)
    assert "katex" in out

def test_format_html_sub_css():
    out = format_html("http://x.com", "T", MSGS)
    assert "bubble sub" in out

def test_format_html_image_max_width():
    out = format_html("http://x.com", "T", MSGS)
    assert "max-width:100%" in out

def test_format_html_code_block_rendered():
    msgs = [_make_msg("model", "```python\nprint('hi')\n```")]
    out = format_html("http://x.com", "T", msgs)
    assert "<code" in out

def test_format_html_sub_passthrough():
    msgs = [_make_msg("model", "A<sub>0</sub>")]
    out = format_html("http://x.com", "T", msgs)
    assert "<sub>0</sub>" in out

def test_format_html_sup_passthrough():
    msgs = [_make_msg("model", "e<sup>iπ</sup>")]
    out = format_html("http://x.com", "T", msgs)
    assert "<sup>iπ</sup>" in out

def test_format_html_image_tag():
    msgs = [_make_msg("user", '![photo](https://cdn.example.com/img.png)')]
    out = format_html("http://x.com", "T", msgs)
    assert "cdn.example.com/img.png" in out

def test_format_html_wide_layout():
    out = format_html("http://x.com", "T", MSGS)
    assert "1200px" in out


# ---------------------------------------------------------------------------
# format_text
# ---------------------------------------------------------------------------

def test_format_text_you_label():
    assert "[You]" in format_text("http://x.com", "T", MSGS)

def test_format_text_gemini_label():
    assert "[Gemini]" in format_text("http://x.com", "T", MSGS)

def test_format_text_content_present():
    out = format_text("http://x.com", "T", MSGS)
    assert "Hello there" in out
    assert "Hi back" in out
