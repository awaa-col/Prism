# Prism Framework

一个基于 FastAPI 的现代化插件框架，具有企业级安全架构和强大的扩展能力。

## 🚀 快速开始

### 环境要求

- Python 3.8+
- Docker & Docker Compose (推荐)

### 安装运行

1. **克隆项目**
   ```bash
   git clone <repository-url>
   cd Prism
   ```

2. **配置环境**
   ```bash
   cp env.example .env
   # 编辑 .env 文件配置数据库等信息
   ```

3. **启动服务**
   ```bash
   # Docker 方式 (推荐)
   docker-compose up --build
   
   # 或本地开发
   python -m app.main
   ```

4. **访问应用**
   - API 文档: http://localhost:8080/docs
   - 健康检查: http://localhost:8080/

## 🏗️ 架构特性

### 核心功能

- **🔐 OAuth 认证系统**: 支持 GitHub、Google 等多种 OAuth 提供商
- **🔌 插件化架构**: 动态加载、热重载、链式调用
- **🛡️ 三层安全模型**: 监狱围墙 + 权限锁文件 + 动态审计
- **📊 实时监控**: 性能指标、审计日志、违规检测
- **🔄 热重载**: 开发时插件自动重载

### 技术栈

- **Web框架**: FastAPI + Uvicorn
- **数据库**: SQLAlchemy (支持多种数据库)
- **认证**: OAuth 2.0 + JWT
- **监控**: Prometheus + 结构化日志
- **容器化**: Docker + Docker Compose

## 🔒 安全架构

Prism 采用业界领先的**三层安全架构**：

### 第一层：监狱围墙 (Prison Wall)
- 强制目录访问限制
- 插件只能访问指定目录

### 第二层：权限锁文件 (Permission Lock Files)
- **不可篡改的权限记录**: 插件首次加载时，框架会根据其声明的权限，在插件目录内生成一个 `permissions.lock.json` 文件。
- **唯一真实来源**: 此 lock 文件成为该插件权限的唯一真实来源，防止通过更新代码来悄悄提权。
- **插件隔离**: 插件无法修改自身的 lock 文件。

### 第三层：动态审计 (Dynamic Audit)
- **实时监控**: 基于 Python 的 `sys.addaudithook`，在解释器层面拦截文件、网络、系统等危险操作。
- **即时阻止**: 当插件行为超出 `permissions.lock.json` 中授予的权限时，操作将被立即阻止并抛出 `PermissionError`。

## 🔌 插件开发

### 🚨 重要安全须知

**插件开发者必须遵守以下安全规则：**

- 🚫 **绝对禁止**尝试读取或修改自己或其他插件的 `permissions.lock.json` 文件。
- ✅ **必须**在 `plugin.yml` 中声明所有需要的权限。
- ✅ **只能**访问自己的插件目录

### 快速创建插件

1. **创建插件目录**
   ```bash
   mkdir plugins/my_plugin
   cd plugins/my_plugin
   ```

2. **创建插件清单** (`plugin.yml`)
   ```yaml
   name: my_plugin
   version: 1.0.0
   description: 我的插件
   author: 你的名字

   # 声明插件所需的权限
   permissions:
     - type: file.read
       resource: "config.yml" # 只能读取自己目录下的 config.yml
       description: "读取插件配置"
     - type: api.route
       resource: "/my_plugin/hello" # 将会注册为 /api/v1/plugins/my_plugin/hello
       description: "创建插件专属的 API 路由"
   ```

3. **创建插件代码** (`plugin.py`)
   ```python
   from app.plugins.interface import PluginMetadata, MetaPlugin
   
   class MyPlugin(MetaPlugin):
       def get_metadata(self) -> PluginMetadata:
           return PluginMetadata(
               name="my_plugin",
               version="1.0.0",
               description="我的插件",
               author="你的名字"
           )
       
       async def initialize(self) -> None:
           """插件初始化"""
           self.logger.info("插件初始化完成")
       
       async def handle(self, context, next_callback):
           """处理请求"""
           # 你的业务逻辑
           context.response_data["message"] = "Hello from my plugin!"
           await next_callback()
       
       async def shutdown(self) -> None:
           """插件关闭"""
           pass
   ```

4. **启用插件**
   
   在 `config.yml` 中添加：
   ```yaml
   plugins:
     enabled:
       - oauth_key_frontend
       - example_chain_plugin
       - my_plugin  # 添加你的插件
   ```

5. **重启服务器**
   ```bash
   # 重启以加载新插件
   docker-compose restart
   ```

### 权限系统

**可用权限类型：**

- `file.read_plugin` / `file.write_plugin`: 文件操作
- `file.read_temp` / `file.write_temp`: 临时文件操作
- `api.create_route`: 创建API路由
- `network.http` / `network.https`: 网络连接
- `network.specific_domain`: 特定域名访问
- `system.subprocess`: 执行系统命令
- `system.environment`: 访问环境变量
- `database.access`: 数据库访问

**权限资源模式：**
- `*`: 匹配所有
- `config.yml`: 精确匹配
- `data/*`: 目录匹配
- `*.json`: 扩展名匹配

## 📚 文档

- **📖 [项目文档](docs/项目文档.md)**: 完整的架构和API文档
- **🔒 [锁文件保护机制](docs/锁文件保护机制.md)**: 安全架构详解
- **🔌 [插件开发指南](docs/插件开发指南.md)**: 完整的插件开发教程

## 🛠️ 开发

### 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 启动开发服务器
python -m app.main

# 或使用热重载
python run.py
```

### 调试插件

```bash
# 启用插件调试日志
export PRISM_PLUGIN_DEBUG=1

# 查看权限检查日志
tail -f logs/audit.log
```

### 测试

```bash
# 运行测试
python -m pytest tests/

# 测试特定插件
python -m pytest tests/test_plugins/
```

## 🚨 故障排除

### 常见错误

1. **PermissionError: Plugin attempted to access a protected resource**
   - 原因：插件尝试访问一个它没有权限的资源，例如其他插件的目录或 `permissions.lock.json` 文件。
   - 解决：检查代码，确保插件只在自己的沙箱内活动。

2. **PermissionError: Plugin blocked from performing unauthorized action...**
   - 原因：插件执行了一个未在 `plugin.yml` 中声明的操作（如网络请求、文件读写）。
   - 解决：在 `plugin.yml` 的 `permissions` 列表中添加相应权限，然后删除 `permissions.lock.json` 文件并重启应用以重新生成权限锁。

3. **Failed to load plugin metadata**
   - 原因：`plugin.yml` 格式错误或缺少必要字段。
   - 解决：检查 YAML 格式和 `name`, `version` 等必需字段。

### 调试技巧

```python
# 在插件中添加调试日志
self.logger.info("插件开始处理请求")
self.logger.debug(f"接收到的数据: {context.request_data}")

# 测试权限边界
try:
    with open("test_file.txt", 'r') as f:
        content = f.read()
except PermissionError as e:
    self.logger.warning(f"权限测试: {e}")
```

## 🤝 贡献

我们欢迎任何形式的贡献！请阅读我们的 [**贡献指南 (CONTRIBUTING.md)**](CONTRIBUTING.md) 来了解如何参与。

同时，请确保你遵守我们的 [**行为准则 (CODE_OF_CONDUCT.md)**](CODE_OF_CONDUCT.md)。

## 📄 许可证

本项目采用 MIT 许可证。详情请查看 [LICENSE](LICENSE) 文件。

## 🆘 支持

- 📖 查看 [项目文档](白岚/Project_Documentation.md)
- 🐛 提交 [Issue](../../issues)
- 💬 参与 [讨论](../../discussions)

---

**⚠️ 安全提醒**: 开发插件时请严格遵守安全规范，任何尝试绕过安全机制的行为都会被系统检测并阻止。详细安全要求请参考 [插件开发指南](docs/插件开发指南.md)。