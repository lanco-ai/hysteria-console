export function hasExactKeys(record: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(record).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

export async function postFormJson(
  path: string,
  fields: Record<string, string>,
  signal: AbortSignal,
): Promise<{ value: unknown; status: number }> {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Accept: 'application/json' },
    body: new URLSearchParams(fields),
    signal,
  });
  const contentType = response.headers.get('content-type') || '';
  if (!/^application\/json(?:\s*;|$)/i.test(contentType)) throw new Error('Invalid JSON response');
  return { value: await response.json(), status: response.status };
}
