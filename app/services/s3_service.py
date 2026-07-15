from __future__ import annotations

import logging
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from app import crud
from sqlmodel import Session
from datetime import datetime
from app.core.settings import settings
from app.core.time import apply_timezone_offset, parse_timezone_offset_to_minutes
from app.i18n import translate
from app.services.crypto import decrypt_secret

logger = logging.getLogger(__name__)

def _build_s3_config(region: str, max_attempts: int) -> Config:
    kwargs = {
        "region_name": region if region else None,
        "retries": {"max_attempts": max_attempts, "mode": "standard"},
        "signature_version": "s3v4",
        "s3": {"addressing_style": "path", "payload_signing_enabled": False},
    }
    try:
        return Config(
            **kwargs,
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
    except TypeError:
        return Config(**kwargs)

def upload_backup_to_s3(session: Session, device_name: str, host: str, config_text: str, finished_at: datetime) -> bool:
    """
    将备份内容上传到 S3
    """
    s3_enabled = crud.get_setting(session, key="s3_enabled") == "1"
    if not s3_enabled:
        return False

    endpoint = crud.get_setting(session, key="s3_endpoint")
    access_key = decrypt_secret(crud.get_setting(session, key="s3_access_key"))
    secret_key = decrypt_secret(crud.get_setting(session, key="s3_secret_key"))
    bucket = crud.get_setting(session, key="s3_bucket")
    region = crud.get_setting(session, key="s3_region")
    prefix = crud.get_setting(session, key="s3_prefix") or "backups"
    
    # 获取时区偏移
    tz_str = crud.get_setting(session, key="timezone_offset") or settings.timezone_offset
    offset_minutes = parse_timezone_offset_to_minutes(tz_str) or 0
    
    # 将 UTC 时间转换为本地时间
    local_dt = apply_timezone_offset(finished_at, offset_minutes) or finished_at

    if not all([access_key, secret_key, bucket]):
        logger.error("S3 configuration is incomplete")
        return False
    
    # 清理 bucket 名称，防止用户输入了带路径的名称
    bucket = bucket.strip().strip("/")

    try:
        s3_config = _build_s3_config(region, max_attempts=3)
        
        s3_client = boto3.client(
            's3',
            endpoint_url=endpoint if endpoint else None,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region if region else None,
            config=s3_config
        )

        # 构建文件名: {prefix}/YYYY-MM-DD/device_name_host_timestamp.txt
        date_str = local_dt.strftime("%Y-%m-%d")
        time_str = local_dt.strftime("%H%M%S")
        safe_device_name = "".join([c if c.isalnum() or c in "-_." else "_" for c in device_name]).strip("_") or "device"
        safe_host = "".join([c if c.isalnum() or c in "-_." else "_" for c in host]).strip("_") or "host"
        
        # 处理前缀，确保末尾没有斜杠
        clean_prefix = prefix.strip().strip("/")
        if clean_prefix:
            file_key = f"{clean_prefix}/{date_str}/{safe_device_name}_{safe_host}_{time_str}.txt"
        else:
            file_key = f"{date_str}/{safe_device_name}_{safe_host}_{time_str}.txt"

        body = config_text.encode("utf-8")
        s3_client.put_object(
            Bucket=bucket,
            Key=file_key,
            Body=body,
            ContentType="text/plain",
            ContentLength=len(body),
        )
        
        logger.info(f"Successfully uploaded backup to S3: {file_key}")
        return True
    except Exception as e:
        logger.error(f"Failed to upload backup to S3: {str(e)}")
        return False

def test_s3_connection(
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    region: str,
    locale: str | None = None,
) -> tuple[bool, str]:
    """
    测试 S3 连接
    """
    # 清理 bucket 名称
    bucket = bucket.strip().strip("/")

    try:
        s3_config = _build_s3_config(region, max_attempts=1)
        
        s3_client = boto3.client(
            's3',
            endpoint_url=endpoint if endpoint else None,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region if region else None,
            config=s3_config
        )

        # 尝试上传一个极小的测试文件来验证写入权限，这比 head_bucket 更实用
        test_key = f"connection_test_{int(datetime.utcnow().timestamp())}.txt"
        test_body = b"connection test"
        s3_client.put_object(
            Bucket=bucket,
            Key=test_key,
            Body=test_body,
            ContentType="text/plain",
            ContentLength=len(test_body),
        )
        # 测试成功后删除测试文件
        try:
            s3_client.delete_object(Bucket=bucket, Key=test_key)
        except:
            pass
            
        return True, translate(locale, "message.storage.s3.connection_success")
    except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            if error_code == '403' or error_code == 'Forbidden':
                return False, translate(locale, "error.storage.s3.forbidden")
            elif error_code == '404' or error_code == 'NoSuchBucket':
                return False, translate(locale, "error.storage.s3.bucket_not_found")
            elif error_code == 'InvalidAccessKeyId':
                return False, translate(locale, "error.storage.s3.invalid_access_key")
            elif error_code == 'SignatureDoesNotMatch':
                return False, translate(locale, "error.storage.s3.signature_mismatch")
            return False, translate(
                locale,
                "error.storage.s3.client_error",
                {"error_code": error_code or "Unknown", "error": str(e)},
            )
    except Exception as e:
        return False, translate(locale, "error.storage.s3.connection_failed", {"error": str(e)})
