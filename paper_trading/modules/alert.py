"""Alert module: check thresholds and send notifications."""

import logging
import os
import smtplib
from email.mime.text import MIMEText
from typing import Optional

from .recorder import Recorder

logger = logging.getLogger("paper_trading.alert")


class AlertManager:
    """Monitors account metrics and triggers alerts."""

    def __init__(self, config: dict, recorder: Recorder):
        alert_cfg = config.get("alert", {})
        self.enabled = alert_cfg.get("enabled", False)
        self.email = os.environ.get("ALERT_EMAIL", alert_cfg.get("email", ""))
        self.daily_loss_threshold = alert_cfg.get("daily_loss_threshold", -0.03)
        self.max_drawdown_threshold = alert_cfg.get("max_drawdown_threshold", -0.10)
        self.consecutive_loss_days = alert_cfg.get("consecutive_loss_days", 5)
        self.recorder = recorder

    def check_alerts(self, date_str: str, summary: dict) -> list[str]:
        """Check alert conditions and return list of triggered alert messages."""
        alerts = []

        daily_return = summary.get("daily_return", 0)
        if daily_return and daily_return < self.daily_loss_threshold:
            alerts.append(
                f"[单日亏损告警] {date_str}: 当日收益率 {daily_return*100:.2f}% "
                f"超过阈值 {self.daily_loss_threshold*100:.1f}%"
            )

        df = self.recorder.get_account_summary()
        df = df[df["date"] != "init"]
        if not df.empty:
            total_value = df["total_value"]
            peak = total_value.expanding().max()
            drawdown = ((total_value - peak) / peak).min()
            if drawdown < self.max_drawdown_threshold:
                alerts.append(
                    f"[最大回撤告警] {date_str}: 最大回撤 {drawdown*100:.2f}% "
                    f"超过阈值 {self.max_drawdown_threshold*100:.1f}%"
                )

            recent = df.tail(self.consecutive_loss_days)
            if len(recent) >= self.consecutive_loss_days:
                if (recent["daily_return"] < 0).all():
                    alerts.append(
                        f"[连续亏损告警] {date_str}: 连续 {self.consecutive_loss_days} "
                        f"个交易日亏损"
                    )

        for msg in alerts:
            logger.warning(msg)
            self.recorder.save_system_log("WARNING", "alert", msg)

        if alerts and self.enabled and self.email:
            self._send_email(date_str, alerts)

        return alerts

    def _send_email(self, date_str: str, alerts: list[str]):
        """Send alert email. Fails silently if SMTP is not configured."""
        try:
            smtp_host = os.environ.get("SMTP_HOST", "smtp.126.com")
            smtp_port = int(os.environ.get("SMTP_PORT", "587"))
            smtp_user = os.environ.get("SMTP_USER", "xqyu1993@126.com")
            smtp_pass = os.environ.get("SMTP_PASS", "Yxq199304203615$")

            if not smtp_host or not smtp_user:
                logger.warning("SMTP not configured, skipping email alert")
                return

            body = f"模拟盘告警 - {date_str}\n\n" + "\n".join(alerts)
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = f"[模拟盘告警] {date_str}"
            msg["From"] = smtp_user
            msg["To"] = self.email

            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, [self.email], msg.as_string())
            logger.info("Alert email sent to %s", self.email)
        except Exception as e:
            logger.error("Failed to send alert email: %s", e)
