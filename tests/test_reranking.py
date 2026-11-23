"""Optional model paths run without downloads or a fastembed installation."""
import logging
import asyncio
import os
from pathlib import Path
import sys

import pytest

from knowledge_mcp import server
from knowledge_mcp.reranking import RerankConfig, get_reranker, load_config
from knowledge_mcp.vault import Vault


@pytest.fixture
def corpus(tmp_path):
    (tmp_path / 'personal').mkdir()
    for i in range(15):
        (tmp_path / f'personal/{i:02}.md').write_text(
            f'# 队列 {i}\n\n队列处理消息，说明编号 {i}。\n', encoding='utf-8')
    (tmp_path / 'company').mkdir()
    (tmp_path / 'company/secret.md').write_text('# 队列\n机密队列\n', encoding='utf-8')
    (tmp_path / 'archive').mkdir()
    (tmp_path / 'archive/old.md').write_text('# 队列\n旧队列\n', encoding='utf-8')
    return tmp_path


@pytest.fixture(autouse=True)
def clear_cache():
    get_reranker.cache_clear()
    yield
    get_reranker.cache_clear()


def test_disabled_is_exact_bm25_and_does_not_load(corpus, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('disabled mode must not load optional dependency')
    monkeypatch.setattr('knowledge_mcp.reranking._load_encoder', forbidden)
    original = Vault(corpus).search('personal', '队列', limit=4)
    disabled = Vault(corpus, rerank=RerankConfig()).search('personal', '队列', limit=4)
    assert disabled == original
    assert set(Vault.serialize_result(original[0])) == {
        'scope', 'path', 'line_start', 'line_end', 'title', 'snippet'}


def test_reranks_larger_pool_preserves_locations_and_adds_scores(corpus, monkeypatch):
    observed = []
    class Encoder:
        def rerank(self, query, documents, **kwargs):
            observed.append((query, list(documents)))
            return range(len(documents))
    monkeypatch.setattr('knowledge_mcp.reranking._load_encoder', lambda _: Encoder())
    bm25 = Vault(corpus).search('personal', '队列', limit=12)
    results = Vault(corpus, rerank=RerankConfig(enabled=True, top_n=12)).search(
        'personal', '队列', limit=3)
    assert [r.path for r in results] == [r.path for r in reversed(bm25[-3:])]
    assert [r.rerank_score for r in results] == [11., 10., 9.]
    assert observed == [('队列', [f'{r.title}\n{r.snippet}' for r in bm25])]
    assert all('机密' not in doc and '旧队列' not in doc for doc in observed[0][1])
    for r in results:
        original = next(b for b in bm25 if b.path == r.path)
        assert (r.line_start, r.line_end, r.snippet) == (
            original.line_start, original.line_end, original.snippet)
        assert Vault.serialize_result(r)['rerank_score'] == r.rerank_score


@pytest.mark.parametrize('failure', ['missing', 'load', 'inference', 'short', 'nan', 'infinite'])
def test_failures_fall_back_exactly_without_leaking_text(corpus, monkeypatch, caplog, failure):
    attempts = []
    class Encoder:
        def rerank(self, query, docs, **kwargs):
            if failure == 'inference':
                raise RuntimeError('PRIVATE_QUERY_AND_DOCUMENT')
            if failure == 'short':
                return [1.]
            return [float('nan') if failure == 'nan' else float('inf')] * len(docs)
    def load(config):
        attempts.append(config)
        if failure == 'missing':
            raise ImportError('PRIVATE_QUERY_AND_DOCUMENT')
        if failure == 'load':
            raise OSError('PRIVATE_QUERY_AND_DOCUMENT')
        return Encoder()
    monkeypatch.setattr('knowledge_mcp.reranking._load_encoder', load)
    expected = Vault(corpus).search('personal', '队列', limit=4)
    config = RerankConfig(enabled=True, top_n=12)
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            actual = Vault(corpus, rerank=config).search('personal', '队列', limit=4)
            assert actual == expected
    assert len(attempts) == 1
    assert 'BM25' in caplog.text
    assert 'PRIVATE_QUERY_AND_DOCUMENT' not in caplog.text


def test_reuses_model_across_vault_instances_and_keeps_stable_ties(corpus, monkeypatch):
    loads = []
    class Encoder:
        def rerank(self, query, documents, **kwargs):
            return [0.] * len(documents)
    def load(config):
        loads.append(config)
        return Encoder()
    monkeypatch.setattr('knowledge_mcp.reranking._load_encoder', load)
    config = RerankConfig(enabled=True)
    for _ in range(2):
        actual = Vault(corpus, rerank=config).search('personal', '队列', limit=5)
        assert [r.path for r in actual] == [
            r.path for r in Vault(corpus).search('personal', '队列', limit=5)]
    assert len(loads) == 1


def test_limit_larger_than_top_n_and_archive_opt_in(corpus, monkeypatch):
    class Encoder:
        def rerank(self, query, docs, **kwargs):
            return [0.] * len(docs)
    monkeypatch.setattr('knowledge_mcp.reranking._load_encoder', lambda _: Encoder())
    v = Vault(corpus, rerank=RerankConfig(enabled=True, top_n=2))
    assert len(v.search('personal', '队列', limit=10)) == 10
    results = v.search('personal', '队列', limit=100, include_archive=True)
    assert 'archive/old.md' in [r.path for r in results]
    assert 'company/secret.md' not in [r.path for r in results]
    assert all(r.rerank_score is not None for r in results)


def test_empty_and_invalid_queries_do_not_load(corpus, monkeypatch):
    def forbidden(_):
        pytest.fail('unexpected model load')
    monkeypatch.setattr('knowledge_mcp.reranking._load_encoder', forbidden)
    v = Vault(corpus, rerank=RerankConfig(enabled=True))
    assert v.search('personal', 'not-present') == []
    assert v.search('personal', '') == []
    with pytest.raises(ValueError):
        v.search('personal', '队列', limit=0)


def test_configuration_and_mcp_serialization(corpus, tmp_path, monkeypatch):
    config_file = tmp_path / 'settings.yml'
    config_file.write_text('rerank:\n  enabled: true\n  model: test/model\n  top_n: 12\n')
    assert load_config(config_file) == RerankConfig(True, 'test/model', 12)
    monkeypatch.setenv('KNOWLEDGE_VAULT', str(corpus))
    monkeypatch.setenv('KNOWLEDGE_MCP_CONFIG', str(config_file))
    class Encoder:
        def rerank(self, query, docs, **kwargs):
            return range(len(docs))
    monkeypatch.setattr('knowledge_mcp.reranking._load_encoder', lambda _: Encoder())
    result = server.search('personal', '队列', limit=3)
    assert [r['rerank_score'] for r in result] == [11., 10., 9.]
    assert all('path' in r and 'line_start' in r for r in result)


@pytest.mark.parametrize('kwargs', [
    {'enabled': 'false'}, {'top_n': 0}, {'top_n': 101}, {'top_n': True}, {'model': ''}])
def test_config_validation(kwargs):
    with pytest.raises(ValueError):
        RerankConfig(**kwargs)


def test_empty_config_is_default(tmp_path):
    assert load_config(None) == RerankConfig()
    empty = tmp_path / 'empty.yml'
    empty.write_text('')
    assert load_config(empty) == RerankConfig()


def test_config_cli_overrides_environment(corpus, tmp_path, monkeypatch):
    config_file = tmp_path / 'settings.yml'
    config_file.write_text('rerank:\n  top_n: 12\n')
    monkeypatch.setenv('KNOWLEDGE_VAULT', str(corpus))
    monkeypatch.setenv('KNOWLEDGE_MCP_CONFIG', 'missing.yml')
    found = []
    monkeypatch.setattr(server.mcp, 'run', lambda **_: found.append(server.get_vault().rerank))
    server.main(['--vault', str(corpus), '--config', str(config_file)])
    assert found == [RerankConfig(top_n=12)]


def test_rerank_preserves_partial_diagnostics(corpus, monkeypatch):
    (corpus / 'personal/broken.md').write_text('---\ntitle: [PRIVATE\n---\n')
    class Encoder:
        def rerank(self, query, documents, **kwargs):
            return [1.] * len(documents)
    monkeypatch.setattr('knowledge_mcp.reranking._load_encoder', lambda _: Encoder())
    vault = Vault(corpus, rerank=RerankConfig(enabled=True))
    diagnostics = []
    assert vault.search('personal', '队列', diagnostics=diagnostics)
    assert diagnostics == [{'path': 'personal/broken.md', 'code': 'invalid-frontmatter'}]
    with pytest.warns(RuntimeWarning, match='Partial search') as captured:
        vault.search('personal', '队列')
    assert len(captured) == 1
    assert 'PRIVATE' not in str(captured[0].message)


def test_input_is_bounded_but_returned_title_is_preserved(corpus, monkeypatch):
    long_title = '队列' * 1000
    (corpus / 'personal/long.md').write_text(f'# {long_title}\n\n队列\n')
    observed = []
    class Encoder:
        def rerank(self, query, docs, **kwargs):
            observed.extend(docs)
            return [1.] * len(docs)
    monkeypatch.setattr('knowledge_mcp.reranking._load_encoder', lambda _: Encoder())
    results = Vault(corpus, rerank=RerankConfig(enabled=True)).search('personal', '队列')
    assert all(len(doc) <= 1257 for doc in observed)
    assert next(r for r in results if r.path.endswith('/long.md')).title == long_title


def test_real_stdio_protocol_exposes_rerank_scores(corpus, tmp_path):
    # A subprocess-local fake optional package verifies the actual MCP wiring
    # in normal CI, without requiring the 1 GB model or network access.
    package = tmp_path / 'fake/fastembed/rerank/cross_encoder'
    package.mkdir(parents=True)
    for parent in (package.parent, package.parent.parent):
        (parent / '__init__.py').write_text('')
    (package / '__init__.py').write_text(
        'class TextCrossEncoder:\n'
        '    def __init__(self, **kwargs):\n'
        '        assert kwargs["providers"] == ["CPUExecutionProvider"]\n'
        '    def rerank(self, query, documents, **kwargs):\n'
        '        return range(len(documents))\n')
    config = tmp_path / 'settings.yml'
    config.write_text('rerank:\n  enabled: true\n  top_n: 12\n')
    asyncio.run(_assert_stdio_scores(corpus, config, tmp_path / 'fake'))


async def _assert_stdio_scores(root, config, fake):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(
        command=sys.executable,
        args=['-m', 'knowledge_mcp.server', '--vault', str(root), '--config', str(config)],
        env={**os.environ, 'PYTHONPATH': os.pathsep.join([
            str(fake), str(Path(__file__).resolve().parents[1] / 'src')])})
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            for _ in range(2):
                response = await session.call_tool('search', {
                    'scope': 'personal', 'query': '队列', 'limit': 3})
                assert not response.isError
                entries = response.structuredContent['result']
                assert [r['rerank_score'] for r in entries] == [11., 10., 9.]
                assert all(r['path'].startswith('personal/') for r in entries)
