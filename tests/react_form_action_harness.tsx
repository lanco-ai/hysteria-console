import { createRoot, type Root } from 'react-dom/client';
import { useFormAction } from '../frontend/src/shared/useFormAction';

let root: Root | null = null;
let settle: (() => void) | null = null;
let resultCount = 0;
let errorCount = 0;
let abortCount = 0;

document.body.innerHTML = `
  <button id="mount" type="button">挂载</button>
  <button id="unmount" type="button">卸载</button>
  <button id="settle" type="button">完成</button>
  <span id="results">0</span>
  <span id="errors">0</span>
  <span id="aborts">0</span>
  <div id="test-root"></div>
`;

function update(id: string, value: number) {
  const target = document.getElementById(id);
  if (target) target.textContent = String(value);
}

function Harness() {
  const { busy, run } = useFormAction();
  const callbacks = {
    onResult: () => {
      resultCount += 1;
      update('results', resultCount);
    },
    onError: () => {
      errorCount += 1;
      update('errors', errorCount);
    },
  };
  const start = () => {
    void run(
      signal => new Promise<string>(resolve => {
        signal.addEventListener('abort', () => {
          abortCount += 1;
          update('aborts', abortCount);
        }, { once: true });
        settle = () => resolve('finished');
      }),
      callbacks,
    );
  };
  const timeoutThenResolve = () => {
    void run(
      signal => new Promise<string>(resolve => {
        signal.addEventListener('abort', () => resolve('resolved-during-abort'), { once: true });
      }),
      callbacks,
    );
  };
  const throwingStart = () => {
    void run(
      async () => 'operation-must-not-run',
      {
        ...callbacks,
        onStart: () => {
          throw new Error('start callback failed');
        },
      },
    );
  };
  return <>
    <button type="button" onClick={start}>开始</button>
    <button type="button" onClick={timeoutThenResolve}>超时后同步完成</button>
    <button type="button" onClick={throwingStart}>开始回调抛错</button>
    <span id="busy">{String(busy)}</span>
  </>;
}

document.getElementById('mount')?.addEventListener('click', () => {
  if (root) return;
  const target = document.getElementById('test-root');
  if (!target) throw new Error('Missing harness root');
  root = createRoot(target);
  root.render(<Harness/>);
});
document.getElementById('unmount')?.addEventListener('click', () => {
  root?.unmount();
  root = null;
});
document.getElementById('settle')?.addEventListener('click', () => {
  settle?.();
  settle = null;
});
