from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from email.header import Header
from typing import List, Optional
import logging

from app import crud
from app.db import session_scope
from app.services.crypto import decrypt_secret

logger = logging.getLogger(__name__)

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
        smtp_host = smtp_config.get("smtp_host")
        smtp_port_str = smtp_config.get("smtp_port")
        smtp_user = smtp_config.get("smtp_user")
        smtp_pass = smtp_config.get("smtp_pass")
        smtp_from = smtp_config.get("smtp_from")
        if not to_addrs:
            smtp_to = smtp_config.get("smtp_to")
            if smtp_to:
                to_addrs = [addr.strip() for addr in smtp_to.split(",") if addr.strip()]
    else:
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
                    to_addrs = [addr.strip() for addr in smtp_to.split(",") if addr.strip()]
    
    if not all([smtp_host, smtp_port_str, smtp_user, smtp_pass, smtp_from, to_addrs]):
        logger.warning("Email notification skipped: SMTP settings incomplete")
        return False

    try:
        smtp_port = int(smtp_port_str)
        msg = MIMEText(content, content_type, "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = smtp_from
        msg["To"] = ",".join(to_addrs)

        server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
        try:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, to_addrs, msg.as_string())
            logger.info(f"Email sent successfully to {to_addrs}")
            
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
