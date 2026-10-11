import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ReactElement } from 'react';
import { journalKindLabels } from './journalLabels';
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

/**
 * A record's text written in Markdown. Raw HTML is dropped and outside images are not loaded:
 * the records are private, and a picture link would tell its host when they are read.
 */
export function JournalMarkdown({ text }: { text: string }): ReactElement {
  return <div className="journal-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
    a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
    img: ({ alt }) => <span>[图片：{alt || '未加载外部图片'}]</span>,
  }}>{text}</ReactMarkdown></div>;
}

const dayFormat = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long', timeZone: 'UTC' });

function clock(item: JournalRecord, value: string): string {
  return new Intl.DateTimeFormat('zh-CN', { timeZone: item.timezone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(value));
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
    const time = clock(item, item.occurred_at) + (item.ended_at ? `–${clock(item, item.ended_at)}` : '');
    lines.push(`### ${time} · ${journalKindLabels[item.kind]}${item.title ? ` · ${item.title}` : ''}`, '');
    if (item.chat_source) lines.push(`> 来自 AI 对话：${item.chat_source.title}`, '');
    if (item.body.trim()) lines.push(item.body.trim(), '');
    const details = recordDetails(item);
    if (details.length) lines.push(...details.map(([label, value]) => `- **${label}**：${value.replace(/\s*\n\s*/g, ' ')}`), '');
  }
  return `${lines.join('\n').trimEnd()}\n`;
}
