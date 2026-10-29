"""Authentication contract shared by JWT verification and authorization."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Principal:
    """Authoritative identity resolved from a signed token and the database."""

    subject_id: UUID | None = None
    tenant_id: UUID | None = None
    role: str = "anonymous"
    scope_type: str = "tenant"
    teacher_id: UUID | None = None
    active_student_id: UUID | None = None

    @property
    def is_parent(self) -> bool:
        return self.role == "parent"

    @property
    def is_authenticated(self) -> bool:
        return self.subject_id is not None

    @property
    def has_tenant(self) -> bool:
        return self.tenant_id is not None
