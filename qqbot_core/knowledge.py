"""Small, scoped candidate ledger. This database never writes legacy memory."""
from contextlib import contextmanager
from dataclasses import dataclass, asdict
import hashlib
import json
import re
import sqlite3
import time
import unicodedata
import math
from pathlib import Path
from .group_policy import policy_locked

CATEGORIES = frozenset({"meme", "style", "personal_preference", "external_fact",
                        "correction", "reply_experience"})
TRANSITIONS = {
    "candidate": {"reviewed", "verified", "disputed", "rejected", "expired", "retracted"},
    "reviewed": {"verified", "disputed", "rejected", "expired", "retracted"},
    "verified": {"disputed", "expired", "retracted"},
    "disputed": {"reviewed", "verified", "rejected", "expired", "retracted"},
    "rejected": set(), "expired": set(), "retracted": set(),
}
UNSAFE = re.compile(
    r"忽略.{0,8}(?:指令|规则|提示)|系统提示|system\s*prompt|ignore.{0,20}instruction|"
    r"(?:执行|运行).{0,8}(?:命令|代码)|API.?KEY|密码|身份证|银行卡|手机号|家庭住址|"
    r"\b\d{11,18}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", re.I)


def safe_content(text):
    return bool(text and len(text) <= 500 and not UNSAFE.search(text))


def normalized(text):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).casefold())


@dataclass(frozen=True)
class Knowledge:
    id: str
    schema_version: int
    category: str
    group_id: str
    subject_ref: str
    content: str
    source_refs: list
    evidence_status: str
    created_at: float
    last_checked_at: float
    expires_at: float
    applicability: str
    invalidation_rule: str
    consent_ref: str
    policy_version: int
    supersedes_id: str
    observation_count: int = 1
    last_seen_at: float = 0


class KnowledgeStore:
    def __init__(self, path, policy, *, clock=time.time, read_only=False):
        self.path, self.policy, self.clock = str(path), policy, clock
        self.read_only = read_only
        if read_only:
            return
        # A separate bounded connection per operation: no thread ownership leaks.
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS knowledge (
                id TEXT PRIMARY KEY, schema_version INTEGER, category TEXT,
                group_id TEXT, subject_ref TEXT, content TEXT, source_refs TEXT,
                evidence_status TEXT, created_at REAL, last_checked_at REAL,
                expires_at REAL, applicability TEXT, invalidation_rule TEXT,
                consent_ref TEXT, policy_version INTEGER, supersedes_id TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS knowledge_scope ON knowledge(group_id, category, evidence_status)")
            db.execute("""CREATE TABLE IF NOT EXISTS reviews (
                knowledge_id TEXT, actor_id TEXT, status TEXT, checked_at REAL,
                verification_refs TEXT, method TEXT)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge)")}
            for name, declaration in (("observation_count", "INTEGER NOT NULL DEFAULT 1"),
                                      ("last_seen_at", "REAL NOT NULL DEFAULT 0")):
                if name not in columns:
                    db.execute(f"ALTER TABLE knowledge ADD COLUMN {name} {declaration}")
            db.execute("""CREATE TABLE IF NOT EXISTS observations (
                knowledge_id TEXT, source_hash TEXT, PRIMARY KEY(knowledge_id, source_hash))""")

    @contextmanager
    def _connection(self):
        read_only = getattr(self, "read_only", False)
        target = Path(self.path).resolve().as_uri() + "?mode=ro" if read_only else self.path
        db = sqlite3.connect(target, timeout=0.05, uri=read_only)
        if read_only:
            db.execute("PRAGMA query_only=ON")
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _decode(row):
        values = dict(row)
        values["source_refs"] = json.loads(values["source_refs"])
        return Knowledge(**values)

    def get(self, knowledge_id):
        with self._connection() as db:
            row = db.execute("SELECT * FROM knowledge WHERE id=?", (knowledge_id,)).fetchone()
        return self._decode(row) if row else None

    @policy_locked
    def add_candidate(self, category, group_id, content, source_refs, *, subject_ref="",
                      consent_ref="", applicability="group", ttl=604800,
                      supersedes_id="", invalidation_rule="expiry_or_retraction", expected_policy_version=None):
        gid, subject_ref = str(group_id), str(subject_ref)
        if not self.policy.allows(gid, "knowledge_learning"):
            raise PermissionError("learning not authorized")
        if expected_policy_version is not None and self.policy.version(gid) != expected_policy_version:
            raise PermissionError("queued learning authorization changed")
        if category not in CATEGORIES or not safe_content(content):
            raise ValueError("invalid or unsafe candidate")
        if applicability not in {"group", "personal"}:
            raise ValueError("invalid applicability")
        if not source_refs or len(source_refs) > 20 or not all(
            isinstance(s, dict) and s.get("message_id") and str(s.get("group_id")) == gid
            and s.get("kind") == "human" for s in source_refs
        ):
            raise ValueError("independent human message references required")
        if category == "personal_preference":
            if (applicability != "personal" or not self.policy.has_consent(
                    gid, subject_ref, category, consent_ref)):
                raise PermissionError("subject consent required")
            if not all(str(s.get("user_id")) == subject_ref for s in source_refs):
                raise PermissionError("preference must originate from subject")
        if category == "correction" and not supersedes_id:
            raise ValueError("correction must identify original assertion")
        if supersedes_id:
            original = self.get(supersedes_id)
            if not original or original.group_id != gid:
                raise ValueError("original assertion outside scope")
        now = self.clock()
        if not math.isfinite(float(ttl)):
            raise ValueError("finite TTL required")
        ttl = min(max(float(ttl), 1), 30 * 86400)
        version = self.policy.version(gid)
        digest = hashlib.sha256(json.dumps(
            [gid, subject_ref, category, normalized(content), version, supersedes_id],
            ensure_ascii=False).encode()).hexdigest()
        item = Knowledge(digest, 1, category, gid, subject_ref, content, source_refs,
                         "candidate", now, 0, now + ttl, applicability,
                         invalidation_rule, consent_ref, version, supersedes_id)
        values = asdict(item)
        values["source_refs"] = json.dumps(source_refs, ensure_ascii=False)
        with self._connection() as db:
            existing = db.execute("SELECT * FROM knowledge WHERE id=?", (digest,)).fetchone()
            if not existing and db.execute("SELECT count(*) FROM knowledge WHERE group_id=?", (gid,)).fetchone()[0] >= 2000:
                raise ValueError("group knowledge capacity reached; archive review required")
            if existing and (existing["expires_at"] <= now or existing["evidence_status"] in
                             {"rejected", "expired", "retracted", "disputed"}):
                return digest
            old_refs = json.loads(existing["source_refs"]) if existing else []
            # Exact source deduplication is capped per item; never count repeated batch windows.
            count = db.execute("SELECT count(*) FROM observations WHERE knowledge_id=?", (digest,)).fetchone()[0]
            if existing and count == 0:
                for ref in old_refs:
                    identity = {k: str(ref.get(k, "")) for k in ("group_id", "user_id", "message_id")}
                    source_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
                    db.execute("INSERT OR IGNORE INTO observations VALUES (?,?)", (digest, source_hash))
            count = db.execute("SELECT count(*) FROM observations WHERE knowledge_id=?", (digest,)).fetchone()[0]
            added = 0
            accepted_refs = []
            for ref in source_refs:
                if count >= 200:
                    break
                identity = {k: str(ref.get(k, "")) for k in ("group_id", "user_id", "message_id")}
                source_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
                inserted = db.execute("INSERT OR IGNORE INTO observations VALUES (?,?)", (digest, source_hash)).rowcount
                count += inserted
                added += inserted
                accepted_refs.append(ref)
            source_refs = accepted_refs
            if existing:
                # Merge punctuation/spacing variants, never infer truth from repetition.
                refs = json.loads(existing["source_refs"])
                known = {(s["message_id"], s.get("user_id")) for s in refs}
                refs += [s for s in source_refs if (s["message_id"], s.get("user_id")) not in known]
                db.execute("UPDATE knowledge SET source_refs=?,observation_count=?,last_seen_at=?,expires_at=? WHERE id=?",
                           (json.dumps(refs[-20:], ensure_ascii=False), count,
                            now if added else existing["last_seen_at"],
                            min(existing["created_at"] + 30 * 86400, now + ttl) if added else existing["expires_at"], digest))
                return digest
            values.update(observation_count=count, last_seen_at=now)
            db.execute("INSERT INTO knowledge (" + ",".join(values) + ") VALUES (" + ",".join("?" for _ in values) + ")", tuple(values.values()))
            if subject_ref and (category in {"external_fact", "correction"}
                                or (category == "style" and subject_ref == "group_sentence_length")):
                conflicts = db.execute(
                    "SELECT id, content FROM knowledge WHERE group_id=? AND subject_ref=? AND category=? AND id<>? AND evidence_status IN ('candidate','reviewed','verified')",
                    (gid, subject_ref, category, digest)).fetchall()
                if any(normalized(r["content"]) != normalized(content) for r in conflicts):
                    ids = [digest] + [r["id"] for r in conflicts]
                    db.executemany("UPDATE knowledge SET evidence_status='disputed' WHERE id=?", [(i,) for i in ids])
                    self.policy.touch(gid)
        return digest

    @policy_locked
    def transition(self, knowledge_id, status, actor_id, *, trusted_role="",
                   verification_refs=(), method=""):
        if not self.policy.is_admin(actor_id, trusted_role):
            raise PermissionError("reviewer must be trusted administrator")
        with self._connection() as db:
            row = db.execute("SELECT * FROM knowledge WHERE id=?", (knowledge_id,)).fetchone()
            if not row:
                raise KeyError(knowledge_id)
            item = self._decode(row)
            if (not self.policy.allows(item.group_id, "knowledge_learning") or
                    item.policy_version != self.policy.version(item.group_id)):
                raise PermissionError("policy revoked or replaced")
            if status not in TRANSITIONS[item.evidence_status]:
                raise ValueError("invalid evidence transition")
            if status in {"reviewed", "verified"} and item.expires_at <= self.clock():
                raise ValueError("expired knowledge")
            if status == "verified":
                if method not in {"manual_external_check", "trusted_source_check"} or not verification_refs:
                    raise ValueError("independent verification required")
                sources = {s.get("message_id") for s in item.source_refs}
                if not all(isinstance(ref, str) and ref.startswith("https://") and ref not in sources
                           for ref in verification_refs):
                    raise ValueError("external evidence references required")
            # Reviewed external facts still cannot be retrieved as factual evidence.
            db.execute("UPDATE knowledge SET evidence_status=?,last_checked_at=? WHERE id=?",
                       (status, self.clock(), knowledge_id))
            db.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?)",
                       (knowledge_id, str(actor_id), status, self.clock(), json.dumps(list(verification_refs)), method))
        self.policy.touch(item.group_id)

    @policy_locked
    def eligible(self, group_id, categories, *, user_id="", limit=100, expression_candidates=False):
        gid = str(group_id)
        if not self.policy.allows(gid, "knowledge_retrieval"):
            return []
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM knowledge WHERE group_id=? AND policy_version=? AND expires_at>? AND "
                "(evidence_status IN ('reviewed','verified') OR (? AND evidence_status='candidate' "
                "AND category IN ('style','meme','reply_experience'))) ORDER BY last_seen_at DESC,last_checked_at DESC LIMIT ?",
                (gid, self.policy.version(gid), self.clock(), bool(expression_candidates), min(limit, 100))).fetchall()
        result = []
        for row in rows:
            item = self._decode(row)
            if item.category not in categories:
                continue
            if item.category in {"external_fact", "correction"} and item.evidence_status != "verified":
                continue
            if item.category == "personal_preference" and (
                item.subject_ref != str(user_id) or not self.policy.has_consent(
                    gid, user_id, item.category, item.consent_ref)):
                continue
            if item.applicability == "personal" and item.subject_ref != str(user_id):
                continue
            result.append(item)
        return result
