"""ORM models grouped by the three layers of the 0.4 data dictionary.

Importing this package registers every table on ``Base.metadata`` so that Alembic
autogenerate and the schema tests see the complete model.
"""

from app.models.archive import Classroom, Parent, ParentStudent, Student, Teacher
from app.models.attendance import Attendance
from app.models.course import Class, ClassEnrollment, Course, CoursePackage
from app.models.ledger import LessonLedger
from app.models.org import OrgConfig, StaffAccount
from app.models.platform import Plan, Tenant, TenantSubscription, UsageMeter
from app.models.schedule import Lesson, ScheduleRule
from app.models.trade import Purchase, Refund
from app.models.wechat import SubscribeAuth, WechatOpenid, WechatUser

#: Platform layer (4) — owned by the SaaS operator, no tenant-level RLS.
PLATFORM_TABLES: tuple[str, ...] = (
    "tenant",
    "plan",
    "tenant_subscription",
    "usage_meter",
)

#: WeChat identity layer (3) — ``wechat_user``/``wechat_openid`` are platform-wide.
WECHAT_TABLES: tuple[str, ...] = (
    "wechat_user",
    "wechat_openid",
    "subscribe_auth",
)

#: Business layer (17) — every table carries ``tenant_id`` (red line 1).
BUSINESS_TABLES: tuple[str, ...] = (
    "student",
    "parent",
    "teacher",
    "classroom",
    "parent_student",
    "course",
    "class",
    "class_enrollment",
    "course_package",
    "purchase",
    "lesson_ledger",
    "schedule_rule",
    "lesson",
    "attendance",
    "staff_account",
    "org_config",
    "refund",
)

#: Tables protected by RLS + FORCE RLS: all tenant-scoped business data, plus the
#: tenant-scoped subscription record. Platform tables (plan / tenant_subscription /
#: usage_meter) are deliberately exempt: the platform administrator must aggregate
#: across tenants (0.3 decision 6).
RLS_TABLES: tuple[str, ...] = (*BUSINESS_TABLES, "subscribe_auth")

__all__ = [
    "BUSINESS_TABLES",
    "PLATFORM_TABLES",
    "RLS_TABLES",
    "WECHAT_TABLES",
    "Attendance",
    "Class",
    "ClassEnrollment",
    "Classroom",
    "Course",
    "CoursePackage",
    "Lesson",
    "LessonLedger",
    "OrgConfig",
    "Parent",
    "ParentStudent",
    "Plan",
    "Purchase",
    "Refund",
    "ScheduleRule",
    "StaffAccount",
    "Student",
    "SubscribeAuth",
    "Teacher",
    "Tenant",
    "TenantSubscription",
    "UsageMeter",
    "WechatOpenid",
    "WechatUser",
]
