"""
backend/app/repositories/base.py
通用泛型仓储基类 (BaseRepository)
"""
from typing import Generic, TypeVar, Type, Optional, List, Any, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func
from app.core.database import Base

ModelType = TypeVar("ModelType", bound=Base)

class BaseRepository(Generic[ModelType]):
    def __init__(self, model: Type[ModelType]):
        self.model = model

    async def get(self, db: AsyncSession, id: Any) -> Optional[ModelType]:
        return await db.get(self.model, id)

    async def get_multi(
        self,
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        **filters
    ) -> List[ModelType]:
        stmt = select(self.model)
        for key, val in filters.items():
            if hasattr(self.model, key) and val is not None:
                stmt = stmt.where(getattr(self.model, key) == val)
        stmt = stmt.offset(skip).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def count(self, db: AsyncSession, **filters) -> int:
        stmt = select(func.count()).select_from(self.model)
        for key, val in filters.items():
            if hasattr(self.model, key) and val is not None:
                stmt = stmt.where(getattr(self.model, key) == val)
        result = await db.execute(stmt)
        return result.scalar() or 0

    async def create(self, db: AsyncSession, obj_in: ModelType) -> ModelType:
        db.add(obj_in)
        await db.flush()
        return obj_in

    async def delete(self, db: AsyncSession, id: Any) -> bool:
        obj = await self.get(db, id)
        if obj:
            await db.delete(obj)
            await db.flush()
            return True
        return False
