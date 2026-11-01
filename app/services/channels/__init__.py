"""通知中心（6.x）的通道适配器。

每个通道实现 ``notification_center.ChannelAdapter`` 的 ``send(ctx)`` 协议，在模块导入时
通过 ``register_channel(name, adapter)`` 注册进 6.1 的通道注册表。目前只有 6.2 的
``wechat_subscribe``；``in_app`` 通道直接在 ``notification_center`` 里注册。
"""
