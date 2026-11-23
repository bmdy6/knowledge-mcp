"""Check evaluation arithmetic and the vault's read-only output boundary."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location('retrieval_eval', Path(__file__).resolve().parents[1] / 'evals/run.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_percentiles_interpolate():
    assert evaluation.percentile([40., 10., 30., 20.], .5) == 25.
    assert evaluation.percentile([40., 10., 30., 20.], .95) == pytest.approx(38.5)


def test_evaluation_metrics_and_summary_privacy(tmp_path, monkeypatch):
    class Match:
        def __init__(self, path):
            self.path = path
    class FakeVault:
        def __init__(self, root):
            pass
        def search(self, scope, query, **kwargs):
            return {'PRIVATE_A': [Match('personal/a.md')],
                    'PRIVATE_B': [Match('personal/a.md'), Match('personal/b.md')],
                    'PRIVATE_C': []}[query]
    corpus = tmp_path / 'vault/personal'
    corpus.mkdir(parents=True)
    for name in ['a', 'b', 'c']:
        (corpus / f'{name}.md').write_text('private document')
    questions = tmp_path / 'questions.jsonl'
    questions.write_text(''.join(json.dumps({'id':name, 'scope':'personal', 'query':f'PRIVATE_{name.upper()}',
                                           'expected_paths':[f'personal/{name}.md']}) + '\n'
                                 for name in ['a', 'b', 'c']))
    output = tmp_path / 'summary.json'
    monkeypatch.setattr(evaluation, 'Vault', FakeVault)
    monkeypatch.setattr(sys, 'argv', ['run.py', '--vault', str(corpus.parent), '--questions', str(questions),
                                     '--output', str(output), '--summary-only'])
    evaluation.main()
    result = json.loads(output.read_text())
    assert result['summary']['recall@1'] == pytest.approx(1/3)
    assert result['summary']['recall@5'] == pytest.approx(2/3)
    assert result['summary']['recall@10'] == pytest.approx(2/3)
    assert result['summary']['mrr@10'] == .5
    assert 'details' not in result
    assert 'PRIVATE' not in output.read_text()
    assert 'personal/' not in output.read_text()


def test_output_cannot_write_inside_vault(tmp_path, monkeypatch):
    output = tmp_path / 'result.json'
    monkeypatch.setattr(sys, 'argv', ['run.py', '--vault', str(tmp_path), '--output', str(output)])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 2
    assert not output.exists()
