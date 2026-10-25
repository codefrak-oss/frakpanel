# HITLP permission overlay

HITLP pushes changes on `GET /events` (`text/event-stream`, spec §7), and its task view is at
`GET /tasks/{id}`. edged (class `Hitlp`) keeps one outbound stream and holds the open tasks plus
the dismissed ids behind a condition variable. Each shell connection to `GET /hitlp/events` waits
on that condition and gets `show {tasks}` or `hide` whenever the set changes.

The overlay is a fixed `#hitlp` div that sits next to the slot iframes (80vw × 85vh, 68% of the screen).
Its text is set with `textContent`. It shows information only: dismiss is per task and kept on the
panel. Decisions stay on HITLP.

The parser accepts any event whose name or status contains closed, decided, cancelled, expired,
withdrawn, answered or resolved as a close; any other event that carries a task id counts as
new or updated.
