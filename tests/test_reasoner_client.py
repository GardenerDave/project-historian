import json
from pathlib import Path
from historian.reasoner_client import durable
import pytest
from historian.reasoner_client import run

def test_journal_durable_transitions(tmp_path):
    p=tmp_path/'q.transaction.json'; durable(p,{'state':'PREPARED','response_present':False}); assert json.loads(p.read_text())['state']=='PREPARED'
    for state in ('REQUEST_STARTING','RESPONSE_RECEIVED','RESPONSE_PRESERVED','COMPLETE'):
        durable(p,{'state':state,'response_present':state!='REQUEST_STARTING'}); assert json.loads(p.read_text())['state']==state

def test_no_endpoint_literal_in_client_source():
    text=Path('historian/reasoner_client.py').read_text(); assert '192.168.' not in text

@pytest.mark.parametrize('suffix',['.transaction.json','.request.json','.raw-http','.response.json','.content.json','.result.json'])
def test_any_attempt_artifact_blocks_before_network(tmp_path,monkeypatch,suffix):
    (tmp_path/('q'+suffix)).write_bytes(b'x'); called=[]
    monkeypatch.setattr('historian.reasoner_client.urllib.request.urlopen',lambda *a,**k: called.append(1))
    with pytest.raises(RuntimeError): run('q',tmp_path,'http://private','missing.json')
    assert called==[]

def test_durable_bytes_is_exact(tmp_path):
    from historian.reasoner_client import durable_bytes
    p=tmp_path/'x.raw-http'; durable_bytes(p,b'bad-json'); assert p.read_bytes()==b'bad-json'

def test_dynamic_schema_does_not_mutate_global():
    from historian.grounded_schema import SCHEMA,schema_for_record_ids
    import copy
    before=copy.deepcopy(SCHEMA); s=schema_for_record_ids(['B','A'])
    assert s['properties']['cited_record_ids']['items']['enum']==['A','B']; assert SCHEMA==before

def test_dynamic_schema_rejects_duplicate_ids():
    from historian.grounded_schema import schema_for_record_ids
    with pytest.raises(ValueError): schema_for_record_ids(['A','A'])

def test_request_artifact_is_written_before_http_and_hash_matches(tmp_path, monkeypatch):
    retrieval = tmp_path / 'retrieval.json'
    retrieval.write_text('{"queries":[{"id":"q","question":"question"}]}')
    run_dir = tmp_path / 'run'
    bundle = {'records':[{'record_id':'A'}]}
    monkeypatch.setattr('historian.reasoner_client.load_documents', lambda *a, **k: [])
    monkeypatch.setattr('historian.reasoner_client.structured_index', lambda *a, **k: ({}, [], {'canonical_records': 0, 'declared_relationships': 0, 'in_corpus_edges': 0, 'external_targets': 0, 'malformed': 0, 'duplicates': 0}))
    monkeypatch.setattr('historian.reasoner_client.materialize_frozen_retrieval_result', lambda *a, **k: bundle)
    monkeypatch.setattr('historian.reasoner_client.reasoner_view', lambda b: b)
    monkeypatch.setattr('historian.reasoner_client.reasoning_request', lambda question, bundle: {'system': 's', 'user': question + '\n\nEVIDENCE BUNDLE:\n## [A] A'})
    monkeypatch.setattr('historian.reasoner_client.validate_schema', lambda parsed: {'valid': True, 'errors': []})
    monkeypatch.setattr('historian.reasoner_client.validate_answer', lambda parsed, bundle: {'valid': True, 'errors': []})
    import hashlib, json
    captured = {}
    class Resp:
        status = 200
        def read(self):
            return b'{"choices":[{"message":{"content":"{\\"answer\\":\\"ok\\",\\"cited_record_ids\\":[\\"A\\"],\\"evidence_used\\":[\\"A\\"],\\"uncertainty_or_limitations\\":\\"none\\",\\"contradictions_or_missing_evidence\\":[]}"}}]}'
    def fake_urlopen(req, timeout=0):
        captured['body'] = req.data
        assert (run_dir / 'q.request.json').exists()
        return Resp()
    monkeypatch.setattr('historian.reasoner_client.urllib.request.urlopen', fake_urlopen)
    run('q', run_dir, 'http://example', retrieval, max_tokens=16)
    req = (run_dir / 'q.request.json').read_bytes()
    assert req == captured['body']
    assert hashlib.sha256(req).hexdigest() == json.loads((run_dir / 'q.transaction.json').read_text())['request_sha256']

def test_malformed_http_json_preserves_request_and_raw_http(tmp_path, monkeypatch):
    retrieval = tmp_path / 'retrieval.json'
    retrieval.write_text('{"queries":[{"id":"q","question":"question"}]}')
    run_dir = tmp_path / 'run'
    bundle = {'records':[{'record_id':'A'}]}
    monkeypatch.setattr('historian.reasoner_client.load_documents', lambda *a, **k: [])
    monkeypatch.setattr('historian.reasoner_client.structured_index', lambda *a, **k: ({}, [], {'canonical_records': 0, 'declared_relationships': 0, 'in_corpus_edges': 0, 'external_targets': 0, 'malformed': 0, 'duplicates': 0}))
    monkeypatch.setattr('historian.reasoner_client.materialize_frozen_retrieval_result', lambda *a, **k: bundle)
    monkeypatch.setattr('historian.reasoner_client.reasoner_view', lambda b: b)
    monkeypatch.setattr('historian.reasoner_client.reasoning_request', lambda question, bundle: {'system': 's', 'user': question + '\n\nEVIDENCE BUNDLE:\n## [A] A'})
    class Resp:
        status = 200
        def read(self):
            return b'not-json'
    monkeypatch.setattr('historian.reasoner_client.urllib.request.urlopen', lambda *a, **k: Resp())
    run('q', run_dir, 'http://example', retrieval, max_tokens=16)
    assert (run_dir / 'q.request.json').exists()
    assert (run_dir / 'q.raw-http').read_bytes() == b'not-json'
    assert json.loads((run_dir / 'q.transaction.json').read_text())['state'] == 'SEND_STATE_AMBIGUOUS'


def test_reasoner_client_uses_repo_root_not_cwd(tmp_path, monkeypatch):
    retrieval = tmp_path / 'retrieval.json'
    retrieval.write_text('{"queries":[{"id":"q","question":"question"}]}')
    run_dir = tmp_path / 'run'
    bundle = {'records':[{'record_id':'A'}]}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('historian.reasoner_client.load_documents', lambda *a, **k: [])
    monkeypatch.setattr('historian.reasoner_client.structured_index', lambda *a, **k: ({}, [], {'canonical_records': 0, 'declared_relationships': 0, 'in_corpus_edges': 0, 'external_targets': 0, 'malformed': 0, 'duplicates': 0}))
    monkeypatch.setattr('historian.reasoner_client.materialize_frozen_retrieval_result', lambda *a, **k: bundle)
    monkeypatch.setattr('historian.reasoner_client.reasoner_view', lambda b: b)
    monkeypatch.setattr('historian.reasoner_client.reasoning_request', lambda question, bundle: {'system': 's', 'user': question + '\n\nEVIDENCE BUNDLE:\n## [A] A'})
    monkeypatch.setattr('historian.reasoner_client.validate_schema', lambda parsed: {'valid': True, 'errors': []})
    monkeypatch.setattr('historian.reasoner_client.validate_answer', lambda parsed, bundle: {'valid': True, 'errors': []})
    class Resp:
        status = 200
        def read(self):
            return b'{"choices":[{"message":{"content":"{\\"answer\\":\\"ok\\",\\"cited_record_ids\\":[\\"A\\"],\\"evidence_used\\":[\\"A\\"],\\"uncertainty_or_limitations\\":\\"none\\",\\"contradictions_or_missing_evidence\\":[]}"}}]}'
    monkeypatch.setattr('historian.reasoner_client.urllib.request.urlopen', lambda *a, **k: Resp())
    run('q', run_dir, 'http://example', retrieval, max_tokens=16)
    assert (run_dir / 'q.result.json').exists()
