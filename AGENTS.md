# Key Principles
- You should NEVER make speculative fixes, always investigate first if you don't have the necessary information in context
- After fixing/implementing, make sure to review your work for any oversights, bugs; and also any unwanted side-effects, any adjacent things or downstream consequences that need to be reviewed. Review should cover not just the modified code but any other code area affected directly or indirectly
- Go for the proper fix, do not implement spot fixes, or hacks or bandaid solutions. Investigate the root cause and fix the class of problems altogether.
- When an issue appears in a downstream step, investigate upstream steps before fixing it. The downstream failure may be only where the problem surfaced; the root cause may be earlier routing, discovery, planning, or evidence-shaping behavior.
- Don't keep backwards compatibility when making changes unless explicitly told to do so.
- All else being equal, favor simplicity over complexity

# Workflow
- The user controls git. Never commit, push, or stage. When a piece of work is done, end with a suggested commit message in the style of `git log` (short, lowercase subject; a body only when the change needs explaining).
- The user runs the app. Don't start the dev server; verify changes with the test suite (`.venv/bin/pytest`). If a live server is truly needed, stop it when done.
- Never ask the user to delete their database after a schema change. Back up `traqen.db` (a timestamped copy next to it), then update it in place to the new schema in one transaction, carrying the existing data over to the new model. Bump `SCHEMA_VERSION` in `db.py`, and verify the result matches a freshly created database. No migration framework needed.
- `TODO.md` holds what's left to do. When an item is finished, move it from `TODO.md` to `CHANGELOG.md` (newest first, under the date it was finished).
