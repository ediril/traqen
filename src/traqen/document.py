"""A traq's text is Markdown (CommonMark, with tables, strikethrough, and
checklists), written in the traq's editor.

Traqen reads it a line at a time: the first line is the title, and a line of a
paragraph that's nothing but a link is shown as that link's card. Lines are
numbered from 0 in the text as stored (newlines are always "\\n"), which is
also how a checklist item is pointed at when it's ticked from the traq's page.
"""

import re

from markdown_it import MarkdownIt
from markdown_it.token import Token
from markupsafe import Markup

from .live.links import normalize_url

# A pasted link: an explicit http(s) URL, or a bare host followed by a path.
_LINK = re.compile(r"^(https?://\S+|(www\.)?[\w-]+(\.[\w-]+)+/\S*)$", re.IGNORECASE)
# The same, written as a Markdown autolink.
_AUTOLINK = re.compile(r"^<(https?://[^\s<>]+)>$", re.IGNORECASE)
# What starts a heading, quote, or list item line, before its text.
_BLOCK_MARKERS = re.compile(
    r"^ {0,3}(?:#{1,6}(?=\s)|>|[-*+](?=\s)|\d{1,9}[.)](?=\s)|\[[ xX]\](?=\s)|\s)+"
)
_TASK = re.compile(r"\[([ xX])\][ \t]+")
_TASK_MARKER = re.compile(r"^((?:\s*>)*\s*(?:[-*+]|\d{1,9}[.)])\s+)\[[ xX]\]")


def link_in(line: str) -> str | None:
    """The normalized URL when `line` is just a link, else None."""
    line = line.strip()
    autolink = _AUTOLINK.match(line)
    if autolink:
        line = autolink.group(1)
    elif not _LINK.match(line):
        return None
    try:
        return normalize_url(line)
    except ValueError:
        return None


def link_lines(text: str) -> dict[int, str]:
    """The lines (by number) that are a link on their own in a paragraph (not
    in a list, table, quote, or code), each with its URL."""
    lines = text.split("\n")
    found = {}
    for token in _markdown.parse(text):
        if token.type == "paragraph_open" and token.level == 0 and token.map:
            for number in range(*token.map):
                url = link_in(lines[number])
                if url:
                    found[number] = url
    return found


def first_line_number(text: str) -> int | None:
    """The first line with something on it: the traq's title."""
    return next((n for n, line in enumerate(text.split("\n")) if line.strip()), None)


def plain(line: str) -> str:
    """A line's text as read, without its Markdown: `## **Q4** plan` -> `Q4 plan`."""
    line = _BLOCK_MARKERS.sub("", line).strip()
    line = re.sub(r"\s+#+\s*$", "", line)  # a heading's optional closing hashes
    words = []
    for token in _markdown.parseInline(line):
        for child in token.children or []:
            if child.type in ("text", "code_inline", "image"):
                words.append(child.content)
            elif child.type in ("softbreak", "hardbreak"):
                words.append(" ")
    return " ".join("".join(words).split())


def paragraphs(text: str) -> str:
    """Plain text (typed in the New traq box, or picked on a page) as Markdown:
    each line its own paragraph, as it was typed."""
    return "\n\n".join(
        line.rstrip() for line in normalized(text).split("\n") if line.strip()
    )


def append(text: str, markdown: str) -> str:
    """Add a paragraph (or any block) at the end."""
    return f"{normalized(text).rstrip()}\n\n{markdown}".strip()


def normalized(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def render(text: str, first_line: int = 0) -> Markup:
    """Lines of the text as HTML. `first_line` is where they start in the whole
    text, so each checklist item knows its line there."""
    return Markup(_markdown.render(text, {"first_line": first_line}))


def tasks(text: str) -> dict[int, bool]:
    """The checklist items (by line), and whether each is ticked."""
    env: dict = {}
    _markdown.parse(text, env)
    return env.get("tasks", {})


def tick(text: str, line: int, done: bool) -> str | None:
    """The text with the checklist item on `line` ticked (or not); None when
    there's no checklist item there."""
    if line not in tasks(text):
        return None
    lines = text.split("\n")
    lines[line] = _TASK_MARKER.sub(rf"\1[{'x' if done else ' '}]", lines[line], count=1)
    return "\n".join(lines)


def _checklists(state) -> None:
    """A list item starting with `[ ]` or `[x]` is a checklist item, shown with
    a checkbox that knows its line."""
    tokens = state.tokens
    first_line = state.env.get("first_line", 0)
    for index, item in enumerate(tokens[:-2]):
        paragraph, inline = tokens[index + 1], tokens[index + 2]
        if (
            item.type != "list_item_open"
            or paragraph.type != "paragraph_open"
            or inline.type != "inline"
            or not inline.children
            or inline.children[0].type != "text"
        ):
            continue
        marker = _TASK.match(inline.children[0].content)
        if not marker:
            continue
        done = marker.group(1) != " "
        line = first_line + item.map[0]
        state.env.setdefault("tasks", {})[line] = done
        inline.children[0].content = inline.children[0].content[marker.end() :]
        checkbox = Token("html_inline", "", 0)
        checkbox.content = (
            f'<input class="form-check-input task-check" type="checkbox" '
            f'data-task-line="{line}"{" checked" if done else ""} aria-label="Done">'
        )
        inline.children.insert(0, checkbox)
        item.attrJoin("class", "task-item")


def _link_open(self, tokens, index, options, env):
    tokens[index].attrSet("target", "_blank")
    tokens[index].attrSet("rel", "noopener")
    return self.renderToken(tokens, index, options, env)


def _image(self, tokens, index, options, env):
    tokens[index].attrSet("loading", "lazy")
    return self.image(tokens, index, options, env)


# Raw HTML in the text is shown as text, never run.
_markdown = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable(
    ["table", "strikethrough", "linkify"]
)
_markdown.core.ruler.push("checklists", _checklists)
_markdown.add_render_rule("link_open", _link_open)
_markdown.add_render_rule("image", _image)
