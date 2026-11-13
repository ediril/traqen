## 2026-11-13

- Traqs are edited in a rich text editor (Tiptap): bold, italic, strikethrough, code, links, headings, quotes, bulleted, numbered, and checklists, tables, and images (pasted, dropped, or picked; up to 5 MB, stored in the database, shown only to you). The first line still is the title, and a link on a line of its own still becomes a card.
- A traq's text is stored as Markdown, rendered on its page with raw HTML shown as text. Existing traqs were converted, each line its own paragraph.
- Checklist items tick from the traq's page.
- Edit always starts from the saved text; Cancel drops your edits.
- Traq lists show the most recently updated first, until you pick another order.

## 2026-11-12

- Opening a traq checks its links (unless they were checked in the last minute), instead of showing when each was last checked or offering "refresh now".
- A link card lights up when something changed and stays lit until you click "Mark as seen" or open its link; a card with nothing new is dimmed.
- Text a visitor can't see (hidden elements) no longer counts as a page's content, so it can't flag a change or show in the preview.
- Traq lists (Everything, projects, Archive, search) show when each traq was last updated (its text, or anything it links to: a GitHub or Jira item's own last-updated time, or when a check saw a page change), and sort by title, created, or updated from the column headings; the choice is remembered.
- The breadcrumb on a traq's page reads as a link back to its project.

## 2026-10-25

- Adopt PolyForm Strict License 1.0.0, with license terms, package metadata, and commercial licensing contact details.

## 2026-10-08

- Traqs: write a note like a sticky; the first line is its title. Mark done to archive, reopen, delete, search, and group into projects.
- Live link cards: each line that is just a link becomes a card showing whether it's live and what changed since you last looked. Covers public GitHub, GitHub and Jira through your own account, and public web pages (title and content changes).
- Checks run when you open a traq and every 15 minutes for open traqs.
- "Save to Traqen" bookmarklet: save the page you're on to a new or existing traq, with a warning when it's already there.
- The UI runs on Bootstrap 5.3, in light and dark, on desktop and phone.
