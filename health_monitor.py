#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import threading
import sqlite3
import shutil
from datetime import datetime
from typing import Optional, Dict

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False


class HealthMonitor:
    def __init__(self, config: dict, logger=None):
        self.logger = logger
        self._psutil_available = PSUTIL_AVAILABLE
        
        if not self._psutil_available:
            if self.logger:
                self.logger.warning(f"[健康监控] psutil模块不可用，健康监控功能受限")
            return
        
        self.memory_warning_mb = config.get('memory_warning_mb', 4096)
        self.memory_critical_mb = config.get('memory_critical_mb', 6144)
        self.disk_warning_gb = config.get('disk_warning_gb', 100)
        self.disk_critical_gb = config.get('disk_critical_gb', 50)
        self.connection_warning = config.get('connection_warning', 100)
        self.connection_critical = config.get('connection_critical', 500)
        self.check_interval = config.get('health_check_interval', 60)
        self.disk_check_interval = config.get('disk_check_interval', 86400)
        
        self.index_dir = config.get('index_dir', '.dedup_index')
        self.enable_email = config.get('health_enable_email', False)
        self.email_config = {
            'smtp_server': config.get('smtp_server', ''),
            'smtp_port': config.get('smtp_port', 587),
            'smtp_username': config.get('smtp_username', ''),
            'smtp_password': config.get('smtp_password', ''),
            'from_addr': config.get('from_addr', ''),
            'to_addrs': config.get('to_addrs', '')
        }
        
        self.auto_cleanup_enabled = config.get('auto_cleanup_enabled', True)
        self.max_disk_gb = config.get('max_disk_usage_gb', 3500)
        self.cleanup_trigger_percent = config.get('cleanup_trigger_percent', 95)
        self.cleanup_target_percent = config.get('cleanup_target_percent', 90)
        self.cleanup_batch_size = config.get('cleanup_batch_size', 100000)
        
        self.wal_passive_checkpoint = config.get('wal_passive_checkpoint', True)
        self.checkpoint_interval = config.get('wal_checkpoint_interval', 300)
        
        self._running = False
        self._thread = None
        
        self.last_memory_alert_time = 0
        self.last_disk_alert_time = 0
        self.last_connection_alert_time = 0
        self.last_cleanup_time = 0
        self.last_checkpoint_time = 0
        self.alert_cooldown = 1800
        self.cleanup_cooldown = 7200
        
        self.last_disk_check_time = 0
        self.connection_count = 0
        
        self.process = psutil.Process()
        self.cleanup_callback = None
        self.db_path = None
        
        if self.logger:
            self.logger.info(f"[健康监控] 初始化完成")
            self.logger.info(f"[健康监控] 内存告警阈值: {self.memory_warning_mb}MB (警告) / {self.memory_critical_mb}MB (严重)")
            self.logger.info(f"[健康监控] 磁盘告警阈值: {self.disk_warning_gb}GB (警告) / {self.disk_critical_gb}GB (严重)")
            self.logger.info(f"[健康监控] 连接数告警阈值: {self.connection_warning} (警告) / {self.connection_critical} (严重)")
            self.logger.info(f"[健康监控] 检查间隔: {self.check_interval}秒")
    
    def set_connection_count(self, count: int):
        self.connection_count = count
    
    def set_cleanup_callback(self, callback):
        self.cleanup_callback = callback
    
    def set_db_path(self, db_path: str):
        self.db_path = db_path
    
    def start(self):
        if not self._psutil_available:
            if self.logger:
                self.logger.warning("[健康监控] psutil不可用，无法启动健康监控")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        if self.logger:
            self.logger.info("[健康监控] 已启动")
    
    def stop(self):
        if not self._psutil_available:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        if self.logger:
            self.logger.info("[健康监控] 已停止")
    
    def _monitor_loop(self):
        while self._running:
            try:
                self._check_memory()
                self._check_connections()
                
                now = time.time()
                if now - self.last_disk_check_time >= self.disk_check_interval:
                    self._check_disk()
                    self.last_disk_check_time = now
                
                if self.wal_passive_checkpoint and self.db_path:
                    if now - self.last_checkpoint_time >= self.checkpoint_interval:
                        self._passive_checkpoint()
                        self.last_checkpoint_time = now
                
                time.sleep(min(self.check_interval, 30))
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[健康监控] 检查异常: {str(e)}")
                time.sleep(60)
    
    def _passive_checkpoint(self):
        """后台被动合并WAL文件，减少HDD业务高峰期的I/O压力"""
        try:
            if not os.path.exists(self.db_path):
                return
            wal_path = self.db_path + '-wal'
            if not os.path.exists(wal_path):
                return
            
            wal_size_mb = os.path.getsize(wal_path) / (1024 * 1024)
            if wal_size_mb < 1:
                return
            
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            finally:
                conn.close()
        except Exception:
            pass
    
    def _check_memory(self):
        try:
            mem_info = self.process.memory_info()
            mem_mb = mem_info.rss / (1024 * 1024)
            
            system_mem = psutil.virtual_memory()
            system_used_percent = system_mem.percent
            
            now = time.time()
            
            if mem_mb >= self.memory_critical_mb:
                if now - self.last_memory_alert_time >= self.alert_cooldown:
                    self.last_memory_alert_time = now
                    msg = f"服务端内存使用严重超标!\n\n进程内存: {mem_mb:.1f} MB\n系统内存: {system_used_percent:.1f}%\n\n请立即检查服务端运行状态!"
                    if self.logger:
                        self.logger.error(f"[告警] 内存使用严重超标: {mem_mb:.1f} MB (阈值: {self.memory_critical_mb} MB)")
                    self._send_alert("[严重] 服务端内存告警", msg)
            
            elif mem_mb >= self.memory_warning_mb:
                if now - self.last_memory_alert_time >= self.alert_cooldown:
                    self.last_memory_alert_time = now
                    msg = f"服务端内存使用超标\n\n进程内存: {mem_mb:.1f} MB\n系统内存: {system_used_percent:.1f}%\n\n建议关注服务端运行状态。"
                    if self.logger:
                        self.logger.warning(f"[告警] 内存使用超标: {mem_mb:.1f} MB (阈值: {self.memory_warning_mb} MB)")
                    self._send_alert("[警告] 服务端内存告警", msg)
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"[健康监控] 内存检查异常: {str(e)}")
    
    def _check_disk(self):
        try:
            index_path = os.path.abspath(self.index_dir)
            if os.path.exists(index_path):
                disk_usage = shutil.disk_usage(index_path)
            else:
                disk_usage = shutil.disk_usage('.')
            
            free_gb = disk_usage.free / (1024 * 1024 * 1024)
            total_gb = disk_usage.total / (1024 * 1024 * 1024)
            used_gb = disk_usage.used / (1024 * 1024 * 1024)
            used_percent = (1 - disk_usage.free / disk_usage.total) * 100
            
            now = time.time()
            
            if free_gb <= self.disk_critical_gb:
                if now - self.last_disk_alert_time >= self.alert_cooldown:
                    self.last_disk_alert_time = now
                    msg = f"服务端磁盘空间严重不足!\n\n剩余空间: {free_gb:.1f} GB\n总容量: {total_gb:.1f} GB\n使用率: {used_percent:.1f}%\n\n请立即清理磁盘空间!"
                    if self.logger:
                        self.logger.error(f"[告警] 磁盘空间严重不足: {free_gb:.1f} GB (阈值: {self.disk_critical_gb} GB)")
                    self._send_alert("[严重] 服务端磁盘空间告警", msg)
            
            elif free_gb <= self.disk_warning_gb:
                if now - self.last_disk_alert_time >= self.alert_cooldown:
                    self.last_disk_alert_time = now
                    msg = f"服务端磁盘空间不足\n\n剩余空间: {free_gb:.1f} GB\n总容量: {total_gb:.1f} GB\n使用率: {used_percent:.1f}%\n\n建议关注磁盘使用情况。"
                    if self.logger:
                        self.logger.warning(f"[告警] 磁盘空间不足: {free_gb:.1f} GB (阈值: {self.disk_warning_gb} GB)")
                    self._send_alert("[警告] 服务端磁盘空间告警", msg)
            
            self._maybe_auto_cleanup(used_gb, total_gb, free_gb, used_percent)
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"[健康监控] 磁盘检查异常: {str(e)}")
    
    def check_and_cleanup_if_full(self):
        if not self._psutil_available:
            return None
            
        now = time.time()
        if now - self.last_disk_check_time < 30:
            return
        
        try:
            index_path = os.path.abspath(self.index_dir)
            if os.path.exists(index_path):
                disk_usage = shutil.disk_usage(index_path)
            else:
                disk_usage = shutil.disk_usage('.')
            
            free_gb = disk_usage.free / (1024 * 1024 * 1024)
            total_gb = disk_usage.total / (1024 * 1024 * 1024)
            used_gb = disk_usage.used / (1024 * 1024 * 1024)
            used_percent = (1 - disk_usage.free / disk_usage.total) * 100
            
            self.last_disk_check_time = now
            
            if free_gb <= self.disk_critical_gb:
                if now - self.last_disk_alert_time >= self.alert_cooldown:
                    self.last_disk_alert_time = now
                    msg = f"服务端磁盘空间严重不足!\n\n剩余空间: {free_gb:.1f} GB\n总容量: {total_gb:.1f} GB\n使用率: {used_percent:.1f}%\n\n请立即清理磁盘空间!"
                    if self.logger:
                        self.logger.error(f"[告警] 磁盘空间严重不足: {free_gb:.1f} GB (阈值: {self.disk_critical_gb} GB)")
                    self._send_alert("[严重] 服务端磁盘空间告警", msg)
            
            elif free_gb <= self.disk_warning_gb:
                if now - self.last_disk_alert_time >= self.alert_cooldown:
                    self.last_disk_alert_time = now
                    msg = f"服务端磁盘空间不足\n\n剩余空间: {free_gb:.1f} GB\n总容量: {total_gb:.1f} GB\n使用率: {used_percent:.1f}%\n\n建议关注磁盘使用情况。"
                    if self.logger:
                        self.logger.warning(f"[告警] 磁盘空间不足: {free_gb:.1f} GB (阈值: {self.disk_warning_gb} GB)")
                    self._send_alert("[警告] 服务端磁盘空间告警", msg)
            
            self._maybe_auto_cleanup(used_gb, total_gb, free_gb, used_percent)
            
            return {
                'total_gb': round(total_gb, 1),
                'used_gb': round(used_gb, 1),
                'free_gb': round(free_gb, 1),
                'used_percent': round(used_percent, 1)
            }
        except Exception as e:
            if self.logger:
                self.logger.error(f"[自动清理检查] 异常: {str(e)}")
            return None
    
    def _maybe_auto_cleanup(self, used_gb: float, total_gb: float, free_gb: float, used_percent: float):
        if not self.auto_cleanup_enabled or not self.cleanup_callback:
            return
        if used_percent < self.cleanup_trigger_percent:
            return
        
        now = time.time()
        if now - self.last_cleanup_time < self.cleanup_cooldown:
            return
        
        self.last_cleanup_time = now
        self._trigger_cleanup(used_gb, total_gb, free_gb, used_percent)
    
    def _check_connections(self):
        try:
            count = self.connection_count
            
            now = time.time()
            
            if count >= self.connection_critical:
                if now - self.last_connection_alert_time >= self.alert_cooldown:
                    self.last_connection_alert_time = now
                    msg = f"服务端连接数严重超标!\n\n当前连接数: {count}\n阈值: {self.connection_critical}\n\n请检查是否有异常客户端连接!"
                    if self.logger:
                        self.logger.error(f"[告警] 连接数严重超标: {count} (阈值: {self.connection_critical})")
                    self._send_alert("[严重] 服务端连接数告警", msg)
            
            elif count >= self.connection_warning:
                if now - self.last_connection_alert_time >= self.alert_cooldown:
                    self.last_connection_alert_time = now
                    msg = f"服务端连接数较高\n\n当前连接数: {count}\n阈值: {self.connection_warning}\n\n建议关注连接状态。"
                    if self.logger:
                        self.logger.warning(f"[告警] 连接数较高: {count} (阈值: {self.connection_warning})")
                    self._send_alert("[警告] 服务端连接数告警", msg)
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"[健康监控] 连接数检查异常: {str(e)}")
    
    def _trigger_cleanup(self, used_gb: float, total_gb: float, free_gb: float, used_percent: float):
        target_used = total_gb * (self.cleanup_target_percent / 100)
        need_free_gb = max(0, used_gb - target_used)
        need_free_count = int(need_free_gb * 1024 * 1024 * 1024 / 120)
        delete_count = min(need_free_count, self.cleanup_batch_size)
        
        if delete_count <= 0:
            return
        
        if self.logger:
            self.logger.warning(f"[自动清理] 磁盘使用率 {used_percent:.1f}%，触发自动清理")
            self.logger.warning(f"[自动清理] 需要释放 {need_free_gb:.1f} GB，预计删除 {delete_count:,} 条最早数据")
        
        try:
            deleted = self.cleanup_callback(delete_count)
            if self.logger:
                if deleted > 0:
                    self.logger.info(f"[自动清理] 成功删除 {deleted:,} 条最早数据")
                else:
                    self.logger.warning(f"[自动清理] 无可删除数据")
            
            msg = f"服务端自动清理完成\n\n"
            msg += f"清理前: 使用 {used_gb:.1f}GB / {total_gb:.1f}GB ({used_percent:.1f}%)\n"
            msg += f"已删除: {deleted:,} 条最早数据\n"
            msg += f"目标: 将使用率降至 {self.cleanup_target_percent}% 以下\n\n"
            msg += f"如有疑问请检查服务端日志。"
            self._send_alert("[通知] 服务端自动清理完成", msg)
        except Exception as e:
            if self.logger:
                self.logger.error(f"[自动清理] 清理失败: {str(e)}")
            self._send_alert("[严重] 服务端自动清理失败", f"自动清理执行失败!\n\n错误: {str(e)}\n\n请手动检查磁盘空间!")
    
    def _send_alert(self, title: str, message: str):
        if not self.enable_email:
            if self.logger:
                self.logger.info(f"[健康监控] 邮件告警未启用，跳过发送: {title}")
            return
        
        try:
            from notifier import email_notification
            
            full_message = f"========================================\n"
            full_message += f"          TXT查重工具 - 健康告警\n"
            full_message += f"========================================\n\n"
            full_message += f"告警时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            full_message += f"{message}\n\n"
            full_message += f"========================================\n"
            full_message += f"        TXT查重工具自动发送\n"
            full_message += f"========================================"
            
            success = email_notification(self.email_config, title, full_message)
            if self.logger:
                if success:
                    self.logger.info(f"[健康监控] 告警邮件发送成功: {title}")
                else:
                    self.logger.error(f"[健康监控] 告警邮件发送失败: {title}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"[健康监控] 告警邮件发送异常: {str(e)}")
    
    def send_crash_alert(self, error_msg: str):
        title = "[严重] 服务端崩溃告警"
        message = f"服务端发生崩溃并已自动重启!\n\n崩溃时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n错误信息:\n{error_msg}\n\n服务端已自动重启，请检查运行状态。"
        
        try:
            from notifier import email_notification
            
            full_message = f"========================================\n"
            full_message += f"          TXT查重工具 - 崩溃告警\n"
            full_message += f"========================================\n\n"
            full_message += f"告警时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            full_message += f"{message}\n\n"
            full_message += f"========================================\n"
            full_message += f"        TXT查重工具自动发送\n"
            full_message += f"========================================"
            
            if self.enable_email:
                email_notification(self.email_config, title, full_message)
        except Exception:
            pass
    
    def get_health_report(self) -> dict:
        report = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'memory': {},
            'disk': {},
            'connections': 0,
            'status': 'healthy'
        }
        
        if not self._psutil_available:
            report['memory'] = {'error': 'psutil模块不可用'}
            report['disk'] = {'error': 'psutil模块不可用'}
            report['status'] = 'limited'
            return report
        
        try:
            mem_info = self.process.memory_info()
            system_mem = psutil.virtual_memory()
            report['memory'] = {
                'process_mb': round(mem_info.rss / (1024 * 1024), 1),
                'system_percent': system_mem.percent,
                'system_total_gb': round(system_mem.total / (1024**3), 1),
                'system_available_gb': round(system_mem.available / (1024**3), 1)
            }
        except Exception:
            report['memory'] = {'error': '无法获取'}
        
        try:
            index_path = os.path.abspath(self.index_dir)
            if os.path.exists(index_path):
                disk_usage = shutil.disk_usage(index_path)
            else:
                disk_usage = shutil.disk_usage('.')
            report['disk'] = {
                'total_gb': round(disk_usage.total / (1024**3), 1),
                'used_gb': round(disk_usage.used / (1024**3), 1),
                'free_gb': round(disk_usage.free / (1024**3), 1),
                'percent': round((1 - disk_usage.free / disk_usage.total) * 100, 1)
            }
        except Exception:
            report['disk'] = {'error': '无法获取'}
        
        report['connections'] = self.connection_count
        
        return report