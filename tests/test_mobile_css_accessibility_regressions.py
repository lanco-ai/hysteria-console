from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "hysteria" / "admin.css").read_text(encoding="utf-8")


def _block(source: str, marker: str) -> str:
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"Unclosed CSS block: {marker}")


def test_mobile_user_table_header_is_visually_hidden_but_semantic() -> None:
    mobile = _block(CSS, "@media (max-width: 720px)")
    table_head = _block(mobile, ".users-table thead")

    assert "display: none" not in table_head
    assert "position: absolute" in table_head
    assert "width: 1px" in table_head
    assert "height: 1px" in table_head
    assert "overflow: hidden" in table_head
    assert "clip-path: inset(50%)" in table_head
    assert "white-space: nowrap" in table_head


def test_mobile_user_cards_fit_inside_the_table_viewport() -> None:
    mobile = _block(CSS, "@media (max-width: 720px)")
    card = _block(mobile, ".users-table tbody tr")

    assert "width: calc(100% - 24px)" in card
    assert "margin: 0 12px 12px" in card


def test_mobile_user_identity_and_notes_wrap_unbroken_content() -> None:
    mobile = _block(CSS, "@media (max-width: 720px)")

    assert '.users-table td[data-label="用户"] .bold' in mobile
    assert '.users-table td[data-label="用户"] .small' in mobile
    assert ".user-title" in mobile
    assert ".user-title ~ .small.faint" in mobile
    assert "overflow-wrap: anywhere" in mobile
    assert "word-break: break-word" in mobile


def test_narrow_user_panel_wraps_long_host_and_username() -> None:
    narrow = _block(CSS, "@media (max-width: 560px)")
    navigation = _block(narrow, ".user-panel-nav")
    badge = _block(narrow, ".user-panel-nav .badge {")

    assert "min-width: 0" in navigation
    assert "flex-wrap: wrap" in navigation
    assert ".user-panel-nav > .row > div" in narrow
    assert ".user-panel-nav > div:last-child" in narrow
    assert "overflow-wrap: anywhere" in narrow
    assert "word-break: break-word" in narrow
    assert "white-space: normal" in badge
