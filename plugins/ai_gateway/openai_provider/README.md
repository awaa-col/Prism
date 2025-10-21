# OpenAI Provider Plugin

OpenAI GPT API integration for Prism.

## Supported Models

### GPT-4 Turbo
- `gpt-4-turbo-preview` - Latest preview
- `gpt-4-0125-preview` - January 2024
- `gpt-4-1106-preview` - November 2023

### GPT-4
- `gpt-4` - Latest stable
- `gpt-4-32k` - Extended context

### GPT-3.5 Turbo
- `gpt-3.5-turbo` - Latest
- `gpt-3.5-turbo-0125` - January 2024
- `gpt-3.5-turbo-16k` - Extended context

## Setup

### 1. Install Dependencies

```bash
pip install openai
```

### 2. Set API Key

```bash
# Linux/Mac
export OPENAI_API_KEY="sk-proj-..."

# Windows PowerShell
$env:OPENAI_API_KEY="sk-proj-..."

# Or in .env file
OPENAI_API_KEY=sk-proj-...
```

### 3. Configure Router

Update `plugins/model_router/config.yml`:

```yaml
prefix_map:
  gpt: openai_provider
```

## Usage

### Basic Request

```bash
curl -X POST http://your-prism-server/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "gpt-4-turbo-preview",
    "messages": [
      {"role": "user", "content": "Hello, GPT!"}
    ]
  }'
```

### With System Message

```bash
curl -X POST http://your-prism-server/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "gpt-3.5-turbo",
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
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Tell me a story"}],
    "stream": true
  }'
```

### Python SDK

```python
import openai

# Just change the base_url!
client = openai.OpenAI(
    base_url="http://your-prism-server/v1",
    api_key="your-prism-api-key"  # Prism API key, not OpenAI key
)

response = client.chat.completions.create(
    model="gpt-4-turbo-preview",
    messages=[
        {"role": "user", "content": "Hello, GPT!"}
    ]
)

print(response.choices[0].message.content)
```

## Features

### ✅ Fully Supported

- Non-streaming responses
- Streaming responses (SSE)
- System messages
- Temperature control
- Max tokens limit
- Top-p sampling
- Stop sequences
- Token usage tracking
- Function calling
- Multiple choices (n > 1)
- Logprobs
- Logit bias

### Why This Plugin is Simple

Since Prism's API is designed to be OpenAI-compatible, this plugin is essentially a pass-through! It just forwards the request to OpenAI and returns the response with minimal processing.

## Parameters

All OpenAI parameters are supported:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | string | required | GPT model name |
| `messages` | array | required | Array of message objects |
| `temperature` | float | 1.0 | Sampling temperature (0.0-2.0) |
| `max_tokens` | integer | null | Maximum tokens to generate |
| `top_p` | float | 1.0 | Nucleus sampling parameter |
| `n` | integer | 1 | Number of completions |
| `stop` | string/array | null | Stop sequences |
| `stream` | boolean | false | Enable streaming |
| `presence_penalty` | float | 0.0 | Presence penalty (-2.0-2.0) |
| `frequency_penalty` | float | 0.0 | Frequency penalty (-2.0-2.0) |
| `logit_bias` | object | null | Logit bias |
| `functions` | array | null | Function definitions |
| `function_call` | string/object | null | Function calling control |

## Error Handling

| Status | Meaning |
|--------|---------|
| 400 | Invalid request parameters |
| 401 | Invalid or missing API key |
| 429 | Rate limit exceeded |
| 500 | OpenAI API error or internal error |
| 503 | Service unavailable |

## Development

### Testing

```python
import pytest
from app.plugins.interface import RequestContext

@pytest.mark.asyncio
async def test_openai_basic():
    provider = OpenAIProvider()
    await provider.initialize()
    
    context = RequestContext(request_data={
        "model": "gpt-3.5-turbo",
        "messages": [{"role": "user", "content": "Hello"}]
    })
    
    await provider.handle(context, None)
    
    assert "choices" in context.response_data
    assert context.response_data["object"] == "chat.completion"
```

## Resources

- [OpenAI API Documentation](https://platform.openai.com/docs/api-reference)
- [Models Overview](https://platform.openai.com/docs/models)
- [Pricing](https://openai.com/pricing)

## License

MIT
