"""6.3 降级链（服务号 → 短信）+ 短信日预算 + timeout/临时失败分流。

全部在真实 PostgreSQL 上跑，stub 客户端注入（不走真微信/真短信）。What must never break：

* 服务号未关注（43004）→ 跳过本级，短信通道被尝试且成功；
* 服务号成功 → 短信通道零调用（第一个成功即停）；
* 服务号未配置（env 无 appid）→ 整级静默跳过直达短信；
* 短信日预算：当日成功条数达到 ``org_config.sms_daily_budget`` → 跳过不发、不报错，次日恢复；
* timeout → 不重试该通道、直达下一级、日志含 ``timeout_unknown``；
* 临时失败 → 抛异常进 6.1 重试，重试只补发失败的通道。
"""

import logging
import os
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.models.notification_center import Notification
from app.services import notification_center as nc
from app.services.channels.sms import SmsChannel, StubSmsClient
from app.services.channels.wechat_mp import StubWechatMpClient, WechatMpChannel
from app.services.channels.wechat_subscribe import StubWechatSubscribeClient, WechatSubscribeChannel
from tests.auth_support import unique_openid, unique_phone

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DB_TESTS") != "1",
        reason="set RUN_DB_TESTS=1 and start PostgreSQL to run fallback chain tests",
    ),
]

MP_APP_ID = "wx_mp_test"
MP_TEMPLATE_ID = "mp_tpl_1"
SMS_PROVIDER = "aliyun"


@dataclass(frozen=True, slots=True)
class Seed:
    tenant_id: UUID
    parent_id: UUID
    wechat_user_id: UUID
    openid: str


def configured_settings(**overrides) -> Settings:
    """服务号 + 短信都已配置（测试的「正常」机构）。"""

    values: dict = dict(
        wechat_mp_app_id=MP_APP_ID,
        wechat_mp_template_id=MP_TEMPLATE_ID,
        sms_provider=SMS_PROVIDER,
    )
    values.update(overrides)
    return Settings(**values)


def register_channels(settings: Settings, mp_stub, sms_stub) -> None:
    nc.register_channel("wechat_mp", WechatMpChannel(client=mp_stub, settings=settings))
    nc.register_channel("sms", SmsChannel(client=sms_stub, settings=settings))


@pytest.fixture
def db_factory(owner_engine):
    return async_sessionmaker(owner_engine, expire_on_commit=False)


@pytest.fixture
def mp_stub() -> StubWechatMpClient:
    return StubWechatMpClient()


@pytest.fixture
def sms_stub() -> StubSmsClient:
    return StubSmsClient()


@pytest.fixture(autouse=True)
def isolated_channels():
    """每个测试后恢复通道注册表，隔离注入的假通道。"""

    before = dict(nc._channels)
    yield
    nc._channels.clear()
    nc._channels.update(before)


@pytest.fixture(autouse=True)
async def clean_slate(owner_engine):
    """清空出站队列 / 站内信 / 机构配置，避免 process 扫到别的测试的 pending 行。"""

    async with owner_engine.begin() as connection:
        await connection.execute(text("DELETE FROM in_app_message"))
        await connection.execute(text("DELETE FROM notification"))
        await connection.execute(text("DELETE FROM org_config"))
    yield


@pytest.fixture
async def seed(owner_engine) -> Seed:
    """一个机构 + 一个微信身份（带服务号 openid）+ 一个带手机号的家长。"""

    tenant_id, parent_id, wechat_user_id = uuid4(), uuid4(), uuid4()
    openid = unique_openid("mp")
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name) VALUES (:id, '降级链机构')"), {"id": tenant_id}
        )
        await connection.execute(
            text("INSERT INTO wechat_user (id) VALUES (:id)"), {"id": wechat_user_id}
        )
        await connection.execute(
            text(
                "INSERT INTO wechat_openid (id, wechat_user_id, app_id, openid) "
                "VALUES (:id, :u, :app, :openid)"
            ),
            {"id": uuid4(), "u": wechat_user_id, "app": MP_APP_ID, "openid": openid},
        )
        await connection.execute(
            text(
                "INSERT INTO parent (id, tenant_id, name, phone, wechat_user_id) "
                "VALUES (:id, :t, '家长', :phone, :u)"
            ),
            {
                "id": parent_id,
                "t": tenant_id,
                "phone": unique_phone(),
                "u": wechat_user_id,
            },
        )
    return Seed(
        tenant_id=tenant_id, parent_id=parent_id, wechat_user_id=wechat_user_id, openid=openid
    )


def chain_payload(parent_id: UUID) -> dict:
    return {"parent_ids": [str(parent_id)], "title": "测试标题", "body": "测试正文"}


async def enqueue(
    db_factory, *, tenant_id: UUID, dedupe_key: str, payload: dict, channels: list[str]
) -> None:
    async with db_factory() as db:
        async with db.begin():
            await nc.enqueue_notification(
                db,
                tenant_id=tenant_id,
                event_type="test_event",
                dedupe_key=dedupe_key,
                payload=payload,
                channels=channels,
            )


async def get_notification(db_factory, dedupe_key: str) -> Notification:
    async with db_factory() as db:
        return (
            await db.execute(select(Notification).where(Notification.dedupe_key == dedupe_key))
        ).scalar_one()


async def set_next_retry_to_past(db_factory, dedupe_key: str) -> None:
    async with db_factory() as db:
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE notification SET next_retry_at = now() - interval '1 minute' "
                    "WHERE dedupe_key = :k"
                ),
                {"k": dedupe_key},
            )


async def insert_sms_success(owner_engine, *, tenant_id: UUID, dedupe_key: str) -> None:
    """造一条「当日短信成功」的通知（成功条数 = 预算统计口径）。"""

    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO notification "
                "(tenant_id, event_type, dedupe_key, status, succeeded_channels, updated_at) "
                "VALUES (:t, 'x', :k, 'succeeded', ARRAY['sms'], now())"
            ),
            {"t": tenant_id, "k": dedupe_key},
        )


async def test_mp_not_following_falls_back_to_sms(
    db_factory, owner_engine, seed, mp_stub, sms_stub
) -> None:
    """服务号未关注（43004）→ 跳过本级，短信通道被尝试且成功。"""

    register_channels(configured_settings(), mp_stub, sms_stub)
    mp_stub.reject_next_with = 43004
    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="fb-1",
        payload=chain_payload(seed.parent_id),
        channels=["wechat_mp", "sms"],
    )
    await nc.process_pending_notifications(db_factory)

    notification = await get_notification(db_factory, "fb-1")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert len(mp_stub.calls) == 1  # 服务号试过一次（返回 43004）
    assert len(sms_stub.calls) == 1  # 短信补上
    assert notification.succeeded_channels == ["sms"]


async def test_mp_success_stops_before_sms(
    db_factory, owner_engine, seed, mp_stub, sms_stub
) -> None:
    """服务号成功 → 短信零调用（第一个成功即停）。"""

    register_channels(configured_settings(), mp_stub, sms_stub)
    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="fb-2",
        payload=chain_payload(seed.parent_id),
        channels=["wechat_mp", "sms"],
    )
    await nc.process_pending_notifications(db_factory)

    notification = await get_notification(db_factory, "fb-2")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert len(mp_stub.calls) == 1
    assert len(sms_stub.calls) == 0
    assert notification.succeeded_channels == ["wechat_mp"]


async def test_mp_not_configured_skips_to_sms(
    db_factory, owner_engine, seed, mp_stub, sms_stub
) -> None:
    """服务号未配置（env 无 appid）→ 整级静默跳过直达短信。"""

    register_channels(configured_settings(wechat_mp_app_id=""), mp_stub, sms_stub)
    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="fb-3",
        payload=chain_payload(seed.parent_id),
        channels=["wechat_mp", "sms"],
    )
    await nc.process_pending_notifications(db_factory)

    notification = await get_notification(db_factory, "fb-3")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert len(mp_stub.calls) == 0  # 未配置，一次都没试
    assert len(sms_stub.calls) == 1
    assert notification.succeeded_channels == ["sms"]


async def test_sms_budget_exhausted_then_recovers_next_day(
    db_factory, owner_engine, seed, mp_stub, sms_stub
) -> None:
    """短信日预算：满额后第 N+1 条跳过不发、不报错；次日恢复。"""

    register_channels(configured_settings(), mp_stub, sms_stub)
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO org_config (id, tenant_id, sms_daily_budget) "
                "VALUES (gen_random_uuid(), :t, 2)"
            ),
            {"t": seed.tenant_id},
        )
    await insert_sms_success(owner_engine, tenant_id=seed.tenant_id, dedupe_key="sent-1")
    await insert_sms_success(owner_engine, tenant_id=seed.tenant_id, dedupe_key="sent-2")

    # 第 3 条：预算已满 → 跳过不发，通知 exhausted（不报错、不重试）
    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="budget-1",
        payload=chain_payload(seed.parent_id),
        channels=["sms"],
    )
    await nc.process_pending_notifications(db_factory)
    notification = await get_notification(db_factory, "budget-1")
    assert notification.status == nc.STATUS_EXHAUSTED
    assert len(sms_stub.calls) == 0  # 没发

    # 次日：把两条成功记录回拨到昨天 → 今日用量归零 → 恢复发送
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE notification SET updated_at = now() - interval '1 day' "
                "WHERE dedupe_key IN ('sent-1', 'sent-2')"
            )
        )
    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="budget-2",
        payload=chain_payload(seed.parent_id),
        channels=["sms"],
    )
    await nc.process_pending_notifications(db_factory)
    notification = await get_notification(db_factory, "budget-2")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert len(sms_stub.calls) == 1
    assert notification.succeeded_channels == ["sms"]


async def test_mp_timeout_falls_through_to_sms(
    db_factory, owner_engine, seed, mp_stub, sms_stub, caplog
) -> None:
    """服务号超时（TIMEOUT_UNKNOWN）→ 不重试该通道、直达短信、日志含 timeout_unknown。"""

    register_channels(configured_settings(), mp_stub, sms_stub)
    mp_stub.timeout_next = True
    with caplog.at_level(logging.WARNING):
        await enqueue(
            db_factory,
            tenant_id=seed.tenant_id,
            dedupe_key="fb-5",
            payload=chain_payload(seed.parent_id),
            channels=["wechat_mp", "sms"],
        )
        await nc.process_pending_notifications(db_factory)

    notification = await get_notification(db_factory, "fb-5")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert len(mp_stub.calls) == 1
    assert len(sms_stub.calls) == 1
    assert notification.succeeded_channels == ["sms"]
    assert any(r.getMessage() == "timeout_unknown" for r in caplog.records)


async def test_transient_failure_retries_only_failed_channel(
    db_factory, owner_engine, seed, mp_stub, sms_stub
) -> None:
    """临时失败 → 抛异常进重试；重试只补发失败的短信，服务号（未配置）不再重复调。"""

    register_channels(configured_settings(wechat_mp_app_id=""), mp_stub, sms_stub)
    sms_stub.fail_next_with = RuntimeError("network blip")
    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="fb-6",
        payload=chain_payload(seed.parent_id),
        channels=["wechat_mp", "sms"],
    )
    await nc.process_pending_notifications(db_factory)
    notification = await get_notification(db_factory, "fb-6")
    assert notification.status == nc.STATUS_PENDING
    assert notification.succeeded_channels == []
    assert len(sms_stub.calls) == 1
    assert len(mp_stub.calls) == 0  # 未配置，从不调客户端

    await set_next_retry_to_past(db_factory, "fb-6")
    await nc.process_pending_notifications(db_factory)
    notification = await get_notification(db_factory, "fb-6")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert notification.succeeded_channels == ["sms"]
    assert len(sms_stub.calls) == 2  # 只补发短信


async def test_full_chain_degrade_to_in_app(
    db_factory, owner_engine, seed, mp_stub, sms_stub
) -> None:
    """订阅耗尽 → 服务号未配置 → 短信超预算 → 站内信兜底，家长最终只收到一条站内信。"""

    subscribe_stub = StubWechatSubscribeClient()
    settings = configured_settings(wechat_mp_app_id="")  # 服务号未配置
    nc.register_channel(
        "wechat_subscribe", WechatSubscribeChannel(client=subscribe_stub, settings=settings)
    )
    register_channels(settings, mp_stub, sms_stub)

    # 订阅：给小程序 appid 配 openid + 额度 0（耗尽）→ 订阅级跳过
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO wechat_openid (id, wechat_user_id, app_id, openid) "
                "VALUES (:id, :u, :app, :openid)"
            ),
            {
                "id": uuid4(),
                "u": seed.wechat_user_id,
                "app": "wx_platform_stub",
                "openid": unique_openid("sub"),
            },
        )
        await connection.execute(
            text(
                "INSERT INTO subscribe_auth "
                "(id, tenant_id, wechat_user_id, template_id, status, available_count, updated_at) "
                "VALUES (gen_random_uuid(), :t, :u, 'stub_lesson_remind', 'authorized', 0, now())"
            ),
            {"t": seed.tenant_id, "u": seed.wechat_user_id},
        )
        # 短信日预算 0 → 超预算跳过
        await connection.execute(
            text(
                "INSERT INTO org_config (id, tenant_id, sms_daily_budget) "
                "VALUES (gen_random_uuid(), :t, 0)"
            ),
            {"t": seed.tenant_id},
        )

    payload = {
        "parent_ids": [str(seed.parent_id)],
        "template_scene": "lesson_remind",
        "title": "测试标题",
        "body": "测试正文",
        "source_type": "test",
        "source_id": str(uuid4()),
    }
    await enqueue(
        db_factory,
        tenant_id=seed.tenant_id,
        dedupe_key="full-chain-1",
        payload=payload,
        channels=["wechat_subscribe", "wechat_mp", "sms", "in_app"],
    )
    await nc.process_pending_notifications(db_factory)

    notification = await get_notification(db_factory, "full-chain-1")
    assert notification.status == nc.STATUS_SUCCEEDED
    assert notification.succeeded_channels == ["in_app"]
    assert len(subscribe_stub.calls) == 0  # 额度耗尽，没调微信订阅
    assert len(mp_stub.calls) == 0  # 未配置，从不调客户端
    assert len(sms_stub.calls) == 0  # 超预算
    async with owner_engine.connect() as connection:
        inapp = int(
            await connection.scalar(
                text("SELECT count(*) FROM in_app_message WHERE parent_id = :p"),
                {"p": seed.parent_id},
            )
            or 0
        )
    assert inapp == 1
