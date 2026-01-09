from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.db.models.equivalent import Equivalent as EquivalentModel
from app.schemas.equivalents import EquivalentsList, Equivalent

router = APIRouter()


@router.get("", response_model=EquivalentsList)
async def list_equivalents(
    db: AsyncSession = Depends(deps.get_db),
    _current_participant=Depends(deps.get_current_participant),
) -> EquivalentsList:
    items = (
        await db.execute(
            select(EquivalentModel)
            .where(EquivalentModel.is_active.is_(True))
            .order_by(EquivalentModel.code.asc())
        )
    ).scalars().all()

    return EquivalentsList(items=[Equivalent.model_validate(x) for x in items])
