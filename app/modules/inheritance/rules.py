"""箱号继承回单领域规则。

船开前临时并箱：多只原箱货物进入同一只新箱。码头沿用旧箱号作业，
船公司按重贴后的新箱号出具回执。本模块按两侧证据能否闭合裁决：
申报侧（主单逐件数/净重/毛重与原箱封签）与回执侧（新箱号与合计），
生成让每张主单追到唯一最终箱号的继承回单；重量未对平或来源有歧义时写明阻断。
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib, json
from datetime import datetime, timezone
from app.config import RULE_VERSION
from app.errors import BusinessError
from app.reasons import INHERITANCE_BLOCKS

DEC_ZERO = Decimal('0')


def _nonneg_decimal(value, field):
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', f'{field} 须为非负数值')
    if not d.is_finite() or d < 0:
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', f'{field} 须为非负数值')
    return d


def _require_text(obj, key, label):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', f'{label}不能为空')
    return value.strip()


def _unique(seq):
    seen, out = set(), []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def parse_payload(payload):
    """解析并校验并箱指令的形状；形状问题直接 422，业务阻断写入回单。"""
    if not isinstance(payload, dict):
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', '请求体须为对象')
    sources = payload.get('source_containers')
    if not isinstance(sources, list) or not sources:
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', 'source_containers 须为非空数组')
    source_containers = _unique([str(x).strip() for x in sources if str(x).strip()])
    if len(source_containers) < 2:
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', '并箱至少需要两只原箱')
    if len(source_containers) != len([x for x in sources if str(x).strip()]):
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', 'source_containers 存在重复或空箱号')

    terminal = payload.get('terminal')
    if not isinstance(terminal, dict):
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', 'terminal 码头证据须为对象')
    t_containers = terminal.get('containers')
    if not isinstance(t_containers, list) or not t_containers:
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', 'terminal.containers 须为非空数组')
    terminal_containers = _unique([str(x).strip() for x in t_containers if str(x).strip()])
    if len(terminal_containers) != len([x for x in t_containers if str(x).strip()]):
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', 'terminal.containers 存在重复或空箱号')

    seals = terminal.get('seals')
    if not isinstance(seals, list) or not seals:
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', 'terminal.seals 须为非空数组')
    terminal_seals = []
    for item in seals:
        if not isinstance(item, dict):
            raise BusinessError('BAD_INHERITANCE_PAYLOAD', '封签证据须为对象')
        terminal_seals.append({'container': _require_text(item, 'container', '封签箱号'),
                               'seal': _require_text(item, 'seal', '封签号')})

    receipt = payload.get('receipt')
    if not isinstance(receipt, dict):
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', 'receipt 船公司回执须为对象')
    receipt_obj = {
        'receipt_no': _require_text(receipt, 'receipt_no', '回执编号'),
        'container': _require_text(receipt, 'container', '回执新箱号'),
        'pieces': int(_nonneg_decimal(receipt.get('pieces'), '回执件数')),
        'net_kg': str(_nonneg_decimal(receipt.get('net_kg'), '回执净重')),
        'gross_kg': str(_nonneg_decimal(receipt.get('gross_kg'), '回执毛重')),
    }
    if Decimal(receipt_obj['net_kg']) > Decimal(receipt_obj['gross_kg']):
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', '回执毛重不得低于净重')
    if Decimal(receipt_obj['pieces']) != _nonneg_decimal(receipt.get('pieces'), '回执件数'):
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', '回执件数须为整数')
    return {'source_containers': source_containers,
            'terminal_containers': terminal_containers,
            'terminal_seals': terminal_seals,
            'sealed_at': terminal.get('sealed_at'),
            'receipt': receipt_obj}


def canonical_key(instruction):
    """同一并箱事件 + 同一回执的稳定幂等键。"""
    basis = {'source_containers': instruction['source_containers'],
             'terminal_containers': instruction['terminal_containers'],
             'terminal_seals': sorted((s['container'], s['seal']) for s in instruction['terminal_seals']),
             'receipt': instruction['receipt']}
    return hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def _subtree(node_id, children):
    order, stack = [], [node_id]
    while stack:
        cur = stack.pop()
        order.append(cur)
        stack.extend(sorted(children.get(cur, []), reverse=True))
    return order


def layer_recheck(records, children, affected_roots):
    """拆并关系变化后，对受影响主单整棵子树重新逐层核对件数/净重/毛重。"""
    nodes = {r['id']: r for r in records}
    findings = []
    for root in affected_roots:
        for node in _subtree(root, children):
            kids = children.get(node, [])
            if not kids:
                continue
            sums = {'pieces': sum(nodes[c]['pieces'] for c in kids),
                    'net_kg': sum((Decimal(nodes[c]['net_kg']) for c in kids), DEC_ZERO),
                    'gross_kg': sum((Decimal(nodes[c]['gross_kg']) for c in kids), DEC_ZERO)}
            for field, total in sums.items():
                declared = Decimal(str(nodes[node][field]))
                diff = declared - total
                if diff:
                    findings.append({'id': node, 'field': field, 'declared': str(declared),
                                     'children_sum': str(total), 'difference': str(diff)})
    return findings


def build_receipt(state, payload):
    """纯函数：来源批次状态 + 并箱指令 -> 继承回单（含阻断与两侧证据裁决）。"""
    instruction = parse_payload(payload)
    sources = instruction['source_containers']
    source_set = set(sources)
    receipt = instruction['receipt']
    blocks = []

    def block(code, detail):
        blocks.append({'code': code, 'detail': detail or INHERITANCE_BLOCKS[code]})

    # 申报连续性前提：来源批次自身须已通过封存核验
    if state.get('archive', {}).get('status') != '可封存':
        block('SOURCE_NOT_READY', f"来源批次状态为 {state.get('archive', {}).get('status', '未知')}，申报链未闭合")

    records = state['records']
    children = state['graph']['children']
    roots = state['graph']['roots']
    nodes = {r['id']: r for r in records}
    declared_containers = {r['container'] for r in records}

    # 每只原箱必须能落到实际货柜
    missing_boxes = sorted(source_set - declared_containers)
    if missing_boxes:
        block('SOURCE_CONTAINER_AMBIGUOUS', f"原箱号在申报记录中无实际货柜：{', '.join(missing_boxes)}")

    # 每张受影响主单的全部货物必须完整落入并箱范围，跨箱即来源歧义
    affected_roots, master_rows = [], []
    for root in roots:
        sub = _subtree(root, children)
        sub_containers = {nodes[n]['container'] for n in sub}
        touched = sub_containers & source_set
        if not touched:
            continue
        affected_roots.append(root)
        enclosed = sub_containers <= source_set
        if not enclosed:
            block('SOURCE_CONTAINER_AMBIGUOUS',
                  f"主单 {root} 货物跨并箱范围内外的箱号：{', '.join(sorted(sub_containers))}")
        master_rows.append((root, enclosed, sorted(touched)))

    if not affected_roots:
        block('SOURCE_CONTAINER_AMBIGUOUS', '没有任何主单的货物落在申报原箱内')

    # 码头侧证据：沿用的旧箱号集合必须与申报原箱集合逐箱闭合
    if set(instruction['terminal_containers']) != source_set:
        block('TERMINAL_CONTAINER_MISMATCH',
              f"码头箱单 {instruction['terminal_containers']} 与申报原箱 {sources} 不一致")
    seal_map = {s['container']: s['seal'] for s in instruction['terminal_seals']}
    if set(seal_map) != source_set:
        block('SEAL_EVIDENCE_MISSING',
              f"封签证据须逐原箱留存，缺失：{', '.join(sorted(source_set - set(seal_map)))}")

    # 船公司侧证据：回执新箱号必须能落到实际并箱货柜
    if receipt['container'] in source_set:
        block('RECEIPT_BOX_UNLINKED', '回执箱号仍为旧箱号，未落到重贴后的实际货柜')
    outside = declared_containers - source_set
    if receipt['container'] in outside:
        block('NEW_CONTAINER_COLLISION',
              f"新箱号 {receipt['container']} 与并箱范围外在役箱号重复")

    # 拆并关系变化后重新逐层核对重量
    findings = layer_recheck(records, children, affected_roots)
    if findings:
        block('RECHECK_NOT_BALANCED',
              '；'.join(f"{f['id']}.{f['field']} 差异 {f['difference']}" for f in findings[:5]))

    # 逐主单合计（只纳入完整封闭在并箱原箱内的主单；根节点即完整子树申报量）
    lines = []
    sum_pieces, sum_net, sum_gross = 0, DEC_ZERO, DEC_ZERO
    for root, enclosed, touched in master_rows:
        row = nodes[root]
        sub_ids = set(_subtree(root, children))
        line_seals = [{'container': c, 'seal': seal_map.get(c, '')}
                      for c in sources if c in {nodes[n]['container'] for n in sub_ids}]
        line = {'master_id': root,
                'source_containers': touched,
                'pieces': row['pieces'], 'net_kg': str(row['net_kg']), 'gross_kg': str(row['gross_kg']),
                'terminal_seals': line_seals,
                'final_container': receipt['container'],
                'trace_status': '已追踪' if enclosed and not blocks else '阻断'}
        lines.append(line)
        if enclosed:
            sum_pieces += row['pieces']
            sum_net += Decimal(row['net_kg'])
            sum_gross += Decimal(row['gross_kg'])
    source_totals = {'pieces': sum_pieces, 'net_kg': str(sum_net), 'gross_kg': str(sum_gross)}

    # 回执合计与原箱逐主单合计对平
    receipt_totals = {k: receipt[k] for k in ('pieces', 'net_kg', 'gross_kg')}
    diffs = {}
    for field in ('pieces', 'net_kg', 'gross_kg'):
        d = Decimal(str(receipt_totals[field])) - Decimal(str(source_totals[field]))
        if d:
            diffs[field] = str(d)
    if diffs:
        block('RECEIPT_TOTAL_MISMATCH', f"回执与原箱合计差异 {diffs}")

    # 两侧证据闭合裁决
    terminal_closed = not any(b['code'] in {'TERMINAL_CONTAINER_MISMATCH', 'SEAL_EVIDENCE_MISSING'} for b in blocks)
    carrier_closed = not any(b['code'] in {'RECEIPT_BOX_UNLINKED', 'NEW_CONTAINER_COLLISION',
                                           'RECEIPT_TOTAL_MISMATCH'} for b in blocks)
    declaration_closed = not any(b['code'] in {'SOURCE_NOT_READY', 'SOURCE_CONTAINER_AMBIGUOUS',
                                               'RECHECK_NOT_BALANCED'} for b in blocks)
    verdict = {'declaration_side': '闭合' if declaration_closed else '阻断',
               'terminal_side': '闭合' if terminal_closed else '阻断',
               'carrier_side': '闭合' if carrier_closed else '阻断',
               'conclusion': '已确认' if not blocks else '阻断待处理'}

    merge_key = 'MERGE-' + hashlib.sha256(
        '|'.join(sources).encode()).hexdigest()[:12]
    idem = canonical_key(instruction)
    receipt_id = 'RCP-' + idem[:12]
    evidence = {'instruction': deepcopy(instruction),
                'source_totals': source_totals,
                'receipt_totals': receipt_totals,
                'recheck_findings': findings,
                'affected_roots': affected_roots,
                'original_seals': instruction['terminal_seals'],
                'run_id': state['manifest']['run_id'],
                'run_sha256': state['manifest']['sha256']}
    return {'receipt_id': receipt_id,
            'merge_key': merge_key,
            'idempotency_key': idem,
            'run_id': state['manifest']['run_id'],
            'source_containers': sources,
            'final_container': receipt['container'],
            'receipt_no': receipt['receipt_no'],
            'master_lines': lines,
            'source_totals': source_totals,
            'receipt_totals': receipt_totals,
            'recheck_findings': findings,
            'terminal_seals': instruction['terminal_seals'],
            'verdict': verdict,
            'status': verdict['conclusion'],
            'block_codes': [b['code'] for b in blocks],
            'blocks': blocks,
            'rule_version': RULE_VERSION,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'evidence': evidence}
