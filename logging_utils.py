#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import logging
from datetime import datetime
from pathlib import Path


class DedupLogger:
    def __init__(self, name: str, log_dir: str = "logs", level: str = "INFO"):
        self.name = name
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self.level = self._parse_level(level)
        
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        
        self.console_handler = None
        self.file_handler = None
        self.error_file_handler = None
        
        self._current_log_file = None
        self._current_file_name = None
        self._file_start_time = None
        
        self._setup_console_handler()
        
        self.request_count = 0
        self.duplicate_count = 0
        self.unique_count = 0
        self.error_count = 0

    def _parse_level(self, level_str: str) -> int:
        levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL
        }
        return levels.get(level_str.upper(), logging.INFO)

    def _setup_console_handler(self):
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        self.console_handler = logging.StreamHandler()
        self.console_handler.setLevel(self.level)
        self.console_handler.setFormatter(formatter)
        self.logger.addHandler(self.console_handler)
    
    def set_log_file(self, filename: str):
        """设置日志文件名，包含日期和文件名"""
        self._close_current_log()
        
        date_str = datetime.now().strftime('%Y%m%d')
        safe_filename = Path(filename).stem.replace(' ', '_')
        self._current_log_file = self.log_dir / f"{date_str}_{safe_filename}.log"
        self._current_file_name = str(filename)
        self._file_start_time = time.time()
        
        error_log_file = self.log_dir / f"{date_str}_{safe_filename}_error.log"
        
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        self.file_handler = logging.FileHandler(self._current_log_file, encoding='utf-8')
        self.file_handler.setLevel(logging.INFO)
        self.file_handler.setFormatter(formatter)
        self.logger.addHandler(self.file_handler)
        
        self.error_file_handler = logging.FileHandler(error_log_file, encoding='utf-8')
        self.error_file_handler.setLevel(logging.ERROR)
        self.error_file_handler.setFormatter(formatter)
        self.logger.addHandler(self.error_file_handler)
        
        header_line = "=" * 70
        self.logger.info(header_line)
        self.logger.info(f"  文件处理开始: {filename}")
        self.logger.info(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.logger.info(header_line)

    def _close_current_log(self):
        """关闭当前日志文件并写入尾部摘要"""
        if self.file_handler:
            if self._current_log_file and self._file_start_time:
                elapsed = time.time() - self._file_start_time
                footer_line = "=" * 70
                self.logger.info(footer_line)
                self.logger.info(f"  文件处理结束: {self._current_file_name}")
                self.logger.info(f"  总耗时: {elapsed:.1f}秒")
                self.logger.info(f"  日志文件: {self._current_log_file}")
                self.logger.info(footer_line)
            self.logger.removeHandler(self.file_handler)
            self.file_handler.close()
            self.file_handler = None
        
        if self.error_file_handler:
            self.logger.removeHandler(self.error_file_handler)
            self.error_file_handler.close()
            self.error_file_handler = None
        
        self._current_log_file = None
        self._current_file_name = None
        self._file_start_time = None

    def close(self):
        self._close_current_log()

    def debug(self, message: str, **kwargs):
        self.logger.debug(message)

    def info(self, message: str, **kwargs):
        self.logger.info(message)

    def warning(self, message: str, **kwargs):
        self.logger.warning(message)

    def error(self, message: str, exception: Exception = None, **kwargs):
        if exception:
            self.logger.error(f"{message} | 异常: {str(exception)}")
        else:
            self.logger.error(message)

    def critical(self, message: str, exception: Exception = None, **kwargs):
        if exception:
            self.logger.critical(f"{message} | 异常: {str(exception)}")
        else:
            self.logger.critical(message)

    def log_request(self, client_ip: str, endpoint: str, method: str, status_code: int, duration_ms: float):
        self.request_count += 1

    def log_duplicate_check(self, hash_value: str, is_duplicate: bool, source: str = ""):
        if is_duplicate:
            self.duplicate_count += 1
        else:
            self.unique_count += 1

    def log_batch_result(self, total_count: int, duplicate_count: int, unique_count: int, duration_ms: float, file_path: str = ""):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if file_path:
            file_name = Path(file_path).name
            dir_path = Path(file_path).parent.resolve()
            if duplicate_count > 0:
                self.info(f"[重复检测] 日期: {timestamp} | 文件: {file_name} | 路径: {dir_path} | 总数: {total_count:,} | 重复: {duplicate_count:,} | 唯一: {unique_count:,} | 重复率: {(duplicate_count/total_count*100):.2f}% | 耗时: {duration_ms:.0f}ms")
            else:
                self.info(f"[文件处理] 日期: {timestamp} | 文件: {file_name} | 路径: {dir_path} | 总数: {total_count:,} | 重复: {duplicate_count:,} | 唯一: {unique_count:,} | 耗时: {duration_ms:.0f}ms")
        else:
            self.info(f"[批量检测] 日期: {timestamp} | 总数: {total_count:,} | 重复: {duplicate_count:,} | 唯一: {unique_count:,} | 耗时: {duration_ms:.0f}ms")

    def log_file_process(self, file_path: str, file_size: int, line_count: int, encoding: str):
        self.info(f"文件处理 | 路径: {file_path} | 大小: {file_size:,} 字节 | 行数: {line_count:,} | 编码: {encoding}")

    def log_connection(self, client_ip: str, connected: bool):
        if connected:
            self.info(f"连接 | IP: {client_ip} | 状态: 已连接")
        else:
            self.info(f"连接 | IP: {client_ip} | 状态: 断开")

    def get_stats(self) -> dict:
        return {
            "请求总数": self.request_count,
            "重复总数": self.duplicate_count,
            "唯一总数": self.unique_count,
            "错误总数": self.error_count
        }

    def log_stats(self):
        stats = self.get_stats()
        self.info(f"统计汇总 | 请求: {stats['请求总数']} | 重复: {stats['重复总数']} | 唯一: {stats['唯一总数']} | 错误: {stats['错误总数']}")