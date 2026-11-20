import json
import copy
import pytest
from fastapi.testclient import TestClient
from app import main, store
from app.engine import evaluate
from app.modules.inheritance.rules import build_receipt, parse_payload, canonical_key


def make_payload(**over):
    payload = {'source_containers': ['C001', 'C002'],
               'terminal': {'containers': ['C001', 'C002'],
                            'seals': [{'container': 'C001', 'seal': 'SEAL-1'},
                                      {'container': 'C002', 'seal': 'SEAL-2'}]},
               'receipt': {'receipt_no': 'RCP-1', 'container': 'C901',
                           'pieces': 80, 'net_kg': '800', 'gross_kg': '880'}}
    payload.update(over)
    return payload


def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'inh.sqlite'))
    return TestClient(main.app)


# ---------- 纯规则：两侧证据闭合 ----------

def test_clean_merge_confirmed(clean):
    r = build_receipt(clean, make_payload())
    assert r['status'] == '已确认'
    assert r['block_codes'] == []
    assert {l['master_id'] for l in r['master_lines']} == {'M001', 'M002'}
    assert all(l['final_container'] == 'C901' and l['trace_status'] == '已追踪' for l in r['master_lines'])
    assert r['source_totals'] == {'pieces': 80, 'net_kg': '800', 'gross_kg': '880'}
    assert r['verdict'] == {'declaration_side': '闭合', 'terminal_side': '闭合',
                            'carrier_side': '闭合', 'conclusion': '已确认'}


def test_every_master_line_keeps_seal_chain(clean):
    r = build_receipt(clean, make_payload())
    for line in r['master_lines']:
        seals = {s['container']: s['seal'] for s in line['terminal_seals']}
        assert set(seals) == set(line['source_containers'])
    assert {s['seal'] for s in r['terminal_seals']} == {'SEAL-1', 'SEAL-2'}


def test_dirty_source_not_ready_blocks(sample):
    r = build_receipt(sample, make_payload())
    assert 'SOURCE_NOT_READY' in r['block_codes']
    assert r['status'] == '阻断待处理'


def test_terminal_mismatch_blocks(clean):
    p = make_payload()
    p['terminal']['containers'] = ['C001', 'C003']
    r = build_receipt(clean, p)
    assert 'TERMINAL_CONTAINER_MISMATCH' in r['block_codes']


def test_missing_seal_evidence_blocks(clean):
    p = make_payload()
    p['terminal']['seals'] = [{'container': 'C001', 'seal': 'SEAL-1'}]
    r = build_receipt(clean, p)
    assert 'SEAL_EVIDENCE_MISSING' in r['block_codes']
    # 原箱封签证据仍随回单留存
    assert r['evidence']['original_seals'] == p['terminal']['seals']


def test_receipt_box_unlinked_blocks(clean):
    p = make_payload()
    p['receipt']['container'] = 'C001'  # 回执仍写旧箱号，落不到实际新货柜
    r = build_receipt(clean, p)
    assert 'RECEIPT_BOX_UNLINKED' in r['block_codes']
    assert r['verdict']['carrier_side'] == '阻断'


def test_new_container_collision_blocks(clean):
    p = make_payload()
    p['receipt']['container'] = 'C003'  # 并箱范围外在役箱号
    r = build_receipt(clean, p)
    assert 'NEW_CONTAINER_COLLISION' in r['block_codes']


def test_unknown_source_container_ambiguous(clean):
    p = make_payload()
    p['source_containers'] = ['C001', 'CXXX']
    p['terminal']['containers'] = ['C001', 'CXXX']
    p['terminal']['seals'].append({'container': 'CXXX', 'seal': 'SX'})
    r = build_receipt(clean, p)
    assert 'SOURCE_CONTAINER_AMBIGUOUS' in r['block_codes']


def test_ambiguous_master_line_marked_blocked(clean):
    # 原箱 C001 之外不存在的箱号被纳入，主单 M001 仍封闭；构造跨箱主单需让某主单同时触及内外箱
    p = make_payload()
    p['source_containers'] = ['C001', 'CXXX']
    p['terminal']['containers'] = ['C001', 'CXXX']
    p['terminal']['seals'].append({'container': 'CXXX', 'seal': 'SX'})
    r = build_receipt(clean, p)
    statuses = {l['master_id']: l['trace_status'] for l in r['master_lines']}
    assert statuses['M001'] == '阻断'
    # 歧义主单的根合计不纳入原箱合计
    assert r['source_totals']['pieces'] == 40  # 仅 M002（C002）未触及歧义箱


def test_receipt_total_mismatch_blocks(clean):
    p = make_payload()
    p['receipt']['gross_kg'] = '881'
    r = build_receipt(clean, p)
    assert 'RECEIPT_TOTAL_MISMATCH' in r['block_codes']
    detail = next(b['detail'] for b in r['blocks'] if b['code'] == 'RECEIPT_TOTAL_MISMATCH')
    assert 'gross_kg' in detail


def test_layer_recheck_after_relation_change(clean):
    state = copy.deepcopy(clean)
    # 改动拆并关系后的子节点重量，逐层核对必须抓到差异
    node = next(r for r in state['records'] if r['id'] == 'M001-P1-1')
    node['gross_kg'] = '200'
    r = build_receipt(state, make_payload())
    assert 'RECHECK_NOT_BALANCED' in r['block_codes']
    assert any(f['id'] == 'M001-P1' for f in r['recheck_findings'])


def test_bad_payload_shape_rejected(clean):
    with pytest.raises(Exception):
        parse_payload({'source_containers': ['C001']})
    with pytest.raises(Exception):
        parse_payload(make_payload(receipt={}))


def test_idempotency_key_stable(clean):
    p = make_payload()
    a = canonical_key(parse_payload(p))
    b = canonical_key(parse_payload(json.loads(json.dumps(p))))
    assert a == b
    p2 = make_payload()
    p2['receipt']['gross_kg'] = '881'
    assert canonical_key(parse_payload(p2)) != a


# ---------- 持久化：幂等、版本、回滚 ----------

def test_retry_does_not_create_second_receipt(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    run = c.post('/api/runs', json={'dataset': 'clean'}).json()
    rid = run['manifest']['run_id']
    url = f'/api/runs/{rid}/receipts'
    first = c.post(url, json=make_payload()).json()
    second = c.post(url, json=make_payload()).json()
    assert first['receipt_id'] == second['receipt_id']
    assert first['receipt_db_id'] == second['receipt_db_id']
    rows = c.get(url).json()['receipts']
    assert len(rows) == 1


def test_conflicting_retry_rejected_not_overwritten(tmp_path, monkeypatch, clean):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={'dataset': 'clean'}).json()['manifest']['run_id']
    url = f'/api/runs/{rid}/receipts'
    ok = c.post(url, json=make_payload()).json()
    # 篡改重试请求的证据，但保留同一幂等键，模拟同键不同回执
    tampered = build_receipt(clean, make_payload())
    tampered['idempotency_key'] = ok['idempotency_key']
    tampered['evidence']['receipt_totals']['gross_kg'] = '881'
    from app.main import DB_PATH
    with pytest.raises(Exception) as exc:
        store.save_receipt(DB_PATH, tampered)
    assert exc.value.code == 'RECEIPT_CONFLICT'
    # 旧版回单原样保留
    stored = c.get(url).json()['receipts'][0]
    assert stored['receipt_id'] == ok['receipt_id']
    assert stored['receipt_totals']['gross_kg'] == '880'


def test_blocked_receipt_persisted_and_retry_idempotent(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={'dataset': 'clean'}).json()['manifest']['run_id']
    url = f'/api/runs/{rid}/receipts'
    p = make_payload()
    p['receipt']['gross_kg'] = '900'
    first = c.post(url, json=p).json()
    assert first['status'] == '阻断待处理'
    second = c.post(url, json=p).json()
    assert second['receipt_db_id'] == first['receipt_db_id']
    assert len(c.get(url).json()['receipts']) == 1


def test_blocked_receipt_does_not_displace_confirmed_current(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={'dataset': 'clean'}).json()['manifest']['run_id']
    url = f'/api/runs/{rid}/receipts'
    confirmed = c.post(url, json=make_payload()).json()
    bad = make_payload()
    bad['receipt']['gross_kg'] = '900'
    blocked = c.post(url, json=bad).json()
    assert blocked['current'] is False
    rows = {r['receipt_id']: r['current'] for r in c.get(url).json()['receipts']}
    assert rows[confirmed['receipt_id']] is True
    # 追踪页仍按已确认回单展示，阻断仅留痕
    page = c.get(f'/inheritance?run_id={rid}').text
    assert '已追踪' in page


def test_new_version_keeps_old_receipt_and_seals(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={'dataset': 'clean'}).json()['manifest']['run_id']
    url = f'/api/runs/{rid}/receipts'
    first = c.post(url, json=make_payload()).json()
    p2 = make_payload()
    p2['receipt'].update(receipt_no='RCP-2', container='C902', gross_kg='880')
    second = c.post(url, json=p2).json()
    assert second['merge_key'] == first['merge_key']
    assert second['current'] is True
    rows = c.get(url).json()['receipts']
    assert len(rows) == 2
    old = next(r for r in rows if r['receipt_id'] == first['receipt_id'])
    assert old['current'] is False
    assert {s['seal'] for s in old['terminal_seals']} == {'SEAL-1', 'SEAL-2'}


def test_rollback_restores_previous_current(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={'dataset': 'clean'}).json()['manifest']['run_id']
    url = f'/api/runs/{rid}/receipts'
    first = c.post(url, json=make_payload()).json()
    p2 = make_payload()
    p2['receipt'].update(receipt_no='RCP-2', container='C902')
    second = c.post(url, json=p2).json()
    rb = c.post(f'/api/runs/{rid}/receipts/rollback', json={'merge_key': first['merge_key']})
    assert rb.status_code == 200
    restored = rb.json()
    assert restored['receipt_id'] == first['receipt_id']
    assert restored['current'] is True
    assert restored['retained_versions'] == 2  # 历史一行不删
    rows = c.get(url).json()['receipts']
    assert {r['receipt_id']: r['current'] for r in rows} == {first['receipt_id']: True,
                                                             second['receipt_id']: False}


def test_rollback_impossible_without_history(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={'dataset': 'clean'}).json()['manifest']['run_id']
    c.post(f'/api/runs/{rid}/receipts', json=make_payload())
    resp = c.post(f'/api/runs/{rid}/receipts/rollback', json={'merge_key': 'MERGE-whatever'})
    assert resp.status_code == 422
    assert resp.json()['code'] == 'ROLLBACK_NOT_POSSIBLE'


def test_container_exclusivity_across_events(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={'dataset': 'clean'}).json()['manifest']['run_id']
    url = f'/api/runs/{rid}/receipts'
    c.post(url, json=make_payload())  # C001+C002 -> C901
    other = make_payload()
    other['source_containers'] = ['C002', 'C003']
    other['terminal']['containers'] = ['C002', 'C003']
    other['terminal']['seals'] = [{'container': 'C002', 'seal': 'SEAL-2'},
                                  {'container': 'C003', 'seal': 'SEAL-3'}]
    other['receipt'].update(receipt_no='RCP-3', container='C903',
                            pieces=80, net_kg='800', gross_kg='880')
    resp = c.post(url, json=other)
    assert resp.status_code == 409
    assert resp.json()['code'] == 'MERGE_CONTAINER_IN_USE'


def test_export_bundle_includes_receipts(tmp_path, monkeypatch):
    import io, zipfile
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={'dataset': 'clean'}).json()['manifest']['run_id']
    c.post(f'/api/runs/{rid}/receipts', json=make_payload())
    z = zipfile.ZipFile(io.BytesIO(c.get(f'/api/export/{rid}').content))
    data = json.loads(z.read('inheritance_receipts.json'))
    assert data[0]['final_container'] == 'C901'
    assert {s['seal'] for s in data[0]['terminal_seals']} == {'SEAL-1', 'SEAL-2'}


def test_inheritance_page_renders(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={'dataset': 'clean'}).json()['manifest']['run_id']
    resp = c.get(f'/inheritance?run_id={rid}')
    assert resp.status_code == 200
    assert '箱号继承' in resp.text
    c.post(f'/api/runs/{rid}/receipts', json=make_payload())
    rendered = c.get(f'/inheritance?run_id={rid}').text
    assert 'C901' in rendered and 'M001' in rendered


def test_failed_generation_keeps_store_untouched(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = c.post('/api/runs', json={'dataset': 'clean'}).json()['manifest']['run_id']
    url = f'/api/runs/{rid}/receipts'
    bad = c.post(url, json={'source_containers': ['only-one']})
    assert bad.status_code == 422
    assert c.get(url).json()['receipts'] == []
