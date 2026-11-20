REASONS = {
    'COLUMN_COUNT':'列数错误', 'MISSING_ID':'编号缺失', 'DUPLICATE_CONFLICT':'重复编号冲突',
    'MISSING_VALUE':'数值缺失', 'BAD_NUMBER':'数值格式错误', 'BAD_TIME':'时间格式错误',
    'BAD_UNIT':'单位不受支持', 'UNKNOWN_REFERENCE':'引用对象不存在', 'INVALID_RANGE':'范围错误',
    'VERSION_CONFLICT':'版本冲突', 'EMPTY_FILE':'空文件', 'BLANK_FILE':'全空行',
    'HEADER_ONLY':'仅有表头', 'STALE_VERSION':'复核版本已更新', 'NOT_FOUND':'批次不存在',
    'BAD_INHERITANCE_PAYLOAD':'并箱指令不完整', 'SOURCE_NOT_READY':'来源批次未达可封存',
    'SOURCE_CONTAINER_AMBIGUOUS':'原箱来源有歧义', 'TERMINAL_CONTAINER_MISMATCH':'码头箱单与原箱不一致',
    'SEAL_EVIDENCE_MISSING':'原箱封签证据缺失', 'RECEIPT_BOX_UNLINKED':'回执箱号落不到实际货柜',
    'NEW_CONTAINER_COLLISION':'新箱号与未并箱货柜冲突', 'RECHECK_NOT_BALANCED':'拆并后逐层重量未对平',
    'RECEIPT_TOTAL_MISMATCH':'回执合计与原箱合计不一致', 'RECEIPT_CONFLICT':'同一幂等键回执内容冲突',
    'MERGE_CONTAINER_IN_USE':'原箱已被其他并箱事件占用', 'ROLLBACK_NOT_POSSIBLE':'无可恢复的旧版回单',
}
# 箱号继承回单阻断码（写入回单 block_codes，逐条可复核）
INHERITANCE_BLOCKS = {
    'SOURCE_NOT_READY':'来源批次存在未处理阻断，申报链不完整',
    'SOURCE_CONTAINER_AMBIGUOUS':'主单货物跨多个箱号或原箱无实际货柜，来源有歧义',
    'TERMINAL_CONTAINER_MISMATCH':'码头沿用的旧箱号集合与申报原箱集合不一致',
    'SEAL_EVIDENCE_MISSING':'原箱封签证据缺失，无法保留原箱封签链',
    'RECEIPT_BOX_UNLINKED':'船公司回执新箱号未出现在实际并箱货柜上',
    'NEW_CONTAINER_COLLISION':'新箱号与并箱范围外的在役箱号重复',
    'RECHECK_NOT_BALANCED':'拆并关系变化后件数/净重/毛重逐层核对未对平',
    'RECEIPT_TOTAL_MISMATCH':'回执件数或重量合计与原箱逐主单合计对不平',
}
def reject(state, record, code, detail):
    state['quarantine'].append({'line':record.get('_line',0),'id':record.get('id',''),
       'code':code,'detail':detail,'raw':{k:v for k,v in record.items() if not k.startswith('_')}})
