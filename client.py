#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import gzip
import threading
import argparse
import logging
import configparser
import platform
import ctypes
from datetime import datetime
from pathlib import Path
from typing import Set, Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests


def enable_vt100():
    """在Windows上启用VT100支持，以便显示彩色输出"""
    if platform.system() == 'Windows':
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass


enable_vt100()

# 尝试使用colorama库（如果安装了）
try:
    from colorama import init
    init()
except ImportError:
    pass

try:
    import xxhash
    HAS_XXHASH = True
except ImportError:
    import hashlib
    HAS_XXHASH = False

from notifier import NotificationManager, test_email_config
from logging_utils import DedupLogger

try:
    from version import VERSION, BUILD_TIME, AUTHOR, CONTACT
except ImportError:
    VERSION = "1.2.0"
    BUILD_TIME = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    AUTHOR = "张文龙"
    CONTACT = "18053292127"

MAX_BATCH_SIZE = 10000
CONCURRENT_REQUESTS = 4

CONFIG_FILE = "config.ini"


def _shorten_error(err_str: str) -> str:
    import re
    m = re.search(r'\[WinError \d+\]\s*(.+)', err_str)
    if m:
        return m.group(1).strip().rstrip("\"'() ")
    m = re.search(r'Failed to establish a new connection: (.+)', err_str)
    if m:
        return m.group(1).strip().rstrip("\"'() ")
    if 'Connection refused' in err_str or '10061' in err_str:
        return '目标计算机拒绝连接'
    if 'timed out' in err_str.lower():
        return '连接超时'
    if 'Name or service not known' in err_str:
        return '无法解析主机名'
    return err_str.split('\n')[0][:80].rstrip("\"'() ")


def _safe_input(prompt: str = "") -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt, RuntimeError):
        return ""


class ConfigManager:
    def __init__(self, config_path: str = CONFIG_FILE):
        self.config_path = self._resolve_config_path(config_path)
        self.config = configparser.ConfigParser()
        self.load_config()

    def _resolve_config_path(self, config_path: str) -> str:
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            return os.path.join(exe_dir, config_path)
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            return os.path.join(script_dir, config_path)

    def load_config(self):
        old_config_path = os.path.join(os.path.dirname(self.config_path), 'client_config.txt')
        
        if os.path.exists(self.config_path):
            self.config.read(self.config_path, encoding='utf-8')
        elif os.path.exists(old_config_path):
            self._migrate_from_old_config(old_config_path)
        else:
            self._create_default_config()

    def _migrate_from_old_config(self, old_path: str):
        try:
            with open(old_path, 'r', encoding='utf-8') as f:
                server_address = f.read().strip()
            
            if server_address and server_address.startswith('http'):
                self._create_default_config()
                self.config['Server']['address'] = server_address
                self.save_config()
                os.remove(old_path)
        except Exception:
            self._create_default_config()

    def _create_default_config(self):
        self.config['Server'] = {
            'address': 'http://localhost:8888'
        }
        self.config['Client'] = {
            'output_dir': '',
            'save_unique': 'false',
            'save_duplicates': 'false',
            'threads': '0',
            'chunk_size': '100000',
            'machine_id': ''
        }
        self.config['Notification'] = {
            'notify_type': 'none',
            'notify_duplicate_rate': '0',
            'notify_on_connection_failure': 'false',
            'wecom_webhook': '',
            'bark_url': '',
            'dingtalk_webhook': ''
        }
        self.config['Email'] = {
            'smtp_server': '',
            'smtp_port': '587',
            'smtp_username': '',
            'smtp_password': '',
            'from_addr': '',
            'to_addrs': ''
        }
        self.save_config()

    def save_config(self):
        with open(self.config_path, 'w', encoding='utf-8') as f:
            self.config.write(f)

    def get_server_address(self) -> str:
        return self.config.get('Server', 'address', fallback='http://localhost:8888')

    def set_server_address(self, address: str):
        self.config['Server']['address'] = address
        self.save_config()

    def get_output_dir(self) -> str:
        return self.config.get('Client', 'output_dir', fallback='')

    def get_save_unique(self) -> bool:
        return self.config.getboolean('Client', 'save_unique', fallback=False)

    def get_save_duplicates(self) -> bool:
        return self.config.getboolean('Client', 'save_duplicates', fallback=False)

    def get_threads(self) -> int:
        return self.config.getint('Client', 'threads', fallback=0)

    def get_chunk_size(self) -> int:
        return self.config.getint('Client', 'chunk_size', fallback=100000)
    
    def get_machine_id(self) -> str:
        return self.config.get('Client', 'machine_id', fallback='')
    
    def get_notify_type(self) -> str:
        return self.config.get('Notification', 'notify_type', fallback='none')
    
    def get_notify_duplicate_rate(self) -> float:
        return self.config.getfloat('Notification', 'notify_duplicate_rate', fallback=0)
    
    def get_notify_on_connection_failure(self) -> bool:
        return self.config.getboolean('Notification', 'notify_on_connection_failure', fallback=False)
    
    def get_min_notify_interval(self) -> int:
        return self.config.getint('Notification', 'min_notify_interval', fallback=300)
    
    def get_wecom_webhook(self) -> str:
        return self.config.get('Notification', 'wecom_webhook', fallback='')
    
    def get_bark_url(self) -> str:
        return self.config.get('Notification', 'bark_url', fallback='')
    
    def get_dingtalk_webhook(self) -> str:
        return self.config.get('Notification', 'dingtalk_webhook', fallback='')
    
    def get_smtp_server(self) -> str:
        return self.config.get('Email', 'smtp_server', fallback='')
    
    def get_smtp_port(self) -> int:
        return self.config.getint('Email', 'smtp_port', fallback=587)
    
    def get_smtp_username(self) -> str:
        return self.config.get('Email', 'smtp_username', fallback='')
    
    def get_smtp_password(self) -> str:
        return self.config.get('Email', 'smtp_password', fallback='')
    
    def get_from_addr(self) -> str:
        return self.config.get('Email', 'from_addr', fallback='')
    
    def get_to_addrs(self) -> str:
        return self.config.get('Email', 'to_addrs', fallback='')
    
    # ---------- 性能优化配置 ----------
    
    def get_batch_size(self) -> int:
        return self.config.getint('Performance', 'batch_size', fallback=8000)
    
    def get_concurrent_requests(self) -> int:
        return self.config.getint('Performance', 'concurrent_requests', fallback=16)
    
    def get_prefer_xxhash(self) -> bool:
        return self.config.getboolean('Performance', 'prefer_xxhash', fallback=True)
    
    def get_request_timeout(self) -> int:
        return self.config.getint('Performance', 'request_timeout', fallback=60)
    
    def get_connection_pool_size(self) -> int:
        return self.config.getint('Performance', 'connection_pool_size', fallback=20)

    def get_server_compute_mode(self) -> bool:
        """是否启用服务端计算模式（客户端上传原始数据，服务端计算哈希）"""
        return self.config.getboolean('Performance', 'server_compute_mode', fallback=False)

    def get_server_compute_chunk_size(self) -> int:
        """服务端计算模式的每批行数（gzip 压缩后推荐 20K 行一批）"""
        return self.config.getint('Performance', 'server_compute_chunk_size', fallback=20000)


class RemoteHashIndex:
    def __init__(self, server_url: str, logger=None, max_retries=3, retry_delay=2, config=None, machine_id='', api_token=''):
        self.server_url = server_url.rstrip('/')
        self._config = config or {}
        self._default_timeout = self._config.get('request_timeout', 120)
        self._headers = {'Content-Type': 'application/json'}
        if api_token:
            self._headers['X-API-Token'] = api_token
        self._logger = logger
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._machine_id = machine_id
        self._batch_size = self._config.get('batch_size', MAX_BATCH_SIZE)
        self._use_gzip = self._config.get('use_gzip', True)

    def _compress_and_post(self, url: str, json_data: dict, timeout: int):
        """对 POST 请求体进行 gzip 压缩后发送（文本数据压缩率 5-10x）"""
        compressed = gzip.compress(json.dumps(json_data).encode('utf-8'), compresslevel=6)
        headers = dict(self._headers)
        headers['Content-Encoding'] = 'gzip'
        return requests.post(url, data=compressed, headers=headers, timeout=timeout)

    def _make_request(self, endpoint: str, method='GET', data=None):
        url = f"{self.server_url}/{endpoint}"
        start_time = time.time()
        timeout = self._config.get('request_timeout', self._default_timeout)
        
        for attempt in range(self.max_retries):
            try:
                if method == 'GET':
                    response = requests.get(url, params=data, timeout=timeout)
                elif method == 'POST':
                    # 对大请求体启用 gzip 压缩（原始文本极适合压缩）
                    if self._use_gzip and data and isinstance(data, dict):
                        response = self._compress_and_post(url, data, timeout)
                    else:
                        response = requests.post(url, json=data, timeout=timeout, headers=self._headers)
                else:
                    return None
                response.raise_for_status()
                duration_ms = (time.time() - start_time) * 1000
                return response.json()
            except requests.exceptions.RequestException as e:
                duration_ms = (time.time() - start_time) * 1000
                err_msg = _shorten_error(str(e))
                if attempt < self.max_retries - 1:
                    if self._logger:
                        self._logger.warning(f"[连接] {self.server_url} 失败 ({attempt+1}/{self.max_retries}): {err_msg}")
                    time.sleep(self.retry_delay)
                else:
                    if self._logger:
                        self._logger.error(f"[连接] {self.server_url} {self.max_retries}次重试均失败: {err_msg}")
                    else:
                        print(f"  [网络错误] {err_msg}")
                    self._notify_connection_failure(err_msg)
                    return None
    
    def _notify_connection_failure(self, error_msg: str):
        try:
            from notifier import send_notification
            
            notify_config = {
                'smtp_server': self._config.get('smtp_server', ''),
                'smtp_port': self._config.get('smtp_port', 587),
                'smtp_username': self._config.get('smtp_username', ''),
                'smtp_password': self._config.get('smtp_password', ''),
                'from_addr': self._config.get('from_addr', ''),
                'to_addrs': self._config.get('to_addrs', '')
            }
            
            if self._config.get('notify_on_connection_failure', False):
                now = datetime.now()
                min_interval = 20 * 60
                
                if hasattr(self, '_last_notify_time'):
                    elapsed = (now - self._last_notify_time).total_seconds()
                    if elapsed < min_interval:
                        if self._logger:
                            self._logger.info(f"[通知] 连接失败邮件发送间隔未到 ({elapsed:.0f}秒 < {min_interval}秒)，跳过发送")
                        return
                
                self._last_notify_time = now
                
                subject = "⚠️ TXT查重工具 - 服务端连接失败"
                body = f"""服务端连接失败！

时间: {now.strftime('%Y-%m-%d %H:%M:%S')}
服务端地址: {self.server_url}
错误信息: {error_msg}

请检查服务端是否正常运行！
"""
                success = send_notification('email', subject, body, notify_config)
                if self._logger:
                    if success:
                        self._logger.info(f"[通知] 已发送服务端连接失败邮件")
                    else:
                        self._logger.warning(f"[通知] 服务端连接失败邮件发送失败")
        except Exception as notify_e:
            if self._logger:
                self._logger.warning(f"[通知] 发送连接失败邮件失败: {str(notify_e)}")

    def check_and_add_batch(self, hash_vals: List[str], filename: str = "", file_path: str = "") -> List[bool]:
        results = []
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for i in range(0, len(hash_vals), self._batch_size):
            batch = hash_vals[i:i+self._batch_size]
            result = self._make_request(
                'api/check',
                method='POST',
                data={'hashes': batch, 'check_only': False, 'filename': filename, 'file_path': file_path, 'machine_id': self._machine_id, 'timestamp': timestamp}
            )
            if result and result.get('success'):
                results.extend(result.get('results', []))
            else:
                # 服务端失败时保守计为重复，避免漏报导致脏数据入库
                results.extend([True] * len(batch))
        return results

    def check_only_batch(self, hash_vals: List[str], filename: str = "") -> List[bool]:
        results = []
        for i in range(0, len(hash_vals), self._batch_size):
            batch = hash_vals[i:i+self._batch_size]
            result = self._make_request(
                'api/check',
                method='POST',
                data={'hashes': batch, 'check_only': True, 'filename': filename, 'machine_id': self._machine_id}
            )
            if result and result.get('success'):
                results.extend(result.get('results', []))
            else:
                # 服务端失败时保守计为重复，避免漏报
                results.extend([True] * len(batch))
        return results

    def get_stats(self):
        result = self._make_request('api/stats')
        if result and result.get('success'):
            return result.get('data', {})
        return {}

    def commit(self):
        self._make_request('api/commit', method='POST')
    
    def notify_complete(self, filename: str, total: int, duplicate: int, unique: int, duplicate_rate: float, duration_ms: float):
        self._make_request(
            'api/complete',
            method='POST',
            data={
                'filename': filename,
                'total': total,
                'duplicate': duplicate,
                'unique': unique,
                'duplicate_rate': duplicate_rate,
                'duration_ms': duration_ms,
                'machine_id': self._machine_id
            }
        )

    def record_history(self, filename: str, file_path: str, total: int, duplicate: int, unique: int, duplicate_rate: float, duration_ms: float):
        """记录查重历史到服务端"""
        self._make_request(
            'api/history',
            method='POST',
            data={
                'filename': filename,
                'file_path': file_path,
                'machine_id': self._machine_id,
                'total': total,
                'duplicate': duplicate,
                'unique': unique,
                'duplicate_rate': duplicate_rate,
                'duration_ms': duration_ms
            }
        )
    
    def query_sources(self, hash_vals: List[str]) -> Dict[str, Dict]:
        """查询哈希值的来源信息"""
        result = self._make_request(
            'api/query_sources',
            method='POST',
            data={'hashes': hash_vals}
        )
        if result and result.get('success'):
            return result.get('sources', {})
        return {}

    def query_machine_compare(self, hash_vals: List[str]) -> Dict:
        """查询机台对比信息 - 找出重复数据在哪些机台出现过"""
        result = self._make_request(
            'api/query_machine_compare',
            method='POST',
            data={'hashes': hash_vals}
        )
        if result and result.get('success'):
            return {
                'duplicate_count': result.get('duplicate_count', 0),
                'machine_counts': result.get('machine_counts', {}),
                'machine_list': result.get('machine_list', []),
                'details': result.get('details', [])
            }
        return {'duplicate_count': 0, 'machine_counts': {}, 'machine_list': [], 'details': []}

    def health_check(self):
        result = self._make_request('api/health')
        if result and result.get('success'):
            if self._logger:
                self._logger.info(f"[健康检查] 服务端正常 | 版本: {result.get('version', '未知')}")
            return True
        return False

    def dedup_file_server_compute(self, lines: List[str], filename: str = "", file_path: str = "", check_only: bool = False) -> Optional[Dict]:
        """
        服务端计算模式：客户端上传原始数据，服务端负责哈希计算和查重
        返回：{'success': True, 'total': ..., 'duplicate_count': ..., 'unique_count': ...,
              'duplicate_line_indices': [...], 'unique_line_indices': [...],
              'server_hash_ms': ..., 'server_check_ms': ..., 'total_ms': ...}
        """
        result = self._make_request(
            'api/dedup_file',
            method='POST',
            data={
                'lines': lines,
                'filename': filename,
                'file_path': file_path,
                'machine_id': self._machine_id,
                'check_only': check_only
            }
        )
        return result


class ProgressBar:
    def __init__(self, total: int, bar_width: int = 40, prefix: str = '', suffix: str = ''):
        self.total = total
        self.bar_width = bar_width
        self.prefix = prefix
        self.suffix = suffix
        self.current = 0
        self.remaining = total
        self.start_time = time.time()
        self.total_lines_processed = 0
        self.lock = threading.Lock()

    def update(self, current: int = None, remaining: int = None, line_count: int = 0):
        with self.lock:
            if current is not None:
                self.current = current
            if remaining is not None:
                self.remaining = remaining
            elif current is None:
                self.current += 1
                self.remaining = self.total - self.current
            else:
                self.remaining = self.total - self.current
            self.total_lines_processed += line_count
            self._draw()

    def set_suffix(self, suffix: str):
        self.suffix = suffix
        self._draw()

    def _draw(self):
        if self.total == 0:
            percent = 100
        else:
            percent = min(100, self.current * 100 // self.total)
        filled = self.bar_width * percent // 100

        bar = '█' * filled + '░' * (self.bar_width - filled)

        elapsed = time.time() - self.start_time
        speed = 0
        if elapsed > 0 and self.total_lines_processed > 0:
            speed = self.total_lines_processed / elapsed

        dynamic_suffix = f'剩余 {self.remaining}个'
        if speed > 0:
            speed_str = f'  {speed:,.0f} 条/秒'
            
            # 计算预计剩余时间
            if self.current > 0 and self.total > 0:
                avg_time_per_chunk = elapsed / self.current
                remaining_time = avg_time_per_chunk * self.remaining
                if remaining_time < 60:
                    eta_str = f' | 预计剩余: {remaining_time:.0f}秒'
                elif remaining_time < 3600:
                    eta_str = f' | 预计剩余: {remaining_time/60:.1f}分钟'
                else:
                    eta_str = f' | 预计剩余: {remaining_time/3600:.1f}小时'
            else:
                eta_str = ''
        else:
            speed_str = ''
            eta_str = ''

        progress_str = f'\r{self.prefix} |{bar}| {percent:3d}% {dynamic_suffix}{speed_str}{eta_str}'
        sys.stdout.write(progress_str)
        sys.stdout.flush()

    def close(self):
        self.current = self.total
        self.remaining = 0
        self._draw()
        sys.stdout.write('\n')
        sys.stdout.flush()


class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    _enabled = True

    @staticmethod
    def red(text: str) -> str:
        return f"{Colors.RED}{text}{Colors.RESET}" if Colors._enabled else text

    @staticmethod
    def green(text: str) -> str:
        return f"{Colors.GREEN}{text}{Colors.RESET}" if Colors._enabled else text

    @staticmethod
    def yellow(text: str) -> str:
        return f"{Colors.YELLOW}{text}{Colors.RESET}" if Colors._enabled else text

    @staticmethod
    def blue(text: str) -> str:
        return f"{Colors.BLUE}{text}{Colors.RESET}" if Colors._enabled else text


class TableOutput:
    @staticmethod
    def print_separator(col_widths, style='top'):
        if style == 'top':
            left, mid, right = '┌', '┬', '┐'
        elif style == 'mid':
            left, mid, right = '├', '┼', '┤'
        else:
            left, mid, right = '└', '┴', '┘'

        line = left
        for w in col_widths:
            line += '─' * (w + 2) + mid
        line = line[:-1] + right
        print(line)

    @staticmethod
    def print_row(values, col_widths, align='left'):
        line = '│'
        for i, (val, w) in enumerate(zip(values, col_widths)):
            val = str(val)
            if align == 'right':
                line += f' {val.rjust(w)} │'
            else:
                line += f' {val.ljust(w)} │'
        print(line)

    @staticmethod
    def print_summary_table(file_results):
        if not file_results:
            return

        headers = ['文件名', '数据量', '唯一数', '重复数', '重复率']
        col_widths = [30, 10, 10, 10, 10]

        print(f"\n{Colors.yellow('='*60)}")
        print(f"{Colors.yellow('              查重结果汇总表')}")
        print(f"{Colors.yellow('='*60)}")

        TableOutput.print_separator(col_widths, style='top')
        TableOutput.print_row(headers, col_widths)
        TableOutput.print_separator(col_widths, style='mid')

        for result in file_results:
            rate = f"{result['duplicate']/result['total']*100:.1f}%" if result['total'] > 0 else '0%'
            duplicate_str = f"{result['duplicate']:,}"
            values = [
                os.path.basename(result['filename'])[:28] + '..' if len(result['filename']) > 30 else result['filename'],
                f"{result['total']:,}",
                f"{result['unique']:,}",
                Colors.red(duplicate_str) if result['duplicate'] > 0 else duplicate_str,
                Colors.red(rate) if result['duplicate'] > 0 else rate
            ]
            TableOutput.print_row(values, col_widths)

        TableOutput.print_separator(col_widths, style='bottom')

    @staticmethod
    def print_duplicate_patterns(duplicate_patterns, top_n=5):
        if not duplicate_patterns:
            return

        filtered_patterns = {k: v for k, v in duplicate_patterns.items() if v > 1}
        if not filtered_patterns:
            return

        sorted_patterns = sorted(filtered_patterns.items(), key=lambda x: x[1], reverse=True)[:top_n]

        print(f"\n{Colors.yellow('='*60)}")
        print(f"{Colors.yellow('            重复内容 TOP 5')}")
        print(f"{Colors.yellow('='*60)}")

        for i, (content, count) in enumerate(sorted_patterns, 1):
            truncated = content[:50] + '...' if len(content) > 50 else content
            print(f"{Colors.red(f'{i}.')} \"{truncated}\" - 重复 {Colors.red(count)} 次")

        print(f"{Colors.yellow('='*60)}")


class RemoteTXTDeduplicator:
    def __init__(self, server_url: str, chunk_size: int = 100000, threads: int = None, log_dir: str = "logs", config=None, machine_id='', api_token=''):
        self.server_url = server_url
        self.chunk_size = chunk_size
        self._config = config or {}
        self.threads = threads or self._config.get('concurrent_requests', max(1, min(8, os.cpu_count() - 1)) if os.cpu_count() else 4)
        self.filename = ""
        self._machine_id = machine_id
        
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            self.log_dir = os.path.join(exe_dir, log_dir)
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            self.log_dir = os.path.join(script_dir, log_dir)
        
        self.logger = DedupLogger("dedup_client", log_dir=self.log_dir, level="INFO")
        self.remote_index = RemoteHashIndex(server_url, logger=self.logger, config=self._config, machine_id=self._machine_id, api_token=api_token)
        self.notifier = None
        self._file_path = ""

    def compute_hash(self, line: str) -> str:
        if HAS_XXHASH:
            return xxhash.xxh64(line.encode('utf-8')).hexdigest()
        else:
            return hashlib.sha256(line.encode('utf-8')).hexdigest()

    def detect_encoding(self, filepath: str) -> str:
        with open(filepath, 'rb') as f:
            raw = f.read(4096)
            null_count = raw.count(b'\x00')
            if len(raw) > 0 and null_count / len(raw) > 0.01:
                raise ValueError("检测到二进制文件，请提供文本文件")
            for enc in ['utf-8-sig', 'utf-8', 'gbk', 'gb2312', 'gb18030', 'big5', 'utf-16']:
                try:
                    raw.decode(enc)
                    return enc
                except (UnicodeDecodeError, LookupError):
                    continue
        return 'utf-8'

    def process_chunk_parallel(self, lines: List[str]) -> Tuple[List[str], List[str], List[Tuple[int, str]]]:
        line_data = []
        for i, line in enumerate(lines):
            line_stripped = line.rstrip('\r\n')
            if line_stripped:
                line_data.append((i, line_stripped))

        if not line_data:
            return [], [], []

        if len(line_data) >= 1000 and self.threads > 1:
            # 批量提交哈希计算：每批至少500条，避免每行一个任务的巨大开销
            batch_size = max(500, len(line_data) // (self.threads * 4))
            chunks = [line_data[i:i + batch_size] for i in range(0, len(line_data), batch_size)]
            
            with ThreadPoolExecutor(max_workers=self.threads) as executor:
                def _compute_batch(chunk):
                    return [(i, line, self.compute_hash(line)) for i, line in chunk]
                
                futures = {executor.submit(_compute_batch, chunk): i for i, chunk in enumerate(chunks)}
                results_by_batch = {}
                for future in as_completed(futures):
                    batch_idx = futures[future]
                    results_by_batch[batch_idx] = future.result()
                line_hashes = []
                for idx in sorted(results_by_batch):
                    line_hashes.extend(results_by_batch[idx])
        else:
            line_hashes = [(i, line, self.compute_hash(line)) for i, line in line_data]

        hashes_to_check = [h for _, _, h in line_hashes]
        
        with ThreadPoolExecutor(max_workers=CONCURRENT_REQUESTS) as executor:
            batches = [hashes_to_check[i:i+MAX_BATCH_SIZE] for i in range(0, len(hashes_to_check), MAX_BATCH_SIZE)]
            futures = {executor.submit(self.remote_index.check_and_add_batch, batch, self.filename, self._file_path): idx
                      for idx, batch in enumerate(batches)}
            results_by_batch = {}
            for future in as_completed(futures):
                batch_idx = futures[future]
                results_by_batch[batch_idx] = future.result()
            all_results = []
            for idx in sorted(results_by_batch):
                all_results.extend(results_by_batch[idx])

        new_hashes = []
        duplicate_hashes = []
        duplicates = []
        logged_duplicates = 0
        max_log_duplicates = 10
        
        for (i, line, h), is_duplicate in zip(line_hashes, all_results):
            if is_duplicate:
                duplicate_hashes.append(h)
                duplicates.append((i, line))
                self.logger.duplicate_count += 1
                if logged_duplicates < max_log_duplicates:
                    logged_duplicates += 1
            else:
                new_hashes.append(h)
                self.logger.unique_count += 1

        if len(duplicate_hashes) > max_log_duplicates:
            self.logger.debug(f"[重复检测] chunk内共发现 {len(duplicate_hashes)} 个重复项")

        return new_hashes, duplicate_hashes, duplicates

    def _deduplicate_file_server_mode(self, input_file: str, output_file: str,
                                      output_duplicates: str, save_index: bool,
                                      detected_encoding: str, file_size: int,
                                      operation_name: str) -> Dict:
        """服务端计算模式：客户端上传原始数据，服务端计算哈希并查重"""
        total_start = time.time()

        self.logger.log_file_process(input_file, file_size, 0, detected_encoding)

        stats = {
            "输入文件": input_file,
            "服务端": self.server_url,
            "文件大小(字节)": file_size,
            "文件大小(MB)": round(file_size / 1024 / 1024, 2),
            "分块大小": self._config.get('server_compute_chunk_size', 100000),
            "计算模式": "服务端计算",
            "开始时间": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

        server_chunk_size = self._config.get('server_compute_chunk_size', 100000)

        print("  正在分析文件...")
        # 优化：对于大文件，直接使用估算或分块处理，避免逐行统计耗时
        file_size_mb = file_size / (1024 * 1024)
        
        if file_size_mb > 10:
            # 大文件：使用文件大小估算（假设平均每行100字节），跳过耗时的逐行统计
            # 实际行数会在分块处理时统计
            estimated_line_count = int(file_size / 100)  # 估算：每行约100字节
            print(f"  文件较大({file_size_mb:.1f}MB)，采用估算模式: ~{estimated_line_count:,} 行")
            total_line_count = estimated_line_count
            
            # 保持配置的块大小不变，只是用估算值来计算块数和进度条
            # 不要调整 server_chunk_size，保持配置值（用户已设置为1,000,000）
        else:
            # 小文件：精确统计行数
            print("  正在统计文件总行数...")
            with open(input_file, 'r', encoding=detected_encoding, errors='ignore') as count_f:
                total_line_count = sum(1 for _ in count_f)
            
            # 小文件：保持配置的块大小，但确保至少有合理的块数
            # 如果文件太小（比如只有几千行），可以适当调整以避免过度分片
        
        estimated_chunks = (total_line_count + server_chunk_size - 1) // server_chunk_size if total_line_count > 0 else 6
        print(f"  文件总行数: ~{total_line_count:,}, 预计块数: {estimated_chunks}, 块大小: {server_chunk_size:,}")

        self.logger.info(f"[文件分析] 文件: {input_file} | 总行数: {total_line_count:,} | 预计块数: {estimated_chunks} | 块大小: {server_chunk_size:,} | 模式: 服务端计算")

        progress_bar = ProgressBar(
            total=estimated_chunks,
            bar_width=30,
            prefix='  块进度',
            suffix=f'/ {estimated_chunks} 块'
        )

        total_lines = 0
        unique_lines = 0
        duplicate_lines = 0
        chunks_processed = 0
        duplicate_counter = {}
        line_pos = 0
        dup_writer = None
        dup_output_file = None
        all_duplicate_hashes = []
        unique_writer = None
        unique_tmp_file = None
        server_hash_total_ms = 0
        server_check_total_ms = 0

        if output_duplicates:
            dup_output_file = output_duplicates

        with open(input_file, 'r', encoding=detected_encoding, errors='ignore') as f:
            while True:
                lines = []
                for _ in range(server_chunk_size):
                    line = f.readline()
                    if not line:
                        break
                    lines.append(line)

                if not lines:
                    break

                if dup_output_file and not dup_writer:
                    dup_writer = open(dup_output_file, 'w', encoding='utf-8')
                    dup_writer.write(f"# 查重时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    dup_writer.write(f"# 源文件: {input_file}\n")
                    dup_writer.write(f"# 模式: 服务端计算\n")
                    dup_writer.write("# 格式: 行号|重复内容\n")

                chunk_lines = len(lines)
                total_lines += chunk_lines

                # 调用服务端接口
                chunk_start = time.time()
                result = self.remote_index.dedup_file_server_compute(
                    lines=lines,
                    filename=operation_name,
                    file_path=input_file,
                    check_only=not save_index
                )
                chunk_duration = (time.time() - chunk_start) * 1000

                if result and result.get('success'):
                    dup_count = result.get('duplicate_count', 0)
                    uniq_count = result.get('unique_count', 0)
                    dup_indices = result.get('duplicate_line_indices', [])
                    uniq_indices = result.get('unique_line_indices', [])
                    dup_hashes = result.get('duplicate_hashes', [])
                    server_hash_ms = result.get('server_hash_ms', 0)
                    server_check_ms = result.get('server_check_ms', 0)
                    server_hash_total_ms += server_hash_ms
                    server_check_total_ms += server_check_ms

                    duplicate_lines += dup_count
                    unique_lines += uniq_count
                    
                    # 收集重复哈希用于追溯来源（仅保留前200个，避免内存膨胀）
                    if dup_hashes and len(all_duplicate_hashes) < 200:
                        all_duplicate_hashes.extend(dup_hashes[:200 - len(all_duplicate_hashes)])

                    # 记录重复行内容
                    if dup_writer:
                        for idx in dup_indices:
                            if idx < len(lines):
                                dup_writer.write(f"{line_pos + idx + 1}|{lines[idx]}")
                        dup_writer.flush()

                    # 流式写入唯一行到临时文件（按块顺序，无需排序）
                    if output_file:
                        if unique_writer is None:
                            unique_tmp_file = output_file + '.tmp'
                            unique_writer = open(unique_tmp_file, 'w', encoding='utf-8')
                        for idx in uniq_indices:
                            if idx < len(lines):
                                unique_writer.write(lines[idx].rstrip('\r\n') + '\n')

                    # 简单记录重复行内容用于统计（仅保留前20条，避免内存膨胀）
                    if len(duplicate_counter) < 20:
                        for idx in dup_indices:
                            if idx < len(lines):
                                line_stripped = lines[idx].rstrip('\r\n')
                                if line_stripped and line_stripped not in duplicate_counter:
                                    duplicate_counter[line_stripped] = 1

                    self.logger.debug(f"[块处理] 块 {chunks_processed + 1} | 行数:{chunk_lines} | 唯一:{uniq_count} | 重复:{dup_count} | 耗时:{chunk_duration:.0f}ms")
                else:
                    self.logger.error(f"[块处理] 块 {chunks_processed + 1} 服务端返回失败")
                    # 请求失败时保守计为全部重复，避免漏报
                    duplicate_lines += chunk_lines

                chunks_processed += 1
                progress_bar.update(chunks_processed, line_count=chunk_lines)

                if chunks_processed % 10 == 0:
                    print(f"\r  [进度] 已处理 {total_lines:,} 条, 当前块重复: {result.get('duplicate_count', 0) if result else 0:,}", end='')
                    sys.stdout.flush()

                line_pos += chunk_lines

        progress_bar.close()

        if dup_writer:
            dup_writer.close()

        if output_file:
            if unique_writer:
                unique_writer.close()
                unique_writer = None
                os.replace(unique_tmp_file, output_file)
                self.logger.info(f"[文件输出] 唯一数据已保存: {output_file}")
            else:
                print(f"\n  生成唯一数据文件...")
                with open(output_file, 'w', encoding='utf-8') as f:
                    pass
                self.logger.info(f"[文件输出] 无唯一数据: {output_file}")

        total_time = time.time() - total_start

        duplicate_content_list = list(duplicate_counter.keys())[:10]

        stats.update({
            "总数据量": total_lines,
            "唯一数据量": unique_lines,
            "重复数据量": duplicate_lines,
            "重复率(%)": round(duplicate_lines / total_lines * 100, 4) if total_lines > 0 else 0,
            "查重耗时(秒)": round(total_time, 2),
            "处理速度(条/秒)": round(total_lines / total_time) if total_time > 0 else 0,
            "服务端哈希耗时(ms)": round(server_hash_total_ms, 2),
            "服务端查重耗时(ms)": round(server_check_total_ms, 2),
            "结束时间": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "重复计数器": duplicate_counter,
            "duplicate_hashes": all_duplicate_hashes,
            "duplicate_lines": duplicate_content_list
        })

        self.logger.log_batch_result(total_lines, duplicate_lines, unique_lines, total_time * 1000, input_file)

        try:
            self.remote_index.notify_complete(
                filename=operation_name,
                total=total_lines,
                duplicate=duplicate_lines,
                unique=unique_lines,
                duplicate_rate=stats["重复率(%)"],
                duration_ms=total_time * 1000
            )
            self.remote_index.record_history(
                filename=operation_name,
                file_path=input_file,
                total=total_lines,
                duplicate=duplicate_lines,
                unique=unique_lines,
                duplicate_rate=stats["重复率(%)"],
                duration_ms=total_time * 1000
            )
        except Exception as e:
            self.logger.debug(f"[通知服务端] 调用 /api/complete 失败: {str(e)}")

        return stats

    def deduplicate_file(self, input_file: str, output_file: str = None,
                        output_duplicates: str = None, save_index: bool = True) -> Dict:
        total_start = time.time()
        operation_name = Path(input_file).name
        self.filename = operation_name
        self._file_path = input_file

        # 检查是否启用服务端计算模式
        server_compute_mode = self._config.get('server_compute_mode', False)

        print(f"\n{'='*60}")
        print(f"TXT查重工具 - 客户端 v{VERSION}")
        print(f"作者: {AUTHOR} | 联系: {CONTACT}")
        print(f"{'='*60}")
        print(f"操作: {operation_name}")
        print(f"服务端: {self.server_url}")
        print(f"{'='*60}")

        detected_encoding = self.detect_encoding(input_file)
        file_size = os.path.getsize(input_file)
        print(f" 文件大小: {file_size:,} 字节 ({file_size / 1024 / 1024:.2f} MB)")
        print(f" 文件编码: {detected_encoding}")

        if server_compute_mode:
            print(f" 计算模式: {Colors.green('服务端计算（推荐多核服务器）')}")
            print(f" 服务端计算批大小: {self._config.get('server_compute_chunk_size', 100000):,}")
            return self._deduplicate_file_server_mode(input_file, output_file, output_duplicates, save_index, detected_encoding, file_size, operation_name)

        prefer_xxhash = self._config.get('prefer_xxhash', True)
        use_xxhash = prefer_xxhash and HAS_XXHASH
        print(f" 计算模式: {'客户端计算（本地哈希）'}")
        print(f" 使用哈希算法: {'xxhash64' if use_xxhash else 'sha256'} {'(优先xxhash)' if prefer_xxhash else ''}")
        print(f" 线程数: {self.threads}")
        print(f" 分块大小: {self.chunk_size:,}")
        print(f" 批量大小: {self._config.get('batch_size', MAX_BATCH_SIZE)}")
        print(f" 并发请求: {self._config.get('concurrent_requests', CONCURRENT_REQUESTS)}")

        self.logger.log_file_process(input_file, file_size, 0, detected_encoding)

        stats = {
            "输入文件": input_file,
            "服务端": self.server_url,
            "文件大小(字节)": file_size,
            "文件大小(MB)": round(file_size / 1024 / 1024, 2),
            "分块大小": self.chunk_size,
            "线程数": self.threads,
            "开始时间": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

        total_lines = 0
        unique_lines = 0
        duplicate_lines = 0
        chunks_processed = 0
        duplicate_counter = {}
        line_pos = 0
        dup_writer = None
        dup_output_file = None
        unique_lines_set = set()
        all_duplicate_hashes = []

        if output_duplicates:
            dup_output_file = output_duplicates

        print("  正在统计文件总行数...")
        with open(input_file, 'r', encoding=detected_encoding, errors='ignore') as count_f:
            total_line_count = sum(1 for _ in count_f)
        
        # 优化：如果文件行数小于块大小，缩小块大小，确保有多个块
        client_chunk_size = self.chunk_size
        if total_line_count > 0 and total_line_count < client_chunk_size:
            client_chunk_size = max(10000, total_line_count // 5)  # 至少1万行一块，或者至少5个块
            
        estimated_chunks = (total_line_count + client_chunk_size - 1) // client_chunk_size
        print(f"  文件总行数: {total_line_count:,}, 预计块数: {estimated_chunks}, 块大小: {client_chunk_size:,}")

        self.logger.info(f"[文件分析] 文件: {input_file} | 总行数: {total_line_count:,} | 预计块数: {estimated_chunks}")

        progress_bar = ProgressBar(
            total=estimated_chunks,
            bar_width=30,
            prefix=f'  块进度',
            suffix=f'/ {estimated_chunks} 块'
        )

        with open(input_file, 'r', encoding=detected_encoding, errors='ignore') as f:
            while True:
                lines = []
                for _ in range(client_chunk_size):
                    line = f.readline()
                    if not line:
                        break
                    lines.append(line)

                if not lines:
                    break

                if dup_output_file and not dup_writer:
                    dup_writer = open(dup_output_file, 'w', encoding='utf-8')
                    dup_writer.write(f"# 查重时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    dup_writer.write(f"# 源文件: {input_file}\n")
                    dup_writer.write("# 格式: 行号|重复内容\n")

                chunk_lines = len(lines)
                total_lines += chunk_lines

                chunk_start = time.time()
                new_hashes, dup_hashes, duplicates = self.process_chunk_parallel(lines)
                if dup_hashes and len(all_duplicate_hashes) < 200:
                    all_duplicate_hashes.extend(dup_hashes[:200 - len(all_duplicate_hashes)])
                chunk_duration = (time.time() - chunk_start) * 1000

                self.logger.debug(f"[块处理] 块 {chunks_processed + 1} | 行数: {chunk_lines} | 唯一: {len(new_hashes)} | 重复: {len(dup_hashes)} | 耗时: {chunk_duration:.2f}ms")

                unique_lines += len(new_hashes)
                duplicate_lines += len(dup_hashes)

                if dup_writer:
                    for dup_line_num, line in duplicates:
                        dup_writer.write(f"{line_pos + dup_line_num + 1}|{line}\n")
                    dup_writer.flush()

                if output_file:
                    unique_lines_set.update(new_hashes)

                if len(duplicate_counter) < 20:
                    for _, line in duplicates:
                        line_stripped = line.rstrip('\r\n')
                        if line_stripped and line_stripped not in duplicate_counter:
                            duplicate_counter[line_stripped] = 1

                chunks_processed += 1
                progress_bar.update(chunks_processed, line_count=chunk_lines)

                if chunks_processed % 10 == 0:
                    print(f"\r  [进度] 已处理 {total_lines:,} 条, 当前块重复: {len(dup_hashes):,}", end='')
                    sys.stdout.flush()

                line_pos += chunk_lines

        progress_bar.close()

        if dup_writer:
            dup_writer.close()

        if save_index:
            self.logger.info("[索引保存] 正在提交索引到服务端...")
            self.remote_index.commit()

        if output_file:
            print(f"\n  生成唯一数据文件...")
            hash_to_line = {}
            line_idx = 0
            with open(input_file, 'r', encoding=detected_encoding, errors='ignore') as src:
                for line in src:
                    line_stripped = line.rstrip('\r\n')
                    if not line_stripped:
                        continue
                    h = self.compute_hash(line_stripped)
                    if h in unique_lines_set and h not in hash_to_line:
                        hash_to_line[h] = line_stripped
                    line_idx += 1
            with open(output_file, 'w', encoding='utf-8') as f:
                for line in hash_to_line.values():
                    f.write(line + '\n')
            self.logger.info(f"[文件输出] 唯一数据已保存: {output_file}")

        total_time = time.time() - total_start

        duplicate_content_list = list(duplicate_counter.keys())[:10]
        
        stats.update({
            "总数据量": total_lines,
            "唯一数据量": unique_lines,
            "重复数据量": duplicate_lines,
            "重复率(%)": round(duplicate_lines / total_lines * 100, 4) if total_lines > 0 else 0,
            "查重耗时(秒)": round(total_time, 2),
            "处理速度(条/秒)": round(total_lines / total_time) if total_time > 0 else 0,
            "结束时间": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "重复计数器": duplicate_counter,
            "duplicate_hashes": all_duplicate_hashes,
            "duplicate_lines": duplicate_content_list
        })

        self.logger.log_batch_result(total_lines, duplicate_lines, unique_lines, total_time * 1000, input_file)
        
        try:
            self.remote_index.notify_complete(
                filename=operation_name,
                total=total_lines,
                duplicate=duplicate_lines,
                unique=unique_lines,
                duplicate_rate=stats["重复率(%)"],
                duration_ms=total_time * 1000
            )
            self.remote_index.record_history(
                filename=operation_name,
                file_path=input_file,
                total=total_lines,
                duplicate=duplicate_lines,
                unique=unique_lines,
                duplicate_rate=stats["重复率(%)"],
                duration_ms=total_time * 1000
            )
        except Exception as e:
            self.logger.debug(f"[通知服务端] 调用 /api/complete 失败: {str(e)}")

        return stats


def create_parser():
    parser = argparse.ArgumentParser(
        description=f'TXT查重工具 - 客户端 v{VERSION}',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  直接拖放文件到EXE上进行查重
  或命令行: python client.py input.txt
  
  指定服务端（覆盖配置文件）:
    python client.py input.txt -s http://192.168.1.100:8888

  查看帮助:
    python client.py --help
        """
    )
    parser.add_argument('input_files', nargs='*', help='输入的TXT文件路径（支持拖放）')
    parser.add_argument('-s', '--server', default=None, help='服务端地址（覆盖配置文件）')
    parser.add_argument('-o', '--output', default=None, help='输出唯一数据文件路径')
    parser.add_argument('-d', '--duplicates', default=None, help='输出重复数据文件路径')
    parser.add_argument('-c', '--chunk-size', type=int, default=None, help='每块处理的数据量')
    parser.add_argument('-t', '--threads', type=int, default=None, help='线程数')
    parser.add_argument('--check-only', action='store_true', help='仅检查重复，不保存索引')
    parser.add_argument('--config', default=CONFIG_FILE, help='配置文件路径')
    parser.add_argument('--set-server', default=None, help='设置服务端地址到配置文件')
    parser.add_argument('--test-email', action='store_true', help='测试邮件配置是否正确')
    parser.add_argument('--watch', default=None, help='监控目录，新TXT文件自动查重')
    parser.add_argument('--api-token', default=None, help='API Token（覆盖配置文件）')
    parser.add_argument('-v', '--version', action='version', version=f'v{VERSION}')

    return parser


def main():
    try:
        _main()
    except Exception as e:
        import traceback
        import datetime
        
        error_msg = f"致命错误: {str(e)}"
        print(error_msg)
        
        try:
            log_dir = os.path.join(os.path.dirname(sys.executable), 'logs') if getattr(sys, 'frozen', False) else 'logs'
            os.makedirs(log_dir, exist_ok=True)
            
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            log_file = os.path.join(log_dir, f'error_{timestamp}.log')
            
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(f"========== 错误日志 ==========\n")
                f.write(f"时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"错误类型: {type(e).__name__}\n")
                f.write(f"错误消息: {str(e)}\n")
                f.write(f"\n命令行参数: {sys.argv}\n")
                f.write(f"\n堆栈跟踪:\n")
                f.write(traceback.format_exc())
                f.write("\n========== 结束 ==========\n")
            
            print(f"错误日志已保存至: {log_file}")
        except Exception as log_e:
            print(f"记录错误日志失败: {str(log_e)}")
        
        try:
            _safe_input("\n按 Enter 键退出...")
        except Exception:
            pass


def _main():
    parser = create_parser()
    args = parser.parse_args()

    config = ConfigManager(args.config)

    if args.set_server:
        config.set_server_address(args.set_server)
        print(f"服务端地址已设置为: {args.set_server}")
        return
    
    if args.test_email:
        print("\n" + "="*60)
        print("  邮件配置测试")
        print("="*60)
        
        smtp_server = config.get_smtp_server()
        smtp_port = config.get_smtp_port()
        smtp_username = config.get_smtp_username()
        smtp_password = config.get_smtp_password()
        from_addr = config.get_from_addr()
        to_addrs = config.get_to_addrs()
        
        if not smtp_server:
            print("[ERROR] 请先在配置文件中设置SMTP服务器地址")
            return
        if not smtp_username:
            print("[ERROR] 请先在配置文件中设置邮箱用户名")
            return
        if not smtp_password:
            print("[ERROR] 请先在配置文件中设置邮箱密码或授权码")
            return
        if not to_addrs:
            print("[ERROR] 请先在配置文件中设置收件人地址")
            return
        
        print(f"\n配置信息:")
        print(f"  SMTP服务器: {smtp_server}")
        print(f"  SMTP端口: {smtp_port}")
        print(f"  邮箱用户名: {smtp_username}")
        print(f"  发件人地址: {from_addr if from_addr else smtp_username}")
        print(f"  收件人地址: {to_addrs}")
        print("\n正在发送测试邮件...\n")
        
        success = test_email_config(
            smtp_server=smtp_server,
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            from_addr=from_addr if from_addr else smtp_username,
            to_addrs=to_addrs
        )
        
        if success:
            print("\n[OK] 测试成功！请检查收件箱是否收到测试邮件。")
        else:
            print("\n[ERROR] 测试失败！请检查配置是否正确。")
        
        return
    
    server_url = args.server if args.server else config.get_server_address()
    chunk_size = args.chunk_size if args.chunk_size else config.get_chunk_size()
    threads = args.threads if args.threads else config.get_threads()
    if threads == 0:
        threads = None
    machine_id = config.get_machine_id()

    input_files = []
    for path in args.input_files:
        if os.path.isdir(path):
            for f in os.listdir(path):
                if f.lower().endswith('.txt'):
                    input_files.append(os.path.join(path, f))
        elif os.path.isfile(path):
            input_files.append(path)

    if len(input_files) == 0:
        if len(sys.argv) == 1:
            print(f"\n{'='*60}")
            print(f"TXT查重工具 - 客户端 v{VERSION}")
            print(f"作者: {AUTHOR} | 联系: {CONTACT}")
            print(f"{'='*60}")
            print(f"当前服务端: {server_url}")
            print(f"{'='*60}")
            print("\n请将TXT文件拖放到此窗口或EXE文件上进行查重")
            print("或使用命令行: client.exe input.txt")
            print(f"\n如需修改服务端地址，请编辑 {CONFIG_FILE}")
            print(f"或运行: client.exe --set-server http://新地址:8888")
            _safe_input("\n按 Enter 键退出...")
            return
        else:
            print(f"{Colors.red('错误: 未找到任何TXT文件')}")
            _safe_input("\n按 Enter 键退出...")
            return

    print(f"\n{'='*60}")
    print(f"TXT查重工具 - 客户端 v{VERSION}")
    print(f"{'='*60}")
    print(f"服务端地址: {server_url}")
    print(f"待检查文件: {len(input_files)} 个")
    print(f"{'='*60}\n")

    try:
        # 合并完整配置，确保所有配置项都包含
        full_config = {
            'notify_type': config.get_notify_type(),
            'notify_duplicate_rate': config.get_notify_duplicate_rate(),
            'min_notify_interval': config.get_min_notify_interval(),
            'wecom_webhook': config.get_wecom_webhook(),
            'bark_url': config.get_bark_url(),
            'dingtalk_webhook': config.get_dingtalk_webhook(),
            'smtp_server': config.get_smtp_server(),
            'smtp_port': config.get_smtp_port(),
            'smtp_username': config.get_smtp_username(),
            'smtp_password': config.get_smtp_password(),
            'from_addr': config.get_from_addr(),
            'to_addrs': config.get_to_addrs(),
            'server_compute_mode': config.get_server_compute_mode(),
            'server_compute_chunk_size': config.get_server_compute_chunk_size(),
            'prefer_xxhash': config.get_prefer_xxhash(),
            'batch_size': config.get_batch_size(),
            'concurrent_requests': config.get_concurrent_requests()
        }
        
        deduplicator = RemoteTXTDeduplicator(
            server_url=server_url,
            chunk_size=chunk_size,
            threads=threads,
            config=full_config,
            machine_id=machine_id,
            api_token=args.api_token or ''
        )

        if not deduplicator.remote_index.health_check():
            print(f"{Colors.red('错误: 无法连接到服务端')}")
            print(f"{Colors.yellow('请检查服务端是否已启动，或修改配置文件中的服务端地址')}")
            deduplicator.logger.error(f"[连接失败] 无法连接到服务端: {server_url}")
            _safe_input("\n按 Enter 键退出...")
            return

        print(f"{Colors.green('已连接到服务端')}")
        server_stats = deduplicator.remote_index.get_stats()
        if server_stats:
            print(f"服务端索引记录: {server_stats.get('total_records', 0):,} 条")
            print(f"存储类型: {server_stats.get('storage_type', 'Unknown')}")
            deduplicator.logger.info(f"[服务端信息] 索引记录: {server_stats.get('total_records', 0):,} | 存储类型: {server_stats.get('storage_type', 'Unknown')}")
            print()

        # 文件夹监控模式
        if args.watch:
            watch_dir = args.watch
            if not os.path.isdir(watch_dir):
                print(f"{Colors.red(f'错误: 目录不存在: {watch_dir}')}")
                return
            print(f"\n{'='*60}")
            print(f"{Colors.green('文件夹监控模式')}")
            print(f"监控目录: {watch_dir}")
            print(f"按 Ctrl+C 停止监控")
            print(f"{'='*60}\n")
            processed_files = set()
            for f in os.listdir(watch_dir):
                if f.lower().endswith('.txt'):
                    processed_files.add(os.path.join(watch_dir, f))
            try:
                while True:
                    time.sleep(5)
                    for f in os.listdir(watch_dir):
                        if not f.lower().endswith('.txt'):
                            continue
                        fpath = os.path.join(watch_dir, f)
                        if fpath in processed_files:
                            continue
                        processed_files.add(fpath)
                        print(f"\n{Colors.blue('[新文件]')} 检测到新文件: {f}")
                        try:
                            deduplicator.logger.set_log_file(fpath)
                            stats = deduplicator.deduplicate_file(
                                input_file=fpath,
                                output_file=None,
                                output_duplicates=None,
                                save_index=not args.check_only
                            )
                            print(f"{Colors.green('[完成]')} {f}: 总数 {stats['总数据量']:,} | 重复 {stats['重复数据量']:,} | 重复率 {stats['重复率(%)']:.2f}%")
                        except Exception as e:
                            print(f"{Colors.red(f'[错误]')} 处理 {f} 失败: {e}")
            except KeyboardInterrupt:
                print(f"\n{Colors.yellow('监控已停止')}")
                deduplicator.logger.close()
                return

        total_stats = {
            '总文件数': len(input_files),
            '已处理': 0,
            '总数据量': 0,
            '唯一数据量': 0,
            '重复数据量': 0,
            '总耗时': 0
        }

        file_results = []
        output_dir = config.get_output_dir()
        save_unique = config.get_save_unique()
        save_duplicates = config.get_save_duplicates()
        
        notifier = NotificationManager(full_config, machine_id)
        notifier.set_logger(deduplicator.logger)

        # Disable console logging during processing, keep only file logging
        deduplicator.logger.logger.removeHandler(deduplicator.logger.console_handler)
        
        for idx, input_file in enumerate(input_files, 1):
            if not os.path.isfile(input_file):
                print(f"{Colors.red(f'警告: 跳过目录或不存在: {input_file}')}")
                deduplicator.logger.warning(f"[文件跳过] 文件不存在或不是文件: {input_file}")
                continue

            print(f"\n{'='*60}")
            print(f"{Colors.blue(f'[{idx}/{len(input_files)}]')} 检查: {input_file}")
            print(f"{'='*60}")
            
            deduplicator.logger.set_log_file(input_file)
            log_path = deduplicator.logger._current_log_file
            print(f"  日志文件: {log_path}")

            if output_dir:
                base_name = os.path.splitext(os.path.basename(input_file))[0]
                output_file = os.path.join(output_dir, f"{base_name}_unique.txt") if save_unique else None
                output_duplicates = os.path.join(output_dir, f"{base_name}_duplicates.txt") if save_duplicates else None
            else:
                base_name = os.path.splitext(input_file)[0]
                output_file = f"{base_name}_unique.txt" if save_unique else None
                output_duplicates = f"{base_name}_duplicates.txt" if save_duplicates else None

            stats = deduplicator.deduplicate_file(
                input_file=input_file,
                output_file=output_file,
                output_duplicates=output_duplicates,
                save_index=not args.check_only
            )

            total_stats['已处理'] += 1
            total_stats['总数据量'] += stats['总数据量']
            total_stats['唯一数据量'] += stats['唯一数据量']
            total_stats['重复数据量'] += stats['重复数据量']
            total_stats['总耗时'] += stats['查重耗时(秒)']

            file_results.append({
                'filename': os.path.basename(input_file),
                'total': stats['总数据量'],
                'unique': stats['唯一数据量'],
                'duplicate': stats['重复数据量'],
                'has_duplicate': stats['重复数据量'] > 0
            })

            print(f"\n{Colors.green('[OK]')} 查重完成")
            print(f"╭──────────────────────────────────────────────────────╮")
            print(f"│ [统计]  查重报告")
            print(f"╰──────────────────────────────────────────────────────╯")
            print(f"  总数据量  : {stats['总数据量']:,}")
            print(f"  唯一数据量: {Colors.green(f'{stats['唯一数据量']:,}')}")
            
            if stats['重复数据量'] > 0:
                print(f"  重复数据量: {Colors.red(f'{stats['重复数据量']:,}')}")
                print(f"  重复率    : {Colors.red(f'{stats['重复率(%)']:.2f}%')}")
            else:
                print(f"  重复数据量: {stats['重复数据量']:,}")
                print(f"  重复率    : {stats['重复率(%)']:.2f}%")
            
            print(f"  总耗时    : {stats['查重耗时(秒)']:.2f}秒")
            print(f"  处理速度  : {stats['处理速度(条/秒)']:,} 条/秒")
            print(f"╰──────────────────────────────────────────────────────╯\n")
            
            duplicate_sources = {}
            machine_compare = {}
            if stats['重复数据量'] > 0:
                duplicate_hashes_list = stats.get('duplicate_hashes', [])
                sample_dup_hashes = list(duplicate_hashes_list)[:100]
                if sample_dup_hashes:
                    deduplicator.logger.info(f"[来源查询] 准备查询 {len(sample_dup_hashes)} 个重复哈希的来源...")
                    try:
                        duplicate_sources = deduplicator.remote_index.query_sources(sample_dup_hashes)
                        deduplicator.logger.info(f"[来源查询] query_sources 返回 {len(duplicate_sources)} 条结果")
                        if duplicate_sources:
                            first_source = list(duplicate_sources.values())[0]
                            deduplicator.logger.info(f"[来源查询] 查询 {len(sample_dup_hashes)} 个哈希 → 获取 {len(duplicate_sources)} 条 | 最早文件: {first_source.get('filename', '未知')} | 路径: {first_source.get('file_path', '未知')} | 时间: {first_source.get('timestamp', '未知')} | 机台: {first_source.get('machine_id', '未知')}")
                        else:
                            deduplicator.logger.warning(f"[来源查询] query_sources 返回空结果，尝试 machine_compare 作为备选")
                        
                        machine_compare = deduplicator.remote_index.query_machine_compare(sample_dup_hashes)
                        deduplicator.logger.info(f"[机台对比] query_machine_compare 返回 {machine_compare.get('duplicate_count', 0)} 条结果")
                        if machine_compare and machine_compare.get('machine_counts'):
                            deduplicator.logger.info(f"[机台对比] 涉及机台: {', '.join(machine_compare['machine_list'])} | 各机台重复数: {machine_compare['machine_counts']}")
                        
                        # 如果 query_sources 为空但 machine_compare 有详情，从 machine_compare 提取来源信息
                        if not duplicate_sources and machine_compare and machine_compare.get('details'):
                            deduplicator.logger.info(f"[来源查询] 从 machine_compare 详情中提取来源信息")
                            for detail in machine_compare['details']:
                                hash_key = detail.get('hash', '')
                                if hash_key and hash_key not in duplicate_sources:
                                    duplicate_sources[hash_key] = {
                                        'filename': detail.get('filename', '未知'),
                                        'file_path': detail.get('file_path', ''),
                                        'machine_id': detail.get('machine_id', ''),
                                        'timestamp': detail.get('timestamp', '')
                                    }
                            deduplicator.logger.info(f"[来源查询] 从 machine_compare 提取了 {len(duplicate_sources)} 条来源信息")
                    except Exception as e:
                        deduplicator.logger.error(f"[来源查询] 查询失败: {str(e)}")
                        import traceback
                        deduplicator.logger.error(f"[来源查询] 堆栈: {traceback.format_exc()}")
                else:
                    deduplicator.logger.warning(f"[来源查询] duplicate_hashes 为空，无法查询来源")
            
            # 显示重复来源信息
            if stats['重复数据量'] > 0 and duplicate_sources:
                print(f"\n{Colors.yellow('╭──────────────────────────────────────────────────────╮')}")
                print(f"{Colors.yellow('│ [追溯]  重复来源信息')}")
                print(f"{Colors.yellow('╰──────────────────────────────────────────────────────╯')}")
                
                source_list = []
                for h, source in duplicate_sources.items():
                    key = (source.get('filename', '未知'), source.get('file_path', ''), source.get('timestamp', '未知'), source.get('machine_id', ''))
                    if key not in source_list:
                        source_list.append(key)
                
                for i, (fname, fpath, ts, mid) in enumerate(source_list[:5]):
                    print(f"  {i+1}. 文件: {fname}")
                    if fpath:
                        print(f"     路径: {fpath}")
                    print(f"     时间: {ts}", end='')
                    if mid:
                        print(f" | 机台: {mid}")
                    else:
                        print()
                if len(source_list) > 5:
                    print(f"  ... 还有 {len(source_list) - 5} 个不同来源文件")
                print(f"{Colors.yellow('╰──────────────────────────────────────────────────────╯\n')}")
            
            notifier.notify_completed(
                file_name=os.path.basename(input_file),
                file_path=input_file,
                file_import_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                total=stats['总数据量'],
                duplicate=stats['重复数据量'],
                duplicate_rate=stats['重复率(%)'],
                duplicate_sources=duplicate_sources,
                duplicate_lines=stats.get('duplicate_lines', [])[:3] if stats.get('duplicate_lines') else [],
                machine_compare=machine_compare
            )

            deduplicator.logger.info(f"[文件处理完成] 文件: {os.path.basename(input_file)} | 路径: {input_file} | 导入时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 总数: {stats['总数据量']:,} | 唯一: {stats['唯一数据量']:,} | 重复: {stats['重复数据量']:,} | 重复率: {stats['重复率(%)']:.2f}% | 耗时: {stats['查重耗时(秒)']:.2f}s")

            if output_file:
                print(f"唯一数据已保存至: {output_file}")
            if output_duplicates:
                print(f"重复数据已保存至: {output_duplicates}")

    except KeyboardInterrupt:
        print(f"\n{Colors.yellow('用户中断操作')}")
        if 'deduplicator' in locals():
            deduplicator.logger.info("[中断] 用户手动中断操作")

    except Exception as e:
        if 'deduplicator' in locals():
            deduplicator.logger.error(f"[异常] 运行时错误: {str(e)}", exception=e)
        print(f"{Colors.red(f'错误: {str(e)}')}")

    print()
    print(f"\n{Colors.yellow('='*60)}")
    print(f"{Colors.yellow('              查重报告汇总')}")
    print(f"{Colors.yellow('='*60)}\n")

    if len(input_files) > 1:
        TableOutput.print_summary_table(file_results)

        total_dup_rate = (total_stats['重复数据量'] / total_stats['总数据量'] * 100) if total_stats['总数据量'] > 0 else 0

        print(f"\n{Colors.yellow('='*60)}")
        print(f"{Colors.yellow('            批量处理汇总')}")
        print(f"{Colors.yellow('='*60)}")
        print(f"文件进度: {Colors.blue(f'{total_stats['已处理']}')}/{total_stats['总文件数']} 个")
        print(f"总数据量: {total_stats['总数据量']:,}")
        print(f"唯一数据量: {Colors.green(f'{total_stats['唯一数据量']:,}')}")
        if total_stats['重复数据量'] > 0:
            print(f"{Colors.red('重复数据量:')} {Colors.red(f'{total_stats['重复数据量']:,}')}")
            print(f"{Colors.red('重复率:')} {Colors.red(f'{total_dup_rate:.2f}%')}")
        else:
            print(f"重复数据量: {total_stats['重复数据量']:,}")
            print(f"重复率: {total_dup_rate:.2f}%")
        print(f"总耗时: {total_stats['总耗时']:.2f}秒")
        print(f"{'='*60}\n")
    elif len(input_files) == 1 and file_results:
        r = file_results[0]
        rate = f"{r['duplicate']/r['total']*100:.2f}%" if r['total'] > 0 else '0%'
        print(f"  文件名    : {r['filename']}")
        print(f"  总数据量  : {r['total']:,}")
        print(f"  唯一数据量: {r['unique']:,}")
        print(f"  重复数据量: {r['duplicate']:,}")
        print(f"  重复率    : {Colors.red(rate) if r['duplicate'] > 0 else rate}")
        print(f"{'='*60}\n")

    if 'deduplicator' in locals():
        deduplicator.logger.log_stats()
        deduplicator.logger.close()

    try:
        _safe_input("\n按 Enter 键退出...")
    except (EOFError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    main()