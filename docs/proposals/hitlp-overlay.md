# HITLP permission overlay

HITLP pushes changes on `GET /events` (`text/event-stream`, spec §7), and its task view is at
`GET /tasks/{id}`. edged (class `Hitlp`) keeps one outbound stream and holds the open tasks plus
the dismissed ids behind a condition variable. Each shell connection to `GET /hitlp/events` waits
on that condition and gets `show {tasks}` or `hide` whenever the set changes.

The overlay is a fixed `#hitlp` div that sits next to the slot iframes (80vw × 85vh, 68% of the screen).
Its text is set with `textContent`. Each task in `show` carries `primitive` (`ask`/`approve`), an Ask's
`options` (`[{value, label}]`) and the ISO `deadline`, which the page shows in local time. The popup
draws option buttons, a free-text field (an Ask without options) or approve/reject, and posts the
choice to `POST /hitlp/answer`. edged forwards it to HITLP's `POST /tasks/{id}/decision`
(`{"answer"}` or `{"outcome", "payloadDigest"}`) with the surface token; errors (HITLP's `{"error"}`,
or no connection) come back as 502 and are shown on the popup. The popup closes only when the
stream reports the task decided. Dismiss is per task and kept on the panel.

The parser accepts any event whose name or status contains closed, decided, cancelled, expired,
withdrawn, answered or resolved as a close; any other event that carries a task id counts as
new or updated.
