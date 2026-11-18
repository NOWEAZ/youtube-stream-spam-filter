import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from scripts.check_ai_labels import check, load_snapshot

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'data/ai_labels/pilot-v1.jsonl'


class SharedLabelsTest(unittest.TestCase):
    def test_shared_snapshot_provenance_counts_and_excluded_fields(self):
        rows, manifest = load_snapshot(DATA)
        self.assertEqual(dict(Counter(r['ai_label'] for r in rows)), manifest['ai_label_counts'])
        self.assertEqual(dict(Counter(r['source'] for r in rows)), manifest['source_counts'])
        for row in rows:
            self.assertTrue(row['ai_reason'])
            self.assertEqual(len(row['prompt_hash']), 64)
            self.assertFalse({'AUTHOR', 'CLASS', 'source_id', 'imported_at', 'api_key', 'token'} & row.keys())

    def test_team_comparison_makes_no_network_requests(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('Network calls forbidden')) as network:
            result = check(DATA)
        network.assert_not_called()
        self.assertEqual(result['api_requests'], 0)
        self.assertEqual(sum(result['rule_label_counts'].values()), result['records'])
        self.assertNotIn('accuracy', result)
        self.assertLessEqual(result['agreed_on_resolved'], result['resolved_comparisons'])

    def test_checksum_and_unreviewed_status_are_both_validated(self):
        root = ROOT/'.local/test-temp'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            path = Path(directory)/'pilot-v1.jsonl'
            path.write_bytes(DATA.read_bytes() + b'\n')
            manifest = json.loads(DATA.with_suffix('.manifest.json').read_text(encoding='utf-8'))
            metadata = path.with_suffix('.manifest.json')
            metadata.write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_snapshot(path)
            rows, _ = load_snapshot(DATA)
            rows[0]['eligible_for_gold_evaluation'] = True
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows), encoding='utf-8')
            manifest['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            metadata.write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_snapshot(path)


if __name__ == '__main__':
    unittest.main()
