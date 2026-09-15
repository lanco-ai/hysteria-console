import { useCallback, useEffect, useState } from 'react';

const REQUEST_TIMEOUT_MS = 10_000;

// Keep successful reads in the current document so returning to a page is
// immediate. Every mount still revalidates in the background, so this is a
// stale-while-revalidate cache rather than a replacement for server reads.
const RESOURCE_CACHE = new Map<string, unknown>();

export type ResourceAccessCode = 'login_required' | 'forbidden' | 'disabled' | 'expired' | 'password_change_required';

const ACCESS_CODES = new Set<ResourceAccessCode>([
  'login_required',
  'forbidden',
  'disabled',
  'expired',
  'password_change_required',
]);

export class ResourceError extends Error {
  readonly status: number | undefined;
  readonly code: ResourceAccessCode | undefined;

  constructor(message: string, status?: number, code?: ResourceAccessCode) {
    super(message);
    this.name = 'ResourceError';
    this.status = status;
    this.code = code;
  }
}

export async function readAccessCode(response: Response): Promise<ResourceAccessCode | undefined> {
  try {
    const value: unknown = await response.json();
    if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
    const code = (value as Record<string, unknown>).error;
    return typeof code === 'string' && ACCESS_CODES.has(code as ResourceAccessCode)
      ? code as ResourceAccessCode
      : undefined;
  } catch {
    return undefined;
  }
}

type ResourceState<T> =
  | { status: 'idle' | 'loading'; data?: undefined; error?: undefined }
  | { status: 'success'; data: T; error?: undefined }
  | { status: 'error'; data?: undefined; error: ResourceError };

type ReadResourceOptions<T> = {
  enabled?: boolean;
  validate: (value: unknown) => T;
};

type ReadResourceResult<T> =
  | { status: 'idle' | 'loading'; data?: undefined; error?: undefined; retry: () => void }
  | { status: 'success'; data: T; error?: undefined; retry: () => void }
  | { status: 'error'; data?: undefined; error: ResourceError; retry: () => void };

export function useReadResource<T>(url: string, { enabled = true, validate }: ReadResourceOptions<T>): ReadResourceResult<T> {
  const [version, setVersion] = useState(0);
  const [state, setState] = useState<ResourceState<T>>(() => {
    if (!enabled) return { status: 'idle' };
    return RESOURCE_CACHE.has(url)
      ? { status: 'success', data: RESOURCE_CACHE.get(url) as T }
      : { status: 'loading' };
  });

  const retry = useCallback(() => setVersion(value => value + 1), []);

  useEffect(() => {
    if (!enabled) {
      setState({ status: 'idle' });
      return;
    }

    const controller = new AbortController();
    let active = true;
    let timedOut = false;
    const timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, REQUEST_TIMEOUT_MS);
    const hasCachedData = RESOURCE_CACHE.has(url);
    if (hasCachedData) {
      setState({ status: 'success', data: RESOURCE_CACHE.get(url) as T });
    } else {
      setState({ status: 'loading' });
    }

    void (async () => {
      try {
        const response = await fetch(url, {
          cache: 'no-store',
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new ResourceError(
            `HTTP ${response.status}`,
            response.status,
            await readAccessCode(response),
          );
        }
        let raw: unknown;
        try {
          raw = await response.json();
        } catch {
          throw new ResourceError('响应不是有效的 JSON');
        }
        let data: T;
        try {
          data = validate(raw);
        } catch {
          throw new ResourceError('响应数据格式无效');
        }
        if (active) {
          RESOURCE_CACHE.set(url, data);
          setState({ status: 'success', data });
        }
      } catch (error) {
        if (!active) return;
        // Keep stale data visible if a background revalidation fails. A page
        // that has never loaded still receives the normal error state.
        if (hasCachedData) return;
        if (timedOut) {
          setState({ status: 'error', error: new ResourceError('请求超时，请重试') });
        } else if (error instanceof ResourceError) {
          setState({ status: 'error', error });
        } else if (!(error instanceof DOMException && error.name === 'AbortError')) {
          setState({ status: 'error', error: new ResourceError('网络请求失败') });
        }
      } finally {
        window.clearTimeout(timeout);
      }
    })();

    return () => {
      active = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [enabled, url, validate, version]);

  return { ...state, retry };
}
