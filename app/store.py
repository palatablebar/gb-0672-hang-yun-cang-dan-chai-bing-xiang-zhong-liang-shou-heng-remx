import sqlite3, json
from pathlib import Path
from app.errors import BusinessError

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')")
    con.execute("""CREATE TABLE IF NOT EXISTS inheritance_receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        merge_key TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        current INTEGER NOT NULL DEFAULT 1,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(run_id, idempotency_key))""")
    return con

def save(path, result):
    run_id=result['manifest']['run_id']
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result) VALUES(?,?,?)', (run_id,result['manifest']['sha256'],json.dumps(result,ensure_ascii=False)))
    return get(path,run_id)

def get(path, run_id):
    with connect(path) as con: row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
    result=json.loads(row['result']);result.update(revision=row['revision'],status=row['status'])
    return result

def history(path):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT id,revision,status FROM runs ORDER BY rowid DESC LIMIT 100')]

def review(path,run_id,revision):
    with connect(path) as con:
        cur=con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',('已复核',run_id,revision))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise BusinessError('NOT_FOUND','批次不存在',404)
            raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
    return get(path,run_id)

def _receipt_row(row):
    payload=json.loads(row['payload'])
    payload.update(receipt_db_id=row['id'],current=bool(row['current']))
    return payload

def receipts_for_run(path,run_id):
    """同一批次的全部回单版本（含历史版本），按时间倒序。"""
    with connect(path) as con:
        rows=con.execute('SELECT * FROM inheritance_receipts WHERE run_id=? ORDER BY id DESC',(run_id,)).fetchall()
    return [_receipt_row(r) for r in rows]

def current_receipts(path,run_id):
    with connect(path) as con:
        rows=con.execute('SELECT * FROM inheritance_receipts WHERE run_id=? AND current=1 ORDER BY id',(run_id,)).fetchall()
    return [_receipt_row(r) for r in rows]

def save_receipt(path, receipt):
    """事务化写入回单。

    - 同 (run_id, 幂等键) 重试：原样返回已存回单，不新增第二套；
      若请求内容与已存内容冲突则 422，绝不覆盖。
    - 同并箱事件再次并箱：新回单成为当前版本，旧版回单与原箱封签证据整行保留。
    - 并箱原箱不得与其他在途事件的当前回单交叉占用。
    任何校验失败都在 COMMIT 前抛错，库内旧版回单原样不动。
    """
    run_id=receipt['run_id']; idem=receipt['idempotency_key']; merge_key=receipt['merge_key']
    sources=set(receipt['source_containers'])
    with connect(path) as con:
        if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
            raise BusinessError('NOT_FOUND','批次不存在',404)
        existing=con.execute('SELECT * FROM inheritance_receipts WHERE run_id=? AND idempotency_key=?',
                             (run_id,idem)).fetchone()
        if existing:
            stored=json.loads(existing['payload'])
            if stored['receipt_id']!=receipt['receipt_id'] or stored['evidence']!=receipt['evidence']:
                raise BusinessError('RECEIPT_CONFLICT','同一幂等键对应回执内容不一致，拒绝覆盖',409)
            return _receipt_row(existing)
        busy=con.execute("SELECT DISTINCT merge_key FROM inheritance_receipts WHERE run_id=? AND current=1",
                         (run_id,)).fetchall()
        for row in busy:
            other=json.loads(con.execute('SELECT payload FROM inheritance_receipts WHERE run_id=? AND merge_key=? AND current=1',
                                         (run_id,row['merge_key'])).fetchone()['payload'])
            overlap=sources & set(other['source_containers'])
            if overlap and row['merge_key']!=merge_key:
                raise BusinessError('MERGE_CONTAINER_IN_USE',
                                    f"原箱 {', '.join(sorted(overlap))} 已被并箱事件 {row['merge_key']} 占用",409)
        con.execute('UPDATE inheritance_receipts SET current=0 WHERE run_id=? AND merge_key=? AND ?',
                    (run_id,merge_key,1 if receipt['status']=='已确认' else 0))
        is_current = 1 if receipt['status'] == '已确认' else 0
        cur=con.execute("""INSERT INTO inheritance_receipts
            (run_id,receipt_id,merge_key,idempotency_key,current,payload,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (run_id,receipt['receipt_id'],merge_key,idem,is_current,json.dumps(receipt,ensure_ascii=False),receipt['generated_at']))
        saved=con.execute('SELECT * FROM inheritance_receipts WHERE id=?',(cur.lastrowid,)).fetchone()
    return _receipt_row(saved)

def rollback_receipt(path,run_id,merge_key,receipt_id=None):
    """恢复旧版回单：当前版本下线，指定或最近一版已确认旧版重新置为当前。

    所有历史行（含被下线的当前版）均保留，原箱封签证据不删除。
    """
    with connect(path) as con:
        if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
            raise BusinessError('NOT_FOUND','批次不存在',404)
        family=con.execute('SELECT * FROM inheritance_receipts WHERE run_id=? AND merge_key=? ORDER BY id',
                           (run_id,merge_key)).fetchall()
        if not family:
            raise BusinessError('ROLLBACK_NOT_POSSIBLE','该并箱事件没有任何回单版本')
        if receipt_id is None:
            candidates=[r for r in family if not r['current'] and json.loads(r['payload'])['status']=='已确认']
            if not candidates:
                raise BusinessError('ROLLBACK_NOT_POSSIBLE','无可恢复的已确认旧版回单')
            target=candidates[-1]
        else:
            target=con.execute('SELECT * FROM inheritance_receipts WHERE run_id=? AND merge_key=? AND receipt_id=?',
                               (run_id,merge_key,receipt_id)).fetchone()
            if not target:
                raise BusinessError('NOT_FOUND','指定旧版回单不存在',404)
            if json.loads(target['payload'])['status']!='已确认':
                raise BusinessError('ROLLBACK_NOT_POSSIBLE','阻断回单不能恢复为当前版本')
        con.execute('UPDATE inheritance_receipts SET current=0 WHERE run_id=? AND merge_key=?',(run_id,merge_key))
        con.execute('UPDATE inheritance_receipts SET current=1 WHERE id=?',(target['id'],))
        restored=con.execute('SELECT * FROM inheritance_receipts WHERE id=?',(target['id'],)).fetchone()
        versions=con.execute('SELECT COUNT(*) AS n FROM inheritance_receipts WHERE run_id=? AND merge_key=?',
                             (run_id,merge_key)).fetchone()['n']
    result=_receipt_row(restored)
    result['retained_versions']=versions
    return result
