import { useCallback, useEffect, useState } from 'react';

const REQUEST_TIMEOUT_MS = 10_000;

export class ResourceError extends Error {
  readonly status: number | undefined;

  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ResourceError';
    this.status = status;
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
  const [state, setState] = useState<ResourceState<T>>({ status: enabled ? 'loading' : 'idle' });

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
    setState({ status: 'loading' });

    void (async () => {
      try {
        const response = await fetch(url, {
          cache: 'no-store',
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
          signal: controller.signal,
        });
        if (!response.ok) throw new ResourceError(`HTTP ${response.status}`, response.status);
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
        if (active) setState({ status: 'success', data });
      } catch (error) {
        if (!active) return;
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
