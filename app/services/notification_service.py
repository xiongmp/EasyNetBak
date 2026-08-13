from __future__ import annotations

import smtplib
from email import policy
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr, getaddresses, parseaddr
from typing import List, Optional
import logging

from app import crud
from app.db import session_scope
from app.i18n import get_current_locale, translate
from app.i18n.validators import normalize_locale
from app.services.crypto import decrypt_secret
from app.services.errors import ServiceError

logger = logging.getLogger(__name__)

EMAIL_MIME_MAX_BYTES = 3 * 1024 * 1024


def _build_email_message(
    subject: str,
    content: str,
    *,
    content_type: str,
    smtp_from: str,
    to_addrs: List[str],
    locale: str,
) -> tuple[bytes, str, List[str]]:
    from_display_name, envelope_from = parseaddr(str(smtp_from or ""))
    parsed_recipients = [
        (display_name, address)
        for display_name, address in getaddresses([str(value) for value in to_addrs])
        if address
    ]
    if not envelope_from or not parsed_recipients:
        raise ValueError("Invalid sender or recipient email address")

    # SMTP envelope addresses must remain plain addr-spec values. Non-ASCII
    # display names belong only in RFC 2047-encoded message headers.
    envelope_from.encode("ascii")
    envelope_recipients = [address for _, address in parsed_recipients]
    for address in envelope_recipients:
        address.encode("ascii")

    message = MIMEText(content, content_type, "utf-8")
    message["Subject"] = Header(subject, "utf-8").encode()
    message["From"] = formataddr((from_display_name, envelope_from), charset="utf-8")
    message["To"] = ", ".join(
        formataddr((display_name, address), charset="utf-8")
        for display_name, address in parsed_recipients
    )
    message_bytes = message.as_bytes(policy=policy.SMTP)
    actual_bytes = len(message_bytes)
    if actual_bytes > EMAIL_MIME_MAX_BYTES:
        params = {
            "actual_bytes": actual_bytes,
            "limit_bytes": EMAIL_MIME_MAX_BYTES,
        }
        raise ServiceError(
            translate(locale, "notification.error.email_mime_too_large", params),
            code="NOTIFICATION_EMAIL_MIME_TOO_LARGE",
            context=params,
            message_key="notification.error.email_mime_too_large",
            params=params,
        )
    return message_bytes, envelope_from, envelope_recipients

def send_email(
    subject: str,
    content: str,
    to_addrs: Optional[List[str]] = None,
    content_type: str = "plain",
    smtp_config: Optional[dict] = None
) -> bool:
    """
    发送邮件通知
    :param smtp_config: 可选的 SMTP 配置字典，用于测试尚未保存的配置
    """
    if smtp_config:
        locale = normalize_locale(str(smtp_config.get("_locale") or get_current_locale()))
        smtp_host = smtp_config.get("smtp_host")
        smtp_port_str = smtp_config.get("smtp_port")
        smtp_user = smtp_config.get("smtp_user")
        smtp_pass = smtp_config.get("smtp_pass")
        smtp_from = smtp_config.get("smtp_from")
        if not to_addrs:
            smtp_to = smtp_config.get("smtp_to")
            if smtp_to:
                to_addrs = [str(smtp_to)]
    else:
        locale = normalize_locale(get_current_locale())
        with session_scope() as session:
            smtp_host = crud.get_setting(session, key="smtp_host")
            smtp_port_str = crud.get_setting(session, key="smtp_port")
            smtp_user = crud.get_setting(session, key="smtp_user")
            smtp_pass = decrypt_secret(crud.get_setting(session, key="smtp_pass"))
            smtp_from = crud.get_setting(session, key="smtp_from")
            
            # 如果没有配置收件人，则从设置中获取默认收件人
            if not to_addrs:
                smtp_to = crud.get_setting(session, key="smtp_to")
                if smtp_to:
                    to_addrs = [str(smtp_to)]
    
    if not all([smtp_host, smtp_port_str, smtp_user, smtp_pass, smtp_from, to_addrs]):
        logger.warning("Email notification skipped: SMTP settings incomplete")
        return False

    try:
        smtp_port = int(smtp_port_str)
        message_bytes, envelope_from, envelope_recipients = _build_email_message(
            subject,
            content,
            content_type=content_type,
            smtp_from=smtp_from,
            to_addrs=to_addrs,
            locale=locale,
        )

        server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
        try:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(envelope_from, envelope_recipients, message_bytes)
            logger.info("Email sent successfully recipient_count=%s", len(envelope_recipients))
            
            # 邮件已成功送达，尝试正常退出
            try:
                server.quit()
            except Exception:
                # 某些服务器在发送完邮件后会强制断开连接，导致 quit() 报错
                # 既然邮件已经 sendmail 成功，这里可以忽略退出时的异常
                pass
            return True
        except Exception as e:
            # 发送过程中的异常需要处理
            try:
                server.close()
            except Exception:
                pass
            raise e
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        raise e  # 向上抛出异常以便在 API 中捕获详细错误信息
