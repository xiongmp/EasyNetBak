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
from app.services.crypto import decrypt_secret

logger = logging.getLogger(__name__)

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
        s3_config = Config(
            region_name=region if region else None,
            retries={'max_attempts': 3, 'mode': 'standard'},
            signature_version="s3v4",
            s3={"addressing_style": "path", "payload_signing_enabled": False}
        )
        
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
        safe_device_name = "".join([c if c.isalnum() else "_" for c in device_name])
        
        # 处理前缀，确保末尾没有斜杠
        clean_prefix = prefix.strip().strip("/")
        if clean_prefix:
            file_key = f"{clean_prefix}/{date_str}/{safe_device_name}_{host}_{time_str}.txt"
        else:
            file_key = f"{date_str}/{safe_device_name}_{host}_{time_str}.txt"

        s3_client.put_object(
            Bucket=bucket,
            Key=file_key,
            Body=config_text.encode('utf-8'),
            ContentType='text/plain'
        )
        
        logger.info(f"Successfully uploaded backup to S3: {file_key}")
        return True
    except Exception as e:
        logger.error(f"Failed to upload backup to S3: {str(e)}")
        return False

def test_s3_connection(endpoint: str, access_key: str, secret_key: str, bucket: str, region: str) -> tuple[bool, str]:
    """
    测试 S3 连接
    """
    # 清理 bucket 名称
    bucket = bucket.strip().strip("/")

    try:
        s3_config = Config(
            region_name=region if region else None,
            retries={'max_attempts': 1, 'mode': 'standard'},
            signature_version="s3v4",
            s3={"addressing_style": "path", "payload_signing_enabled": False}
        )
        
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
        s3_client.put_object(
            Bucket=bucket,
            Key=test_key,
            Body=b"connection test",
            ContentType='text/plain'
        )
        # 测试成功后删除测试文件
        try:
            s3_client.delete_object(Bucket=bucket, Key=test_key)
        except:
            pass
            
        return True, "S3 连接成功，写入权限验证通过。"
    except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            if error_code == '403' or error_code == 'Forbidden':
                return False, "S3 连接失败 (403 Forbidden): 权限不足。请检查：1. SecretId/SecretKey 是否正确；2. 存储桶名称是否包含 APPID 后缀；3. 子账号是否有该存储桶的访问权限 (cos:PutObject)。"
            elif error_code == '404' or error_code == 'NoSuchBucket':
                return False, "S3 连接失败 (404 Not Found): 存储桶不存在。请检查存储桶名称和 Region 是否匹配。"
            elif error_code == 'InvalidAccessKeyId':
                return False, "S3 连接失败 (InvalidAccessKeyId): Access Key ID 格式错误。请检查 SecretId 是否包含 'AKID' 前缀且无空格。"
            elif error_code == 'SignatureDoesNotMatch':
                return False, "S3 连接失败 (SignatureDoesNotMatch): 签名不匹配。请检查：1. SecretKey 是否填写正确；2. Region (地域) 是否与存储桶实际地域一致；3. 系统时间是否准确。"
            return False, f"S3 连接失败 ({error_code}): {str(e)}"
    except Exception as e:
        return False, f"S3 连接失败: {str(e)}"
