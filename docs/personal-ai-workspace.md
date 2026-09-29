# Personal learning workspace

Implements four personal workflows: durable conversations, paper sources, learning projects, and explicit journal capture. Team permissions, credits/payments, web search and code execution are outside this feature.

## Reference

Concepts were checked against Aivory at commit `788b0a5d7578dc17e0d3b664f964a8de81caf4e5` (Apache-2.0): conversations, citation identifiers, import receipts and backup integrity. This implementation is independently written in the existing Python/React stack; no upstream source is copied.

- https://github.com/hjxwz123/Aivory/tree/788b0a5d7578dc17e0d3b664f964a8de81caf4e5
- https://docs.aivorygo.com/docs/user-guide/conversations-files/

## Storage and privacy

`/root/hysteria/state/chat/workspace.sqlite3` contains projects, conversation messages, import/request receipts and original document BLOBs plus extracted page text. Directory 0700, file 0600. SQLite transactions, revision checks and request receipts prevent lost updates and repeated upstream requests. Streaming deltas are committed before delivery. Disconnects preserve partial text. Generation has an eight-minute deadline; a lease left by a killed worker expires after ten minutes (read/refresh recovers it). The UI also provides Stop for an active conversation.

Admin authentication and same-origin checks apply to all writes. Document downloads also require admin authentication. Provider credentials remain in the existing service center. Only explicitly selected documents from the conversation's project are retrieved. Journal entries are never automatically included in model input.

A browser-only unsent draft is kept for network recovery. Sent conversations and synced drafts come from the server. The old `hy2.chat.sessions.v1` key is explicitly imported, one session per request, with hash receipts; it is never deleted by migration. Changed legacy payloads create a separate imported conversation rather than overwrite server data. A single legacy conversation larger than 128 KiB must be split manually; import reports the error and preserves its original.

## Document limits

PDF/TXT/Markdown up to 10 MiB; PDF up to 200 pages; extracted text up to 500,000 characters. PDF extraction runs in a subprocess with 384 MiB address-space, 20 CPU seconds and a 25-second wall limit. Encrypted files and image-only PDFs are rejected. No OCR is provided.

Retrieval uses local English words and Chinese bigrams, selecting at most eight page-aware snippets, representing each selected document. This is lexical retrieval, not embedding search or a guarantee of reading the entire paper. Source title, SHA-256, page and original quote persist with each assistant message. The raw PDF link uses PDF page numbering. A citation is evidence supplied to the model, not verification that its conclusion is correct. Deleting the source retains historical quote snapshots.

Each turn includes up to 40 recent messages and 24,000 history characters, plus project instructions, selected sources and the current question. Earlier history remains stored; the UI marks truncated model context. Maximum 500 messages per conversation, 2,000 conversations, 100 projects, and 200 documents / 200 MiB original file content.

## Journal capture

Select “存入学习日记”, edit the draft, then explicitly confirm. Capture preserves conversation/message IDs and content digest. Repeated capture of the same response returns the existing record; further editing happens in the journal. Provenance survives journal edits. Chat and journal use separate stores; there is no claim of an atomic transaction across them.

## Deployment and recovery

Install `requirements-web.txt` into the existing web venv. Deploy the four new workspace/document Python modules together with `app.py` and `journal_service.py`, plus the built React release. Existing chat provider routes remain compatible. Deploy the updated release validator (KaTeX fonts include `.woff`/`.ttf`).

Nginx needs the exact `/api/chat/documents/upload` location with a 10m body limit. Other chat requests keep the existing body limit. `/api/chat/` must disable response buffering and use a 660s proxy timeout for durable streaming turns. Apply only the panel route changes; proxy protocol services do not need restarts.

`hy2-backup.sh` uses SQLite's online backup API to add a consistent database (including originals) to the existing private archive. JSON export includes conversations/projects/citation snapshots and document metadata, not PDF bytes. Download individual originals or use the runtime backup for full recovery. To restore, stop the web process, retain a copy of the current state, restore `state/chat/workspace.sqlite3` and journal `entries.json` from the archive with private permissions, then start the web process. The two stores may reflect slightly different instants during a live backup.

Frontend rollback can switch the release symlink; backend rollback restores the previous web modules. If journal captures have already been saved, retain the upgraded, backwards-compatible `journal_service.py` so it can continue to read provenance fields; do not discard journal records to roll back code. Never remove the new SQLite file to roll back code. The legacy frontend still has its original browser data, but cannot display newly created server conversations.
