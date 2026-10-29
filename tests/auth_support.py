"""Seeding helpers for authentication and authorization integration tests."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.security import hash_password


@dataclass(frozen=True, slots=True)
class StaffSeed:
    id: UUID
    tenant_id: UUID
    role: str
    phone: str
    password: str
    teacher_id: UUID | None = None


def unique_phone() -> str:
    """A phone unique per test run; the database persists across local runs."""

    return f"1{uuid4().int % 10**10:010d}"


def unique_openid(prefix: str) -> str:
    """An openid unique per test run (wechat_openid has a unique constraint)."""

    return f"{prefix}-{uuid4().hex[:12]}"


@dataclass(frozen=True, slots=True)
class FamilySeed:
    wechat_user_id: UUID
    tenant_ids: tuple[UUID, ...]
    student_ids: tuple[UUID, ...]


async def seed_staff(
    engine: AsyncEngine,
    *,
    tenant_name: str,
    role: str,
    phone: str,
    password: str = "correct-horse-battery",
    status: str = "active",
) -> StaffSeed:
    """Create one tenant and one staff account through the owner connection."""

    tenant_id, teacher_id, staff_id = uuid4(), None, uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
            {"id": tenant_id, "name": tenant_name},
        )
        if role == "teacher":
            teacher_id = uuid4()
            await connection.execute(
                text("INSERT INTO teacher (id, tenant_id, name) VALUES (:id, :tenant, :name)"),
                {"id": teacher_id, "tenant": tenant_id, "name": f"{tenant_name}老师"},
            )
        await connection.execute(
            text(
                "INSERT INTO staff_account "
                "(id, tenant_id, name, phone, password_hash, role, teacher_id, status) "
                "VALUES (:id, :tenant, :name, :phone, :hash, :role, :teacher, :status)"
            ),
            {
                "id": staff_id,
                "tenant": tenant_id,
                "name": f"{role} user",
                "phone": phone,
                "hash": hash_password(password),
                "role": role,
                "teacher": teacher_id,
                "status": status,
            },
        )
    return StaffSeed(
        id=staff_id,
        tenant_id=tenant_id,
        role=role,
        phone=phone,
        password=password,
        teacher_id=teacher_id,
    )


async def seed_family(
    engine: AsyncEngine,
    *,
    entries: list[tuple[str, str]],
    openid: str | None = None,
    unionid: str | None = None,
    app_id: str = "wx_platform_stub",
) -> FamilySeed:
    """Create one WeChat person with a parent binding in every listed tenant."""

    wechat_user_id = uuid4()
    tenant_ids: list[UUID] = []
    student_ids: list[UUID] = []
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO wechat_user (id, unionid) VALUES (:id, :unionid)"),
            {"id": wechat_user_id, "unionid": unionid},
        )
        if openid:
            await connection.execute(
                text(
                    "INSERT INTO wechat_openid (id, wechat_user_id, app_id, openid) "
                    "VALUES (:id, :user, :app, :openid)"
                ),
                {
                    "id": uuid4(),
                    "user": wechat_user_id,
                    "app": app_id,
                    "openid": openid,
                },
            )
        for tenant_name, student_name in entries:
            tenant_id, student_id, parent_id = uuid4(), uuid4(), uuid4()
            tenant_ids.append(tenant_id)
            student_ids.append(student_id)
            await connection.execute(
                text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
                {"id": tenant_id, "name": tenant_name},
            )
            await connection.execute(
                text("INSERT INTO student (id, tenant_id, name) VALUES (:id, :tenant, :name)"),
                {"id": student_id, "tenant": tenant_id, "name": student_name},
            )
            await connection.execute(
                text(
                    "INSERT INTO parent (id, tenant_id, name, phone, wechat_user_id) "
                    "VALUES (:id, :tenant, :name, :phone, :user)"
                ),
                {
                    "id": parent_id,
                    "tenant": tenant_id,
                    "name": f"{student_name}家长",
                    "phone": "13800000000",
                    "user": wechat_user_id,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO parent_student "
                    "(id, tenant_id, parent_id, student_id, relation) "
                    "VALUES (:id, :tenant, :parent, :student, '家长')"
                ),
                {
                    "id": uuid4(),
                    "tenant": tenant_id,
                    "parent": parent_id,
                    "student": student_id,
                },
            )
    return FamilySeed(
        wechat_user_id=wechat_user_id,
        tenant_ids=tuple(tenant_ids),
        student_ids=tuple(student_ids),
    )
