from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse
from app.errors import BusinessError
from app.modules.inheritance.rules import build_receipt
from app.modules.inheritance.view import trace_view
from app.web import render
from app import store

router = APIRouter()


@router.get('/inheritance')
def page(request: Request):
    return render(request, 'inheritance')


@router.get('/api/runs/{run_id}/receipts')
def list_receipts(run_id: str, request: Request):
    from app.main import DB_PATH
    state = store.get(DB_PATH, run_id)
    rows = store.receipts_for_run(DB_PATH, run_id)
    return {'run_id': run_id, 'receipts': rows, 'trace': trace_view(state, rows)}


@router.post('/api/runs/{run_id}/receipts')
def create_receipt(run_id: str, payload: dict = Body(...)):
    from app.main import DB_PATH
    state = store.get(DB_PATH, run_id)
    receipt = build_receipt(state, payload)
    saved = store.save_receipt(DB_PATH, receipt)
    return JSONResponse(saved, status_code=200)


@router.post('/api/runs/{run_id}/receipts/rollback')
def rollback(run_id: str, payload: dict = Body(...)):
    from app.main import DB_PATH
    merge_key = payload.get('merge_key')
    if not isinstance(merge_key, str) or not merge_key.strip():
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', 'merge_key 不能为空')
    receipt_id = payload.get('receipt_id')
    if receipt_id is not None and not isinstance(receipt_id, str):
        raise BusinessError('BAD_INHERITANCE_PAYLOAD', 'receipt_id 须为字符串')
    return store.rollback_receipt(DB_PATH, run_id, merge_key.strip(),
                                  receipt_id.strip() if isinstance(receipt_id, str) else None)
