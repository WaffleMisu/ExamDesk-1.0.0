from enum import StrEnum


class AdminRole(StrEnum):
    SUPERVISOR = "supervisor"
    ADMIN = "admin"


class QuestionType(StrEnum):
    SINGLE = "single"
    MULTIPLE = "multiple"
    JUDGE = "judge"
    FILL = "fill"


class QuestionStatus(StrEnum):
    DRAFT = "draft"
    ENABLED = "enabled"
    DISABLED = "disabled"


class UsageScope(StrEnum):
    PRACTICE_ONLY = "practice_only"
    EXAM_ONLY = "exam_only"
    BOTH = "both"


class SessionStatus(StrEnum):
    DRAFT = "draft"
    LOCKED = "locked"
    ARCHIVED = "archived"


class ReviewPolicy(StrEnum):
    IMMEDIATE = "immediate"
    AFTER_RELEASE = "after_release"
    SCORE_ONLY = "score_only"


class AttemptStatus(StrEnum):
    ACTIVE = "active"
    SUBMITTED = "submitted"
    INCOMPLETE = "incomplete"
    VOID = "void"


class SubmitReason(StrEnum):
    MANUAL = "manual"
    TIMEOUT = "timeout"
    RECOVERED_TIMEOUT = "recovered_timeout"


class MatchMode(StrEnum):
    STRICT = "strict"
    TEXT_SIMILARITY = "text_similarity"

