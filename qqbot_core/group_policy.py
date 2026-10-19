"""Explicit in-memory grants. No chat text can grant permissions."""
from dataclasses import dataclass
from functools import wraps
import json
import os
import sqlite3
import secrets
from threading import RLock


def policy_locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        policy = getattr(self, "policy", self)
        with policy._lock:
            return method(self, *args, **kwargs)
    return wrapped

FEATURES = frozenset({"observe", "style_learning", "knowledge_learning",
                      "knowledge_retrieval", "proactive_chat"})


@dataclass(frozen=True)
class Grant:
    group_id: str
    actor_id: str
    notice_ref: str
    features: frozenset
    version: int


class GroupPolicy:
    def __init__(self, admin_ids=(), *, persistence_path=None):
        self._lock = RLock()
        self.admin_ids = frozenset(map(str, admin_ids))
        # Versions are embedded in persistent knowledge/session records.  A
        # fresh process needs a new epoch so an old grant cannot collide with
        # the first grant created after restart.
        self._epoch = secrets.randbits(47) << 16
        self._grants = {}
        self._versions = {}
        self._consents = {}
        self._revisions = {}
        self.persistence_path = os.fspath(persistence_path) if persistence_path else ""
        self._persistence_error = ""
        if self.persistence_path and os.path.isfile(self.persistence_path):
            self._load_persistent()

    @property
    def persistence_ok(self):
        return not self._persistence_error

    def _load_persistent(self):
        versions, grants, consents = {}, {}, {}
        db = None
        try:
            db = sqlite3.connect(self.persistence_path, timeout=1)
            db.row_factory = sqlite3.Row
            state_rows = db.execute("SELECT group_id, version FROM policy_state").fetchall()
            grant_rows = db.execute("SELECT * FROM group_grants").fetchall()
            consent_rows = db.execute("SELECT * FROM subject_consents").fetchall()
            versions = {str(row["group_id"]): int(row["version"]) for row in state_rows}
            for row in grant_rows:
                gid, version = str(row["group_id"]), int(row["version"])
                features = frozenset(json.loads(row["features"]))
                notice_ref, actor_id = str(row["notice_ref"]), str(row["actor_id"])
                if (versions.get(gid) == version and features and features <= FEATURES
                        and notice_ref and len(notice_ref) <= 500 and actor_id):
                    grants[gid] = Grant(gid, actor_id, notice_ref, features, version)
            for row in consent_rows:
                gid, subject, purpose = str(row["group_id"]), str(row["subject_id"]), str(row["purpose"])
                version, reference = int(row["version"]), str(row["reference"])
                if (gid in grants and grants[gid].version == version
                        and purpose == "personal_preference" and reference):
                    consents[(gid, subject, purpose)] = (reference, version)
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            self._persistence_error = type(exc).__name__
            return
        finally:
            if db is not None:
                db.close()
        self._versions.update(versions)
        self._grants.update(grants)
        self._consents.update(consents)

    @staticmethod
    def _create_schema(db):
        db.execute("CREATE TABLE IF NOT EXISTS policy_state (group_id TEXT PRIMARY KEY, version INTEGER NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS group_grants (
            group_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, notice_ref TEXT NOT NULL,
            features TEXT NOT NULL, version INTEGER NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS subject_consents (
            group_id TEXT NOT NULL, subject_id TEXT NOT NULL, purpose TEXT NOT NULL,
            reference TEXT NOT NULL, version INTEGER NOT NULL,
            PRIMARY KEY (group_id, subject_id, purpose))""")

    def _write(self, operation):
        if not self.persistence_path:
            return
        if self._persistence_error:
            raise RuntimeError("group policy storage unavailable")
        db = None
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.persistence_path)), exist_ok=True)
            db = sqlite3.connect(self.persistence_path, timeout=1)
            with db:
                self._create_schema(db)
                operation(db)
        except (OSError, sqlite3.Error) as exc:
            self._persistence_error = type(exc).__name__
            raise RuntimeError("group policy persistence failed") from exc
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _store_version(db, group_id, version):
        db.execute("INSERT INTO policy_state VALUES (?,?) ON CONFLICT(group_id) DO UPDATE SET version=excluded.version",
                   (group_id, version))

    def is_admin(self, actor_id, trusted_role=""):
        # trusted_role comes from trusted OneBot metadata, never chat text.
        return str(actor_id) in self.admin_ids or trusted_role in {"owner", "admin"}

    @policy_locked
    def grant(self, group_id, actor_id, features, notice_ref, *, trusted_role=""):
        features = frozenset(features)
        if not self.is_admin(actor_id, trusted_role):
            raise PermissionError("trusted administrator required")
        notice_ref = str(notice_ref or "").strip()
        actor_id = str(actor_id)
        if not notice_ref or len(notice_ref) > 500 or "\n" in notice_ref or "\r" in notice_ref:
            raise ValueError("valid public notice reference required")
        if not features or not features <= FEATURES:
            raise ValueError("notice reference and valid feature scopes required")
        gid = str(group_id)
        current = self._grants.get(gid)
        if current and (current.actor_id, current.notice_ref, current.features) == (actor_id, notice_ref, features):
            return current
        version = self.version(gid) + 1
        grant = Grant(gid, actor_id, notice_ref, features, version)
        def persist(db):
            self._store_version(db, gid, version)
            db.execute("INSERT INTO group_grants VALUES (?,?,?,?,?) ON CONFLICT(group_id) DO UPDATE SET "
                       "actor_id=excluded.actor_id, notice_ref=excluded.notice_ref, "
                       "features=excluded.features, version=excluded.version",
                       (gid, actor_id, notice_ref, json.dumps(sorted(features)), version))
            db.execute("DELETE FROM subject_consents WHERE group_id=?", (gid,))
        self._write(persist)
        self._versions[gid] = version
        self._grants[gid] = grant
        self._consents = {k: v for k, v in self._consents.items() if k[0] != gid}
        self.touch(gid)
        return grant

    @policy_locked
    def revoke(self, group_id, actor_id, *, trusted_role=""):
        if not self.is_admin(actor_id, trusted_role):
            raise PermissionError("trusted administrator required")
        gid = str(group_id)
        version = self.version(gid) + 1
        try:
            self._write(lambda db: (self._store_version(db, gid, version),
                                    db.execute("DELETE FROM group_grants WHERE group_id=?", (gid,)),
                                    db.execute("DELETE FROM subject_consents WHERE group_id=?", (gid,))))
        except RuntimeError:
            # Disable in this process even when durable revocation cannot be written.
            self._versions[gid] = version
            self._grants.pop(gid, None)
            self._consents = {k: v for k, v in self._consents.items() if k[0] != gid}
            self.touch(gid)
            raise
        self._versions[gid] = version
        self._grants.pop(gid, None)
        self._consents = {k: v for k, v in self._consents.items() if k[0] != gid}
        self.touch(gid)

    @policy_locked
    def allows(self, group_id, feature):
        if self._persistence_error:
            return False
        grant = self._grants.get(str(group_id))
        return bool(grant and feature in grant.features)

    @policy_locked
    def version(self, group_id):
        return self._versions.get(str(group_id), self._epoch)

    @policy_locked
    def revision(self, group_id):
        return self._revisions.get(str(group_id), self._epoch)

    @policy_locked
    def touch(self, group_id):
        gid = str(group_id)
        self._revisions[gid] = self.revision(gid) + 1

    @policy_locked
    def consent(self, group_id, subject_id, actor_id, purpose, reference):
        if str(subject_id) != str(actor_id) or not reference or purpose != "personal_preference":
            raise PermissionError("explicit subject consent required")
        if not self.allows(group_id, "knowledge_learning"):
            raise PermissionError("group learning not authorized")
        gid, subject, reference = str(group_id), str(subject_id), str(reference)
        version = self.version(gid)
        self._write(lambda db: db.execute(
            "INSERT INTO subject_consents VALUES (?,?,?,?,?) ON CONFLICT(group_id,subject_id,purpose) "
            "DO UPDATE SET reference=excluded.reference, version=excluded.version",
            (gid, subject, purpose, reference, version)))
        self._consents[(gid, subject, purpose)] = (reference, version)
        self.touch(group_id)

    @policy_locked
    def has_consent(self, group_id, subject_id, purpose, reference):
        return bool(reference) and self._consents.get((str(group_id), str(subject_id), purpose)) == (reference, self.version(group_id))

    @policy_locked
    def consent_reference(self, group_id, subject_id, purpose):
        value = self._consents.get((str(group_id), str(subject_id), purpose))
        return value[0] if value and value[1] == self.version(group_id) else ""

    @policy_locked
    def revoke_consent(self, group_id, subject_id, actor_id):
        if str(subject_id) != str(actor_id):
            raise PermissionError("subject required")
        gid, subject = str(group_id), str(subject_id)
        self._write(lambda db: db.execute(
            "DELETE FROM subject_consents WHERE group_id=? AND subject_id=? AND purpose=?",
            (gid, subject, "personal_preference")))
        self._consents.pop((gid, subject, "personal_preference"), None)
        self.touch(group_id)
