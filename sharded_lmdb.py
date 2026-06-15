"""
Sharded LMDB storage backend for TXT dedup server.

Features:
- Sharding by simple prefix hashing into multiple LMDB environments (directories).
- Per-shard read/write locking for concurrent Flask request handling.
- Timestamp-indexed cleanup for efficient eviction of oldest entries.
- Batch check-and-add and check-only APIs.
- Parallel shard processing using ThreadPoolExecutor for full memory utilization.
- Cursor-based batch lookups for high throughput.

Requires: pip install lmdb
"""
import os
import lmdb
import threading
import json
import struct
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Dict, Tuple, Optional




_LMDB_POOL = None
_LMDB_POOL_LOCK = threading.Lock()


def _get_lmdb_pool(shard_count: int):
    """获取持久的 LMDB 线程池（复用，避免每次请求创建/销毁）"""
    global _LMDB_POOL
    with _LMDB_POOL_LOCK:
        if _LMDB_POOL is None:
            _LMDB_POOL = ThreadPoolExecutor(max_workers=shard_count)
        return _LMDB_POOL


def _to_bytes(value: str) -> bytes:
    return value.encode('utf-8') if isinstance(value, str) else value


def _normalize_timestamp(timestamp: Optional[str]) -> str:
    if timestamp:
        try:
            datetime.fromisoformat(timestamp)
            return timestamp
        except Exception:
            pass
    return datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


def _precompute_ts(timestamp: str) -> int:
    """预计算时间戳整数（批次级别一次调用，避免每条hash重复解析）"""
    return int(datetime.fromisoformat(timestamp).timestamp())


class ShardedLMDB:
    def __init__(self, base_dir: str, shard_count: int = 1, map_size: int = 1 << 30):
        self.base_dir = base_dir
        self.shard_count = max(1, int(shard_count))
        self.map_size = int(map_size)
        self.shards: List[Tuple[lmdb.Environment, lmdb._Database, lmdb._Database]] = []
        self.locks: List[threading.RLock] = []

        os.makedirs(base_dir, exist_ok=True)

        # 并行打开所有分片，避免串行 mmap 阻塞启动
        def _open_shard(i):
            shard_dir = os.path.join(base_dir, f"shard_{i}")
            os.makedirs(shard_dir, exist_ok=True)

            # 检查磁盘可用空间，避免预分配超出磁盘
            import shutil
            total, used, free = shutil.disk_usage(shard_dir)
            free_gb = free / (1024**3)
            effective_map_size = min(self.map_size, int(free_gb * 0.8 * 1024**3))
            if effective_map_size < self.map_size:
                effective_map_size = max(256 * 1024 * 1024, effective_map_size)

            env = lmdb.open(
                shard_dir,
                map_size=effective_map_size,
                max_readers=1024,
                max_dbs=2,
                lock=True,
                writemap=True,
                sync=True,
                metasync=True,
                readahead=True,
            )
            hashes_db = env.open_db(b"hashes")
            timestamps_db = env.open_db(b"timestamps")
            return (i, env, hashes_db, timestamps_db)

        if self.shard_count > 1:
            from concurrent.futures import as_completed
            pool = _get_lmdb_pool(min(self.shard_count, 8))
            futures = {pool.submit(_open_shard, i): i for i in range(self.shard_count)}
            results = {}
            for future in as_completed(futures):
                i, env, hashes_db, timestamps_db = future.result()
                results[i] = (env, hashes_db, timestamps_db)
            for i in sorted(results):
                env, hashes_db, timestamps_db = results[i]
                self.shards.append((env, hashes_db, timestamps_db))
                self.locks.append(threading.RLock())
        else:
            for i in range(self.shard_count):
                _, env, hashes_db, timestamps_db = _open_shard(i)
                self.shards.append((env, hashes_db, timestamps_db))
                self.locks.append(threading.RLock())

    def _shard_for(self, key_hex: str) -> int:
        try:
            k = int(key_hex[:8], 16)
        except Exception:
            k = 0
            for ch in key_hex:
                k = (k * 31 + ord(ch)) & 0xFFFFFFFF
        return k % self.shard_count

    def _ensure_metadata(self, metadata: Optional[Dict]) -> Dict:
        if metadata is None:
            metadata = {}
        metadata.setdefault('timestamp', _normalize_timestamp(metadata.get('timestamp')))
        return metadata

    def _process_shard_check_only(self, shard: int, items: List[Tuple[int, str]]) -> Dict[int, bool]:
        """在单个分片中批量检查哈希（小批次事务减少锁持有时间）"""
        results: Dict[int, bool] = {}
        if not items:
            return results

        env, hashes_db, _ = self.shards[shard]
        lock = self.locks[shard]

        SUB_BATCH = 5000
        for start in range(0, len(items), SUB_BATCH):
            sub = items[start:start + SUB_BATCH]
            with lock:
                with env.begin(write=False) as txn:
                    cursor = txn.cursor(db=hashes_db)
                    for idx, h in sub:
                        key = _to_bytes(h)
                        if cursor.set_key(key):
                            results[idx] = True
                        else:
                            results[idx] = False
        return results

    def _process_shard_check_and_add(self, shard: int, items: List[Tuple[int, str]],
                                      metadata: Dict, ts_int: int) -> Dict[int, bool]:
        """在单个分片中批量检查并添加哈希（小批次事务减少锁持有时间）"""
        results: Dict[int, bool] = {}
        if not items:
            return results

        env, hashes_db, timestamps_db = self.shards[shard]
        lock = self.locks[shard]
        meta_value = json.dumps(metadata, ensure_ascii=False).encode('utf-8')

        SUB_BATCH = 2000
        for start in range(0, len(items), SUB_BATCH):
            sub = items[start:start + SUB_BATCH]
            with lock:
                with env.begin(write=True) as txn:
                    cursor = txn.cursor(db=hashes_db)
                    for idx, h in sub:
                        key = _to_bytes(h)
                        if cursor.set_key(key):
                            results[idx] = True
                        else:
                            txn.put(key, meta_value, db=hashes_db)
                            hash_bytes = _to_bytes(h)
                            ts_key = struct.pack('>Q', ts_int) + b'|' + hash_bytes
                            txn.put(ts_key, b'', db=timestamps_db)
                            results[idx] = False
        return results

    def check_and_add_batch(self, hash_vals: List[str], metadata: Dict = None) -> List[bool]:
        metadata = self._ensure_metadata(metadata)
        results = [False] * len(hash_vals)
        per_shard: Dict[int, List[Tuple[int, str]]] = {}

        for idx, h in enumerate(hash_vals):
            shard = self._shard_for(h)
            per_shard.setdefault(shard, []).append((idx, h))

        # 预计算时间戳整数（批次级别一次，避免每条hash重复解析）
        ts_int = _precompute_ts(metadata['timestamp'])

        # 并行处理所有分片（使用持久化线程池，避免每次请求创建/销毁）
        workers = min(len(per_shard), self.shard_count)
        if workers <= 1:
            for shard, items in per_shard.items():
                shard_results = self._process_shard_check_and_add(shard, items, metadata, ts_int)
                for idx, is_dup in shard_results.items():
                    results[idx] = is_dup
        else:
            pool = _get_lmdb_pool(workers)
            futures = {
                pool.submit(self._process_shard_check_and_add, shard, items, metadata, ts_int): shard
                for shard, items in per_shard.items()
            }
            for future in as_completed(futures):
                shard_results = future.result()
                for idx, is_dup in shard_results.items():
                    results[idx] = is_dup
        return results

    def check_only_batch(self, hash_vals: List[str]) -> List[bool]:
        results = [False] * len(hash_vals)
        per_shard: Dict[int, List[Tuple[int, str]]] = {}

        for idx, h in enumerate(hash_vals):
            shard = self._shard_for(h)
            per_shard.setdefault(shard, []).append((idx, h))

        # 并行处理所有分片（使用持久化线程池，避免每次请求创建/销毁）
        workers = min(len(per_shard), self.shard_count)
        if workers <= 1:
            for shard, items in per_shard.items():
                shard_results = self._process_shard_check_only(shard, items)
                for idx, is_dup in shard_results.items():
                    results[idx] = is_dup
        else:
            pool = _get_lmdb_pool(workers)
            futures = {
                pool.submit(self._process_shard_check_only, shard, items): shard
                for shard, items in per_shard.items()
            }
            for future in as_completed(futures):
                shard_results = future.result()
                for idx, is_dup in shard_results.items():
                    results[idx] = is_dup
        return results

    def get_stats(self) -> Dict:
        total = 0
        for env, hashes_db, _ in self.shards:
            with env.begin(write=False) as txn:
                stat = txn.stat(db=hashes_db)
                total += stat.get('entries', 0)
        return {'total_records': total, 'shards': self.shard_count}

    def cleanup_oldest(self, count: int) -> int:
        if count <= 0:
            return 0

        deleted = 0

        shard_sizes = []
        for env, hashes_db, _ in self.shards:
            with env.begin(write=False) as txn:
                stat = txn.stat(db=hashes_db)
                shard_sizes.append(stat.get('entries', 0))
        total_entries = sum(shard_sizes)

        if total_entries == 0:
            return 0

        shard_quotas = []
        remaining = count
        for i, size in enumerate(shard_sizes):
            if i == len(shard_sizes) - 1:
                quota = remaining
            else:
                quota = max(1, int(count * size / total_entries)) if total_entries > 0 else 1
                quota = min(quota, remaining)
            shard_quotas.append(quota)
            remaining -= quota

        for shard_idx, (env, hashes_db, timestamps_db) in enumerate(self.shards):
            quota = shard_quotas[shard_idx]
            if quota <= 0:
                continue
            if deleted >= count:
                break
            with self.locks[shard_idx]:
                with env.begin(write=True) as txn:
                    cursor = txn.cursor(db=timestamps_db)
                    if not cursor.first():
                        continue

                    keys_to_delete = []
                    while deleted + len(keys_to_delete) < count and len(keys_to_delete) < quota:
                        ts_key = cursor.key()
                        hash_key = ts_key[9:]
                        keys_to_delete.append((hash_key, ts_key))
                        if not cursor.next():
                            break

                    for hash_key, ts_key in keys_to_delete:
                        if txn.delete(hash_key, db=hashes_db):
                            deleted += 1
                        txn.delete(ts_key, db=timestamps_db)
                        if deleted >= count:
                            break
        return deleted

    def commit(self):
        """同步所有分片到磁盘"""
        for env, _, _ in self.shards:
            try:
                env.sync()
            except Exception:
                pass
        return True

    def close(self):
        for env, _, _ in self.shards:
            try:
                env.sync()
            except Exception:
                pass
            env.close()

    def get_duplicate_sources(self, hash_vals: List[str]) -> Dict[str, Dict]:
        sources: Dict[str, Dict] = {}
        per_shard: Dict[int, List[str]] = {}

        for h in hash_vals:
            shard = self._shard_for(h)
            per_shard.setdefault(shard, []).append(h)

        def _query_shard(shard, hs):
            local: Dict[str, Dict] = {}
            env, hashes_db, _ = self.shards[shard]
            with env.begin(write=False) as txn:
                cursor = txn.cursor(db=hashes_db)
                for h in hs:
                    key = _to_bytes(h)
                    if cursor.set_key(key):
                        val = cursor.value()
                        try:
                            meta = json.loads(val.decode('utf-8'))
                        except Exception:
                            meta = {'data': val.decode('utf-8', errors='ignore')}
                        local[h] = meta
            return local

        workers = min(len(per_shard), self.shard_count)
        if workers <= 1:
            for shard, hs in per_shard.items():
                sources.update(_query_shard(shard, hs))
        else:
            pool = _get_lmdb_pool(workers)
            futures = {pool.submit(_query_shard, shard, hs): shard
                       for shard, hs in per_shard.items()}
            for future in as_completed(futures):
                sources.update(future.result())
        return sources

    def get_machine_stats(self) -> Dict[str, int]:
        """统计各个机台的记录数（并行遍历所有分片）"""
        machine_counts: Dict[str, int] = {}

        def _count_shard(env, hashes_db):
            local: Dict[str, int] = {}
            with env.begin(write=False) as txn:
                cursor = txn.cursor(db=hashes_db)
                for key, value in cursor:
                    try:
                        meta = json.loads(value.decode('utf-8'))
                        machine_id = meta.get('machine_id', '未知机台')
                        local[machine_id] = local.get(machine_id, 0) + 1
                    except Exception:
                        pass
            return local

        # 复用持久化线程池，避免每次调用创建/销毁线程
        workers = min(self.shard_count, os.cpu_count() or 4)
        if workers <= 1:
            for env, hashes_db, _ in self.shards:
                local = _count_shard(env, hashes_db)
                for mid, cnt in local.items():
                    machine_counts[mid] = machine_counts.get(mid, 0) + cnt
        else:
            pool = _get_lmdb_pool(workers)
            futures = {pool.submit(_count_shard, env, hashes_db): i
                       for i, (env, hashes_db, _) in enumerate(self.shards)}
            for future in as_completed(futures):
                local = future.result()
                for mid, cnt in local.items():
                    machine_counts[mid] = machine_counts.get(mid, 0) + cnt
        return machine_counts