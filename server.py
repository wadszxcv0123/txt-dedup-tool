#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import gc
import sqlite3
import threading
import argparse
import configparser
import signal
import secrets
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import deque
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from flask import Flask, request, jsonify
except ImportError:
    print("错误: 需要安装 flask 库")
    sys.exit(1)

from logging_utils import DedupLogger

try:
    from version import VERSION, BUILD_TIME, AUTHOR, CONTACT, APP_NAME
except ImportError:
    VERSION = "1.2.0"
    BUILD_TIME = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    AUTHOR = "张文龙"
    CONTACT = "18053292127"
    APP_NAME = "TXT查重工具"

try:
    from sharded_lmdb import ShardedLMDB
except ImportError:
    ShardedLMDB = None

try:
    import xxhash
    HAS_XXHASH = True
except ImportError:
    HAS_XXHASH = False
    import hashlib

SERVER_CONFIG_FILE = "server_config.ini"

logger = None


class ServerConfigManager:
    def __init__(self, config_path: str = SERVER_CONFIG_FILE):
        self.config_path = config_path
        self.config = configparser.ConfigParser()
        self.load_config()

    def load_config(self):
        if os.path.exists(self.config_path):
            self.config.read(self.config_path, encoding='utf-8')
        else:
            self._create_default_config()

    def _create_default_config(self):
        self.config['Server'] = {
            'host': '0.0.0.0',
            'port': '8888',
            'index_dir': '.dedup_index',
            'use_sqlite': 'true',
            'storage_type': 'sqlite',
            'lmdb_shard_count': '8',
            'lmdb_map_size_gb': '4',
            'sqlite_cache_size_mb': '512',
            'log_level': 'INFO',
            'log_file': 'server.log',
            'log_max_size_mb': '100',
            'log_backup_count': '30',
            'error_log_file': 'server_error.log',
            'error_log_max_size_mb': '50',
            'error_log_backup_count': '5'
        }
        self.config['Log'] = {
            'log_level': 'INFO',
            'log_dir': 'logs'
        }
        self.config['Performance'] = {
            'max_batch_size': '10000',
            'request_timeout': '120'
        }
        self.config['HealthMonitor'] = {
            'memory_warning_mb': '4096',
            'memory_critical_mb': '6144',
            'disk_warning_gb': '100',
            'disk_critical_gb': '50',
            'connection_warning': '100',
            'connection_critical': '500',
            'health_check_interval': '60',
            'disk_check_interval': '86400',
            'health_enable_email': 'true'
        }
        self.config['Email'] = {
            'smtp_server': '',
            'smtp_port': '587',
            'smtp_username': '',
            'smtp_password': '',
            'from_addr': '',
            'to_addrs': ''
        }
        self.config['AutoCleanup'] = {
            'enabled': 'true',
            'max_disk_usage_gb': '3800',
            'cleanup_trigger_percent': '95',
            'cleanup_target_percent': '90',
            'cleanup_batch_size': '100000'
        }
        self.save_config()

    def save_config(self):
        with open(self.config_path, 'w', encoding='utf-8') as f:
            self.config.write(f)

    def get_host(self) -> str:
        return self.config.get('Server', 'host', fallback='0.0.0.0')

    def get_port(self) -> int:
        return self.config.getint('Server', 'port', fallback=8888)

    def get_index_dir(self) -> str:
        return self.config.get('Server', 'index_dir', fallback='.dedup_index')

    def get_use_sqlite(self) -> bool:
        return self.config.getboolean('Server', 'use_sqlite', fallback=True)

    def get_sqlite_cache_size(self) -> int:
        return self.config.getint('Server', 'sqlite_cache_size_mb', fallback=512)

    def get_storage_type(self) -> str:
        if self.config.has_option('Server', 'storage_type'):
            return self.config.get('Server', 'storage_type', fallback='sqlite').strip().lower()
        return 'sqlite' if self.get_use_sqlite() else 'memory'

    def get_lmdb_shard_count(self) -> int:
        return self.config.getint('Server', 'lmdb_shard_count', fallback=8)

    def get_lmdb_map_size_gb(self) -> int:
        return self.config.getint('Server', 'lmdb_map_size_gb', fallback=4)

    def get_log_level(self) -> str:
        return self.config.get('Log', 'log_level', fallback='INFO')

    def get_log_dir(self) -> str:
        return self.config.get('Log', 'log_dir', fallback='logs')

    def get_max_batch_size(self) -> int:
        return self.config.getint('Performance', 'max_batch_size', fallback=10000)

    def get_int(self, section: str, key: str, default: int = 0) -> int:
        return self.config.getint(section, key, fallback=default)

    def get_bool(self, section: str, key: str, default: bool = False) -> bool:
        return self.config.getboolean(section, key, fallback=default)

    def get_str(self, section: str, key: str, default: str = '') -> str:
        return self.config.get(section, key, fallback=default)


class ProgressBar:
    def __init__(self, total: int, prefix: str = "", suffix: str = "", length: int = 40):
        self.total = total
        self.prefix = prefix
        self.suffix = suffix
        self.length = length
        self.current = 0
    
    def update(self, current: int):
        self.current = current
        if self.total == 0:
            percent = 100
            filled_length = self.length
        else:
            percent = (self.current / self.total) * 100
            filled_length = int(self.length * self.current // self.total)
        bar = '=' * filled_length + '-' * (self.length - filled_length)
        
        print(f'\r{self.prefix} [{bar}] {percent:.1f}% {self.suffix}', end='', flush=True)
    
    def finish(self):
        self.update(self.total)
        print()


class HashIndex:
    def __init__(self, index_dir: str, storage_type: str = 'sqlite',
                 cache_size_mb: int = 512, server_mode: bool = False,
                 mmap_size_gb: int = 2, sqlite_threads: int = 4,
                 lmdb_shard_count: int = 8, lmdb_map_size_gb: int = 4):
        self.index_dir = index_dir
        self.storage_type = (storage_type or 'sqlite').strip().lower()
        if self.storage_type not in ('sqlite', 'lmdb', 'memory'):
            if logger:
                logger.warning(f"未知存储类型 '{storage_type}'，改用 memory 模式")
            self.storage_type = 'memory'
        self.use_db = self.storage_type == 'sqlite'
        self.use_lmdb = self.storage_type == 'lmdb'
        self.server_mode = server_mode
        
        if self.use_lmdb:
            if ShardedLMDB is None:
                raise RuntimeError("LMDB backend selected but lmdb is not installed or sharded_lmdb.py is missing. Install with 'pip install lmdb'.")
            os.makedirs(index_dir, exist_ok=True)
            self.lmdb_backend = ShardedLMDB(
                index_dir,
                shard_count=lmdb_shard_count,
                map_size=lmdb_map_size_gb * 1024 * 1024 * 1024
            )
            # 启动时不等待统计完成（1200万记录统计需耗时约1分钟）
            # 首日访问 /api/stats 时会触发真实统计
            if logger:
                logger.info(f"[LMDB] 初始化完成，{lmdb_shard_count} 个分片")
            self._lmdb_total_cached = None
            self._lmdb_stats_cache_time = 0
        elif self.use_db:
            os.makedirs(index_dir, exist_ok=True)
            self.db_path = os.path.join(index_dir, "hash_index.db")
            
            is_new_db = not os.path.exists(self.db_path)
            self._db = sqlite3.connect(self.db_path, check_same_thread=False)
            
            if is_new_db:
                self._db.execute("PRAGMA page_size=16384")
                self._db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            
            if server_mode:
                mmap_bytes = mmap_size_gb * 1024 * 1024 * 1024
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=NORMAL")
                self._db.execute("PRAGMA wal_autocheckpoint=8000")
                self._db.execute("PRAGMA locking_mode=EXCLUSIVE")
                self._db.execute(f"PRAGMA threads={sqlite_threads}")
                self._db.execute("PRAGMA busy_timeout=30000")
                self._db.execute("PRAGMA hard_heap_limit=0")
                self._db.execute("PRAGMA soft_heap_limit=0")
                self._db.execute(f"PRAGMA cache_size=-{cache_size_mb * 1024}")
                self._db.execute(f"PRAGMA mmap_size={mmap_bytes}")
                self._db.execute("PRAGMA temp_store=MEMORY")
                self._db.execute("PRAGMA foreign_keys=OFF")
                self._db.execute("PRAGMA automatic_index=OFF")
                # PRAGMA optimize 推迟到后台线程执行，避免阻塞启动（大表可能耗时数秒）
                def _deferred_optimize():
                    try:
                        self._db.execute("PRAGMA optimize")
                        if logger:
                            logger.info("[SQLite] PRAGMA optimize 完成（后台）")
                    except Exception:
                        pass
                threading.Thread(target=_deferred_optimize, daemon=True).start()
                if logger:
                    logger.info(f"[SQLite] 服务器模式 | WAL(EXCLUSIVE) | page=16K | "
                                f"cache={cache_size_mb}MB | mmap={mmap_size_gb}GB | threads={sqlite_threads}")
            else:
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=NORMAL")
                self._db.execute("PRAGMA wal_autocheckpoint=4000")
                self._db.execute(f"PRAGMA cache_size=-{cache_size_mb * 1024}")
                self._db.execute("PRAGMA mmap_size=2147483648")
                self._db.execute("PRAGMA temp_store=MEMORY")
                self._db.execute("PRAGMA busy_timeout=10000")
                self._db.execute("PRAGMA foreign_keys=OFF")
                self._db.execute("PRAGMA automatic_index=OFF")
                def _deferred_optimize2():
                    try:
                        self._db.execute("PRAGMA optimize")
                        if logger:
                            logger.info("[SQLite] PRAGMA optimize 完成（后台）")
                    except Exception:
                        pass
                threading.Thread(target=_deferred_optimize2, daemon=True).start()
                if logger:
                    logger.info(f"[SQLite] 已打开数据库: {self.db_path} | WAL | page=16K | cache={cache_size_mb}MB | mmap=2GB")
            
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS hashes (
                    hash TEXT PRIMARY KEY,
                    filename TEXT NOT NULL DEFAULT '',
                    machine_id TEXT NOT NULL DEFAULT '',
                    timestamp TEXT NOT NULL DEFAULT '',
                    data TEXT NOT NULL DEFAULT ''
                ) WITHOUT ROWID
            """)
            self._db.execute("CREATE INDEX IF NOT EXISTS idx_hashes_ts ON hashes(timestamp)")
            self._db.execute("DROP TABLE IF EXISTS _batch_cleanup")
            self._db.execute("CREATE TEMP TABLE IF NOT EXISTS _batch_cleanup(hash TEXT PRIMARY KEY) WITHOUT ROWID")
            
            self._db.commit()
        else:
            os.makedirs(index_dir, exist_ok=True)
            self.index_file = os.path.join(index_dir, "hash_index.idx")
            self.seen_hashes: Dict[str, Dict] = {}
        
        self._lock = threading.Lock()
        self._deleted_count = 0
        self._stats_cache = None
        self._stats_cache_time = 0
        self._machine_stats_cache = None
        self._machine_stats_cache_time = 0
        self._load_index()

    def _load_index(self):
        if self.use_db:
            # SELECT COUNT(*) 对 WITHOUT ROWID 表是全表扫描，大表可能耗时数秒
            # 推迟到后台线程，不影响服务立即可用
            def _log_count():
                try:
                    total = self._db.execute("SELECT COUNT(*) FROM hashes").fetchone()[0]
                    if total > 0 and logger:
                        logger.info(f"[SQLite] 已有 {total:,} 条哈希记录，无需加载")
                except Exception:
                    pass
            threading.Thread(target=_log_count, daemon=True).start()
        elif self.use_lmdb:
            # 启动时不等待统计完成，避免阻塞启动（1200万记录统计需耗时约1分钟）
            # 首日访问 /api/stats 时会触发真实统计
            if logger:
                logger.info(f"[LMDB] 初始化完成，{self.lmdb_backend.shard_count} 个分片")
        else:
            # memory 模式：从磁盘文件加载已有索引
            if os.path.exists(self.index_file):
                try:
                    with open(self.index_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            parts = line.strip().split('\t')
                            hash_val = parts[0]
                            meta = json.loads(parts[1]) if len(parts) > 1 else {}
                            self.seen_hashes[hash_val] = meta
                    if logger:
                        logger.info(f"[索引加载] 已加载 {len(self.seen_hashes):,} 条哈希记录")
                except Exception as e:
                    if logger:
                        logger.error(f"[索引加载] 加载失败: {e}")
                    self.seen_hashes = {}

    def _query_existing_hashes(self, hash_vals: List[str]) -> set:
        existing = set()
        if not hash_vals:
            return existing
        max_vars = 900
        for i in range(0, len(hash_vals), max_vars):
            chunk = hash_vals[i:i + max_vars]
            placeholders = ','.join(['?'] * len(chunk))
            sql = f"SELECT hash FROM hashes WHERE hash IN ({placeholders})"
            rows = self._db.execute(sql, chunk).fetchall()
            existing.update(row[0] for row in rows)
        return existing

    def _save_index_batch(self, new_hashes: List[str], metadata: Dict = None):
        if metadata is None:
            metadata = {}
        import datetime as dt
        filename = metadata.get('filename', '')
        machine_id = metadata.get('machine_id', '')
        timestamp = metadata.get('timestamp', dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        data_json = json.dumps(metadata, ensure_ascii=False)
        
        with open(self.index_file, 'a', encoding='utf-8') as f:
            for h in new_hashes:
                f.write(f"{h}\t{data_json}\n")
                self.seen_hashes[h] = metadata

    def check_and_add_batch(self, hash_vals: List[str], metadata: Dict = None) -> List[bool]:
        if self.use_lmdb:
            # 统一加锁顺序：总是先获取 HashIndex._lock，再获取 LMDB 内锁，避免死锁
            with self._lock:
                return self.lmdb_backend.check_and_add_batch(hash_vals, metadata)

        results = [False] * len(hash_vals)
        new_hashes = []

        if self.use_db:
            # 整个读→判定→写过程在同一把锁内，消除读-写间隙竞态
            with self._lock:
                existing = self._query_existing_hashes(hash_vals)
                seen = set()
                for i, h in enumerate(hash_vals):
                    if h in existing or h in seen:
                        results[i] = True
                    else:
                        results[i] = False
                        seen.add(h)
                        new_hashes.append(h)

                if new_hashes:
                    import datetime as dt
                    if metadata is None:
                        metadata = {}
                    filename = metadata.get('filename', '')
                    machine_id = metadata.get('machine_id', '')
                    timestamp = metadata.get('timestamp', dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                    data_json = json.dumps(metadata, ensure_ascii=False)
                    rows = [(h, filename, machine_id, timestamp, data_json) for h in new_hashes]
                    self._db.execute("BEGIN IMMEDIATE")
                    try:
                        self._db.executemany(
                            "INSERT OR IGNORE INTO hashes(hash,filename,machine_id,timestamp,data) VALUES(?,?,?,?,?)",
                            rows
                        )
                        self._db.commit()
                        self._stats_cache = None
                        self._machine_stats_cache = None
                    except Exception:
                        self._db.rollback()
                        raise
        else:
            # Memory-backed index
            with self._lock:
                seen = set()
                for i, h in enumerate(hash_vals):
                    if h in self.seen_hashes or h in seen:
                        results[i] = True
                    else:
                        results[i] = False
                        seen.add(h)
                        new_hashes.append(h)
                if new_hashes:
                    self._save_index_batch(new_hashes, metadata)

        return results

    def check_only_batch(self, hash_vals: List[str]) -> List[bool]:
        if self.use_lmdb:
            return self.lmdb_backend.check_only_batch(hash_vals)

        results = [False] * len(hash_vals)
        with self._lock:
            if self.use_db:
                existing = self._query_existing_hashes(hash_vals)
                seen = set()
                for i, h in enumerate(hash_vals):
                    if h in existing or h in seen:
                        results[i] = True
                    else:
                        results[i] = False
                        seen.add(h)
            else:
                seen = set()
                for i, h in enumerate(hash_vals):
                    if h in self.seen_hashes or h in seen:
                        results[i] = True
                    else:
                        results[i] = False
                        seen.add(h)
        return results

    def get_duplicate_sources(self, hash_vals: List[str]) -> Dict[str, Dict]:
        if self.use_lmdb:
            return self.lmdb_backend.get_duplicate_sources(hash_vals)

        sources = {}
        with self._lock:
            if self.use_db:
                if hash_vals:
                    max_vars = 900
                    for i in range(0, len(hash_vals), max_vars):
                        chunk = hash_vals[i:i + max_vars]
                        placeholders = ','.join(['?'] * len(chunk))
                        sql = (
                            "SELECT hash, filename, machine_id, timestamp, data "
                            "FROM hashes WHERE hash IN (" + placeholders + ")"
                        )
                        cursor = self._db.execute(sql, chunk)
                        for row in cursor:
                            # 解析 data JSON 以提取 file_path 等扩展字段
                            meta = {
                                'filename': row[1],
                                'machine_id': row[2],
                                'timestamp': row[3],
                            }
                            try:
                                data_json = json.loads(row[4])
                                if isinstance(data_json, dict):
                                    meta.update(data_json)
                            except (json.JSONDecodeError, TypeError):
                                meta['data'] = row[4]
                            sources[row[0]] = meta
            else:
                for h in hash_vals:
                    if h in self.seen_hashes:
                        sources[h] = self.seen_hashes.get(h, {'exists': True})
        return sources

    def get_stats(self):
        now = time.time()

        if self.use_lmdb:
            if self._lmdb_total_cached is not None and (now - self._lmdb_stats_cache_time) < 60:
                return {
                    'total_records': self._lmdb_total_cached,
                    'storage_type': 'LMDB',
                    'cached': True
                }
            stats = self.lmdb_backend.get_stats()
            self._lmdb_total_cached = stats.get('total_records', 0)
            self._lmdb_stats_cache_time = now
            stats['storage_type'] = 'LMDB'
            stats['cached'] = False
            return stats

        if self.use_db:
            try:
                if self._stats_cache is not None and (now - self._stats_cache_time) < 60:
                    return self._stats_cache
                total = self._db.execute("SELECT COUNT(*) FROM hashes").fetchone()[0]
                result = {
                    'total_records': total,
                    'storage_type': 'SQLite'
                }
                self._stats_cache = result
                self._stats_cache_time = now
                return result
            except Exception:
                return {
                    'total_records': 0,
                    'storage_type': 'SQLite'
                }
        else:
            return {
                'total_records': len(self.seen_hashes),
                'storage_type': 'Memory'
            }

    def commit(self):
        if self.use_db:
            try:
                self._db.commit()
            except Exception:
                pass
        elif self.use_lmdb:
            try:
                self.lmdb_backend.commit()
            except Exception:
                pass

    def release_memory(self):
        """内存超阈值时强制刷盘并释放OS缓存页"""
        if self.use_lmdb:
            try:
                self.lmdb_backend.release_memory()
            except Exception:
                pass
        elif self.use_db:
            try:
                self._db.commit()
                self._db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except Exception:
                pass

    def wal_checkpoint(self):
        if self.use_db:
            try:
                self._db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except Exception:
                pass

    def get_detailed_stats(self):
        stats = self.get_stats()
        if self.use_db:
            db_path = self.db_path
            wal_path = db_path + '-wal'
            try:
                stats['db_size_mb'] = round(os.path.getsize(db_path) / (1024 * 1024), 1)
            except Exception:
                stats['db_size_mb'] = 0
            try:
                stats['wal_size_mb'] = round(os.path.getsize(wal_path) / (1024 * 1024), 1)
            except Exception:
                stats['wal_size_mb'] = 0
            try:
                stats['deleted_count'] = self._deleted_count
            except Exception:
                pass
            try:
                stats['page_count'] = self._db.execute("PRAGMA page_count").fetchone()[0]
                stats['page_size'] = self._db.execute("PRAGMA page_size").fetchone()[0]
                stats['freelist_count'] = self._db.execute("PRAGMA freelist_count").fetchone()[0]
            except Exception:
                pass
        elif self.use_lmdb:
            try:
                stats['db_size_mb'] = round(self.get_disk_usage_bytes() / (1024 * 1024), 1)
            except Exception:
                stats['db_size_mb'] = 0
            try:
                stats['deleted_count'] = self._deleted_count
            except Exception:
                pass
            try:
                stats['shards'] = self.lmdb_backend.shard_count
            except Exception:
                pass
        else:
            stats['db_size_mb'] = 0
        return stats

    def get_machine_stats(self):
        """获取机台统计信息（60秒缓存）"""
        now = time.time()
        if self._machine_stats_cache is not None and (now - self._machine_stats_cache_time) < 60:
            return self._machine_stats_cache

        machine_counts = {}
        total_records = 0
        
        if self.use_db:
            try:
                cursor = self._db.execute("SELECT machine_id, COUNT(*) FROM hashes GROUP BY machine_id")
                for row in cursor:
                    machine_id = row[0] if row[0] else '未标识'
                    count = row[1]
                    machine_counts[machine_id] = count
                    total_records += count
            except Exception as e:
                if logger:
                    logger.warning(f"[机台统计] 查询失败: {e}")
        elif self.use_lmdb:
            try:
                machine_counts = self.lmdb_backend.get_machine_stats()
                total_records = sum(machine_counts.values())
            except Exception as e:
                if logger:
                    logger.warning(f"[机台统计] LMDB查询失败: {e}")
        else:
            for meta in self.seen_hashes.values():
                machine_id = meta.get('machine_id', '未标识')
                machine_counts[machine_id] = machine_counts.get(machine_id, 0) + 1
            total_records = len(self.seen_hashes)
        
        result = {
            'machine_counts': machine_counts,
            'total_records': total_records,
            'machine_count': len(machine_counts)
        }
        self._machine_stats_cache = result
        self._machine_stats_cache_time = now
        return result

    def close(self):
        if self.use_db:
            try:
                self._db.execute("PRAGMA analysis_limit=4000")
                self._db.execute("PRAGMA optimize")
                self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._db.commit()
                self._db.close()
            except Exception:
                pass
        elif self.use_lmdb:
            try:
                self.lmdb_backend.close()
            except Exception:
                pass

    def cleanup_oldest(self, count: int, logger=None) -> int:
        deleted = 0
        with self._lock:
            if self.use_db:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    self._db.execute("DELETE FROM _batch_cleanup")
                    self._db.execute(
                        "INSERT INTO _batch_cleanup SELECT hash FROM hashes ORDER BY timestamp LIMIT ?",
                        (count,)
                    )
                    cursor = self._db.execute(
                        "DELETE FROM hashes WHERE hash IN (SELECT hash FROM _batch_cleanup)"
                    )
                    deleted = cursor.rowcount
                    self._deleted_count += deleted
                    self._db.execute("DELETE FROM _batch_cleanup")
                    self._db.commit()
                    self._stats_cache = None
                    self._machine_stats_cache = None
                except Exception:
                    self._db.rollback()
                    raise
            elif self.use_lmdb:
                try:
                    deleted = self.lmdb_backend.cleanup_oldest(count)
                    self._deleted_count += deleted
                    self._stats_cache = None
                    self._machine_stats_cache = None
                except Exception:
                    raise
            else:
                sorted_keys = sorted(self.seen_hashes.keys())
                delete_count = min(count, len(sorted_keys))
                for i in range(delete_count):
                    self.seen_hashes.pop(sorted_keys[i], None)
                deleted = delete_count
                self._deleted_count += deleted
                self._stats_cache = None
                self._machine_stats_cache = None
        
        if logger and deleted > 0:
            logger.info(f"[清理] 成功删除 {deleted:,} 条最早数据")
        return deleted

    def get_eviction_count(self) -> int:
        if self.use_lmdb:
            try:
                return self.lmdb_backend.get_stats().get('total_records', 0)
            except Exception:
                return 0
        if self.use_db:
            try:
                return self._db.execute("SELECT COUNT(*) FROM hashes").fetchone()[0]
            except Exception:
                return 0
        return len(self.seen_hashes)

    def get_deleted_count(self) -> int:
        return self._deleted_count

    def get_disk_usage_bytes(self) -> int:
        total = 0
        if os.path.exists(self.index_dir):
            for root, dirs, files in os.walk(self.index_dir):
                for f_name in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f_name))
                    except OSError:
                        pass
        return total


app = Flask(__name__)
hash_index = None
index_dir = None
active_connections = 0
connections_lock = threading.Lock()
health_monitor = None
_server_config = None
_server_running = True  # 控制后台定时线程，服务关闭时设为 False 避免访问已关闭的 hash_index

_rate_limit_store: Dict[str, dict] = {}
_rate_limit_lock = threading.Lock()
_history_lock = threading.Lock()
_rate_limit_enabled = True
_rate_limit_max = 120
_rate_limit_window = 60
_rate_limit_block_duration = 60
_csrf_token = secrets.token_hex(32)
_api_token = secrets.token_hex(16)
_api_token_enabled = False
_last_csrf_rotation = time.time()
_csrf_rotation_interval = 3600
_csrf_rotation_lock = threading.Lock()


def _sanitize_error(e: Exception) -> str:
    """脱敏错误信息，不暴露内部路径和异常详情"""
    err_str = str(e)
    if 'no such table' in err_str.lower() or 'database' in err_str.lower():
        return '数据库内部错误'
    if 'permission denied' in err_str.lower() or 'access' in err_str.lower():
        return '文件访问权限错误'
    if 'no space left' in err_str.lower():
        return '磁盘空间不足'
    if len(err_str) > 100:
        return '服务内部错误，请查看日志'
    return err_str


def _check_rate_limit(ip: str) -> bool:
    if not _rate_limit_enabled:
        return True
    now = time.time()
    with _rate_limit_lock:
        entry = _rate_limit_store.get(ip)
        if entry is None:
            entry = {'timestamps': deque(), 'blocked_until': 0}
            _rate_limit_store[ip] = entry
        if now < entry['blocked_until']:
            return False
        while entry['timestamps'] and now - entry['timestamps'][0] > _rate_limit_window:
            entry['timestamps'].popleft()
        if len(entry['timestamps']) >= _rate_limit_max:
            entry['blocked_until'] = now + _rate_limit_block_duration
            return False
        entry['timestamps'].append(now)
        return True


def _cleanup_rate_limit():
    now = time.time()
    with _rate_limit_lock:
        expired = []
        for ip, e in list(_rate_limit_store.items()):
            blocked_expired = now > e['blocked_until'] + 300
            ts = list(e['timestamps'])
            no_recent = not ts or now - ts[-1] > 600
            if blocked_expired and no_recent:
                expired.append(ip)
        for ip in expired:
            del _rate_limit_store[ip]


def _send_event_email(subject: str, body: str, config: dict = None, log=None):
    try:
        from notifier import email_notification
        if config is None:
            config = {}
        email_config = {
            'smtp_server': config.get('smtp_server', ''),
            'smtp_port': config.get('smtp_port', 587),
            'smtp_username': config.get('smtp_username', ''),
            'smtp_password': config.get('smtp_password', ''),
            'from_addr': config.get('from_addr', ''),
            'to_addrs': config.get('to_addrs', '')
        }
        if not email_config['smtp_server'] or not email_config['to_addrs']:
            if log:
                log.info(f"[事件通知] 邮件配置不完整，跳过发送: {subject}")
            return False
        full_body = f"========================================\n"
        full_body += f"          TXT查重工具 - 事件通知\n"
        full_body += f"========================================\n\n"
        full_body += f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        full_body += f"{body}\n\n"
        full_body += f"========================================\n"
        full_body += f"        TXT查重工具自动发送\n"
        full_body += f"========================================"
        success = email_notification(email_config, subject, full_body)
        if log:
            if success:
                log.info(f"[事件通知] 邮件发送成功: {subject}")
            else:
                log.warning(f"[事件通知] 邮件发送失败: {subject}")
        return success
    except Exception as e:
        if log:
            log.warning(f"[事件通知] 异常: {str(e)}")
        return False


@app.before_request
def before_request():
    global active_connections, _csrf_token, _last_csrf_rotation

    # CSRF token 定时轮转（原子操作）
    now = time.time()
    if now - _last_csrf_rotation > _csrf_rotation_interval:
        with _csrf_rotation_lock:
            if now - _last_csrf_rotation > _csrf_rotation_interval:
                _csrf_token = secrets.token_hex(32)
                _last_csrf_rotation = now

    with connections_lock:
        active_connections += 1
        if health_monitor:
            health_monitor.set_connection_count(active_connections)
    request.start_time = time.time()
    request.client_ip = request.remote_addr

    # API Token 认证（跳过健康检查和看板页面）
    if _api_token_enabled:
        skip_auth_endpoints = ('/api/health', '/', '/api/stats')
        if not any(request.path.startswith(ep) for ep in skip_auth_endpoints):
            token = request.headers.get('X-API-Token', '') or request.args.get('token', '')
            if not secrets.compare_digest(token, _api_token):
                return jsonify({'error': '认证失败，请提供有效的 API Token'}), 401
    
    # 自动解压 gzip 压缩的请求体（客户端发送原始文本时可压缩 5-10x）
    if request.content_encoding and request.content_encoding in ('gzip', 'x-gzip'):
        try:
            import gzip
            request._cached_data = gzip.decompress(request.get_data())
        except Exception as e:
            logger.warning(f"[gzip] 解压失败: {str(e)} | IP: {request.remote_addr}")
            from flask import jsonify
            return jsonify({'error': 'gzip解压失败，请确认请求体为有效的gzip数据'}), 400
    
    if not _check_rate_limit(request.client_ip):
        logger.warning(f"[频率限制] IP {request.client_ip} 请求过于频繁，已临时封禁")
        from flask import jsonify
        return jsonify({'error': '请求过于频繁，请稍后再试', 'retry_after': 60}), 429


@app.teardown_request
def teardown_request(exception=None):
    global active_connections
    with connections_lock:
        active_connections = max(0, active_connections - 1)
        if health_monitor:
            health_monitor.set_connection_count(active_connections)


@app.after_request
def after_request(response):
    logger.request_count += 1
    
    if response.status_code >= 400:
        client_ip = getattr(request, 'client_ip', request.remote_addr or 'unknown')
        endpoint = request.endpoint or request.path
        method = request.method
        duration_ms = (time.time() - request.start_time) * 1000
        logger.warning(f"[{response.status_code}] {method} {endpoint} | IP:{client_ip} | {duration_ms:.1f}ms")
    
    return response


@app.route('/api/check', methods=['POST'])
def check_hash():
    start_time = time.time()
    try:
        data = request.get_json()
        if not data or 'hashes' not in data:
            logger.warning(f"[参数错误] 缺少hashes参数 | IP: {request.client_ip}")
            return jsonify({'error': '缺少hashes参数'}), 400
        
        hashes = data['hashes']
        if not isinstance(hashes, list):
            logger.warning(f"[参数错误] hashes必须是列表 | IP: {request.client_ip}")
            return jsonify({'error': 'hashes必须是列表'}), 400
        
        hashes = [h for h in hashes if isinstance(h, str) and h.strip()]
        if not hashes:
            return jsonify({'error': '哈希值列表为空'}), 400
        
        if len(hashes) > 10000:
            logger.warning(f"[参数错误] 哈希数量超过限制: {len(hashes)} | IP: {request.client_ip}")
            return jsonify({'error': '单次最多处理10000个哈希值'}), 400
        
        check_only = data.get('check_only', False)
        filename = data.get('filename', '')
        file_path = data.get('file_path', '')
        machine_id = data.get('machine_id', '')
        import datetime
        timestamp = data.get('timestamp', datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        
        metadata = {
            'filename': filename,
            'file_path': file_path,
            'machine_id': machine_id,
            'timestamp': timestamp
        }
        
        if check_only:
            results = hash_index.check_only_batch(hashes)
        else:
            results = hash_index.check_and_add_batch(hashes, metadata)
        
        duplicate_count = sum(results)
        unique_count = len(results) - duplicate_count
        duration_ms = (time.time() - start_time) * 1000
        dup_rate = (duplicate_count / len(results) * 100) if len(results) > 0 else 0
        
        if not check_only:
            log_parts = [f"[查重] 写入 | 总数:{len(hashes):,} | 重复:{duplicate_count:,}({dup_rate:.1f}%) | {duration_ms:.0f}ms"]
            if filename:
                log_parts.append(f"文件:{os.path.basename(filename)}")
            if machine_id:
                log_parts.append(f"机台:{machine_id}")
            log_parts.append(f"IP:{request.client_ip}")
            logger.debug(" | ".join(log_parts))
        else:
            logger.debug(f"[查重] 检查 | 总数:{len(hashes):,} | 重复:{duplicate_count:,}({dup_rate:.1f}%) | {duration_ms:.0f}ms | IP:{request.client_ip}")
        
        duplicate_hashes = [h for h, r in zip(hashes, results) if r]
        
        return jsonify({
            'success': True,
            'results': results,
            'duplicate_count': duplicate_count,
            'unique_count': unique_count,
            'duplicate_hashes': duplicate_hashes[:100]
        })
    
    except Exception as e:
        import traceback
        logger.error(f"[请求错误] /api/check | 异常: {str(e)} | IP: {request.client_ip}")
        logger.error(f"[堆栈跟踪]:\n{traceback.format_exc()}")
        return jsonify({'error': _sanitize_error(e)}), 500


# ========== 服务端哈希计算优化 ==========
# 多线程哈希计算相关变量和函数
_hash_compute_workers = {}
_hash_compute_lock = threading.Lock()
_hash_compute_pool = None
_hash_compute_pool_lock = threading.Lock()
if PSUTIL_AVAILABLE:
    _server_cpu_count = psutil.cpu_count(logical=True) or 4
    _server_physical_cores = psutil.cpu_count(logical=False) or _server_cpu_count
else:
    _server_cpu_count = 4
    _server_physical_cores = 4


def _get_hash_compute_pool(workers: int = None):
    """获取持久的哈希计算线程池（复用，避免每次创建）"""
    global _hash_compute_pool
    if workers is None:
        workers = max(2, _server_physical_cores)
    with _hash_compute_pool_lock:
        if _hash_compute_pool is None:
            _hash_compute_pool = ThreadPoolExecutor(max_workers=workers)
        return _hash_compute_pool


def _compute_hash(line: str) -> str:
    """计算单行文本的哈希值"""
    if HAS_XXHASH:
        return xxhash.xxh64(line.encode('utf-8')).hexdigest()
    else:
        return hashlib.sha256(line.encode('utf-8')).hexdigest()


def _compute_hashes_parallel(lines: List[str], workers: int = None) -> tuple:
    """多线程并行计算哈希（批量提交，大幅减少线程池开销）
    Returns: (hashes: List[str], valid_indices: List[int])"""
    if workers is None:
        workers = max(2, _server_physical_cores)
    
    # 一次遍历完成：过滤空行 + 记录有效行索引 + rstrip
    valid_lines = []
    valid_indices = []
    for i, line in enumerate(lines):
        stripped = line.rstrip('\r\n')
        if stripped.strip():
            valid_lines.append(stripped)
            valid_indices.append(i)
    
    if not valid_lines:
        return [], []
    
    # 小数据量直接计算
    if len(valid_lines) < 1000:
        return [_compute_hash(line) for line in valid_lines], valid_indices
    
    # 批量提交：每批至少500条，避免数百万次线程池提交
    batch_size = max(500, len(valid_lines) // (workers * 4))
    chunks = [valid_lines[i:i + batch_size] for i in range(0, len(valid_lines), batch_size)]
    
    results = [None] * len(valid_lines)
    pool = _get_hash_compute_pool(workers)
    
    def _compute_batch(chunk: List[str], start_idx: int):
        return [(start_idx + j, _compute_hash(line)) for j, line in enumerate(chunk)]
    
    futures = {pool.submit(_compute_batch, chunk, i * batch_size): i for i, chunk in enumerate(chunks)}
    for future in as_completed(futures):
        for idx, h in future.result():
            results[idx] = h
    
    return results, valid_indices


@app.route('/api/dedup_file', methods=['POST'])
def dedup_file():
    """
    客户端上传文件数据，服务端负责哈希计算和查重
    这样可以让服务端的多核CPU充分发挥硬件查重能力
    """
    start_time = time.time()
    try:
        data = request.get_json()
        if not data or 'lines' not in data:
            logger.warning(f"[参数错误] 缺少lines参数 | IP: {request.client_ip}")
            return jsonify({'error': '缺少lines参数'}), 400
        
        lines = data['lines']
        if not isinstance(lines, list):
            logger.warning(f"[参数错误] lines必须是列表 | IP: {request.client_ip}")
            return jsonify({'error': 'lines必须是列表'}), 400
        
        lines = [l for l in lines if isinstance(l, str)]
        if not lines:
            return jsonify({'error': '数据行列表为空'}), 400
        
        if len(lines) > 2000000:
            logger.warning(f"[参数错误] 行数超过限制: {len(lines)} | IP: {request.client_ip}")
            return jsonify({'error': '单次最多处理2000000行'}), 400
        
        check_only = data.get('check_only', False)
        filename = data.get('filename', '')
        file_path = data.get('file_path', '')
        machine_id = data.get('machine_id', '')
        
        # 服务端计算哈希（多线程），同时获取有效行索引（避免二次遍历）
        hash_start = time.time()
        hashes, valid_indices = _compute_hashes_parallel(lines)
        hash_duration = (time.time() - hash_start) * 1000
        
        if not hashes:
            return jsonify({
                'success': True,
                'total': 0,
                'duplicate_count': 0,
                'unique_count': 0,
                'duplicate_line_indices': [],
                'unique_line_indices': [],
                'server_hash_ms': hash_duration,
                'server_check_ms': 0
            })
        
        # 调用现有的查重接口
        check_start = time.time()
        if check_only:
            results = hash_index.check_only_batch(hashes)
        else:
            import datetime as dt
            metadata = {
                'filename': filename,
                'file_path': file_path,
                'machine_id': machine_id,
                'timestamp': dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            results = hash_index.check_and_add_batch(hashes, metadata)
        check_duration = (time.time() - check_start) * 1000
        
        total = len(hashes)
        duplicate_count = sum(results)
        unique_count = total - duplicate_count
        
        # 直接使用 valid_indices（已在 _compute_hashes_parallel 中计算，无需重复遍历）
        duplicate_line_indices = [valid_indices[i] for i, r in enumerate(results) if r]
        unique_line_indices = [valid_indices[i] for i, r in enumerate(results) if not r]
        
        total_duration_ms = (time.time() - start_time) * 1000
        dup_rate = (duplicate_count / total * 100) if total > 0 else 0
        
        log_msg = f"[服务端查重] 总数:{total:,} | 重复:{duplicate_count:,}({dup_rate:.1f}%) | 哈希耗时:{hash_duration:.0f}ms | 查重耗时:{check_duration:.0f}ms | 总耗时:{total_duration_ms:.0f}ms"
        if filename:
            log_msg += f" | 文件:{os.path.basename(filename)}"
        if machine_id:
            log_msg += f" | 机台:{machine_id}"
        log_msg += f" | IP:{request.client_ip}"
        logger.info(log_msg)
        
        # 提取重复哈希（样本，最多200个），用于客户端追溯来源
        duplicate_hash_samples = []
        for h, r in zip(hashes, results):
            if r:
                duplicate_hash_samples.append(h)
                if len(duplicate_hash_samples) >= 200:
                    break
        
        # 大文件处理后主动释放临时对象，避免内存持续累积
        del lines, hashes, valid_indices, results
        
        return jsonify({
            'success': True,
            'total': total,
            'duplicate_count': duplicate_count,
            'unique_count': unique_count,
            'duplicate_line_indices': duplicate_line_indices,
            'unique_line_indices': unique_line_indices,
            'duplicate_hashes': duplicate_hash_samples,
            'server_hash_ms': hash_duration,
            'server_check_ms': check_duration,
            'total_ms': total_duration_ms
        })
    
    except Exception as e:
        import traceback
        logger.error(f"[请求错误] /api/dedup_file | 异常: {str(e)} | IP: {request.client_ip}")
        logger.error(f"[堆栈跟踪]:\n{traceback.format_exc()}")
        return jsonify({'error': _sanitize_error(e)}), 500


@app.route('/api/check_single', methods=['GET'])
def check_single():
    try:
        hash_val = request.args.get('hash')
        if not hash_val:
            logger.warning(f"[参数错误] 缺少hash参数 | IP: {request.client_ip}")
            return jsonify({'error': '缺少hash参数'}), 400
        
        check_only = request.args.get('check_only', 'false').lower() == 'true'
        
        if check_only:
            results = hash_index.check_only_batch([hash_val])
            is_duplicate = results[0] if results else False
        else:
            results = hash_index.check_and_add_batch([hash_val], {
                'filename': request.args.get('filename', ''),
                'machine_id': request.args.get('machine_id', ''),
                'timestamp': request.args.get('timestamp', '')
            })
            is_duplicate = results[0] if results else False
        
        return jsonify({
            'success': True,
            'is_duplicate': is_duplicate
        })
    
    except Exception as e:
        logger.error(f"[请求错误] /api/check_single | 异常: {str(e)} | IP: {request.client_ip}")
        return jsonify({'error': _sanitize_error(e)}), 500


@app.route('/api/stats', methods=['GET'])
def get_stats():
    try:
        stats = hash_index.get_stats()
        stats.update(logger.get_stats())
        logger.debug(f"[统计查询] IP: {request.client_ip} | 记录数: {stats.get('total_records', 0)}")
        return jsonify({
            'success': True,
            'data': stats
        })
    except Exception as e:
        logger.error(f"[请求错误] /api/stats | 异常: {str(e)} | IP: {request.client_ip}")
        return jsonify({'error': _sanitize_error(e)}), 500


@app.route('/api/commit', methods=['POST'])
def commit_index():
    try:
        logger.info(f"[索引保存] 开始保存索引 | IP: {request.client_ip}")
        hash_index.commit()
        logger.info(f"[索引保存] 索引保存完成 | IP: {request.client_ip}")
        return jsonify({
            'success': True,
            'message': '索引已保存'
        })
    except Exception as e:
        logger.error(f"[请求错误] /api/commit | 异常: {str(e)} | IP: {request.client_ip}")
        return jsonify({'error': _sanitize_error(e)}), 500


@app.route('/api/complete', methods=['POST'])
def file_complete():
    try:
        data = request.get_json()
        if not data:
            logger.warning(f"[参数错误] 缺少请求数据 | IP: {request.client_ip}")
            return jsonify({'error': '缺少请求数据'}), 400
        
        filename = data.get('filename', '')
        total = data.get('total', 0)
        duplicate = data.get('duplicate', 0)
        unique = data.get('unique', 0)
        duplicate_rate = data.get('duplicate_rate', 0.0)
        duration_ms = data.get('duration_ms', 0)
        machine_id = data.get('machine_id', '')
        
        if not filename:
            logger.warning(f"[参数错误] 缺少filename参数 | IP: {request.client_ip}")
            return jsonify({'error': '缺少filename参数'}), 400
        
        log_msg = f"[文件完成] 文件: {filename}"
        if machine_id:
            log_msg += f" | 机台: {machine_id}"
        log_msg += f" | 总数: {total:,} | 重复: {duplicate:,} | 唯一: {unique:,} | 重复率: {duplicate_rate:.2f}% | 耗时: {duration_ms:.0f}ms"
        logger.info(log_msg)
        
        if health_monitor:
            disk_info = health_monitor.check_and_cleanup_if_full()
            if disk_info:
                logger.info(f"[磁盘检查] 使用率: {disk_info['used_percent']:.1f}% | 已用: {disk_info['used_gb']:.1f}GB / {disk_info['total_gb']:.1f}GB")
        
        return jsonify({
            'success': True,
            'message': '处理完成'
        })
    except Exception as e:
        logger.error(f"[请求错误] /api/complete | 异常: {str(e)} | IP: {request.client_ip}")
        return jsonify({'error': _sanitize_error(e)}), 500


@app.route('/api/query_sources', methods=['POST'])
def query_sources():
    """查询哈希值的来源信息"""
    try:
        data = request.get_json()
        if not data or 'hashes' not in data:
            logger.warning(f"[参数错误] 缺少hashes参数 | IP: {request.client_ip}")
            return jsonify({'error': '缺少hashes参数'}), 400
        
        hashes = data['hashes']
        if not isinstance(hashes, list):
            logger.warning(f"[参数错误] hashes必须是列表 | IP: {request.client_ip}")
            return jsonify({'error': 'hashes必须是列表'}), 400
        
        if len(hashes) > 1000:
            logger.warning(f"[参数错误] 哈希数量超过限制: {len(hashes)} | IP: {request.client_ip}")
            return jsonify({'error': '单次最多查询1000个哈希值'}), 400
        
        sources = hash_index.get_duplicate_sources(hashes)
        
        found_count = len(sources)
        logger.info(f"[来源查询] 查询 {len(hashes)} 个哈希值 | 找到 {found_count} 条来源信息")
        if found_count > 0:
            sample_hash = list(sources.keys())[0]
            sample_source = sources[sample_hash]
            logger.debug(f"[来源查询] 示例: hash={sample_hash[:16]}... filename={sample_source.get('filename', '未知')} timestamp={sample_source.get('timestamp', '未知')}")
        
        return jsonify({
            'success': True,
            'sources': sources
        })
    except Exception as e:
        logger.error(f"[请求错误] /api/query_sources | 异常: {str(e)} | IP: {request.client_ip}")
        return jsonify({'error': _sanitize_error(e)}), 500


@app.route('/api/query_machine_compare', methods=['POST'])
def query_machine_compare():
    """查询机台对比信息 - 找出重复数据在哪些机台出现过"""
    try:
        data = request.get_json()
        if not data or 'hashes' not in data:
            logger.warning(f"[参数错误] 缺少hashes参数 | IP: {request.client_ip}")
            return jsonify({'error': '缺少hashes参数'}), 400
        
        hashes = data['hashes']
        if not isinstance(hashes, list):
            logger.warning(f"[参数错误] hashes必须是列表 | IP: {request.client_ip}")
            return jsonify({'error': 'hashes必须是列表'}), 400
        
        if len(hashes) > 1000:
            logger.warning(f"[参数错误] 哈希数量超过限制: {len(hashes)} | IP: {request.client_ip}")
            return jsonify({'error': '单次最多查询1000个哈希值'}), 400
        
        sources = hash_index.get_duplicate_sources(hashes)
        
        machine_compare = []
        machine_counts = {}
        
        for hash_val, source in sources.items():
            machine_id = source.get('machine_id', '未知机台')
            filename = source.get('filename', '未知文件')
            file_path = source.get('file_path', '')
            timestamp = source.get('timestamp', '')
            
            machine_compare.append({
                'hash': hash_val[:16] + '...',
                'machine_id': machine_id,
                'filename': filename,
                'file_path': file_path,
                'timestamp': timestamp,
                'content_preview': hash_val[:32]
            })
            
            machine_counts[machine_id] = machine_counts.get(machine_id, 0) + 1
        
        result = {
            'success': True,
            'duplicate_count': len(machine_compare),
            'machine_counts': machine_counts,
            'machine_list': list(machine_counts.keys()),
            'details': machine_compare[:100]
        }
        
        logger.info(f"[机台对比] 查询 {len(hashes)} 个哈希值 | 找到 {len(machine_compare)} 条重复记录 | 涉及 {len(machine_counts)} 个机台")
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"[请求错误] /api/query_machine_compare | 异常: {str(e)} | IP: {request.client_ip}")
        return jsonify({'error': _sanitize_error(e)}), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    logger.debug(f"[健康检查] IP: {request.client_ip}")
    return jsonify({
        'success': True,
        'service': 'txt-dedup-server',
        'version': VERSION,
        'status': 'running',
        'build_time': BUILD_TIME,
        'storage_type': hash_index.storage_type.upper() if hash_index else 'Unknown'
    })


@app.route('/api/health/detailed', methods=['GET'])
def health_detailed():
    result = {
        'success': True,
        'service': 'txt-dedup-server',
        'version': VERSION,
        'status': 'running',
        'build_time': BUILD_TIME,
        'storage_type': hash_index.storage_type.upper() if hash_index else 'Unknown',
        'active_connections': active_connections,
        'uptime_seconds': time.time() - getattr(app, 'start_time', time.time())
    }
    
    if health_monitor:
        report = health_monitor.get_health_report()
        result['memory'] = report.get('memory', {})
        result['disk'] = report.get('disk', {})
    
    if hash_index:
        result['db'] = hash_index.get_detailed_stats()
    
    return jsonify(result)


@app.route('/api/cleanup', methods=['POST'])
def manual_cleanup():
    data = request.get_json() or {}
    
    token = data.get('csrf_token', '')
    if not secrets.compare_digest(token, _csrf_token):
        return jsonify({'success': False, 'error': 'CSRF token 无效'}), 403
    
    count = data.get('count', 100000)
    
    if not hash_index:
        return jsonify({'success': False, 'error': '索引未初始化'}), 500
    
    try:
        deleted = hash_index.cleanup_oldest(count, logger)
        logger.info(f"[手动清理] 删除 {deleted} 条最早数据，操作者: {request.client_ip}")
        return jsonify({
            'success': True,
            'deleted': deleted,
            'eviction_queue_remaining': hash_index.get_eviction_count()
        })
    except Exception as e:
        logger.error(f"[手动清理] 清理失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cleanup/stats', methods=['GET'])
def cleanup_stats():
    if not hash_index:
        return jsonify({'success': False, 'error': '索引未初始化'}), 500
    
    return jsonify({
        'success': True,
        'eviction_queue_size': hash_index.get_eviction_count(),
        'deleted_count': hash_index.get_deleted_count(),
        'disk_usage_bytes': hash_index.get_disk_usage_bytes(),
        'disk_usage_gb': round(hash_index.get_disk_usage_bytes() / (1024**3), 2)
    })


@app.route('/api/trends', methods=['GET'])
def get_trends():
    trends_file = os.path.join(index_dir, 'trends.json') if index_dir else None
    if not trends_file or not os.path.exists(trends_file):
        return jsonify({'success': True, 'trends': []})
    try:
        with open(trends_file, 'r', encoding='utf-8') as f:
            trends = json.load(f)
    except Exception:
        trends = []
    return jsonify({'success': True, 'trends': trends})


@app.route('/api/backup', methods=['POST'])
def backup_database():
    """备份数据库到指定路径"""
    try:
        data = request.get_json() or {}
        backup_dir = data.get('backup_dir', '')
        if not backup_dir:
            backup_dir = os.path.join(index_dir, 'backups')
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        if hash_index.use_db:
            import shutil
            src = hash_index.db_path
            dst = os.path.join(backup_dir, f'hash_index_{timestamp}.db')
            shutil.copy2(src, dst)
            for suffix in ('-wal', '-shm'):
                sidecar = src + suffix
                if os.path.exists(sidecar):
                    shutil.copy2(sidecar, dst + suffix)
            size_mb = round(os.path.getsize(dst) / (1024 * 1024), 1)
            logger.info(f"[备份] SQLite 数据库已备份: {dst} ({size_mb}MB)")
            return jsonify({'success': True, 'path': dst, 'size_mb': size_mb})
        elif hash_index.use_lmdb:
            import shutil
            dst = os.path.join(backup_dir, f'lmdb_{timestamp}')
            os.makedirs(dst, exist_ok=True)
            for item in os.listdir(hash_index.index_dir):
                if item == 'backups':
                    continue
                src_path = os.path.join(hash_index.index_dir, item)
                dst_path = os.path.join(dst, item)
                if os.path.isdir(src_path):
                    shutil.copytree(src_path, dst_path)
                else:
                    shutil.copy2(src_path, dst_path)
            size_mb = round(sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(dst) for f in fs) / (1024 * 1024), 1)
            logger.info(f"[备份] LMDB 数据已备份: {dst} ({size_mb}MB)")
            return jsonify({'success': True, 'path': dst, 'size_mb': size_mb})
        else:
            return jsonify({'success': False, 'error': '内存模式不支持备份'}), 400
    except Exception as e:
        logger.error(f"[备份] 备份失败: {e}")
        return jsonify({'success': False, 'error': _sanitize_error(e)}), 500


@app.route('/api/history', methods=['GET'])
def get_dedup_history():
    """获取查重历史记录"""
    history_file = os.path.join(index_dir, 'dedup_history.json') if index_dir else None
    if not history_file or not os.path.exists(history_file):
        return jsonify({'success': True, 'history': []})
    try:
        with open(history_file, 'r', encoding='utf-8') as f:
            history = json.load(f)
        try:
            limit = int(request.args.get('limit', 100))
        except (ValueError, TypeError):
            limit = 100
        return jsonify({'success': True, 'history': history[-limit:]})
    except Exception:
        return jsonify({'success': True, 'history': []})


@app.route('/api/history', methods=['POST'])
def add_dedup_history():
    """添加查重历史记录"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': '缺少请求数据'}), 400

        history_file = os.path.join(index_dir, 'dedup_history.json') if index_dir else None
        if not history_file:
            return jsonify({'error': '索引目录未设置'}), 500

        record = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'filename': data.get('filename', ''),
            'file_path': data.get('file_path', ''),
            'machine_id': data.get('machine_id', ''),
            'total': data.get('total', 0),
            'duplicate': data.get('duplicate', 0),
            'unique': data.get('unique', 0),
            'duplicate_rate': data.get('duplicate_rate', 0),
            'duration_ms': data.get('duration_ms', 0)
        }

        with _history_lock:
            history = []
            if os.path.exists(history_file):
                try:
                    with open(history_file, 'r', encoding='utf-8') as f:
                        history = json.load(f)
                except Exception:
                    history = []

            history.append(record)

            if len(history) > 1000:
                history = history[-1000:]

            tmp_file = history_file + '.tmp'
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, history_file)

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"[历史] 记录失败: {e}")
        return jsonify({'error': _sanitize_error(e)}), 500


@app.route('/api/logs', methods=['GET'])
def get_recent_logs():
    """获取最近的日志"""
    try:
        try:
            limit = int(request.args.get('limit', 50))
        except (ValueError, TypeError):
            limit = 50
        log_dir_path = os.path.join(os.path.dirname(index_dir) if index_dir else '.', 'logs')
        if not os.path.exists(log_dir_path):
            return jsonify({'success': True, 'logs': []})

        log_files = sorted([f for f in os.listdir(log_dir_path) if f.endswith('.log')], reverse=True)
        if not log_files:
            return jsonify({'success': True, 'logs': []})

        latest_log = os.path.join(log_dir_path, log_files[0])
        lines = []
        with open(latest_log, 'r', encoding='utf-8', errors='ignore') as f:
            all_lines = f.readlines()
            lines = [l.rstrip() for l in all_lines[-limit:]]

        return jsonify({'success': True, 'logs': lines, 'file': log_files[0]})
    except Exception as e:
        return jsonify({'success': False, 'error': _sanitize_error(e)}), 500


@app.route('/api/rate_limit/stats', methods=['GET'])
def rate_limit_stats():
    now = time.time()
    with _rate_limit_lock:
        blocked = {}
        active = {}
        for ip, entry in _rate_limit_store.items():
            if now < entry['blocked_until']:
                blocked[ip] = round(entry['blocked_until'] - now, 1)
            if entry['timestamps']:
                active[ip] = len(entry['timestamps'])
    return jsonify({
        'success': True,
        'enabled': _rate_limit_enabled,
        'max_per_minute': _rate_limit_max,
        'blocked_ips': blocked,
        'active_ips': dict(list(active.items())[:100])
    })


@app.route('/api/machine_stats', methods=['GET'])
def machine_stats():
    """获取机台统计信息"""
    try:
        stats = hash_index.get_machine_stats() if hash_index else {'machine_counts': {}, 'total_records': 0}
        
        logger.debug(f"[机台统计] 获取机台统计 | 机台数: {len(stats.get('machine_counts', {}))}")
        return jsonify({
            'success': True,
            'data': stats
        })
    except Exception as e:
        logger.error(f"[请求错误] /api/machine_stats | 异常: {str(e)}")
        return jsonify({'error': _sanitize_error(e)}), 500


@app.route('/', methods=['GET'])
def dashboard():
    dash_enabled = True
    refresh = 30
    try:
        dash_enabled = _server_config.get_bool('Dashboard', 'enabled', True)
        refresh = _server_config.get_int('Dashboard', 'refresh_interval', 30)
    except Exception:
        pass
    
    if not dash_enabled:
        return jsonify({'error': 'Web管理看板未启用，请在配置文件中设置 Dashboard.enabled = true'}), 403
    
    from flask import render_template_string
    return render_template_string(DASHBOARD_HTML, refresh=refresh * 1000, csrf_token=_csrf_token)


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TXT查重工具 - 管理看板</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0b0f19;--card:#111827;--border:#1f2937;--accent:#6366f1;--accent2:#8b5cf6;--green:#10b981;--red:#ef4444;--yellow:#f59e0b;--blue:#3b82f6;--text:#f3f4f6;--text2:#9ca3af;--text3:#6b7280}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;background-image:radial-gradient(ellipse at top,#1a1a2e 0%,transparent 50%)}
.header{background:rgba(17,24,39,.8);backdrop-filter:blur(20px);border-bottom:1px solid var(--border);padding:16px 32px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100}
.header h1{font-size:20px;font-weight:600;background:linear-gradient(135deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;display:flex;align-items:center;gap:12px}
.header h1 span{font-size:12px;font-weight:500;-webkit-text-fill-color:var(--text2);background:var(--border);padding:3px 10px;border-radius:20px}
.header .status{display:flex;align-items:center;gap:16px}
.header .status .dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 12px var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.6;transform:scale(.9)}}
.header .uptime,.header .refresh-info{font-size:12px;color:var(--text3)}
.container{max-width:1440px;margin:0 auto;padding:24px 32px}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
@media(max-width:1200px){.cards{grid-template-columns:repeat(2,1fr)}}
@media(max-width:600px){.cards{grid-template-columns:1fr}}
.card{background:var(--card);border-radius:16px;padding:20px;border:1px solid var(--border);transition:all .3s;position:relative;overflow:hidden}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--accent),var(--accent2));opacity:0;transition:opacity .3s}
.card:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:0 8px 32px rgba(99,102,241,.1)}
.card:hover::before{opacity:1}
.card .icon{font-size:24px;margin-bottom:12px}
.card .label{font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;font-weight:500}
.card .value{font-size:28px;font-weight:700;color:var(--text);line-height:1}
.card .sub{font-size:11px;color:var(--text3);margin-top:8px}
.card.warn{border-color:var(--red)}
.card.warn .value{color:var(--red)}
.card.warn::before{background:var(--red)}
.section{margin-bottom:32px}
.section h2{font-size:14px;font-weight:600;color:var(--text2);margin-bottom:16px;display:flex;align-items:center;gap:10px;text-transform:uppercase;letter-spacing:.5px}
.section h2::after{content:'';flex:1;height:1px;background:var(--border)}
.panels{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.panels{grid-template-columns:1fr}}
.panel{background:var(--card);border-radius:16px;padding:20px;border:1px solid var(--border)}
.panel h3{font-size:13px;font-weight:500;color:var(--text2);margin-bottom:16px}
.progress{height:8px;background:var(--border);border-radius:8px;overflow:hidden;margin-bottom:12px}
.progress .fill{height:100%;border-radius:8px;transition:width .8s cubic-bezier(.4,0,.2,1)}
.progress .fill.blue{background:linear-gradient(90deg,var(--blue),var(--accent))}
.progress .fill.green{background:linear-gradient(90deg,#059669,var(--green))}
.progress .fill.yellow{background:linear-gradient(90deg,#d97706,var(--yellow))}
.progress .fill.red{background:linear-gradient(90deg,#dc2626,var(--red))}
.progress-info{display:flex;justify-content:space-between;font-size:12px;color:var(--text3)}
.progress-info .pct{font-weight:600;color:var(--text)}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:11px;font-weight:600;color:var(--text3);padding:10px 12px;border-bottom:1px solid var(--border);text-transform:uppercase;letter-spacing:.5px}
td{font-size:13px;padding:10px 12px;border-bottom:1px solid rgba(31,41,55,.5);color:var(--text)}
tr:hover td{background:rgba(99,102,241,.05)}
.btn{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;border:none;padding:10px 20px;border-radius:10px;cursor:pointer;font-size:13px;font-weight:500;transition:all .2s;box-shadow:0 4px 12px rgba(99,102,241,.3)}
.btn:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(99,102,241,.4)}
.btn:active{transform:translateY(0)}
.btn.danger{background:linear-gradient(135deg,#dc2626,#ef4444);box-shadow:0 4px 12px rgba(239,68,68,.3)}
.btn.danger:hover{box-shadow:0 6px 20px rgba(239,68,68,.4)}
.cleanup-form{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.cleanup-form input{background:var(--border);border:1px solid transparent;color:var(--text);padding:10px 14px;border-radius:10px;font-size:13px;width:140px;transition:border-color .2s}
.cleanup-form input:focus{outline:none;border-color:var(--accent)}
.cleanup-result{margin-top:12px;font-size:13px;padding:12px 16px;border-radius:10px;display:none;border:1px solid}
.cleanup-result.success{background:rgba(16,185,129,.1);color:var(--green);border-color:rgba(16,185,129,.2);display:block}
.cleanup-result.error{background:rgba(239,68,68,.1);color:var(--red);border-color:rgba(239,68,68,.2);display:block}
.log-viewer{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:16px;max-height:320px;overflow-y:auto;font-family:'JetBrains Mono','Fira Code','Consolas',monospace;font-size:12px;line-height:1.8}
.log-viewer::-webkit-scrollbar{width:6px}
.log-viewer::-webkit-scrollbar-track{background:transparent}
.log-viewer::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
.trend-chart{display:flex;align-items:flex-end;gap:6px;height:140px;padding:0 4px}
.trend-bar{flex:1;display:flex;flex-direction:column;align-items:center;gap:6px}
.trend-bar .bar-val{background:linear-gradient(180deg,var(--accent),rgba(99,102,241,.3));border-radius:4px 4px 0 0;width:100%;min-height:4px;transition:height .5s cubic-bezier(.4,0,.2,1)}
.trend-bar:hover .bar-val{background:linear-gradient(180deg,var(--accent2),rgba(139,92,246,.4))}
.trend-bar .bar-lbl{font-size:10px;color:var(--text3);white-space:nowrap}
.trend-bar .bar-cnt{font-size:11px;color:var(--text2);font-weight:500}
.footer{text-align:center;font-size:11px;color:var(--text3);padding:24px;border-top:1px solid var(--border)}
.empty-state{text-align:center;padding:40px;color:var(--text3);font-size:13px}
.rate-limit-table td.blocked{color:var(--red);font-weight:600}
.rate-limit-table td.active{color:var(--yellow)}
.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:500}
.badge.green{background:rgba(16,185,129,.15);color:var(--green)}
.badge.red{background:rgba(239,68,68,.15);color:var(--red)}
.badge.yellow{background:rgba(245,158,11,.15);color:var(--yellow)}
</style>
</head>
<body>
<div class="header">
<h1>TXT 查重管理看板 <span id="version"></span></h1>
<div class="status">
<span class="dot" id="status-dot"></span>
<span class="uptime" id="uptime-display"></span>
<span class="refresh-info" id="refresh-info"></span>
</div>
</div>

<div class="container">

<div class="cards">
<div class="card"><div class="icon">📦</div><div class="label">哈希记录</div><div class="value" id="card-total">-</div><div class="sub">数据库主表</div></div>
<div class="card"><div class="icon">💽</div><div class="label">数据库大小</div><div class="value" id="card-dbsize">-</div><div class="sub">存储占用</div></div>
<div class="card"><div class="icon">🔗</div><div class="label">当前连接</div><div class="value" id="card-conn">-</div><div class="sub">活跃客户端</div></div>
<div class="card"><div class="icon">🏭</div><div class="label">机台数量</div><div class="value" id="card-machines">-</div><div class="sub">已注册机台</div></div>
</div>

<div class="section">
<h2>系统资源</h2>
<div class="panels">
<div class="panel">
<h3>磁盘空间</h3>
<div class="progress"><div class="fill blue" id="disk-bar" style="width:0%"></div></div>
<div class="progress-info"><span id="disk-used-label">已用: -</span><span class="pct" id="disk-pct">0%</span><span id="disk-free-label">剩余: -</span></div>
</div>
<div class="panel">
<h3>系统内存</h3>
<div class="progress"><div class="fill green" id="mem-bar" style="width:0%"></div></div>
<div class="progress-info"><span id="mem-proc-label">进程: -</span><span class="pct" id="mem-pct">0%</span><span id="mem-avail-label">可用: -</span></div>
</div>
</div>
</div>

<div class="section">
<h2>数据趋势</h2>
<div class="panel">
<div id="trend-empty" class="empty-state">暂无趋势数据</div>
<div id="trend-chart" style="display:none"><div class="trend-chart" id="trend-bars"></div></div>
</div>
</div>

<div class="section">
<h2>数据清理</h2>
<div class="panel">
<div class="cleanup-form">
<input type="number" id="cleanup-count" value="100000" min="1000" step="10000" placeholder="清理数量">
<button class="btn" onclick="doCleanup()">执行清理</button>
<button class="btn danger" onclick="doCleanup(500000)">快速清理50万</button>
</div>
<div class="cleanup-result" id="cleanup-result"></div>
</div>
</div>

<div class="section">
<h2>机台统计</h2>
<div class="panel">
<div id="machine-empty" class="empty-state">暂无机台数据</div>
<div id="machine-chart" style="display:none"><div class="trend-chart" id="machine-bars"></div></div>
<div class="table-wrap" id="machine-table" style="display:none">
<table><thead><tr><th>机台标识</th><th>记录数</th><th>占比</th></tr></thead><tbody id="machine-tbody"></tbody></table>
</div>
</div>
</div>

<div class="section">
<h2>频率限制</h2>
<div class="panel">
<div style="margin-bottom:12px"><span id="rl-status"></span></div>
<div id="rl-empty" class="empty-state" style="padding:16px">当前无被封禁IP</div>
<div class="table-wrap" id="rl-table" style="display:none">
<table class="rate-limit-table"><thead><tr><th>IP地址</th><th>状态</th><th>剩余封禁</th><th>请求数</th></tr></thead><tbody id="rl-tbody"></tbody></table>
</div>
</div>
</div>

<div class="section">
<h2>数据库详情</h2>
<div class="panel">
<div class="table-wrap"><table><tbody id="db-detail"></tbody></table></div>
</div>
</div>

</div>

<div class="footer">
TXT查重工具 v<span id="footer-ver">-</span> · 自动刷新 · <span id="footer-build">-</span>
</div>

<script>
var REFRESH={{ refresh }};var CSRF_TOKEN='{{ csrf_token }}';var lastUpdate=null;
function formatNum(n){if(n===null||n===undefined||n==='-')return'-';if(typeof n==='string')return n;return n.toLocaleString('zh-CN')}
function formatSize(mb){if(mb===null||mb===undefined||mb==='-')return'-';if(typeof mb==='string')return mb;if(mb>=1024)return(mb/1024).toFixed(1)+' GB';return mb.toFixed(1)+' MB'}
function formatSeconds(s){if(s<60)return Math.floor(s)+'秒';if(s<3600)return Math.floor(s/60)+'分'+Math.floor(s%60)+'秒';var h=Math.floor(s/3600),m=Math.floor((s%3600)/60),d=Math.floor(h/24);h=h%24;var t='';if(d>0)t+=d+'天';if(h>0)t+=h+'时';t+=m+'分';return t}

function updateDashboard(){
fetch('/api/health/detailed').then(function(r){return r.json()}).then(function(d){
document.getElementById('version').textContent='v'+(d.version||'-');
document.getElementById('footer-ver').textContent=d.version||'-';
document.getElementById('footer-build').textContent=d.build_time||'-';
document.getElementById('status-dot').style.background=d.status==='running'?'var(--green)':'var(--red)';
document.getElementById('uptime-display').textContent=formatSeconds(d.uptime_seconds||0);
document.getElementById('card-conn').textContent=formatNum(d.active_connections);
var db=d.db||{};
document.getElementById('card-total').textContent=formatNum(db.total_records);
document.getElementById('card-dbsize').textContent=formatSize(db.db_size_mb);
var disk=d.disk||{};
if(disk.total_gb){var p=disk.percent||0;document.getElementById('disk-bar').style.width=p+'%';document.getElementById('disk-bar').className='fill '+(p>90?'red':p>75?'yellow':'blue');document.getElementById('disk-pct').textContent=p.toFixed(1)+'%';document.getElementById('disk-used-label').textContent='已用: '+(disk.used_gb||0).toFixed(1)+' GB';document.getElementById('disk-free-label').textContent='剩余: '+(disk.free_gb||0).toFixed(1)+' GB'}
var mem=d.memory||{};
if(mem.process_mb){var mp=mem.system_percent||0;document.getElementById('mem-bar').style.width=mp+'%';document.getElementById('mem-bar').className='fill '+(mp>90?'red':mp>75?'yellow':'green');document.getElementById('mem-pct').textContent=mp.toFixed(1)+'%';document.getElementById('mem-proc-label').textContent='进程: '+mem.process_mb.toFixed(1)+' MB';document.getElementById('mem-avail-label').textContent='可用: '+(mem.system_available_gb||0).toFixed(1)+' GB'}
var h='';[['存储类型',db.storage_type],['数据库大小',formatSize(db.db_size_mb)],['已清理',formatNum(db.deleted_count)],['页面数',formatNum(db.page_count)]].forEach(function(r){h+='<tr><td style="color:var(--text3);width:120px">'+r[0]+'</td><td>'+(r[1]||'-')+'</td></tr>'});
document.getElementById('db-detail').innerHTML=h;
lastUpdate=new Date();document.getElementById('refresh-info').textContent=lastUpdate.toLocaleTimeString('zh-CN');
}).catch(function(e){console.error(e)});

fetch('/api/trends').then(function(r){return r.json()}).then(function(d){
var t=d.trends||[];if(!t.length){document.getElementById('trend-empty').style.display='block';document.getElementById('trend-chart').style.display='none';return}
document.getElementById('trend-empty').style.display='none';document.getElementById('trend-chart').style.display='block';
var mx=0;t.forEach(function(x){if(x.total_records>mx)mx=x.total_records});
var h='';t.slice(-24).forEach(function(x){var bh=mx>0?Math.max(4,(x.total_records/mx)*120):4;h+='<div class="trend-bar"><div class="bar-cnt">'+formatNum(x.total_records)+'</div><div class="bar-val" style="height:'+bh+'px" title="'+x.date+'"></div><div class="bar-lbl">'+(x.date||'').substring(0,7)+'</div></div>'});
document.getElementById('trend-bars').innerHTML=h;
});

fetch('/api/rate_limit/stats').then(function(r){return r.json()}).then(function(d){
document.getElementById('rl-status').innerHTML=d.enabled?'<span class="badge green">已启用</span> '+d.max_per_minute+'次/分/IP':'<span class="badge red">已禁用</span>';
var bl=d.blocked_ips||{},ac=d.active_ips||{},all={};for(var k in bl)all[k]={b:bl[k],a:ac[k]||0};for(var k in ac)if(!(k in all))all[k]={b:0,a:ac[k]};
var ks=Object.keys(all);if(!ks.length){document.getElementById('rl-table').style.display='none';document.getElementById('rl-empty').style.display='block';return}
document.getElementById('rl-table').style.display='';document.getElementById('rl-empty').style.display='none';
var h='';ks.forEach(function(k){var v=all[k];h+='<tr><td>'+k+'</td><td>'+(v.b>0?'<span class="badge red">封禁</span>':'<span class="badge green">活跃</span>')+'</td><td>'+(v.b>0?v.b.toFixed(0)+'s':'-')+'</td><td>'+v.a+'</td></tr>'});
document.getElementById('rl-tbody').innerHTML=h;
});

fetch('/api/machine_stats').then(function(r){return r.json()}).then(function(d){
var m=d.data||{},c=m.machine_counts||{},t=m.total_records||0,mc=m.machine_count||0;
document.getElementById('card-machines').textContent=formatNum(mc);
var ks=Object.keys(c);if(!ks.length){document.getElementById('machine-empty').style.display='block';document.getElementById('machine-chart').style.display='none';document.getElementById('machine-table').style.display='none';return}
document.getElementById('machine-empty').style.display='none';document.getElementById('machine-chart').style.display='block';document.getElementById('machine-table').style.display='';
var mx=0;ks.forEach(function(k){if(c[k]>mx)mx=c[k]});
var ch='';ks.forEach(function(k){var bh=mx>0?Math.max(4,(c[k]/mx)*120):4;ch+='<div class="trend-bar"><div class="bar-cnt">'+formatNum(c[k])+'</div><div class="bar-val" style="height:'+bh+'px"></div><div class="bar-lbl">'+k+'</div></div>'});
document.getElementById('machine-bars').innerHTML=ch;
var th='';ks.forEach(function(k){var p=t>0?(c[k]/t*100).toFixed(1):'0';th+='<tr><td>'+k+'</td><td>'+formatNum(c[k])+'</td><td>'+p+'%</td></tr>'});
document.getElementById('machine-tbody').innerHTML=th;
});
}

function doCleanup(c){
if(!c)c=parseInt(document.getElementById('cleanup-count').value)||100000;
var el=document.getElementById('cleanup-result');el.style.display='block';el.className='cleanup-result';el.style.color='var(--yellow)';el.style.background='rgba(245,158,11,.1)';el.style.borderColor='rgba(245,158,11,.2)';el.textContent='正在清理 '+formatNum(c)+' 条数据...';
fetch('/api/cleanup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count:c,csrf_token:CSRF_TOKEN})}).then(function(r){return r.json()}).then(function(d){
if(d.success){el.className='cleanup-result success';el.textContent='已删除 '+formatNum(d.deleted)+' 条数据'}
else{el.className='cleanup-result error';el.textContent='失败: '+(d.error||'未知错误')}
setTimeout(updateDashboard,500);
}).catch(function(e){el.className='cleanup-result error';el.textContent='请求失败: '+e.message});
}

updateDashboard();setInterval(updateDashboard,REFRESH);
</script>
</body>
</html>"""


def create_parser():
    parser = argparse.ArgumentParser(
        description=f'TXT查重工具 - 服务端 v{VERSION}',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('-i', '--index-dir', default=None,
                       help='索引目录，存储查重索引（默认: 程序同目录/.dedup_index）')
    parser.add_argument('-H', '--host', default=None,
                       help='绑定地址（默认: 0.0.0.0）')
    parser.add_argument('-p', '--port', type=int, default=None,
                       help='监听端口（默认: 5566）')
    parser.add_argument('--no-db', action='store_true',
                       help='不使用数据库，使用内存存储（仅用于小数据量测试）')
    parser.add_argument('--storage', choices=['sqlite', 'lmdb', 'memory'], default=None,
                       help='选择存储后端: sqlite、lmdb 或 memory（优先于配置文件）')
    parser.add_argument('-d', '--debug', action='store_true',
                       help='开启调试模式')
    parser.add_argument('-c', '--config', default=None,
                       help='指定配置文件路径（默认: 程序同目录/server_config.ini）')
    parser.add_argument('-v', '--version', action='version', version=f'v{VERSION}')
    
    return parser


def _detect_server_hardware():
    """检测服务器硬件配置，返回优化建议"""
    info = {
        'server_mode': False,
        'recommended_cache_mb': 512,
        'recommended_mmap_gb': 2,
        'recommended_threads': 4,
        'physical_cores': 4,
        'total_ram_gb': 0,
        'cpu_count': 0,
        'is_windows_server': False,
        'windows_version': '',
        'index_disk_total_gb': 0,
        'index_disk_free_gb': 0
    }
    if not PSUTIL_AVAILABLE:
        return info
    try:
        info['total_ram_gb'] = round(psutil.virtual_memory().total / (1024**3), 1)
        info['cpu_count'] = psutil.cpu_count(logical=True)
        physical_cores = psutil.cpu_count(logical=False) or info['cpu_count']
        info['physical_cores'] = physical_cores

        # 检测 Windows Server 版本
        try:
            import platform
            info['windows_version'] = platform.win32_ver()[0] or ''
            # Windows Server 2022 = 21H2, Windows Server 2025 = 24H2
            win_ver = platform.version()
            info['is_windows_server'] = 'server' in platform.platform().lower()
        except Exception:
            pass

        if info['total_ram_gb'] >= 8 or info['cpu_count'] >= 4:
            info['server_mode'] = True

        # 根据内存大小推荐缓存配置（Windows Server 2025 通常配备大容量内存）
        if info['total_ram_gb'] >= 256:
            # 超大内存服务器：充分利用内存提升性能
            info['recommended_cache_mb'] = 8192
            info['recommended_mmap_gb'] = 16
            info['recommended_threads'] = max(4, min(physical_cores, 16))
        elif info['total_ram_gb'] >= 128:
            info['recommended_cache_mb'] = 6144
            info['recommended_mmap_gb'] = 12
            info['recommended_threads'] = max(4, min(physical_cores, 12))
        elif info['total_ram_gb'] >= 64:
            info['recommended_cache_mb'] = 4096
            info['recommended_mmap_gb'] = 8
            info['recommended_threads'] = max(2, min(physical_cores, 8))
        elif info['total_ram_gb'] >= 32:
            info['recommended_cache_mb'] = 2048
            info['recommended_mmap_gb'] = 4
            info['recommended_threads'] = max(2, min(physical_cores, 8))
        elif info['total_ram_gb'] >= 16:
            info['recommended_cache_mb'] = 1024
            info['recommended_mmap_gb'] = 2
            info['recommended_threads'] = max(2, min(physical_cores, 6))
        elif info['total_ram_gb'] >= 8:
            info['recommended_cache_mb'] = 512
            info['recommended_mmap_gb'] = 1
            info['recommended_threads'] = max(2, min(physical_cores, 4))
        else:
            info['recommended_cache_mb'] = 256
            info['recommended_mmap_gb'] = 1
            info['recommended_threads'] = 2

    except Exception:
        pass
    return info


def _apply_server_optimizations(hw_info: dict = None):
    """应用 Windows Server 级别的进程优化"""
    optimizations = []

    if not PSUTIL_AVAILABLE:
        optimizations.append("GC: 阈值(700,15,15)")
        gc.set_threshold(700, 15, 15)
        try:
            gc.freeze()
        except AttributeError:
            pass
        return optimizations

    try:
        p = psutil.Process()

        if sys.platform == 'win32':
            # Windows Server 2025 进程优先级优化
            try:
                # 尝试设置为 REALTIME_PRIORITY_CLASS，但通常需要管理员权限
                p.nice(psutil.REALTIME_PRIORITY_CLASS)
                optimizations.append("进程优先级: REALTIME")
            except Exception:
                try:
                    p.nice(psutil.HIGH_PRIORITY_CLASS)
                    optimizations.append("进程优先级: HIGH")
                except Exception:
                    try:
                        p.nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
                        optimizations.append("进程优先级: ABOVE_NORMAL")
                    except Exception:
                        pass

            # CPU 亲和性优化：优先使用 P 核（性能核）
            if hw_info and hw_info.get('physical_cores', 0) >= 6:
                try:
                    import ctypes
                    from ctypes import wintypes

                    kernel32 = ctypes.windll.kernel32

                    # 获取当前进程句柄
                    handle = kernel32.GetCurrentProcess()

                    # Windows Server 2025: 优先绑定 P 核（效率核可能更耗电）
                    physical_cores = hw_info.get('physical_cores', 0)
                    if physical_cores > 0:
                        # 创建处理器亲和性掩码，绑定到前 N 个 P 核
                        pcore_mask = (1 << min(physical_cores, 64)) - 1  # 最多64核

                        # 设置亲和性
                        kernel32.SetProcessAffinityMask(
                            handle,
                            ctypes.c_ulonglong(pcore_mask)
                        )
                        optimizations.append(f"CPU亲和性: P核绑定 (0-{min(physical_cores, 64) - 1})")

                        # 获取进程优先级
                        try:
                            priority = p.nice()
                            optimizations.append(f"当前优先级: {priority}")
                        except Exception:
                            pass
                except Exception:
                    pass

            # Windows Server 2025: 设置 I/O 优先级为高
            try:
                # 使用 NtSetInformationProcess 设置 IoPriority
                # 这需要调用 ntdll.dll
                ntdll = ctypes.windll.ntdll
                PROCESS_IO_PRIORITY = 27
                IoPriorityHigh = 2

                class IO_PRIORITY_HINT_UNION(ctypes.Union):
                    _fields_ = [("Ptr", ctypes.c_void_p), ("Value", ctypes.c_ulong)]

                class IO_PRIORITY_HINT_STRUCT(ctypes.Structure):
                    _fields_ = [("Prioritized", IoPriorityHigh)]

                # 简化处理：通过 PowerShell 设置进程 I/O 优先级
                import subprocess
                try:
                    subprocess.run(
                        ['powershell', '-Command',
                         f'(Get-Process -Id {p.pid}).PriorityClass = "High"'],
                        capture_output=True, timeout=5
                    )
                    optimizations.append("I/O优先级: High")
                except Exception:
                    pass
            except Exception:
                pass

    except Exception:
        pass

    # Python GC 优化
    gc.set_threshold(700, 15, 15)
    try:
        gc.freeze()
    except AttributeError:
        pass
    optimizations.append("GC: 阈值(700,15,15)")

    # 启用 PyPyJIT 兼容模式（如果有）
    try:
        import sys
        if hasattr(sys, 'pypy_version_info'):
            optimizations.append("PyPy JIT: 已启用")
    except Exception:
        pass

    return optimizations


def _install_signal_handlers(shutdown_callback):
    """安装信号处理，确保优雅关闭"""
    def _handle_shutdown(signum, frame):
        if logger:
            logger.info(f"[信号] 收到关闭信号: {signal.Signals(signum).name}")
        shutdown_callback()
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)
    
    if sys.platform == 'win32':
        try:
            signal.signal(signal.SIGBREAK, _handle_shutdown)
        except AttributeError:
            pass


def main(config_file_override=None):
    global hash_index, index_dir, logger, health_monitor, _server_config, _rate_limit_enabled, _rate_limit_max, _api_token_enabled, _api_token
    
    if config_file_override:
        config_file = config_file_override
        base_dir = os.path.dirname(os.path.abspath(config_file_override))
        
        class SimpleArgs:
            host = '0.0.0.0'
            port = 8888
            index_dir = None
            no_db = False
            debug = False
        args = SimpleArgs()
    else:
        parser = create_parser()
        args = parser.parse_args()
        
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            base_dir = exe_dir
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            base_dir = script_dir
        
        if args.config:
            config_file = args.config
            base_dir = os.path.dirname(os.path.abspath(args.config))
        else:
            config_file = os.path.join(base_dir, SERVER_CONFIG_FILE)
    
    config = ServerConfigManager(config_file)
    _server_config = config
    
    _rate_limit_enabled = config.get_bool('RateLimit', 'enabled', True)
    _rate_limit_max = config.get_int('RateLimit', 'max_requests_per_minute', 120)

    _api_token_enabled = config.get_bool('Auth', 'api_token_enabled', False)
    configured_token = config.get_str('Auth', 'api_token', '')
    if configured_token:
        _api_token = configured_token
    
    integrity_day = config.get_int('Maintenance', 'integrity_check_day', 6)
    integrity_time_str = config.get_str('Maintenance', 'integrity_check_time', '03:00')
    vacuum_day = config.get_int('Maintenance', 'vacuum_day', 2)
    vacuum_time_str = config.get_str('Maintenance', 'vacuum_time', '03:00')
    trends_enabled = config.get_bool('Trends', 'enabled', True)
    trends_interval_days = config.get_int('Trends', 'stats_interval_days', 30)
    startup_notify = config.get_bool('StartupNotification', 'enabled', True)
    dash_enabled = config.get_bool('Dashboard', 'enabled', True)
    
    email_config = {
        'smtp_server': config.get_str('Email', 'smtp_server', ''),
        'smtp_port': config.get_int('Email', 'smtp_port', 587),
        'smtp_username': config.get_str('Email', 'smtp_username', ''),
        'smtp_password': config.get_str('Email', 'smtp_password', ''),
        'from_addr': config.get_str('Email', 'from_addr', ''),
        'to_addrs': config.get_str('Email', 'to_addrs', '')
    }
    
    # 命令行参数优先于配置文件
    host = args.host if args.host is not None else config.get_host()
    port = args.port if args.port is not None else config.get_port()
    storage_type = args.storage if args.storage else config.get_storage_type()
    if args.no_db:
        storage_type = 'memory'
    use_sqlite = storage_type == 'sqlite'
    log_level = "DEBUG" if args.debug else config.get_log_level()
    log_dir = os.path.join(base_dir, config.get_log_dir())
    
    logger = DedupLogger("dedup_server", log_dir=log_dir, level=log_level)
    
    # 设置日期日志
    date_str = datetime.now().strftime('%Y%m%d')
    logger.set_log_file(f"server_{date_str}")
    
    def is_port_in_use(port_num):
        """检查端口是否被占用"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('0.0.0.0', port_num))
                return False
            except socket.error:
                return True
    
    if is_port_in_use(port):
        logger.error(f"[启动失败] 端口 {port} 已被占用！")
        logger.error(f"[启动失败] 请先结束占用该端口的进程，或使用其他端口")
        print(f"\n错误: 端口 {port} 已被占用！")
        print(f"请先结束占用该端口的进程，或使用 --port 参数指定其他端口")
        sys.exit(1)
    
    # 索引目录
    if args.index_dir:
        index_dir = args.index_dir
    else:
        index_dir = os.path.join(base_dir, config.get_index_dir())
    os.makedirs(index_dir, exist_ok=True)
    
    sqlite_cache_size = config.get_sqlite_cache_size()
    
    hw_info = _detect_server_hardware()
    server_mode = hw_info.get('server_mode', False)
    
    mmap_size_gb = hw_info.get('recommended_mmap_gb', 2)
    sqlite_threads = hw_info.get('recommended_threads', 4)
    
    if server_mode and hw_info.get('recommended_cache_mb', 512) > sqlite_cache_size:
        sqlite_cache_size = hw_info['recommended_cache_mb']
    
    logger.info(f"{'='*60}")
    logger.info(f"TXT查重工具 - 服务端 v{VERSION}")
    logger.info(f"作者: {AUTHOR} | 联系: {CONTACT}")
    logger.info(f"构建时间: {BUILD_TIME}")
    logger.info(f"{'='*60}")
    logger.info(f"配置文件: {os.path.abspath(config_file)}")
    logger.info(f"索引目录: {os.path.abspath(index_dir)}")
    logger.info(f"绑定地址: {host}:{port}")
    storage_type_label = storage_type.upper()
    logger.info(f"存储类型: {storage_type_label}")
    if use_sqlite:
        logger.info(f"SQLite缓存: {sqlite_cache_size}MB")
    elif storage_type == 'lmdb':
        logger.info(f"LMDB分片: {config.get_lmdb_shard_count()} , 映射大小: {config.get_lmdb_map_size_gb()}GB")
    logger.info(f"日志级别: {log_level}")
    logger.info(f"日志目录: {log_dir}")
    
    if server_mode:
        logger.info(f"运行模式: Windows Server 优化")
        hw_ram = hw_info.get('total_ram_gb', '?')
        hw_cpu = hw_info.get('cpu_count', '?')
        hw_pcores = hw_info.get('physical_cores', '?')
        hw_version = hw_info.get('windows_version', '未知')
        logger.info(f"操作系统: Windows {hw_version}")
        logger.info(f"硬件配置: {hw_ram}GB RAM | {hw_cpu}线程 | {hw_pcores}P核")
        optimizations = _apply_server_optimizations(hw_info)
        for opt in optimizations:
            logger.info(f"[优化] {opt}")
    else:
        logger.info(f"运行模式: 标准模式")
    
    logger.info(f"{'='*60}")
    
    hash_index = HashIndex(
        index_dir,
        storage_type=storage_type,
        cache_size_mb=sqlite_cache_size,
        server_mode=server_mode,
        mmap_size_gb=mmap_size_gb,
        sqlite_threads=sqlite_threads,
        lmdb_shard_count=config.get_lmdb_shard_count(),
        lmdb_map_size_gb=config.get_lmdb_map_size_gb()
    )
    
    if use_sqlite:
        # 页面预热放入后台线程，避免大表查询阻塞服务启动
        def _warmup_pages():
            try:
                if not hash_index or not hash_index._db:
                    return
                logger.info("[页面预热] 开始预加载热点页面到缓存（后台）...")
                warm_start = time.time()
                total = hash_index._db.execute("SELECT COUNT(*) FROM hashes").fetchone()[0]
                if total > 0:
                    hash_index._db.execute("SELECT COUNT(*) FROM hashes WHERE timestamp > ?",
                                           (datetime.now().strftime('%Y-%m-%d'),))
                    sample_size = min(50000, total)
                    hash_index._db.execute(
                        "SELECT hash FROM hashes ORDER BY timestamp DESC LIMIT ?",
                        (sample_size,)
                    ).fetchall()
                    page_count = hash_index._db.execute("PRAGMA page_count").fetchone()[0]
                    warm_elapsed = time.time() - warm_start
                    logger.info(f"[页面预热] 完成，{page_count:,} 页已预热，耗时 {warm_elapsed:.1f}秒")
                else:
                    logger.info("[页面预热] 数据库为空，跳过预热")
            except Exception as e:
                logger.warning(f"[页面预热] 预热失败（不影响服务）: {str(e)}")
        threading.Thread(target=_warmup_pages, daemon=True, name="page-warmup").start()
    
    try:
        from health_monitor import HealthMonitor
        
        health_config = {
            'memory_warning_mb': config.get_int('HealthMonitor', 'memory_warning_mb', 4096),
            'memory_critical_mb': config.get_int('HealthMonitor', 'memory_critical_mb', 6144),
            'disk_warning_gb': config.get_int('HealthMonitor', 'disk_warning_gb', 100),
            'disk_critical_gb': config.get_int('HealthMonitor', 'disk_critical_gb', 50),
            'connection_warning': config.get_int('HealthMonitor', 'connection_warning', 100),
            'connection_critical': config.get_int('HealthMonitor', 'connection_critical', 500),
            'health_check_interval': config.get_int('HealthMonitor', 'health_check_interval', 60),
            'disk_check_interval': config.get_int('HealthMonitor', 'disk_check_interval', 86400),
            'index_dir': index_dir,
            'health_enable_email': config.get_bool('HealthMonitor', 'health_enable_email', True),
            'auto_cleanup_enabled': config.get_bool('AutoCleanup', 'enabled', True),
            'max_disk_usage_gb': config.get_int('AutoCleanup', 'max_disk_usage_gb', 3800),
            'cleanup_trigger_percent': config.get_int('AutoCleanup', 'cleanup_trigger_percent', 95),
            'cleanup_target_percent': config.get_int('AutoCleanup', 'cleanup_target_percent', 90),
            'cleanup_batch_size': config.get_int('AutoCleanup', 'cleanup_batch_size', 100000),
            'wal_passive_checkpoint': config.get_bool('ServerOptimization', 'wal_passive_checkpoint', True),
            'wal_checkpoint_interval': config.get_int('ServerOptimization', 'wal_checkpoint_interval', 300),
            'smtp_server': config.get_str('Email', 'smtp_server', ''),
            'smtp_port': config.get_int('Email', 'smtp_port', 587),
            'smtp_username': config.get_str('Email', 'smtp_username', ''),
            'smtp_password': config.get_str('Email', 'smtp_password', ''),
            'from_addr': config.get_str('Email', 'from_addr', ''),
            'to_addrs': config.get_str('Email', 'to_addrs', '')
        }
        
        health_monitor = HealthMonitor(health_config, logger)
        
        if hasattr(hash_index, 'db_path'):
            health_monitor.set_db_path(hash_index.db_path)
        
        health_monitor.set_cleanup_callback(
            lambda count, idx=hash_index, log=logger: idx.cleanup_oldest(count, log)
        )
        health_monitor.set_memory_release_callback(
            lambda idx=hash_index: idx.release_memory()
        )
        health_monitor.start()
        logger.info("[健康监控] 已集成到服务端")
        if health_config['auto_cleanup_enabled']:
            logger.info(f"[自动清理] 已启用，最大磁盘 {health_config['max_disk_usage_gb']}GB，触发阈值 {health_config['cleanup_trigger_percent']}%")
        if health_config['wal_passive_checkpoint'] and hasattr(hash_index, 'db_path'):
            logger.info(f"[WAL优化] 被动checkpoint已启用，间隔 {health_config['wal_checkpoint_interval']}秒")
    except Exception as e:
        logger.warning(f"[健康监控] 初始化失败: {str(e)}")
        logger.warning("[健康监控] 服务将继续运行，但健康监控功能不可用")
    
    app.start_time = time.time()
    
    if trends_enabled:
        def _trends_loop():
            trends_file = os.path.join(index_dir, 'trends.json')
            while True:
                now = datetime.now()
                next_check = now.replace(hour=3, minute=0, second=0, microsecond=0)
                if now >= next_check:
                    next_check += timedelta(days=trends_interval_days)
                else:
                    days_until = (trends_interval_days - (now.day % trends_interval_days)) % trends_interval_days
                    if days_until == 0:
                        days_until = trends_interval_days
                    next_check = now.replace(hour=3, minute=0, second=0, microsecond=0) + timedelta(days=days_until)
                
                sleep_seconds = (next_check - datetime.now()).total_seconds()
                if sleep_seconds < 0:
                    sleep_seconds = 86400
                time.sleep(sleep_seconds)
                
                try:
                    if not _server_running:
                        break  # 服务已关闭，退出循环
                    stats = hash_index.get_stats()
                    total_records = stats.get('total_records', 0)
                    detailed = hash_index.get_detailed_stats()
                    db_size = detailed.get('db_size_mb', 0)
                    
                    trends = []
                    if os.path.exists(trends_file):
                        try:
                            with open(trends_file, 'r', encoding='utf-8') as f:
                                trends = json.load(f)
                        except Exception:
                            trends = []
                    
                    trends.append({
                        'date': datetime.now().strftime('%Y-%m-%d'),
                        'total_records': total_records,
                        'db_size_mb': db_size
                    })
                    
                    if len(trends) > 36:
                        trends = trends[-36:]
                    
                    # 原子写入：先写临时文件，再重命名，防止崩溃损坏趋势数据
                    tmp_file = trends_file + '.tmp'
                    with open(tmp_file, 'w', encoding='utf-8') as f:
                        json.dump(trends, f, ensure_ascii=False, indent=2)
                    os.replace(tmp_file, trends_file)
                    
                    logger.info(f"[趋势统计] 已记录: {total_records:,} 条 | 数据库: {db_size:.1f}MB")
                except Exception as e:
                    logger.warning(f"[趋势统计] 记录失败: {str(e)}")
        
        trends_thread = threading.Thread(target=_trends_loop, daemon=True, name="trends-stats")
        trends_thread.start()
        logger.info(f"[趋势统计] 已启动，每 {trends_interval_days} 天记录一次")
    
    def _integrity_check_loop():
        while True:
            now = datetime.now()
            try:
                integrity_h, integrity_m = map(int, integrity_time_str.split(':'))
            except Exception:
                integrity_h, integrity_m = 3, 0
            
            days_ahead = integrity_day - now.weekday()
            if days_ahead < 0:
                days_ahead += 7
            elif days_ahead == 0:
                if now.hour > integrity_h or (now.hour == integrity_h and now.minute >= integrity_m):
                    days_ahead = 7
            
            next_check = now.replace(hour=integrity_h, minute=integrity_m, second=0, microsecond=0) + timedelta(days=days_ahead)
            sleep_seconds = (next_check - datetime.now()).total_seconds()
            if sleep_seconds < 0:
                sleep_seconds = 86400
            time.sleep(sleep_seconds)
            
            if not _server_running:
                break
            
            if not hash_index.use_db:
                logger.info("[完整性校验] 非SQLite模式，跳过数据库完整性检查")
                continue
            
            try:
                logger.info("[完整性校验] 开始数据库完整性检查...")
                start = time.time()
                # 使用独立连接执行完整性校验，避免长时间阻塞主连接
                check_conn = sqlite3.connect(hash_index.db_path, check_same_thread=False)
                try:
                    check_conn.execute("PRAGMA journal_mode=WAL")
                    result = check_conn.execute("PRAGMA integrity_check").fetchone()
                finally:
                    check_conn.close()
                elapsed = time.time() - start
                
                if result and result[0] == 'ok':
                    logger.info(f"[完整性校验] 数据库完整，耗时 {elapsed:.1f}秒")
                else:
                    error_msg = str(result[0]) if result else '未知错误'
                    logger.error(f"[完整性校验] 数据库损坏！结果: {error_msg}")

                    # 自动修复：备份损坏数据库，尝试 REINDEX
                    try:
                        backup_path = hash_index.db_path + f'.backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
                        import shutil
                        shutil.copy2(hash_index.db_path, backup_path)
                        logger.info(f"[自动修复] 已备份损坏数据库到: {backup_path}")

                        repair_conn = sqlite3.connect(hash_index.db_path, check_same_thread=False)
                        try:
                            repair_conn.execute("PRAGMA journal_mode=WAL")
                            logger.info("[自动修复] 尝试 REINDEX 重建索引...")
                            repair_conn.execute("REINDEX")
                            repair_conn.commit()
                            logger.info("[自动修复] REINDEX 完成，重新校验...")
                            recheck = repair_conn.execute("PRAGMA integrity_check").fetchone()
                            if recheck and recheck[0] == 'ok':
                                logger.info("[自动修复] 数据库修复成功！")
                                _send_event_email(
                                    "[修复] 数据库自动修复成功",
                                    f"数据库完整性校验失败后自动修复成功\n\n原始错误: {error_msg}\n已备份到: {backup_path}",
                                    email_config, logger
                                )
                            else:
                                logger.error(f"[自动修复] 修复后仍存在问题: {recheck[0] if recheck else '未知'}")
                                _send_event_email(
                                    "[严重] 数据库自动修复失败",
                                    f"数据库完整性校验失败且自动修复未解决问题\n\n原始错误: {error_msg}\n备份位置: {backup_path}\n\n请立即人工检查！",
                                    email_config, logger
                                )
                        finally:
                            repair_conn.close()
                    except Exception as repair_e:
                        logger.error(f"[自动修复] 修复过程异常: {repair_e}")
                        _send_event_email(
                            "[严重] 数据库完整性校验失败",
                            f"数据库完整性校验失败！自动修复过程也出现异常\n\n校验结果: {error_msg}\n修复异常: {str(repair_e)}\n\n请立即检查数据库文件！",
                            email_config, logger
                        )
            except Exception as e:
                logger.error(f"[完整性校验] 校验异常: {str(e)}")
                _send_event_email(
                    "[严重] 数据库完整性校验异常",
                    f"数据库完整性校验时发生异常！\n\n异常信息: {str(e)}\n\n请检查服务端日志！",
                    email_config, logger
                )
    
    integrity_thread = threading.Thread(target=_integrity_check_loop, daemon=True, name="integrity-check")
    integrity_thread.start()
    logger.info(f"[完整性校验] 已启动，每周{['一','二','三','四','五','六','日'][integrity_day]} {integrity_time_str} 执行")
    
    def _vacuum_loop():
        while True:
            now = datetime.now()
            try:
                vacuum_h, vacuum_m = map(int, vacuum_time_str.split(':'))
            except Exception:
                vacuum_h, vacuum_m = 3, 0
            
            days_ahead = vacuum_day - now.weekday()
            if days_ahead < 0:
                days_ahead += 7
            elif days_ahead == 0:
                if now.hour > vacuum_h or (now.hour == vacuum_h and now.minute >= vacuum_m):
                    days_ahead = 7
            
            next_check = now.replace(hour=vacuum_h, minute=vacuum_m, second=0, microsecond=0) + timedelta(days=days_ahead)
            sleep_seconds = (next_check - datetime.now()).total_seconds()
            if sleep_seconds < 0:
                sleep_seconds = 86400
            time.sleep(sleep_seconds)
            
            if not _server_running:
                break
            
            if not hash_index.use_db:
                logger.info("[数据库维护] 非SQLite模式，跳过数据库维护")
                continue
            
            try:
                logger.info("[数据库维护] 开始每周深度优化 (VACUUM → REINDEX → ANALYZE)...")
                total_start = time.time()
                
                # 使用独立连接执行维护操作，避免阻塞主连接处理业务请求
                maint_conn = sqlite3.connect(hash_index.db_path, check_same_thread=False)
                try:
                    maint_conn.execute("PRAGMA journal_mode=WAL")
                    maint_conn.execute("PRAGMA cache_size=-524288")
                    maint_conn.execute("PRAGMA mmap_size=2147483648")
                    
                    before_size = os.path.getsize(hash_index.db_path) if os.path.exists(hash_index.db_path) else 0
                    maint_conn.execute("PRAGMA incremental_vacuum(10000)")
                    maint_conn.commit()
                    after_size = os.path.getsize(hash_index.db_path) if os.path.exists(hash_index.db_path) else 0
                    freed = max(0, before_size - after_size)
                    logger.info(f"[VACUUM] 空间回收 {freed / (1024*1024):.1f}MB")
                    
                    reindex_start = time.time()
                    maint_conn.execute("REINDEX")
                    maint_conn.commit()
                    logger.info(f"[REINDEX] 索引重建完成，耗时 {time.time() - reindex_start:.1f}秒")
                    
                    analyze_start = time.time()
                    maint_conn.execute("ANALYZE")
                    maint_conn.commit()
                    logger.info(f"[ANALYZE] 统计更新完成，耗时 {time.time() - analyze_start:.1f}秒")
                    
                    total_elapsed = time.time() - total_start
                    logger.info(f"[数据库维护] 全部完成，总耗时 {total_elapsed:.1f}秒 | 回收 {freed / (1024*1024):.1f}MB")
                finally:
                    maint_conn.close()
            except Exception as e:
                logger.warning(f"[数据库维护] 异常: {str(e)}")
    
    vacuum_thread = threading.Thread(target=_vacuum_loop, daemon=True, name="db-maintenance")
    vacuum_thread.start()
    logger.info(f"[数据库维护] 已启动，每周{['一','二','三','四','五','六','日'][vacuum_day]} {vacuum_time_str} 执行 VACUUM→REINDEX→ANALYZE")
    
    def _rate_limit_cleanup_loop():
        while True:
            time.sleep(300)
            try:
                _cleanup_rate_limit()
            except Exception:
                pass
    
    rl_cleanup_thread = threading.Thread(target=_rate_limit_cleanup_loop, daemon=True, name="rl-cleanup")
    rl_cleanup_thread.start()

    def _periodic_sync_loop():
        while _server_running:
            time.sleep(60)
            if not _server_running:
                break
            try:
                if hash_index and hash_index.use_lmdb:
                    hash_index.commit()
            except Exception:
                pass

    sync_thread = threading.Thread(target=_periodic_sync_loop, daemon=True, name="periodic-sync")
    sync_thread.start()
    
    logger.info(f"服务已启动，监听端口 {port}")
    logger.info(f"健康检查: http://{host}:{port}/api/health")
    logger.info(f"详细健康: http://{host}:{port}/api/health/detailed")
    logger.info(f"统计信息: http://{host}:{port}/api/stats")
    if dash_enabled:
        logger.info(f"管理看板: http://{host}:{port}/")
    if _rate_limit_enabled:
        logger.info(f"[频率限制] 已启用，每IP每分钟最多 {_rate_limit_max} 次请求")
    else:
        logger.info(f"[频率限制] 已禁用")
    if server_mode:
        logger.info(f"[服务器模式] 已针对 Windows Server 进行优化")
    logger.info(f"按 Ctrl+C 停止服务")
    
    _shutting_down = False
    
    def _do_shutdown():
        nonlocal _shutting_down
        if _shutting_down:
            return
        _shutting_down = True
        global _server_running
        _server_running = False
        logger.info("服务正在停止...")

        shutdown_timeout = 30
        shutdown_start = time.time()

        if startup_notify:
            try:
                stats = hash_index.get_stats()
                _send_event_email(
                    "[通知] 服务端已停止",
                    f"TXT查重服务端已正常停止\n\n累计记录: {stats.get('total_records', 0):,} 条",
                    email_config, logger
                )
            except Exception:
                pass

        if health_monitor:
            try:
                health_monitor.stop()
            except Exception:
                pass

        def _close_index():
            try:
                hash_index.commit()
                hash_index.close()
            except Exception as e:
                logger.warning(f"[关闭] 索引关闭异常: {e}")

        close_thread = threading.Thread(target=_close_index, daemon=True)
        close_thread.start()
        close_thread.join(timeout=shutdown_timeout)

        if close_thread.is_alive():
            logger.warning(f"[关闭] 索引关闭超时({shutdown_timeout}秒)，强制退出")
        else:
            logger.log_stats()
            logger.info("服务已停止")
    
    _install_signal_handlers(_do_shutdown)

    def _config_watchdog():
        global _rate_limit_enabled, _rate_limit_max, _api_token_enabled, _api_token, _server_config
        config_mtime = os.path.getmtime(config_file)
        while _server_running:
            time.sleep(10)
            if not _server_running:
                break
            try:
                current_mtime = os.path.getmtime(config_file)
                if current_mtime != config_mtime:
                    config_mtime = current_mtime
                    new_config = ServerConfigManager(config_file)
                    _rate_limit_enabled = new_config.get_bool('RateLimit', 'enabled', True)
                    _rate_limit_max = new_config.get_int('RateLimit', 'max_requests_per_minute', 120)
                    new_token_enabled = new_config.get_bool('Auth', 'api_token_enabled', False)
                    new_token = new_config.get_str('Auth', 'api_token', '')
                    _api_token_enabled = new_token_enabled
                    if new_token:
                        _api_token = new_token
                    _server_config = new_config
                    if logger:
                        logger.info("[配置热重载] 配置文件已更新，重新加载完成")
            except Exception as e:
                if logger:
                    logger.warning(f"[配置热重载] 重新加载失败: {e}")

    config_watchdog_thread = threading.Thread(target=_config_watchdog, daemon=True, name="config-watchdog")
    config_watchdog_thread.start()

    if startup_notify:
        # 启动邮件放入后台线程，避免 SMTP 连接阻塞服务启动（网络故障时可能等60秒）
        def _send_startup_email():
            try:
                logger.info(f"[启动通知] 检查邮件配置并发送启动通知...")
                email_ready = bool(email_config.get('smtp_server') and email_config.get('to_addrs'))
                if not email_ready:
                    logger.warning(f"[启动通知] 邮件配置不完整，无法发送启动通知")
                    logger.warning(f"  smtp_server: {'已配置' if email_config.get('smtp_server') else '未配置'}")
                    logger.warning(f"  to_addrs: {'已配置' if email_config.get('to_addrs') else '未配置'}")
                else:
                    logger.info(f"[启动通知] 邮件配置完整，准备发送...")
                    _send_event_email(
                        "[通知] 服务端已启动",
                        f"TXT查重服务端已成功启动\n\n地址: http://{host}:{port}\n存储类型: {storage_type.upper()}\n缓存: {sqlite_cache_size}MB\n日志级别: {log_level}",
                        email_config, logger
                    )
            except Exception:
                pass
        threading.Thread(target=_send_startup_email, daemon=True, name="startup-email").start()
    
    try:
        import logging
        werkzeug_logger = logging.getLogger('werkzeug')
        werkzeug_logger.setLevel(logging.WARNING)
        
        try:
            from waitress import serve
            logger.info("[服务器] 使用 waitress WSGI 服务器")
            serve(app, host=host, port=port, threads=32, channel_timeout=120)
        except ImportError:
            logger.info("[服务器] waitress 未安装，使用 Flask 内置服务器（仅限开发环境）")
            app.run(host=host, port=port, debug=args.debug, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        _do_shutdown()
    except Exception as e:
        logger.error(f"[致命错误] 服务异常退出: {str(e)}")
        _do_shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()