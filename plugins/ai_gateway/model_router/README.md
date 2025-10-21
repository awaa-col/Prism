# Model Router Plugin

模型路由器插件,根据请求中的 `model` 字段自动选择正确的 AI 提供商插件。

## 功能

- 🎯 **智能路由**: 根据模型名自动选择 provider
- 🚀 **高性能**: 无需数据库查询,使用内存配置
- 🔧 **灵活配置**: 支持前缀匹配和精确匹配
- 📦 **即插即用**: 作为 Chain 中间件使用

## 工作原理

```
用户请求
{"model": "claude-3-opus", "messages": [...]}
    ↓
model_router 插件
    ↓
解析: claude-3-opus → claude_provider
    ↓
调用 claude_provider.handle()
    ↓
返回响应
```

## 配置

### config.yml

```yaml
# 前缀匹配
prefix_map:
  claude: claude_provider
  gpt: openai_provider
  gemini: gemini_provider

# 精确匹配
exact_map:
  my-custom-model: custom_provider

# 默认 provider
default_provider: openai_provider
```

### 路由规则

**优先级** (从高到低):
1. **精确匹配** (`exact_map`)
2. **前缀匹配** (`prefix_map`)
3. **默认 provider** (`default_provider`)

**示例**:
- `"claude-3-opus"` → 前缀 `claude` → `claude_provider`
- `"gpt-4-turbo"` → 前缀 `gpt` → `openai_provider`
- `"my-custom-model"` → 精确匹配 → `custom_provider`
- `"unknown-model"` → 默认 → `openai_provider` (如果配置了)

## 使用

### 在 Chain 中使用

```yaml
# config.yml
routes:
  /v1/chat/completions:
    chain:
      - auth_middleware
      - rate_limit_middleware
      - model_router  # 自动路由到对应的 provider
```

### API 请求

```bash
# 使用 Claude
curl -X POST http://prism/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-opus-20240229",
    "messages": [{"role": "user", "content": "Hello"}]
  }'

# 使用 GPT-4
curl -X POST http://prism/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

## 错误处理

- **400 Bad Request**: 缺少 `model` 字段且没有配置默认 provider
- **404 Not Found**: 没有找到匹配的 provider
- **503 Service Unavailable**: provider 插件未加载

## 开发

### 添加新 Provider

1. 创建新的 provider 插件 (如 `my_provider`)
2. 更新 `config.yml`:
   ```yaml
   prefix_map:
     mymodel: my_provider
   ```
3. 重启 Prism

### 自定义逻辑

可以继承 `ModelRouterPlugin` 并覆盖 `_resolve_provider()` 方法:

```python
class CustomModelRouter(ModelRouterPlugin):
    def _resolve_provider(self, model: str) -> Optional[str]:
        # 自定义逻辑
        if model.endswith("-fast"):
            return "fast_provider"
        return super()._resolve_provider(model)
```

## 日志

插件会记录详细的路由过程:

```
[model_router] Model requested: claude-3-opus
[model_router] Resolved to provider: claude_provider
[model_router] Calling provider plugin 'claude_provider'
[model_router] Provider 'claude_provider' execution completed
```

可以通过请求头 `X-Prism-Trace: true` 启用追踪,查看完整的路由过程。

## 许可

MIT License
