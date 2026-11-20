"""箱号继承页视图：把当前生效回单汇总成每张主单追到唯一最终箱号的追踪表。"""
from collections import OrderedDict


def trace_view(state, receipts):
    current = [r for r in receipts if r.get('current')]
    lines, block_count, confirmed_count = [], 0, 0
    by_master = OrderedDict()
    for receipt in current:
        for line in receipt['master_lines']:
            row = dict(line)
            row['merge_key'] = receipt['merge_key']
            row['receipt_id'] = receipt['receipt_id']
            row['receipt_no'] = receipt['receipt_no']
            row['block_codes'] = receipt['block_codes']
            row['trace_status'] = '阻断' if receipt['block_codes'] else '已追踪'
            by_master.setdefault(line['master_id'], []).append(row)
    for master_id, rows in by_master.items():
        finals = {r['final_container'] for r in rows}
        for row in rows:
            if len(finals) > 1:
                row['trace_status'] = '阻断'
                row['block_codes'] = sorted(set(row['block_codes']) | {'SOURCE_CONTAINER_AMBIGUOUS'})
            lines.append(row)
            block_count += row['trace_status'] == '阻断'
            confirmed_count += row['trace_status'] == '已追踪'
    history = [{'receipt_id': r['receipt_id'], 'merge_key': r['merge_key'],
                'final_container': r['final_container'], 'status': r['status'],
                'current': r.get('current', False),
                'block_codes': r['block_codes'],
                'generated_at': r['generated_at']} for r in receipts]
    return {'title': '箱号继承回单', 'rows': lines, 'history': history,
            'metrics': {'生效回单': len(current), '已追踪主单行': confirmed_count,
                        '阻断主单行': block_count, '历史版本': len(receipts)}}
