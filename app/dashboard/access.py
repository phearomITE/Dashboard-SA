"""Short-lived, single-use access codes issued by the bot to its management admins."""

import hashlib
import secrets
from time import monotonic

from app.data.controllers import GLOBAL_MANAGEMENT_USER_IDS


class DashboardAccess:
    def __init__(self):
        self.codes = {}
        self.sessions = {}

    def _mint(self, entries, user_id, lifetime):
        now = monotonic()
        for key, (_, expiry) in list(entries.items()):
            if expiry <= now:
                del entries[key]
        token = secrets.token_urlsafe(24)
        entries[self._key(token)] = (user_id, now + lifetime)
        return token

    @staticmethod
    def _key(token):
        return hashlib.sha256(token.encode()).digest()

    def issue(self, user_id):
        if user_id not in GLOBAL_MANAGEMENT_USER_IDS:
            raise ValueError("Management access required.")
        self.codes = {k: v for k, v in self.codes.items() if v[0] != user_id}
        return self._mint(self.codes, user_id, 300)

    def exchange(self, code):
        user_id, expiry = self.codes.pop(self._key(code), (None, 0))
        if expiry > monotonic() and user_id in GLOBAL_MANAGEMENT_USER_IDS:
            return self._mint(self.sessions, user_id, 8 * 3600)

    def identify(self, token):
        key = self._key(token)
        user_id, expiry = self.sessions.get(key, (None, 0))
        if expiry > monotonic() and user_id in GLOBAL_MANAGEMENT_USER_IDS:
            return user_id
        self.sessions.pop(key, None)

    def revoke(self, token):
        self.sessions.pop(self._key(token), None)
