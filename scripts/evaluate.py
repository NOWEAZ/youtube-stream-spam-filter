"""Reproducible algorithm benchmarks. Optional labeled CSV: text,label (ham/spam or 0/1)."""
import argparse
import csv
import json
import random
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.algorithms import BloomFilter, DGIM, FlajoletMartin, MinHashLSH, jaccard, shingles
from backend.blocklist import Blocklist
from backend.engine import Engine


def run(size):
    bloom = BloomFilter(size)
    start = time.perf_counter()
    exact = {f'https://sample-{i}.test/a' for i in range(size)}
    set_build = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    for value in exact: bloom.add(value)
    bloom_build = (time.perf_counter() - start) * 1000
    queries = [f'https://unseen-{i}.test/b' for i in range(size)]
    start = time.perf_counter(); hits = sum(value in bloom for value in queries)
    bloom_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter(); exact_hits = sum(value in exact for value in queries)
    set_ms = (time.perf_counter() - start) * 1000
    rng, dgim, fm, bits = random.Random(42), DGIM(1000), FlajoletMartin(), []
    for i in range(4000):
        bit = rng.random() < 0.2; bits.append(bit); dgim.add(bit); fm.add(str(i % 800))
    texts = [f'주제 {i}번째 별도 설명 영상 감상 소감 {rng.getrandbits(64)}' for i in range(150)]
    texts += [text + '!' for text in texts[:30]]
    sets = [shingles(text) for text in texts]
    true_pairs = {(i, j) for i in range(len(sets)) for j in range(i) if jaccard(sets[i], sets[j]) >= .72}
    index, found = MinHashLSH(), set()
    for i, (text, elements) in enumerate(zip(texts, sets)):
        matches, sig = index.query(elements, .72)
        # Compare exact candidates using the actual index buckets for pair-level recall.
        candidates = set().union(*(index.buckets.get(key, set()) for key in index.keys(sig)))
        found.update((i, int(j)) for j in candidates if jaccard(elements, sets[int(j)]) >= .72)
        index.add(str(i), elements, sig, text, 'safe')
    return {'seed': 42, 'synthetic': True, 'bloom': {
        'members': size, 'bits': bloom.n, 'hashes': bloom.k, 'bit_array_bytes': len(bloom.bits),
        'python_set_estimated_bytes': sys.getsizeof(exact) + sum(sys.getsizeof(x) for x in exact),
        'predicted_false_positive_rate': bloom.predicted_error, 'measured_false_positive_rate': hits / size,
        'false_negatives': sum(value not in bloom for value in exact),
        'bloom_build_ms': bloom_build, 'set_build_ms': set_build,
        'bloom_query_ms': bloom_ms, 'set_query_ms': set_ms, 'set_nonmember_hits': exact_hits,
        'timing_note': 'Filter-only timing; SQLite exact confirmation and HTTP are excluded.'},
        'dgim': {'exact': sum(bits[-1000:]), 'estimate': dgim.estimate(), 'buckets': len(dgim.buckets)},
        'fm': {'exact': 800, 'estimate': fm.estimate(), 'note': 'Textbook estimator can have large error.'},
        'lsh': {'documents': len(texts), 'brute_force_pairs': len(texts)*(len(texts)-1)//2,
                'candidate_comparisons': index.comparisons, 'true_similar_pairs': len(true_pairs),
                'found_similar_pairs': len(found & true_pairs),
                'recall': len(found & true_pairs) / len(true_pairs) if true_pairs else None}}


def evaluate_csv(path):
    temp_root = Path(__file__).resolve().parents[1]/'.local/test-temp'
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temp_root) as temp:
        blocklist = Blocklist(Path(temp)/'empty.db')
        engine = Engine(blocklist)
        tp = fp = fn = tn = reviews = 0
        try:
            with open(path, newline='', encoding='utf-8-sig') as source:
                for i, row in enumerate(csv.DictReader(source)):
                    label = row['label'].strip().lower()
                    if label not in ('0', '1', 'ham', 'spam'): raise ValueError('label must be 0, 1, ham or spam')
                    actual = label in ('1', 'spam')
                    prediction = engine.analyze('csv', [{'id': str(i), 'text': row['text'][:2000]}])['results'][0]['verdict']
                    reviews += prediction == 'review'
                    positive = prediction == 'spam'
                    tp += actual and positive; fp += not actual and positive
                    fn += actual and not positive; tn += not actual and not positive
        finally: blocklist.db.close()
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    return {'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn, 'review': reviews,
            'precision': precision, 'recall': recall, 'f1': 2*precision*recall/(precision+recall) if precision+recall else 0,
            'note': 'Empty blacklist, Solar disabled. Review counts as non-positive. Sequential CSV order; no claimed training accuracy.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--size', type=int, default=10000)
    parser.add_argument('--csv', type=Path)
    parser.add_argument('--output', type=Path, default=Path('reports/benchmark.json'))
    args = parser.parse_args()
    if not 1 <= args.size <= 1_000_000: parser.error('size must be 1..1000000')
    report = run(args.size)
    if args.csv: report['classification'] = evaluate_csv(args.csv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
