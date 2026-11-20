from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import DateTime, Integer, String, create_engine, delete, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(BASE_DIR / 'atlas.db').as_posix()}")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
CACHE_TTL = timedelta(hours=24)
sync_lock = asyncio.Lock()
IBGE_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"


class Base(DeclarativeBase):
    pass


class Municipality(Base):
    __tablename__ = "municipalities"
    ibge_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    state_code: Mapped[int] = mapped_column(Integer, index=True)
    state: Mapped[str] = mapped_column(String(80), index=True)
    region: Mapped[str] = mapped_column(String(40), index=True)
    immediate_region: Mapped[str] = mapped_column(String(120), default="")
    intermediate_region: Mapped[str] = mapped_column(String(120), default="")


class SyncState(Base):
    __tablename__ = "sync_state"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    last_synced: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield
    engine.dispose()


app = FastAPI(title="Atlas Brasil", description="Explore municípios e estados brasileiros com dados oficiais do IBGE", version="1.0.0", lifespan=lifespan)
templates = Jinja2Templates(directory=str(BASE_DIR / 'templates'))
app.mount('/static', StaticFiles(directory=str(BASE_DIR / 'static')), name='static')


def get_db():
    with SessionLocal() as db:
        yield db


async def fetch_municipalities() -> list[dict]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=8), headers={"User-Agent": "AtlasBrasil/1.0 educational-portfolio"}) as client:
        try:
            response = await client.get(IBGE_URL, params={"orderBy": "nome"})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Não foi possível consultar os dados do IBGE.") from exc
    try:
        rows = response.json()
        normalized = []
        for item in rows:
            immediate = item.get("regiao-imediata") or {}
            intermediate = immediate.get("regiao-intermediaria") or {}
            uf = intermediate.get("UF")
            if uf is None:
                microregion = item.get("microrregiao") or {}
                mesoregion = microregion.get("mesorregiao") or {}
                uf = mesoregion.get("UF")
            if uf is None:
                raise ValueError(f"UF ausente para município {item.get('id')}.")
            normalized.append({
                "ibge_id": int(item["id"]),
                "name": str(item["nome"])[:120],
                "state_code": int(uf["id"]),
                "state": str(uf["nome"])[:80],
                "region": str(uf["regiao"]["nome"])[:40],
                "immediate_region": str(immediate.get("nome") or "")[:120],
                "intermediate_region": str(intermediate.get("nome") or "")[:120],
            })
        return normalized
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="O formato de dados recebido do IBGE não corresponde ao esperado.") from exc


def needs_sync(db: Session) -> bool:
    count = db.query(Municipality).count()
    state = db.get(SyncState, 'municipalities')
    if count == 0 or state is None:
        return True
    synced = state.last_synced
    if synced.tzinfo is None:
        synced = synced.replace(tzinfo=UTC)
    return datetime.now(UTC) - synced >= CACHE_TTL


async def sync_if_needed(db: Session, force: bool = False) -> int:
    async with sync_lock:
        if not force and not needs_sync(db):
            return db.query(Municipality).count()
        existing_count = db.query(Municipality).count()
        try:
            rows = await fetch_municipalities()
        except HTTPException:
            if existing_count > 0:
                return existing_count
            raise
        if not rows:
            if existing_count > 0:
                return existing_count
            raise HTTPException(status_code=502, detail="O IBGE retornou uma lista vazia.")
        if existing_count and len(rows) < int(existing_count * 0.9):
            raise HTTPException(status_code=502, detail="A resposta do IBGE parece incompleta; a base local foi preservada.")
        db.execute(delete(Municipality))
        db.add_all(Municipality(**row) for row in rows)
        state = db.get(SyncState, 'municipalities')
        if state is None:
            state = SyncState(key='municipalities', last_synced=datetime.now(UTC))
            db.add(state)
        else:
            state.last_synced = datetime.now(UTC)
        db.commit()
        return len(rows)


def serialize(item: Municipality):
    return {"ibge_id": item.ibge_id, "name": item.name, "state_code": item.state_code, "state": item.state, "region": item.region, "immediate_region": item.immediate_region, "intermediate_region": item.intermediate_region}


@app.get('/health')
def health():
    return {"status": "ok"}


@app.get('/api/states')
async def states(db: Session = Depends(get_db)):
    await sync_if_needed(db)
    rows = db.execute(select(Municipality.state_code, Municipality.state, Municipality.region).distinct().order_by(Municipality.state)).all()
    return [{"code": code, "name": name, "region": region} for code, name, region in rows]


@app.post('/api/sync')
async def sync(db: Session = Depends(get_db)):
    count = await sync_if_needed(db, force=True)
    return {"municipalities": count, "synced_at": datetime.now(UTC).isoformat()}


@app.get('/api/municipalities')
async def list_municipalities(q: str = Query(default='', max_length=100), state: str = Query(default='', max_length=80), region: str = Query(default='', max_length=40), limit: int = Query(default=100, ge=1, le=10000), offset: int = Query(default=0, ge=0), db: Session = Depends(get_db)):
    await sync_if_needed(db)
    stmt = select(Municipality)
    filters = []
    if q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(Municipality.name.ilike(f"%{escaped}%", escape="\\"))
    if state.strip():
        filters.append(Municipality.state == state.strip())
    if region.strip():
        filters.append(Municipality.region == region.strip())
    if filters:
        stmt = stmt.where(*filters)
    total = db.scalar(select(func.count()).select_from(Municipality).where(*filters))
    rows = db.scalars(stmt.order_by(Municipality.name).offset(offset).limit(limit)).all()
    return {"total": total, "limit": limit, "offset": offset, "items": [serialize(row) for row in rows]}


@app.get('/api/municipalities/compare')
async def compare_municipalities(ids: list[int] = Query(min_length=2, max_length=4), db: Session = Depends(get_db)):
    await sync_if_needed(db)
    unique_ids = list(dict.fromkeys(ids))
    if len(unique_ids) < 2:
        raise HTTPException(status_code=422, detail="Informe pelo menos dois identificadores diferentes.")
    items = db.scalars(select(Municipality).where(Municipality.ibge_id.in_(unique_ids))).all()
    if len(items) != len(unique_ids):
        raise HTTPException(status_code=404, detail="Um ou mais municípios não foram encontrados.")
    return {"items": [serialize(row) for row in items]}


@app.get('/', response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, 'index.html', {})
