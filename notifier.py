#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import threading
import ctypes
from datetime import datetime
from typing import Optional, Dict, List


def windows_toast(title: str, message: str):
    """Windows系统通知"""
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        toaster.show_toast(
            title,
            message,
            duration=10,
            threaded=False
        )
        return True
    except ImportError:
        pass
    
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            message,
            title,
            0x40 | 0x1
        )
        return True
    except Exception:
        return False


def bark_notification(url: str, title: str, message: str) -> bool:
    """Bark iOS推送通知"""
    try:
        import requests
        bark_url = f"{url.rstrip('/')}/{title}/{message}"
        response = requests.get(bark_url, timeout=5)
        return response.status_code == 200
    except Exception:
        return False


def wecom_webhook(url: str, title: str, message: str) -> bool:
    """企业微信群机器人通知"""
    try:
        import requests
        data = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"### {title}\n\n{message}"
            }
        }
        response = requests.post(url, json=data, timeout=10)
        result = response.json()
        return result.get('errcode') == 0
    except Exception:
        return False


def dingtalk_webhook(url: str, title: str, message: str) -> bool:
    """钉钉群机器人通知"""
    try:
        import requests
        import hashlib
        import time
        import base64
        import hmac
        
        timestamp = str(round(time.time() * 1000))
        secret = ''
        
        if 'secret=' in url:
            parts = url.split('?')
            url_base = parts[0]
            params = dict(p.split('=') for p in parts[1].split('&'))
            secret = params.get('secret', '')
            
            if secret:
                timestamp = str(round(time.time() * 1000))
                secret_enc = secret.encode('utf-8')
                string_to_sign = f'{timestamp}\n{secret}'
                string_to_sign_enc = string_to_sign.encode('utf-8')
                sign = base64.b64encode(hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()).decode('utf-8')
                url = f"{url_base}?timestamp={timestamp}&sign={sign}"
        
        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"### {title}\n\n{message}"
            }
        }
        response = requests.post(url, json=data, timeout=10)
        result = response.json()
        return result.get('errcode') == 0
    except Exception:
        return False


def send_notification(notify_type: str, title: str, message: str, config: dict = None) -> bool:
    """发送通知"""
    if notify_type == "system":
        return windows_toast(title, message)
    elif notify_type == "wecom":
        webhook = config.get('wecom_webhook', '') if config else ''
        if webhook:
            return wecom_webhook(webhook, title, message)
    elif notify_type == "bark":
        url = config.get('bark_url', '') if config else ''
        if url:
            return bark_notification(url, title, message)
    elif notify_type == "dingtalk":
        webhook = config.get('dingtalk_webhook', '') if config else ''
        if webhook:
            return dingtalk_webhook(webhook, title, message)
    elif notify_type == "email":
        return email_notification(config, title, message)
    return False


def email_notification(config: dict, title: str, message: str) -> bool:
    """邮件通知"""
    import smtplib
    import logging
    log = logging.getLogger('dedup_notifier')
    
    if not config:
        log.error("[邮件] 配置为空，无法发送邮件")
        return False
    
    smtp_server = config.get('smtp_server', '')
    smtp_port = config.get('smtp_port', 587)
    smtp_username = config.get('smtp_username', '')
    smtp_password = config.get('smtp_password', '')
    from_addr = config.get('from_addr', smtp_username)
    to_addrs = config.get('to_addrs', '')
    
    if not smtp_server or not smtp_username or not smtp_password or not to_addrs:
        log.error(f"[邮件] 配置不完整: server={bool(smtp_server)}, user={bool(smtp_username)}, pwd={bool(smtp_password)}, to={bool(to_addrs)}")
        return False
    
    try:
        from email.mime.text import MIMEText
        from email.header import Header
        
        body = message.replace('**', '').replace('\n\n', '\n')
        
        if isinstance(body, bytes):
            body = body.decode('utf-8')
        
        part = MIMEText(body, 'plain')
        part.set_charset('utf-8')
        part['From'] = from_addr
        part['To'] = to_addrs
        part['Subject'] = Header(title, 'utf-8')
        
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_username, smtp_password)
            server.sendmail(from_addr, [addr.strip() for addr in to_addrs.split(',')], part.as_string())
        
        return True
    except smtplib.SMTPAuthenticationError:
        log.error(f"[邮件] SMTP认证失败，请检查用户名和授权码: {smtp_username}")
        return False
    except smtplib.SMTPConnectError:
        log.error(f"[邮件] SMTP连接失败，请检查服务器地址: {smtp_server}:{smtp_port}")
        return False
    except smtplib.SMTPResponseException as e:
        log.error(f"[邮件] SMTP响应异常 code={e.smtp_code}: {e.smtp_error}")
        return False
    except smtplib.SMTPException as e:
        log.error(f"[邮件] SMTP异常: {str(e)}")
        return False
    except Exception as e:
        log.error(f"[邮件] 发送异常: {type(e).__name__}: {str(e)}")
        return False


class NotificationManager:
    def __init__(self, config: dict, machine_id: str = ''):
        self.notify_type = config.get('notify_type', 'system')
        self.notify_duplicate_rate = config.get('notify_duplicate_rate', 0)
        self.notify_config = {
            'wecom_webhook': config.get('wecom_webhook', ''),
            'bark_url': config.get('bark_url', ''),
            'dingtalk_webhook': config.get('dingtalk_webhook', ''),
            'smtp_server': config.get('smtp_server', ''),
            'smtp_port': config.get('smtp_port', 587),
            'smtp_username': config.get('smtp_username', ''),
            'smtp_password': config.get('smtp_password', ''),
            'from_addr': config.get('from_addr', ''),
            'to_addrs': config.get('to_addrs', '')
        }
        self.machine_id = machine_id
        self.logger = None
        self._last_notify_time = {}
        self._min_notify_interval = config.get('min_notify_interval', 300)
        self._lock = threading.Lock()
    
    def set_logger(self, logger):
        self.logger = logger
    
    def _check_notify_interval(self, key: str) -> bool:
        """检查通知发送间隔，防止重复发送（原子操作，无竞态）"""
        now = time.time()
        with self._lock:
            last_time = self._last_notify_time.get(key, 0)
            if now - last_time >= self._min_notify_interval:
                self._last_notify_time[key] = now
                return True
            return False
    
    def _get_notify_type_name(self) -> str:
        """获取通知类型的中文名称"""
        type_names = {
            'email': '邮件',
            'wecom': '企业微信',
            'bark': 'Bark推送',
            'dingtalk': '钉钉',
            'system': '系统通知',
            'none': '无'
        }
        return type_names.get(self.notify_type, self.notify_type)

    def notify_completed(self, file_name: str, total: int, duplicate: int, duplicate_rate: float, duplicate_sources: Dict = None, duplicate_lines: List = None, machine_compare: Dict = None, file_path: str = '', file_import_time: str = ''):
        """发送查重完成通知（无论是否有重复）"""
        if duplicate > 0:
            title = "❌ TXT查重警告 - 检测到重复数据"
        else:
            title = "✅ TXT查重完成"
            
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if not file_import_time:
            file_import_time = now_str
            
        message = f"========================================\n"
        message += f"          TXT查重工具 - 查重完成\n"
        message += f"========================================\n\n"

        message += f"📅 完成时间: {now_str}\n\n"
        if self.machine_id:
            message += f"🏭 当前机台: {self.machine_id}\n\n"
        message += f"📁 文件名称: {file_name}\n"
        if file_path:
            message += f"📂 文件路径: {file_path}\n"
        message += f"⏰ 导入时间: {file_import_time}\n\n"
        message += f"📊 统计信息:\n"
        message += f"   • 总数据量: {total:,}\n"
        message += f"   • 重复数据: {duplicate:,}\n"
        message += f"   • 唯一数据: {total - duplicate:,}\n"
        message += f"   • 重复率: {duplicate_rate:.2f}%\n\n"
        
        if duplicate > 0 and machine_compare and machine_compare.get('machine_counts'):
            message += f"🏭 机台对比:\n"
            for mid, count in machine_compare['machine_counts'].items():
                if mid == self.machine_id:
                    message += f"   • {mid} (当前): {count:,} 条重复\n"
                else:
                    message += f"   • {mid}: {count:,} 条重复\n"
            message += "\n"
        
        if duplicate > 0 and duplicate_lines:
            message += f"📋 重复内容预览 (前3条):\n"
            for i, line in enumerate(duplicate_lines[:3]):
                display_line = line[:80] if len(line) > 80 else line
                message += f"   {i+1}. 前3字: {display_line[:3]}...\n"
                message += f"      完整: {display_line}\n"
            message += "\n"
        
        if duplicate > 0 and duplicate_sources:
            source_list = []
            for h, source in duplicate_sources.items():
                key = (source.get('filename', '未知'), source.get('file_path', ''), source.get('timestamp', '未知'), source.get('machine_id', ''))
                if key not in source_list:
                    source_list.append(key)
            
            message += f"📁 重复来源追溯 (前3条):\n"
            for i, (fname, fpath, ts, mid) in enumerate(source_list[:3]):
                message += f"   {i+1}. 原文件名: {fname}\n"
                if fpath:
                    message += f"      原文件路径: {fpath}\n"
                message += f"      导入时间: {ts}"
                if mid:
                    message += f" | 机台: {mid}"
                message += f"\n"
            
            if len(source_list) > 3:
                message += f"   ... 还有 {len(source_list) - 3} 个不同来源文件\n"
            message += "\n"
        
        if duplicate > 0:
            message += f"❌ ⚠️ ❌ 警告: 检测到 {duplicate:,} 条重复数据！\n"
            message += f"   重复率达到 {duplicate_rate:.2f}%，请及时处理！\n\n"
        else:
            message += f"✅ 良好: 未检测到重复数据\n\n"
        message += f"========================================\n"
        message += f"        TXT查重工具自动发送\n"
        message += f"========================================"
        
        if self.logger:
            if duplicate > 0:
                self.logger.warning(f"[警告] 检测到重复数据！文件: {file_name} | 路径: {file_path} | 导入时间: {file_import_time} | 重复数: {duplicate:,} | 重复率: {duplicate_rate:.2f}%")
            self.logger.info(f"[通知] 查重完成，准备发送{self._get_notify_type_name()}通知...")
            self.logger.info(f"[通知] 文件: {file_name} | 路径: {file_path} | 导入时间: {file_import_time} | 机台: {self.machine_id or '未设置'} | 总数: {total:,} | 重复: {duplicate:,} | 重复率: {duplicate_rate:.2f}%")
            
            if duplicate > 0 and duplicate_lines:
                for i, line in enumerate(duplicate_lines[:5]):
                    display_line = line[:200] if len(line) > 200 else line
                    self.logger.info(f"[追溯内容] {i+1}. 前3字: {display_line[:3]}... 完整: {display_line}")
            
            if duplicate > 0 and duplicate_sources:
                source_list = []
                for h, source in duplicate_sources.items():
                    key = (source.get('filename', '未知'), source.get('file_path', ''), source.get('timestamp', '未知'), source.get('machine_id', ''))
                    if key not in source_list:
                        source_list.append(key)
                
                for i, (fname, fpath, ts, mid) in enumerate(source_list[:5]):
                    self.logger.info(f"[追溯来源] {i+1}. 原文件名: {fname} | 原文件路径: {fpath} | 导入时间: {ts} | 机台: {mid}")
                if len(source_list) > 1:
                    self.logger.info(f"[追溯来源] 共涉及 {len(source_list)} 个不同来源文件")
        
        if self.notify_type == 'none':
            if self.logger:
                self.logger.info(f"[通知] 通知类型设置为none，跳过发送")
            return True
        
        notify_key = f"{self.machine_id}_{file_name}"
        if not self._check_notify_interval(notify_key):
            if self.logger:
                self.logger.info(f"[通知] 发送间隔未到，跳过发送")
            return False
        
        success = send_notification(self.notify_type, title, message, self.notify_config)
        
        if self.logger:
            if success:
                self.logger.info(f"[通知] {self._get_notify_type_name()}通知发送成功")
                if self.notify_type == 'email':
                    self.logger.info(f"[通知] 收件人: {self.notify_config.get('to_addrs', '')}")
            else:
                self.logger.error(f"[通知] {self._get_notify_type_name()}通知发送失败")
                if self.notify_type == 'email':
                    self.logger.error(f"[通知] SMTP服务器: {self.notify_config.get('smtp_server', '')}")
                    self.logger.error(f"[通知] 收件人: {self.notify_config.get('to_addrs', '')}")
        
        return success
    
    def test_notification(self) -> bool:
        """测试通知功能"""
        title = "✅ TXT查重工具 - 测试通知"
        message = f"这是一封测试邮件，\n\n如果您收到此邮件，说明TXT查重工具的邮件通知功能配置正确！\n\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\nTXT查重工具"
        
        success = send_notification(self.notify_type, title, message, self.notify_config)
        return success


def test_email_config(smtp_server: str, smtp_port: int, smtp_username: str, 
                     smtp_password: str, from_addr: str, to_addrs: str) -> bool:
    """测试邮件配置"""
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        msg = MIMEMultipart()
        msg['From'] = from_addr if from_addr else smtp_username
        msg['To'] = to_addrs
        msg['Subject'] = "[TXT查重工具] 邮件配置测试"
        
        body = f"""这是一封测试邮件！

如果您收到此邮件，说明TXT查重工具的邮件通知功能配置正确！

配置信息:
- SMTP服务器: {smtp_server}
- SMTP端口: {smtp_port}
- 发件人: {from_addr if from_addr else smtp_username}
- 收件人: {to_addrs}
- 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---
TXT查重工具
"""
        part = MIMEText(body, 'plain')
        part.set_charset('utf-8')
        msg.attach(part)
        
        print(f"正在连接 SMTP 服务器: {smtp_server}:{smtp_port}...")
        
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            print("正在启动TLS加密...")
            server.starttls()
            print(f"正在登录: {smtp_username}...")
            server.login(smtp_username, smtp_password)
            print(f"正在发送测试邮件到: {to_addrs}...")
            server.send_message(msg)
        
        print("[OK] 测试邮件发送成功！")
        return True
        
    except Exception as e:
        print(f"[ERROR] 测试邮件发送失败: {str(e)}")
        return False
