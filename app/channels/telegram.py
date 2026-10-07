"""Telegram adapter. It moves text in and out and does no thinking of its own."""

import asyncio
import contextlib
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.agents.runtime import AgentRuntime
from app.channels.base import split_message
from app.models import User
from app.services.users import UserService

log = logging.getLogger(__name__)

MESSAGE_LIMIT = 4096
TYPING_REFRESH_SECONDS = 4

WELCOME = (
    "Hi {name}, I'm TaskPilot. Tell me about your work in plain words and I'll keep track of it.\n\n"
    "Try:\n"
    "- Create a high priority task for John to deploy the frontend by Friday\n"
    "- Show my pending tasks\n"
    "- What should I focus on today?\n"
    "- Mark the CRM deployment task as completed\n\n"
    "I'll also send you a plan each morning and remind you before things are due."
)
NOT_STARTED = "Please send /start first so I know who you are."
TEXT_ONLY = "I can only read text messages for now. Type what you need and I'll help."


class TelegramChannel:
    name = "telegram"

    def __init__(
        self,
        runtime: AgentRuntime,
        session_factory: async_sessionmaker[AsyncSession],
        token: str = "",
        bot=None,
    ):
        self.runtime = runtime
        self.session_factory = session_factory
        self.token = token
        self.bot = bot
        self._app: Application | None = None

    # --- the behaviour, independent of the Telegram library -----------------------

    async def on_start(self, chat_id: str, display_name: str) -> str:
        async with self.session_factory() as session:
            user = await UserService(session).link_telegram(chat_id, display_name)
            await session.commit()
            return WELCOME.format(name=user.name)

    async def on_text(self, chat_id: str, text: str) -> list[str]:
        async with self.session_factory() as session:
            user = await UserService(session).by_telegram(chat_id)
        if user is None:
            return [NOT_STARTED]
        result = await self.runtime.handle_chat(user.id, text)
        return split_message(result.reply, MESSAGE_LIMIT)

    async def send(self, user: User, text: str) -> bool:
        if self.bot is None or not user.telegram_chat_id:
            return False
        try:
            for part in split_message(text, MESSAGE_LIMIT):
                await self.bot.send_message(chat_id=user.telegram_chat_id, text=part)
        except TelegramError:
            log.warning("Could not send a Telegram message to user %s", user.id, exc_info=True)
            return False
        return True

    # --- wiring to python-telegram-bot ---------------------------------------------

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        name = update.effective_user.first_name or "there"
        await update.message.reply_text(await self.on_start(str(update.effective_chat.id), name))

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        typing = asyncio.create_task(self._keep_typing(update))
        try:
            parts = await self.on_text(str(update.effective_chat.id), update.message.text)
        finally:
            typing.cancel()
        for part in parts:
            await update.message.reply_text(part)

    async def _handle_other(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(TEXT_ONLY)

    async def _keep_typing(self, update: Update) -> None:
        """Telegram shows 'typing' for about five seconds, so it is refreshed while the agents work."""
        with contextlib.suppress(asyncio.CancelledError, TelegramError):
            while True:
                await update.effective_chat.send_action(ChatAction.TYPING)
                await asyncio.sleep(TYPING_REFRESH_SECONDS)

    async def start(self) -> None:
        """Polling: the bot asks Telegram for new messages, so no public web address is needed."""
        self._app = Application.builder().token(self.token).build()
        self._app.add_handler(CommandHandler(["start", "help"], self._handle_start))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))
        self._app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, self._handle_other))
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        self.bot = self._app.bot
        log.info("Telegram polling started")

    async def stop(self) -> None:
        if self._app is None:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
