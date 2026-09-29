"""CPU-only E5 inference in a short-lived process, with no network or model code."""
import json
import os
from pathlib import Path
import resource
import sys

os.environ.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', TOKENIZERS_PARALLELISM='false')
resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024,) * 2)
resource.setrlimit(resource.RLIMIT_CPU, (240, 240))


def main():
    import numpy as np
    import onnxruntime as ort
    import sentencepiece as spm
    root = Path(sys.argv[1])
    payload = json.loads(sys.stdin.buffer.read(4 * 1024 * 1024))
    texts = payload['texts']
    if not isinstance(texts, list) or len(texts) > 2000 or any(not isinstance(t, str) or len(t) > 20000 for t in texts):
        raise ValueError('input_limit')
    tokenizer = spm.SentencePieceProcessor(model_file=str(root / 'sentencepiece.bpe.model'))
    tokens = []
    for text in texts:
        pieces = tokenizer.encode(('query: ' if payload.get('query') else 'passage: ') + text, out_type=int)
        # XLM-R / E5 uses fairseq's +1 vocabulary offset, with <unk>=3 and BOS/EOS=0/2.
        ids = [0, *(piece + 1 if piece else 3 for piece in pieces[:510]), 2]
        tokens.append((ids, [1] * len(ids), [0] * len(ids)))
    # The multilingual vocabulary is large. Release it before loading ONNX weights.
    del tokenizer
    import gc
    import ctypes
    gc.collect()
    ctypes.CDLL(None).malloc_trim(0)
    options = ort.SessionOptions()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    options.enable_mem_pattern = False
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    session = ort.InferenceSession(str(root / 'model_quantized.onnx'), sess_options=options, providers=['CPUExecutionProvider'])
    names = {i.name for i in session.get_inputs()}
    result = []
    for token_ids, attention_mask, type_ids in tokens:
        ids = np.array([token_ids], dtype=np.int64)
        mask = np.array([attention_mask], dtype=np.int64)
        feed = {'input_ids': ids, 'attention_mask': mask}
        if 'token_type_ids' in names:
            feed['token_type_ids'] = np.array([type_ids], dtype=np.int64)
        hidden = session.run(None, feed)[0]
        vector = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
        vector /= np.linalg.norm(vector, axis=1, keepdims=True).clip(min=1e-12)
        result.append(vector[0].astype(float).tolist())
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.stdout.write('{"error":"embedding_failed"}')
        raise SystemExit(1)
