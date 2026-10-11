import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Component, useId, type ReactElement, type ReactNode } from 'react';
import type { Element, Root } from 'hast';
import { journalKindLabels, zonedDay, zonedTime } from './journalLabels';
import type { JournalDraft, JournalRecord } from './journalApi';

const detailLabels: Partial<Record<keyof JournalDraft, string>> = {
  question: '研究问题', explanation: '自己的解释', source: '依据或来源', uncertainty: '仍有疑点',
  next_check: '下一步验证', material: '练习材料', raw_result: '原始输出或结果', correction: '纠错',
  previous_view: '原先认为', trigger_evidence: '触发证据', current_view: '现在认为',
  learning_state: '学习状态', evidence: '支持这个判断的证据', takeaway: '收获',
};
const skillLabels = { listening: '听力', speaking: '口语', reading: '阅读', writing: '写作' } as const;

/** The structured fields a record carries besides its text, as label/value pairs. */
export function recordDetails(item: JournalRecord): Array<[string, string]> {
  const values = Object.entries(detailLabels).flatMap(([key, label]) => {
    const value = item[key as keyof JournalDraft];
    return typeof value === 'string' && value ? [[label, value] as [string, string]] : [];
  });
  if (item.skill) values.unshift(['练习项目', skillLabels[item.skill]]);
  return values;
}

// Parsing and rendering Markdown recurse once per nesting level, so text nested this deep
// (quotes, lists, emphasis runs) is shown as plain text rather than risking the page.
const MAX_NESTING = 20;
const LIST_MARKER = /^(?:[-*+]|\d{1,9}[.)])(?=[ \t])/;

function nestingDepth(line: string): number {
  let depth = 0;
  let indent = 0;
  let index = 0;
  while (index < line.length) {
    const char = line[index];
    if (char === ' ' || char === '\t') { indent += char === '\t' ? 4 : 1; index += 1; continue; }
    if (char === '>') { depth += 1; index += 1; continue; }
    const marker = LIST_MARKER.exec(line.slice(index, index + 11));
    if (!marker) break;
    depth += 1;
    index += marker[0].length;
  }
  return depth + Math.floor(indent / 2);
}

function tooDeep(text: string): boolean {
  return /[*_~]{48,}/.test(text) || text.split('\n').some(line => nestingDepth(line) > MAX_NESTING);
}

const plain = (text: string) => <div className="journal-markdown is-plain"><p>{text}</p></div>;

/** One record's Markdown failing to render shows its plain text; the rest of the page carries on. */
class MarkdownBoundary extends Component<{ text: string; children: ReactNode }, { failed: boolean }> {
  override state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  override componentDidUpdate(previous: { text: string }) {
    if (previous.text !== this.props.text && this.state.failed) this.setState({ failed: false });
  }
  override render() { return this.state.failed ? plain(this.props.text) : this.props.children; }
}

/** Footnotes get ids from this record's prefix; the label's fixed id is prefixed here too. */
function prefixFootnoteLabel(prefix: string) {
  const label = `${prefix}footnote-label`;
  return (tree: Root) => {
    const stack: Array<Root | Element> = [tree];
    while (stack.length) {
      for (const child of stack.pop()!.children) {
        if (child.type !== 'element') continue;
        const properties = child.properties;
        if (properties.id === 'footnote-label') properties.id = label;
        const describedBy = properties.ariaDescribedBy;
        if (Array.isArray(describedBy)) properties.ariaDescribedBy = describedBy.map(id => id === 'footnote-label' ? label : id);
        stack.push(child);
      }
    }
  };
}

// Defined once, so a re-render keeps the rendered elements instead of replacing them.
const components: Components = {
  // Links within the record (footnotes) scroll here; the page's own #fragments choose its view.
  a: ({ node: _node, href = '', children, ...props }) => href.startsWith('#')
    ? <a {...props} href={href} onClick={event => { event.preventDefault(); document.getElementById(href.slice(1))?.scrollIntoView({ block: 'center' }); }}>{children}</a>
    : <a {...props} href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
  img: ({ alt }) => <span>[图片：{alt || '未加载外部图片'}]</span>,
  // A wide table scrolls inside the record instead of widening the page.
  table: ({ node: _node, ...props }) => <div className="journal-markdown-table"><table {...props} /></div>,
};

/**
 * A record's text written in Markdown. Raw HTML is dropped and outside images are not loaded:
 * the records are private, and a picture link would tell its host when they are read.
 */
export function JournalMarkdown({ text }: { text: string }): ReactElement {
  // Several records share the page, so each one's footnote ids start with its own prefix.
  const prefix = `jm-${useId().replace(/[^A-Za-z0-9_-]/g, '')}-`;
  if (tooDeep(text)) return plain(text);
  return <MarkdownBoundary text={text}><div className="journal-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml
    rehypePlugins={[[prefixFootnoteLabel, prefix]]}
    remarkRehypeOptions={{ clobberPrefix: prefix, footnoteLabel: '脚注', footnoteBackLabel: '返回正文' }}
    components={components}>{text}</ReactMarkdown></div></MarkdownBoundary>;
}

const dayFormat = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long', timeZone: 'UTC' });

function clock(item: JournalRecord, value: string): string {
  return zonedTime(value, item.timezone, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
}

/** When the record happened, with the end's date when it ends on another day. */
function span(item: JournalRecord): string {
  if (!item.ended_at) return clock(item, item.occurred_at);
  const endDay = zonedDay(item.ended_at, item.timezone);
  const [, month, day] = endDay.split('-').map(Number);
  const endDate = endDay !== zonedDay(item.occurred_at, item.timezone) ? `${month}月${day}日 ` : '';
  return `${clock(item, item.occurred_at)}–${endDate}${clock(item, item.ended_at)}`;
}

/** All records as one Markdown document: a section per day, oldest first, each record with its fields. */
export function journalMarkdown(items: JournalRecord[], exportedAt = new Date()): string {
  const sorted = [...items].sort((a, b) => a.local_date.localeCompare(b.local_date) || Date.parse(a.occurred_at) - Date.parse(b.occurred_at));
  const lines = ['# 生活与学习记录', '', `导出时间：${exportedAt.toLocaleString('zh-CN', { hour12: false })} · 共 ${sorted.length} 条`, ''];
  let day = '';
  for (const item of sorted) {
    if (item.local_date !== day) {
      day = item.local_date;
      lines.push(`## ${dayFormat.format(new Date(`${day}T12:00:00Z`))}`, '');
    }
    lines.push(`### ${span(item)} · ${journalKindLabels[item.kind]}${item.title ? ` · ${item.title}` : ''}`, '');
    if (item.chat_source) lines.push(`> 来自 AI 对话：${item.chat_source.title}`, '');
    if (item.body.trim()) lines.push(item.body.trim(), '');
    const details = recordDetails(item);
    if (details.length) lines.push(...details.map(([label, value]) => `- **${label}**：${value.replace(/\s*\n\s*/g, ' ')}`), '');
  }
  return `${lines.join('\n').trimEnd()}\n`;
}
