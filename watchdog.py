import subprocess
import time
import os
import sys
import logging
import configparser
from logging.handlers import RotatingFileHandler

SERVICE_NAME = "TXT-Dedup-Server"
CHECK_INTERVAL = 10  
RESTART_DELAY = 5     
MAX_RETRY = 3         
ALERT_EMAIL = True

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('watchdog.log', maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def _load_email_config():
    """从 server_config.ini 加载邮件配置，供看门狗告警使用"""
    config = configparser.ConfigParser()
    config_path = 'server_config.ini'
    if os.path.exists(config_path):
        config.read(config_path, encoding='utf-8')
    return {
        'smtp_server': config.get('Email', 'smtp_server', fallback=''),
        'smtp_port': config.getint('Email', 'smtp_port', fallback=587),
        'smtp_username': config.get('Email', 'smtp_username', fallback=''),
        'smtp_password': config.get('Email', 'smtp_password', fallback=''),
        'from_addr': config.get('Email', 'from_addr', fallback=''),
        'to_addrs': config.get('Email', 'to_addrs', fallback='')
    }

def is_service_running(service_name):
    try:
        result = subprocess.run(
            ['sc', 'query', service_name],
            capture_output=True,
            text=True,
            timeout=30
        )
        return 'RUNNING' in result.stdout
    except Exception as e:
        logger.error(f"Failed to check service status: {e}")
        return False

def start_service(service_name):
    try:
        result = subprocess.run(
            ['net', 'start', service_name],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            logger.info(f"Service {service_name} started successfully")
            return True
        else:
            logger.error(f"Failed to start service: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Failed to start service: {e}")
        return False

def stop_service(service_name):
    try:
        subprocess.run(
            ['net', 'stop', service_name],
            capture_output=True,
            text=True,
            timeout=60
        )
    except Exception as e:
        logger.warning(f"Failed to stop service (may not be running): {e}")

def send_alert(subject, message):
    if not ALERT_EMAIL:
        return
    try:
        from notifier import email_notification
        email_config = _load_email_config()
        if not email_config.get('smtp_server') or not email_config.get('to_addrs'):
            logger.warning(f"[看门狗告警] 邮件配置不完整，跳过发送: smtp_server={bool(email_config.get('smtp_server'))}, to_addrs={bool(email_config.get('to_addrs'))}")
            return
        email_notification(
            config=email_config,
            title=f"[看门狗告警] {subject}",
            message=message
        )
        logger.info("Alert email sent")
    except Exception as e:
        logger.error(f"Failed to send alert email: {e}")

def main():
    logger.info("="*50)
    logger.info("TXT Dedup Watchdog Service Started")
    logger.info(f"Monitoring service: {SERVICE_NAME}")
    logger.info(f"Check interval: {CHECK_INTERVAL}s")
    logger.info("="*50)
    
    consecutive_failures = 0
    
    while True:
        try:
            if not is_service_running(SERVICE_NAME):
                consecutive_failures += 1
                logger.warning(f"Service not running! (Failure #{consecutive_failures})")
                
                if consecutive_failures <= MAX_RETRY:
                    logger.info(f"Attempting to restart service (attempt {consecutive_failures}/{MAX_RETRY})")
                    stop_service(SERVICE_NAME)
                    time.sleep(RESTART_DELAY)
                    
                    if start_service(SERVICE_NAME):
                        consecutive_failures = 0
                        logger.info("Service restarted successfully")
                else:
                    logger.error(f"Service failed {MAX_RETRY} times! Sending alert...")
                    send_alert(
                        "服务连续失败",
                        f"服务 {SERVICE_NAME} 已连续失败 {MAX_RETRY} 次，请检查！"
                    )
                    consecutive_failures = 0
            else:
                consecutive_failures = 0
                
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            logger.info("Watchdog service stopping...")
            break
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()