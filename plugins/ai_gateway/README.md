# AI Gateway Meta Plugin

Unified AI Gateway with OpenAI-compatible API for Prism Framework.

## Features

- 🎯 **Intelligent Model Routing**: Automatically routes to the correct AI provider
- 🔌 **OpenAI Compatible**: Works with any OpenAI SDK
- 🚀 **Multiple Providers**: Claude and OpenAI support out of the box
- 📦 **Easy to Extend**: Add new providers easily

## Included Sub-Plugins

1. **Model Router**: Intelligent routing based on model name
2. **Claude Provider**: Anthropic Claude AI integration
3. **OpenAI Provider**: OpenAI GPT integration

## Installation

### Using Prism CLI

```bash
prism install plugins/ai_gateway
```

### Manual Installation

1. Copy `ai_gateway` directory to `plugins/`
2. Install dependencies:
   ```bash
   pip install anthropic>=0.25.0 openai>=1.0.0 pyyaml>=6.0
   ```
3. Set API keys:
   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."
   export OPENAI_API_KEY="sk-proj-..."
   ```

## Configuration

### Enable in config.yml

```yaml
# 1. 启用插件
plugins:
  enabled:
    - ai_gateway

# 2. 配置路由到元插件的预设chain
routes:
  /chat/completions:
    chain:
      - ai_gateway:chat  # 使用元插件内部的预设chain
```

**注意**: 
- `ai_gateway:chat` 是元插件内部定义的预设chain
- 这个chain内部使用 `model_router` 来自动路由到正确的 provider
- **不需要**直接配置子插件 (model_router, claude_provider等)

## Usage

### Python SDK

```python
import openai

client = openai.OpenAI(
    base_url="http://your-prism-server/v1",
    api_key="your-prism-api-key"
)

# Use Claude
response = client.chat.completions.create(
    model="claude-3-opus-20240229",
    messages=[{"role": "user", "content": "Hello, Claude!"}]
)

# Use GPT-4
response = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello, GPT!"}]
)

# Use any model - just change the name!
response = client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

### cURL

```bash
curl -X POST http://your-prism-server/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "claude-3-opus-20240229",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

## Supported Models

### Claude (Anthropic)
- `claude-3-opus-20240229`
- `claude-3-sonnet-20240229`
- `claude-3-haiku-20240307`
- `claude-2.1`
- `claude-2.0`

### GPT (OpenAI)
- `gpt-4-turbo-preview`
- `gpt-4`
- `gpt-3.5-turbo`
- And all other OpenAI models

## Architecture

### 元插件架构

```
User Request
     ↓
POST /v1/chat/completions
     ↓
ChainRunner 执行 ai_gateway:chat
     ↓
ai_gateway (元插件)
     ├─ 内部 chain: [model_router]
     │       ↓
     │  model_router (分析 model 字段)
     │       ↓
     ├─ claude_provider (子插件) ← 如果是 Claude
     └─ openai_provider (子插件) ← 如果是 OpenAI
     ↓
Response (OpenAI 兼容格式)
```

### 关键点

- **元插件是自成一体的**: 
  - `ai_gateway` 包含所有需要的子插件
  - 有自己内部的预设chain (`ai_gateway:chat`)
  - 外部只需要引用预设chain即可

- **配置简单**:
  ```yaml
  routes:
    /chat/completions:
      chain: [ai_gateway:chat]  # 一行搞定!
  ```

- **自动路由**:
  - 用户请求中的 `model` 字段决定使用哪个 provider
  - 完全自动,无需额外配置

## Adding New Providers

1. Create a new sub-plugin in `ai_gateway/`
2. Implement `PluginInterface`
3. Update `model_router/config.yml`:
   ```yaml
   prefix_map:
     mymodel: my_provider
   ```
4. Restart Prism

## License

MIT
