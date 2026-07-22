"""微信推送通道：Server酱 / PushPlus / 空实现。

密钥只走环境变量（SERVERCHAN_SENDKEY / PUSHPLUS_TOKEN），绝不进配置或代码。
发送失败不抛异常——db 里的告警记录才是事实源，推送是尽力而为。
"""

import logging
import os

import requests

logger = logging.getLogger("live_trading.notifier")

_TIMEOUT = 10


class Notifier:
    """推送通道抽象。"""

    channel = "none"

    def send(self, title: str, content_md: str) -> bool:
        raise NotImplementedError


class NullNotifier(Notifier):
    """空实现：只记日志，用于 channel=none / 密钥缺失降级 / 测试。"""

    def send(self, title: str, content_md: str) -> bool:
        logger.info("[null notifier] %s\n%s", title, content_md)
        return True


class ServerChanNotifier(Notifier):
    channel = "serverchan"

    def __init__(self, sendkey: str):
        self.sendkey = sendkey

    def send(self, title: str, content_md: str) -> bool:
        url = f"https://sctapi.ftqq.com/{self.sendkey}.send"
        try:
            resp = requests.post(
                url, data={"title": title, "desp": content_md}, timeout=_TIMEOUT
            )
            if resp.status_code != 200:
                logger.error("serverchan http %s: %s", resp.status_code, resp.text[:200])
                return False
            code = resp.json().get("code", -1)
            if code != 0:
                logger.error("serverchan business error: %s", resp.text[:200])
                return False
            return True
        except Exception as e:
            logger.error("serverchan send failed: %s", e)
            return False


class PushPlusNotifier(Notifier):
    channel = "pushplus"

    def __init__(self, token: str):
        self.token = token

    def send(self, title: str, content_md: str) -> bool:
        try:
            resp = requests.post(
                "https://www.pushplus.plus/send",
                json={
                    "token": self.token,
                    "title": title,
                    "content": content_md,
                    "template": "markdown",
                },
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.error("pushplus http %s: %s", resp.status_code, resp.text[:200])
                return False
            code = resp.json().get("code", -1)
            if code != 200:
                logger.error("pushplus business error: %s", resp.text[:200])
                return False
            return True
        except Exception as e:
            logger.error("pushplus send failed: %s", e)
            return False


def create_notifier(monitor_cfg: dict) -> Notifier:
    """按 monitor.notify.channel 创建通道；密钥缺失降级 NullNotifier 不抛错。"""
    channel = (monitor_cfg or {}).get("notify", {}).get("channel", "none")
    if channel == "serverchan":
        sendkey = os.environ.get("SERVERCHAN_SENDKEY", "")
        if not sendkey:
            logger.error("SERVERCHAN_SENDKEY not set, falling back to null notifier")
            return NullNotifier()
        return ServerChanNotifier(sendkey)
    if channel == "pushplus":
        token = os.environ.get("PUSHPLUS_TOKEN", "")
        if not token:
            logger.error("PUSHPLUS_TOKEN not set, falling back to null notifier")
            return NullNotifier()
        return PushPlusNotifier(token)
    if channel != "none":
        logger.error("unknown notify channel %r, using null notifier", channel)
    return NullNotifier()
