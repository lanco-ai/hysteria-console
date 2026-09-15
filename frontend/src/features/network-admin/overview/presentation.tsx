export function fmtBytes(value: number): string {
  let n = Math.max(0, Math.trunc(value));
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let index = 0;
  while (n >= 1024 && index < units.length - 1) { n /= 1024; index++; }
  return `${n.toFixed(2)} ${units[index]}`;
}

export function Spark({ values }: { values: [string, number][] | null }) {
  if (!values) return null;
  const n = values.length, width = n * 3 + n - 1;
  if (!n) return <svg className="spark" viewBox="0 0 0 24" aria-hidden="true"/>;
  const peak = values.reduce((a, b) => b[1] > a[1] ? b : a), last = values[n - 1]!;
  const max = peak[1] || 1;
  const points = values.map(([, value], i) => `${(n === 1 ? width / 2 : i * width / (n - 1)).toFixed(2)},${Math.max(1, Math.min(23, 23 - 22 * Math.max(0, Math.trunc(value)) / max)).toFixed(2)}`);
  const [x, y] = points[n - 1]!.split(',');
  return <svg className="spark" viewBox={`0 0 ${width} 24`} aria-label={`${n} 天趋势`}><title>{`${n} 天趋势 · 最新 ${last[0]}: ${fmtBytes(last[1])} · 峰值 ${peak[0]}: ${fmtBytes(peak[1])}`}</title><path className="spark-area" d={`M0,23 ${points.map(p => `L${p}`).join(' ')} L${width},23 Z`}/><path className="spark-line" d={points.map((p, i) => `${i ? 'L' : 'M'}${p}`).join(' ')} vectorEffect="non-scaling-stroke"/><circle className="spark-dot today" cx={x} cy={y} r="1.8"/></svg>;
}

const messages = new Map([
  ['login success', '登录成功'], ['reset usage all', '已清除全部用户本周期已用流量'],
  ['user empty', '用户名不能为空'], ['username_invalid', '用户名只能包含字母、数字、点、下划线、连字符，且不能以 .json 结尾'],
  ['user_exists_use_reset_token', '用户已存在；请在用户列表中使用“重置订阅”，以执行完整的连接撤销与审计流程'],
  ['panel_password_short', '用户面板登录密码至少需要 8 位'], ['panel_password_long', '用户面板登录密码不能超过 256 位'], ['proxy_password_long', '代理连接密码不能超过 256 位'],
  ['max_devices_invalid', '设备数上限必须是 0–100 之间的整数；0 表示不限设备'], ['quota_invalid', '基础流量上限必须是 0–10240 之间的整数；0 表示不限流量'], ['quota_extra_invalid', '加量包必须是 0–10240 之间的整数'],
  ['expiry_invalid', '到期日无效，请使用 YYYY-MM-DD 格式'], ['note_too_long', '备注不能超过 200 个字符'], ['landing_too_long', '落地家宽信息不能超过 120 个字符'], ['landing_invalid', '落地家宽信息不能包含控制字符'], ['landing_ip_invalid', '请输入合法的 IPv4 或 IPv6 地址'], ['settlement_invalid', '结算日无效（请输入 1–28 之间的整数）'], ['cycle_length_invalid', '周期长度无效，请核对允许范围'],
]);
const prefixes = new Map([
  ['updated ', '已更新用户：'], ['created ', '已创建用户：'], ['reset usage ', '已清除用户本周期已用流量：'], ['refresh usage ', '已刷新用户本周期已用流量（服务器总流量不变）：'],
  ['deleted_retry ', '删除请求已安全记录；旧授权、历史数据或连接仍在后台复核，系统会持续自动重试，直至确认完成：'], ['deleted ', '已删除用户：'],
  ['rotated_pending ', '已重置订阅令牌；已确认暂停 Xray/TUIC，正在等待安全同步，Hysteria 连接断开请求将延迟复核：'],
  ['rotated_static_pending ', '已重置订阅令牌；受影响的静态代理因重载未能安排而已确认暂停，正在等待安全同步：'],
  ['rotated_retry ', '已重置订阅令牌，但未能确认所有旧连接或静态代理均已停止；系统会持续自动重试，直至确认完成：'],
  ['rotated ', '已重置订阅令牌（旧订阅/面板链接已失效，连接断开请求正在复核）：'], ['disabled ', '已停用用户（已请求断开连接）：'], ['enabled ', '已启用用户：'], ['paused ', '已暂停用户 1 小时（已请求断开连接）：'],
]);
export function feedback(raw: string): string {
  const msg = raw.startsWith('err:') ? raw.slice(4) : raw;
  if (messages.has(msg)) return messages.get(msg)!;
  for (const [prefix, label] of prefixes) if (msg.startsWith(prefix)) return label + msg.slice(prefix.length);
  if (msg.startsWith('settlement ')) return `已更新结算日：每月 ${msg.slice(11)} 日`;
  return msg;
}
