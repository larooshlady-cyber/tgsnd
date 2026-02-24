import asyncio
import os
import threading
from telethon import TelegramClient
from services.chat_service import register_incoming_handler, unregister_incoming_handler, fetch_missed_messages


class TelegramManager:
    """
    Manages multiple Telethon clients.
    Runs a single asyncio event loop in a dedicated background thread.
    Flask routes bridge sync->async via run_coroutine_threadsafe.
    """

    def __init__(self):
        self._clients = {}
        self._loop = None
        self._thread = None
        self.app = None

    def start(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run_async(self, coro, timeout=120):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def run_async_nonblocking(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def connect_account(self, account_id, phone, api_id, api_hash, session_name):
        sessions_dir = self.app.config['SESSIONS_DIR']
        session_path = os.path.join(sessions_dir, session_name)
        client = TelegramClient(session_path, api_id, api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await client.send_code_request(phone)
            self._clients[account_id] = client
            return {"status": "code_required"}

        me = await client.get_me()
        self._clients[account_id] = client
        self._register_handlers(account_id, client)
        # Fetch any missed incoming messages in background
        asyncio.ensure_future(fetch_missed_messages(account_id, client))
        return {
            "status": "connected",
            "display_name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
            "username": me.username or ""
        }

    async def verify_code(self, account_id, phone, code, password=None):
        client = self._clients.get(account_id)
        if not client:
            raise ValueError("Client not initialized. Connect first.")
        try:
            await client.sign_in(phone, code)
        except Exception:
            if password:
                await client.sign_in(password=password)
            else:
                raise
        me = await client.get_me()
        self._register_handlers(account_id, client)
        # Fetch any missed incoming messages in background
        asyncio.ensure_future(fetch_missed_messages(account_id, client))
        return {
            "status": "connected",
            "display_name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
            "username": me.username or ""
        }

    def _register_handlers(self, account_id, client):
        """Register chat handlers only if not already registered."""
        if not getattr(client, '_chat_handlers', None):
            register_incoming_handler(account_id, client)

    def get_client(self, account_id):
        return self._clients.get(account_id)

    def is_connected(self, account_id):
        client = self._clients.get(account_id)
        return client is not None and client.is_connected()

    async def disconnect_account(self, account_id):
        client = self._clients.pop(account_id, None)
        if client:
            unregister_incoming_handler(client)
            await client.disconnect()

    async def disconnect_all(self):
        for account_id in list(self._clients.keys()):
            await self.disconnect_account(account_id)


telegram_manager = TelegramManager()
