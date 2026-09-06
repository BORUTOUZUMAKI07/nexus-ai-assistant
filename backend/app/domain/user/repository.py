"""
Repository for User Domain operations.
"""
from uuid import UUID

from backend.app.core.security import encrypt_api_key, get_password_hash
from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.user.models import APIKey, User, UserMemory, UserSettings
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, User)

    async def get_by_id(self, user_id: UUID) -> User | None:
        statement = select(User).where(User.id == user_id)
        result = await self.session.exec(statement)
        return result.first()

    async def get_by_email(self, email: str) -> User | None:
        statement = select(User).where(User.email == email.lower().strip())
        result = await self.session.exec(statement)
        return result.first()

    async def get_by_username(self, username: str) -> User | None:
        statement = select(User).where(User.username == username.lower().strip())
        result = await self.session.exec(statement)
        return result.first()

    async def create(self, user_in) -> User:
        db_user = User(
            email=user_in.email.lower().strip(),
            username=user_in.username.lower().strip(),
            hashed_password=get_password_hash(user_in.password),
            full_name=user_in.full_name,
        )
        self.session.add(db_user)
        await self.session.commit()
        await self.session.refresh(db_user)

        # Create default user settings
        default_settings = UserSettings(user_id=db_user.id)
        self.session.add(default_settings)
        await self.session.commit()

        return db_user

    async def update(self, user: User, update_data: dict) -> User:
        if "password" in update_data and update_data["password"]:
            user.hashed_password = get_password_hash(update_data.pop("password"))
        for field, value in update_data.items():
            if value is not None and hasattr(user, field):
                setattr(user, field, value)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    # Settings
    async def get_settings(self, user_id: UUID) -> UserSettings | None:
        statement = select(UserSettings).where(UserSettings.user_id == user_id)
        result = await self.session.exec(statement)
        return result.first()

    async def upsert_settings(self, user_id: UUID, settings_data: dict) -> UserSettings:
        settings = await self.get_settings(user_id)
        if not settings:
            settings = UserSettings(user_id=user_id, **settings_data)
            self.session.add(settings)
        else:
            for key, val in settings_data.items():
                if val is not None and hasattr(settings, key):
                    setattr(settings, key, val)
            self.session.add(settings)
        await self.session.commit()
        await self.session.refresh(settings)
        return settings

    # Memories
    async def get_memories(self, user_id: UUID, active_only: bool = True) -> list[UserMemory]:
        statement = select(UserMemory).where(UserMemory.user_id == user_id)
        if active_only:
            statement = statement.where(UserMemory.is_active == True)
        statement = statement.order_by(UserMemory.created_at.desc())
        result = await self.session.exec(statement)
        return list(result.all())

    async def create_memory(self, user_id: UUID, content: str, category: str = "preference", confidence: float = 1.0, source_conv_id: UUID | None = None) -> UserMemory:
        memory = UserMemory(
            user_id=user_id,
            content=content,
            category=category,
            confidence=confidence,
            source_conversation_id=source_conv_id,
        )
        self.session.add(memory)
        await self.session.commit()
        await self.session.refresh(memory)
        return memory

    async def delete_memory(self, user_id: UUID, memory_id: UUID) -> bool:
        statement = select(UserMemory).where(UserMemory.id == memory_id, UserMemory.user_id == user_id)
        result = await self.session.exec(statement)
        memory = result.first()
        if memory:
            memory.is_active = False
            self.session.add(memory)
            await self.session.commit()
            return True
        return False

    # BYOK API Keys
    async def get_api_keys(self, user_id: UUID) -> list[APIKey]:
        statement = select(APIKey).where(APIKey.user_id == user_id, APIKey.is_active == True)
        result = await self.session.exec(statement)
        return list(result.all())

    async def get_api_key_by_provider(self, user_id: UUID, provider: str) -> APIKey | None:
        statement = select(APIKey).where(
            APIKey.user_id == user_id,
            APIKey.provider == provider,
            APIKey.is_active == True
        )
        result = await self.session.exec(statement)
        return result.first()

    async def save_api_key(self, user_id: UUID, provider: str, raw_key: str, label: str | None = None) -> APIKey:
        # Check if already exists for this provider
        existing = await self.get_api_key_by_provider(user_id, provider)
        encrypted = encrypt_api_key(raw_key)
        preview = f"...{raw_key[-4:]}" if len(raw_key) > 4 else "****"

        if existing:
            existing.encrypted_key = encrypted
            existing.key_preview = preview
            existing.label = label
            self.session.add(existing)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        else:
            api_key = APIKey(
                user_id=user_id,
                provider=provider,
                encrypted_key=encrypted,
                key_preview=preview,
                label=label,
            )
            self.session.add(api_key)
            await self.session.commit()
            await self.session.refresh(api_key)
            return api_key
