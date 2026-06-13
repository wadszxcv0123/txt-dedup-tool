# TXT 查重工具

高效、可扩展的 TXT 文本查重系统，支持大规模数据处理。

## 功能特性

- **双模式查重**: 支持客户端计算和服务端计算两种模式
- **高效存储**: 支持 SQLite 和 LMDB 两种存储后端，LMDB 适用于大规模数据
- **分布式架构**: 客户端-服务端分离设计，支持多机台并行处理
- **智能分片**: LMDB 分片技术，提高并行处理能力
- **数据库自动维护**:
  - 自动清理（磁盘空间不足时删除最早数据）
  - 定期整理（VACUUM + REINDEX + ANALYZE）
  - WAL 自动合并
  - 完整性校验
- **健康监控**: 实时监控内存、磁盘、连接数，支持邮件告警
- **Web 管理看板**: 基于 Flask 的可视化监控界面

## 系统要求

- Windows Server 2016/2019/2022 或 Windows 10/11
- Python 3.8+
- 建议 8GB+ RAM

## 快速开始

### 1. 安装依赖

```batch
install_deps.bat
```

### 2. 配置服务端

编辑 `server_config.ini` 文件，配置数据库路径、端口、邮件通知等。

### 3. 启动服务端

```batch
start_server.bat
```

### 4. 配置客户端

编辑 `config.ini` 文件，配置服务端地址。

### 5. 启动客户端

```batch
start_client.bat
```

## 项目结构

```
txt-dedup-tool/
├── server.py           # 服务端主程序
├── client.py           # 客户端主程序
├── sharded_lmdb.py     # LMDB 分片存储实现
├── health_monitor.py   # 健康监控模块
├── notifier.py         # 通知模块（邮件/系统通知）
├── logging_utils.py    # 日志工具
├── watchdog.py         # 看门狗服务
├── server_service.py   # Windows 服务封装
├── requirements.txt    # Python 依赖
├── server_config.ini   # 服务端配置
├── config.ini          # 客户端配置
└── *.bat              # 启动脚本
```

## 配置说明

### 服务端配置 (server_config.ini)

| 参数 | 说明 | 默认值 |
|------|------|--------|
| storage_type | 存储类型 (sqlite/lmdb) | lmdb |
| lmdb_shards | LMDB 分片数 | 16 |
| bind_host | 绑定地址 | 0.0.0.0 |
| bind_port | 监听端口 | 5566 |
| cleanup_target_percent | 自动清理触发磁盘使用率 | 95 |
| vacuum_schedule | 数据库整理执行时间 | 每周三 03:00 |

### 客户端配置 (config.ini)

| 参数 | 说明 | 默认值 |
|------|------|--------|
| server_url | 服务端地址 | http://localhost:5566 |
| server_compute_mode | 服务端计算模式 | true |
| server_compute_chunk_size | 服务端计算批大小 | 100000 |
| default_machine | 默认机台名称 | 默认 |

## 使用指南

### 基本查重流程

1. 启动服务端 `start_server.bat`
2. 启动客户端 `start_client.bat`
3. 将 TXT 文件拖放到客户端窗口
4. 等待查重完成，查看结果

### 查看管理看板

在浏览器中访问 `http://localhost:5566/` 查看服务端状态和统计数据。

## 性能优化

- **服务端计算模式**: 适合多核服务器，减少网络传输
- **客户端计算模式**: 适合低带宽环境，只传输哈希值
- **LMDB 分片**: 根据数据量调整分片数，建议 16-32

## 许可证

MIT License
