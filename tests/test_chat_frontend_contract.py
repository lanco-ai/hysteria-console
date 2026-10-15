"""AI 对话 frontend contracts (moved here with the chat page in the service split)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_chat_page_uses_the_unified_shell_and_defers_private_state_until_authenticated():
    """Anonymous chat must not read browser history or invoke the chat proxy."""
    page = source('frontend/src/features/chat/ChatPage.tsx')
    sidebar = source('frontend/src/features/chat/ConversationSidebar.tsx')
    api = source('frontend/src/features/chat/chatApi.ts')
    styles = source('frontend/src/styles/sections/18-chat.css')
    browser = source('tests/react_chat_browser.cjs')

    assert 'export type ChatPageProps' in page
    assert 'authenticated?: boolean' in page
    assert 'onUnauthenticated?: () => void' in page
    assert 'import { CodexShell, type CodexShellProps }' in page
    assert 'shell: Shell = CodexShell' in page
    assert '<Shell active="chat"' in page
    assert '<ConversationSidebar' in page
    assert 'chat-history-panel' in sidebar
    assert 'historyOpen' in page
    assert '<AdminShell' not in page
    assert 'historyOpen && authenticated' in page
    assert (
        'if (!authenticated) { setSessions([]); accept(null); setProjects([]); setPapers([]);'
        in page
    )
    assert 'setReady(false); controller.current?.abort(); return;' in page
    assert "api<{ items: LearningProject[] }>('/projects')" in page
    assert "api<{ items: Conversation[] }>('/conversations')" in page
    assert page.index('if (!authenticated) { setSessions([])') < page.index(
        'localStorage.getItem(DRAFT_KEY)'
    )
    assert 'if (authRef.current) { try {' in page
    assert 'disabled={!authenticated || !ready || busy' in page
    assert 'onUnauthenticated?.()' in page
    message = source('frontend/src/features/chat/ChatMessage.tsx')
    assert 'import ReactMarkdown' in message
    assert 'rel="noopener noreferrer"' in message
    assert 'dangerouslySetInnerHTML' not in message
    assert '没有可用容量' in api
    assert "reasoning_effort !== 'auto'" in api
    assert 'ChatApiError' in api
    assert '.sidebar-top' in styles
    assert '.chat-history' in styles
    assert '.sidebar.collapsed .sidebar-top' in styles
    assert 'anonymousContext' in browser
    assert 'anonymousChatRequests' in browser
    assert 'anonymousStorageCalls' in browser
