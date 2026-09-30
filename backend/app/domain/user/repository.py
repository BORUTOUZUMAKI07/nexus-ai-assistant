"""
Repository for User Domain operations.
"""
from datetime import UTC, datetime
from uuid import UUID

from backend.app.core.security import encrypt_api_key, get_password_hash
from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.user.models import APIKey, User, UserMemory, UserSettings
from backend.app.services.memory_lifecycle import (
    CONFIDENCE_FLOOR,
    INITIAL_CONFIDENCE,
    apply_reinforcement,
    decayed_confidence,
    partition_new_memories,
)
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

    async def get_by_oauth_identity(self, provider: str, subject: str) -> User | None:
        """Look a user up by the IdP's stable subject id.

        This is the authoritative SSO join key. Email is deliberately NOT part
        of the predicate: matching on it would let anyone who controls a
        recycled or reassigned address inherit an existing account.
        """
        statement = select(User).where(
            User.oauth_provider == provider,
            User.oauth_sub == subject,
        )
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
    async def get_memories(
        self, user_id: UUID, active_only: bool = True, limit: int = 50, offset: int = 0
    ) -> list[UserMemory]:
        statement = select(UserMemory).where(UserMemory.user_id == user_id)
        if active_only:
            statement = statement.where(UserMemory.is_active == True)
        statement = statement.order_by(UserMemory.created_at.desc()).offset(offset).limit(limit)
        result = await self.session.exec(statement)
        return list(result.all())

    async def create_memory(
        self,
        user_id: UUID,
        content: str,
        category: str = "preference",
        confidence: float = INITIAL_CONFIDENCE,
        source_conv_id: UUID | None = None,
        scope: str = "user",
        mem0_id: str | None = None,
    ) -> UserMemory:
        now = datetime.now(UTC).replace(tzinfo=None)
        memory = UserMemory(
            user_id=user_id,
            content=content,
            category=category,
            confidence=confidence,
            source_conversation_id=source_conv_id,
            scope=scope,
            mem0_id=mem0_id,
            created_at=now,
            updated_at=now,
        )
        self.session.add(memory)
        await self.session.commit()
        await self.session.refresh(memory)
        return memory

    async def find_memory_by_mem0_id(self, mem0_id: str) -> UserMemory | None:
        """Look up the local row mirroring a mem0 memory id."""
        statement = select(UserMemory).where(UserMemory.mem0_id == mem0_id)
        result = await self.session.exec(statement)
        return result.first()

    async def mirror_memories(
        self,
        user_id: UUID,
        facts: list[dict[str, str]],
        source_conv_id: UUID | None = None,
        scope: str = "user",
    ) -> tuple[list[UserMemory], int]:
        """Persist mem0-extracted facts locally, skipping existing ones.

        This is the reconciliation seam: mem0 owns semantic extraction and
        deduplication, this table owns durability, lifecycle and user-facing
        visibility (there is a REST API over it). Before this, a fact stored in
        mem0 was invisible to every local query and to the user, and the same
        fact was re-mirrored on every turn that mentioned it.

        Returns (created, skipped_duplicate) so the caller can log the
        reconciliation rather than guessing at it.
        """
        existing = [m.content for m in await self.get_memories(user_id, active_only=True)]
        new, dupes = partition_new_memories(facts, existing)
        created: list[UserMemory] = []
        for fact in new:
            text = (fact.get("memory") or fact.get("text") or "").strip()
            if not text:
                continue
            created.append(
                await self.create_memory(
                    user_id=user_id,
                    content=text,
                    category=fact.get("category") or "fact",
                    source_conv_id=source_conv_id,
                    scope=scope,
                    mem0_id=fact.get("id"),
                )
            )
        return created, len(dupes)

    async def reinforce_memories(self, memory_ids: list[UUID]) -> int:
        """Record a successful recall: bump usage, raise confidence.

        The write-back half of the lifecycle. Called when memories are actually
        injected into a prompt, so confidence reflects demonstrated usefulness
        rather than staying at whatever the initial value was. Ids that do not
        exist are ignored rather than raising: this runs on the chat hot path
        and must never fail a turn.
        """
        if not memory_ids:
            return 0
        statement = select(UserMemory).where(
            UserMemory.id.in_(memory_ids), UserMemory.is_active == True  # noqa: E712
        )
        result = await self.session.exec(statement)
        memories = list(result.all())
        now = datetime.now(UTC).replace(tzinfo=None)
        for memory in memories:
            memory.retrieval_count += 1
            memory.last_used_at = now
            memory.confidence = apply_reinforcement(memory.confidence)
            memory.updated_at = now
            self.session.add(memory)
        if memories:
            await self.session.commit()
        return len(memories)

    async def decay_stale_memories(
        self, now: datetime | None = None, batch_limit: int = 500
    ) -> int:
        """Apply time-based decay to memories that have not been recalled.

        Computed rather than materialised: an UPDATE ... SET confidence =
        0.5 * confidence would be wrong, because decay must be proportional to
        how long a memory has actually sat unused, and that needs the row.
        Bounded by ``batch_limit`` so this stays safe to run on a schedule
        against a large table.
        """
        now = now or datetime.now(UTC).replace(tzinfo=None)
        statement = (
            select(UserMemory)
            .where(
                UserMemory.is_active == True,  # noqa: E712
                UserMemory.confidence > CONFIDENCE_FLOOR,
            )
            .order_by(UserMemory.updated_at.asc())
            .limit(batch_limit)
        )
        result = await self.session.exec(statement)
        memories = list(result.all())
        changed = 0
        for memory in memories:
            decayed = decayed_confidence(
                memory.confidence,
                last_used_at=memory.last_used_at,
                updated_at=memory.updated_at,
                now=now,
            )
            if abs(decayed - memory.confidence) > 1e-9:
                memory.confidence = decayed
                memory.updated_at = now
                self.session.add(memory)
                changed += 1
        if changed:
            await self.session.commit()
        return changed

    async def delete_memories_by_mem0_ids(self, mem0_ids: list[str]) -> int:
        """Deactivate local rows for memories removed from the mem0 index.

        Without this, deleting a memory through mem0 left the local copy active
        and it kept being injected.
        """
        if not mem0_ids:
            return 0
        statement = select(UserMemory).where(
            UserMemory.mem0_id.in_(mem0_ids), UserMemory.is_active == True  # noqa: E712
        )
        result = await self.session.exec(statement)
        memories = list(result.all())
        for memory in memories:
            memory.is_active = False
            memory.updated_at = datetime.now(UTC).replace(tzinfo=None)
            self.session.add(memory)
        if memories:
            await self.session.commit()
        return len(memories)

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
    async def get_api_keys(self, user_id: UUID, limit: int = 50, offset: int = 0) -> list[APIKey]:
        statement = (
            select(APIKey)
            .where(APIKey.user_id == user_id, APIKey.is_active == True)
            .order_by(APIKey.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
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
