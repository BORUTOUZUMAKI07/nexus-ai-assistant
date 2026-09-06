"""
Base Repository Interface & Generic Implementation.
Adheres strictly to SOLID principles:
- Single Responsibility Principle (SRP): Isolates generic DB CRUD logic
- Open/Closed Principle (OCP): Extensible via subclassing without modifying base
- Liskov Substitution Principle (LSP): Subclasses can be used wherever IBaseRepository is expected
- Interface Segregation Principle (ISP): Focused interfaces for read, write, and lifecycle
- Dependency Inversion Principle (DIP): High-level domain services depend on IBaseRepository abstractions
"""
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

ModelType = TypeVar("ModelType", bound=SQLModel)


class IBaseRepository(ABC, Generic[ModelType]):
    """Abstract generic repository interface adhering to ISP and DIP."""

    @abstractmethod
    async def get_by_id(self, id: UUID) -> ModelType | None:
        """Fetch an entity by its primary key UUID."""
        raise NotImplementedError

    @abstractmethod
    async def get_all(self, limit: int = 100, offset: int = 0) -> list[ModelType]:
        """Fetch paginated list of entities."""
        raise NotImplementedError

    @abstractmethod
    async def create(self, entity: ModelType) -> ModelType:
        """Persist a new entity into the database."""
        raise NotImplementedError

    @abstractmethod
    async def update(self, entity: ModelType, update_data: dict[str, Any]) -> ModelType:
        """Update fields on an existing entity."""
        raise NotImplementedError

    @abstractmethod
    async def delete(self, id: UUID) -> bool:
        """Remove an entity by primary key."""
        raise NotImplementedError


class BaseRepository(IBaseRepository[ModelType]):
    """
    Concrete reusable implementation of IBaseRepository.
    Standardizes database operations across all domain repositories.
    """

    def __init__(self, session: AsyncSession, model_class: type[ModelType]):
        self.session = session
        self.model_class = model_class

    async def get_by_id(self, id: UUID) -> ModelType | None:
        statement = select(self.model_class).where(self.model_class.id == id)
        result = await self.session.exec(statement)
        return result.first()

    async def get_all(self, limit: int = 100, offset: int = 0) -> list[ModelType]:
        statement = select(self.model_class).offset(offset).limit(limit)
        result = await self.session.exec(statement)
        return list(result.all())

    async def create(self, entity: ModelType) -> ModelType:
        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return entity

    async def update(self, entity: ModelType, update_data: dict[str, Any]) -> ModelType:
        for key, value in update_data.items():
            if value is not None and hasattr(entity, key):
                setattr(entity, key, value)
        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return entity

    async def delete(self, id: UUID) -> bool:
        entity = await self.get_by_id(id)
        if entity:
            await self.session.delete(entity)
            await self.session.commit()
            return True
        return False
