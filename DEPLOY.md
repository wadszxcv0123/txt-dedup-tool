# 部署说明

## 📦 快速部署（推荐）

### 方法1：使用打包好的EXE文件（无需安装Python）

1. 在开发机器上运行 `build_all.bat` 打包
2. 将 `release` 文件夹复制到其他电脑
3. 双击运行相应的程序

### 方法2：使用Python源码部署

1. 在目标电脑上安装 Python 3.7+
2. 复制整个项目文件夹到目标电脑
3. 双击运行 `install_deps.bat` 安装依赖
4. 启动服务端或客户端

## 🖥️ 服务端部署

### 步骤1：准备服务端电脑
- 确保电脑在局域网内，IP地址固定
- 开放防火墙8888端口（或自定义端口）

### 步骤2：启动服务端
```
# 使用EXE
txt-dedup-server.exe

# 或使用源码
python server.py
```

### 步骤3：查看服务端IP
打开命令行，运行：
```
ipconfig
```
找到IPv4地址，例如：`192.168.1.100`

## 💻 客户端部署

### 步骤1：配置服务端地址

首次使用前，在客户端电脑上设置服务端地址：
- 运行 `client_interactive.bat`
- 选择选项3设置服务端地址
- 输入服务端地址：`http://192.168.1.100:8888`

### 步骤2：开始查重

- 拖拽TXT文件到窗口，或
- 选择选项1输入文件路径

## 🌐 网络配置

### 服务端电脑防火墙设置

**Windows防火墙：**
1. 打开「Windows Defender 防火墙」
2. 点击「允许应用或功能通过Windows防火墙」
3. 找到Python或txt-dedup-server.exe
4. 勾选「专用」和「公用」网络

**或使用命令行：**
```cmd
netsh advfirewall firewall add rule name="TXT查重服务" dir=in action=allow protocol=TCP localport=8888
```

### 测试连接

在客户端电脑上，打开浏览器访问：
```
http://服务端IP:8888/api/health
```

如果看到 `{"success": true, ...}` 说明连接成功！

## 📋 部署清单

### 服务端电脑
- [ ] 安装Python（源码部署）或EXE（打包部署）
- [ ] 配置防火墙允许8888端口
- [ ] 固定局域网IP地址
- [ ] 启动服务端程序
- [ ] 记录服务端IP地址

### 客户端电脑
- [ ] 安装Python（源码部署）或EXE（打包部署）
- [ ] 配置服务端IP地址
- [ ] 测试连接服务端
- [ ] 开始查重

## 🔧 常见问题

### Q: 客户端无法连接服务端？
A: 检查以下几点：
1. 两台电脑是否在同一局域网？
2. 服务端防火墙是否开放端口？
3. 服务端IP地址是否正确？
4. 服务端程序是否正在运行？

### Q: 打包后的EXE文件很大？
A: PyInstaller打包会包含Python运行时，这是正常的。使用UPX压缩可以减小体积。

### Q: LevelDB在Windows上安装失败？
A: 没关系，服务端会自动降级使用内存存储模式，不影响基本功能。

### Q: 如何更换服务端端口？
A: 使用 `-p` 参数：`txt-dedup-server.exe -p 9999`

## 📁 部署包结构

```
release/
├── txt-dedup-server.exe    # 服务端程序
├── txt-dedup-client.exe    # 客户端程序
└── README.md               # 使用说明
```
