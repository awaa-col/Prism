# Claude Provider Plugin

Anthropic Claude AI integration for Prism with OpenAI-compatible API.

## Supported Models

- `claude-3-opus-20240229` - Most powerful, best for complex tasks
- `claude-3-sonnet-20240229` - Balanced performance and speed
- `claude-3-haiku-20240307` - Fastest, best for simple tasks
- `claude-2.1` - Previous generation
- `claude-2.0` - Previous generation

## Setup

### 1. Install Dependencies

```bash
pip install anthropic
```

### 2. Set API Key

```bash
# Linux/Mac
export ANTHROPIC_API_KEY="sk-ant-api03-..."

# Windows PowerShell
$env:ANTHROPIC_API_KEY="sk-ant-api03-..."

# Or in .env file
ANTHROPIC_API_KEY=sk-ant-api03-...
```

### 3. Configure Router

Update `plugins/model_router/config.yml`:

```yaml
prefix_map:
  claude: claude_provider
```

## Usage

### Basic Request

```bash
curl -X POST http://your-prism-server/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "claude-3-opus-20240229",
    "messages": [
      {"role": "user", "content": "Hello, Claude!"}
    ]
  }'
```

### With System Message

```bash
curl -X POST http://your-prism-server/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "claude-3-sonnet-20240229",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is the capital of France?"}
    ]
  }'
```

### Streaming

```bash
curl -X POST http://your-prism-server/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "claude-3-haiku-20240307",
    "messages": [{"role": "user", "content": "Tell me a story"}],
    "stream": true
  }'
```

### Python SDK

```python
import openai

client = openai.OpenAI(
    base_url="http://your-prism-server/v1",
    api_key="your-api-key"
)

response = client.chat.completions.create(
    model="claude-3-opus-20240229",
    messages=[
        {"role": "user", "content": "Hello, Claude!"}
    ]
)

print(response.choices[0].message.content)
```

## Features

### ✅ Supported

- Non-streaming responses
- Streaming responses (SSE)
- System messages
- Temperature control
- Max tokens limit
- Top-p sampling
- Stop sequences
- Token usage tracking

### ❌ Not Supported

- Function calling (Claude uses tools API differently)
- Logprobs
- Multiple choices (n > 1)
- Logit bias

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | string | required | Claude model name |
| `messages` | array | required | Array of message objects |
| `temperature` | float | 1.0 | Sampling temperature (0.0-1.0) |
| `max_tokens` | integer | 1024 | Maximum tokens to generate |
| `top_p` | float | null | Nucleus sampling parameter |
| `stop` | string/array | null | Stop sequences |
| `stream` | boolean | false | Enable streaming |

## Error Handling

| Status | Meaning |
|--------|---------|
| 400 | Invalid request parameters |
| 401 | Invalid or missing API key |
| 429 | Rate limit exceeded |
| 500 | Claude API error or internal error |
| 503 | Service unavailable |

## Differences from OpenAI

1. **System Messages**: Claude requires system messages to be passed separately. This plugin automatically extracts them.

2. **Content Blocks**: Claude returns content as blocks. This plugin extracts text blocks.

3. **Stop Reason**: Claude's stop reasons are mapped to OpenAI's finish_reason.

4. **Max Tokens**: Claude requires max_tokens, while OpenAI makes it optional.

## Development

### Testing

```python
import pytest
from app.plugins.interface import RequestContext

@pytest.mark.asyncio
async def test_claude_basic():
    provider = ClaudeProvider()
    await provider.initialize()
    
    context = RequestContext(request_data={
        "model": "claude-3-haiku-20240307",
        "messages": [{"role": "user", "content": "Hello"}]
    })
    
    await provider.handle(context, None)
    
    assert "choices" in context.response_data
    assert context.response_data["object"] == "chat.completion"
```

## Resources

- [Anthropic API Documentation](https://docs.anthropic.com/claude/reference)
- [Claude Models Overview](https://docs.anthropic.com/claude/docs/models-overview)
- [Pricing](https://www.anthropic.com/pricing)

## License

MIT
