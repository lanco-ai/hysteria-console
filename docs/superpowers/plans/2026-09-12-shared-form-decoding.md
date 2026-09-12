# Shared bounded form decoding implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Reuse the legacy strict form policy in the future ASGI transport without duplicating validation or buffering an unbounded request.

**Architecture:** Extract header validation and bytes decoding from `http_utils.parse_form`. Keep that legacy entry point as the first consumer, with the same read length, exceptions and results. No FastAPI endpoint is added by this extraction.

**Tech Stack:** Existing Python, email header fixtures, BytesIO and pytest. No dependencies or deployment artifacts added.

**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md

## Global Constraints

- Preserve same-origin mutation protection, credential-generation invalidation, revision conflicts, strict-state failure behavior, request size limits, request concurrency bounds and cache/security headers.
- Keep existing URLs, subscription formats, dedicated-user link exchanges, cookie separation, redirects, downloads, QR responses and status semantics.
- Preserve host-specific settings, TLS, nginx routing, TCP/UDP 443, panel 9444, runtime secrets, proxy configuration and all persistent user data.
- Source and temporary tests only; no deployment, runtime state access, service actions or push.
- Keep `MAX_FORM_BYTES = 256 * 1024`, `MAX_FORM_FIELDS = 128` and existing `BadRequest` / `RequestTooLarge` exception identities.

### Task 1: Share form header validation and body decoding

**Files:** Modify only `hysteria/http_utils.py` and `tests/test_http_utils.py`.

**Interfaces:**

```python
def form_content_length(headers, *, max_bytes=MAX_FORM_BYTES):
    # Validate existing form headers; return the one permitted length.

def decode_form_body(raw, *, max_bytes=MAX_FORM_BYTES):
    # Decode bounded bytes into the same dict[str, list[str]] as legacy parse_form.

def parse_form(handler, *, max_bytes=MAX_FORM_BYTES):
    length = form_content_length(handler.headers, max_bytes=max_bytes)
    try:
        raw = handler.rfile.read(length)
        if len(raw) != length:
            raise BadRequest
        return decode_form_body(raw, max_bytes=max_bytes)
    except (UnicodeDecodeError, ValueError):
        raise BadRequest
```

The header helper moves the existing Transfer-Encoding, Content-Type and
Content-Length checks without changing their ordering. Preserve optional missing
Content-Type, accepted `charset=utf-8` parameters, required one Content-Length,
ASCII decimal rule, repeated-header `get_all` support and dict fallback.
Reject lengths above the supplied limit before reading bytes. Do not introduce
a new stricter duplicate Content-Type policy in a compatibility extraction.

The body helper first rejects actual bytes over the supplied bound with
`RequestTooLarge`, then retains the existing UTF-8, empty-body, invalid-percent
and `parse_qs` logic exactly. It does not own stream reading or claimed-length
validation. Preserve dropped empty values, repeated values and plus decoding.
No state/service calls belong in either helper.

- [ ] Extend the existing parser characterization tests before extraction.
Use `email.message.Message` for real repeated and mixed-case headers and a
recording stream to prove invalid headers cause no read. Cover the exact size
boundary with a smaller injected limit so tests stay cheap. Baseline cases
must pass against the unchanged parser.

```python
headers = Message()
headers['Content-Length'] = '3'
headers['content-length'] = '3'
handler.headers = headers
with pytest.raises(http_utils.BadRequest):
    http_utils.parse_form(handler)
assert stream.read_calls == []
```

- [ ] Add direct helper tests and run red against the missing functions.
Header matrix: absent, duplicate equal and unequal, negative, signed, whitespace,
comma-joined, non-ASCII digits, oversize, explicit zero, valid leading zero;
Transfer-Encoding rejected; existing absent/valid/invalid Content-Type cases.
Body matrix: empty, repeated fields, blank values, UTF-8/plus/percent decoding,
invalid raw UTF-8, invalid percent escape/UTF-8, malformed missing `=`, 128 versus
129 fields, and actual size at/above bound. Compare decoded results with the
legacy entry on valid input rather than testing only helper existence.

```python
assert http_utils.decode_form_body(b'a=1&a=2&empty=&q=a+b') == {
    'a': ['1', '2'], 'q': ['a b'],
}
with pytest.raises(http_utils.RequestTooLarge):
    http_utils.decode_form_body(b'a=12', max_bytes=3)
assert http_utils.decode_form_body(b'a=1', max_bytes=3) == {'a': ['1']}
```

- [ ] Extract the existing header code into `form_content_length`; extract
strict decode into `decode_form_body`, adding its actual-byte bound. Replace
`parse_form` with the short composition shown above. Remove duplicated old
checks instead of leaving two implementations. Keep unrelated URL, origin,
cookie and client-IP helpers unchanged; do not reformat the whole module.

- [ ] Re-run the focused helper suite and actual HTTP security/reliability/form
regressions to verify callers still receive the same 400/413 outcomes and no
state changes on malformed bodies. Run backend lint-only and diff hygiene.
This task changes no frontend, deployed file inventory or endpoint; no browser
suite, deployment script or whole-backend rerun is needed at this bounded gate.

```bash
/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_http_utils.py tests/test_reliability_regressions.py tests/test_subscription_security_regressions.py tests/test_form_recovery.py
PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh --lint-only
git diff --check
```

- [ ] Self-review and commit only these two files locally. Report baseline,
red/green commands and results, including no-read-on-rejection evidence. No push.

## Next consumer and scope limit

An ASGI consumer will retain raw repeated headers, call `form_content_length`
before reading, cap streamed bytes before buffering, verify the final byte count,
and call `decode_form_body`. Worker admission, cancellation, client-IP trust and
cookie/error transport require their own endpoint integration tests. These
helpers alone are not a safe complete write API or production readiness claim.
