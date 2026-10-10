from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def list_all(self) -> list[User]:
        return list((await self.session.scalars(select(User).order_by(User.name))).all())

    async def find_by_name(self, name: str) -> list[User]:
        """Exact match first (ignoring case); otherwise anyone whose name starts with it."""
        wanted = name.strip().lower()
        if not wanted:
            return []
        exact = select(User).where(func.lower(User.name) == wanted)
        found = list((await self.session.scalars(exact)).all())
        if found:
            return found
        prefix = select(User).where(func.lower(User.name).like(f"{wanted}%")).order_by(User.name)
        return list((await self.session.scalars(prefix)).all())

    async def get_or_create_member(self, name: str) -> tuple[User, bool]:
        """A team member can exist before they ever open the chat app, so tasks can be assigned to them."""
        found = await self.find_by_name(name)
        if len(found) == 1:
            return found[0], False
        user = User(name=name.strip().title())
        self.session.add(user)
        await self.session.flush()
        return user, True

    async def _only_member_sharing_first_name(self, name: str) -> User | None:
        """Chat names often carry a surname or initial ("Vinay G") that the team list does not ("Vinay").

        If exactly one unlinked member has the same first name, that is the person. With two or more
        candidates nobody is chosen: a wrong guess would hand one person another person's tasks.
        """
        first = name.split()[0].lower()
        unlinked = (await self.session.scalars(select(User).where(User.telegram_chat_id.is_(None)))).all()
        candidates = [u for u in unlinked if u.name.split() and u.name.split()[0].lower() == first]
        return candidates[0] if len(candidates) == 1 else None

    async def by_telegram(self, chat_id: str) -> User | None:
        stmt = select(User).where(User.telegram_chat_id == str(chat_id))
        return (await self.session.scalars(stmt)).first()

    async def link_telegram(self, chat_id: str, display_name: str) -> User:
        """Called on /start. Reuses an unlinked member with the same name, so earlier assignments carry over."""
        existing = await self.by_telegram(chat_id)
        if existing:
            return existing
        name = display_name.strip() or "Teammate"
        stmt = select(User).where(func.lower(User.name) == name.lower(), User.telegram_chat_id.is_(None))
        user = (await self.session.scalars(stmt)).first()
        if user is None:
            user = await self._only_member_sharing_first_name(name)
        if user is None:
            user = User(name=name)
            self.session.add(user)
        user.telegram_chat_id = str(chat_id)
        await self.session.flush()
        return user
