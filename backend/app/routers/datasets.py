from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_current_user
from ..models import Dataset, User
from ..schemas import DatasetOut
from ..services import datastore
from ..services.query_service import ensure_tables

router = APIRouter(prefix="/datasets", tags=["datasets"])


def _out(d: Dataset) -> DatasetOut:
    return DatasetOut(id=d.id, name=d.name, table_name=d.table_name, filename=d.filename,
                      row_count=d.row_count, columns=d.profile, created_at=d.created_at)


def _owned(db: Session, user: User, dataset_id: int) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if not ds or ds.user_id != user.id:  # 404, not 403: don't reveal other users' ids
        raise HTTPException(404, "Dataset not found")
    return ds


@router.post("", response_model=DatasetOut, status_code=201)
def upload(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = get_settings()
    count = db.scalar(select(func.count()).select_from(Dataset).where(Dataset.user_id == user.id))
    if count >= s.max_datasets_per_user:
        raise HTTPException(400, f"Dataset limit reached ({s.max_datasets_per_user}). Delete one first.")

    content = file.file.read(s.max_upload_mb * 1024 * 1024 + 1)
    if len(content) > s.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File too large (limit {s.max_upload_mb} MB).")

    filename = Path(file.filename or "upload.csv").name
    try:
        df = datastore.read_upload(filename, content)
    except datastore.DataError as e:
        raise HTTPException(400, str(e)) from e

    taken = {t for (t,) in db.execute(select(Dataset.table_name).where(Dataset.user_id == user.id))}
    base = datastore.sanitize_identifier(Path(filename).stem, "data")
    table, n = base, 2
    while table in taken:
        table, n = f"{base}_{n}", n + 1

    rows, profile = datastore.create_table(user.id, table, df)
    ds = Dataset(user_id=user.id, name=Path(filename).stem, table_name=table, filename=filename,
                 file_ext=Path(filename).suffix.lower(), row_count=rows, profile=profile, file_blob=content)
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return _out(ds)


@router.get("", response_model=list[DatasetOut])
def list_datasets(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_out(d) for d in db.scalars(select(Dataset).where(Dataset.user_id == user.id).order_by(Dataset.id))]


@router.get("/{dataset_id}/preview")
def preview(dataset_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ds = _owned(db, user, dataset_id)
    ensure_tables(db, user, [ds])
    res = datastore.run_query(user.id, f'SELECT * FROM "{ds.table_name}" LIMIT 20')
    return {"columns": res.columns, "rows": res.rows}


@router.delete("/{dataset_id}", status_code=204)
def delete(dataset_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ds = _owned(db, user, dataset_id)
    datastore.drop_table(user.id, ds.table_name)
    db.delete(ds)
    db.commit()
