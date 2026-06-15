# Install and Quick Start

## Install

> [!IMPORTANT]
> This README may describe features that are available first in the latest source code.
> If you want the newest features and experiments, install from source.
> If you want the most stable day-to-day experience, install from PyPI or with `uv`.

**Install from source** (latest features, experimental changes may land here first; recommended for development)

```bash
git clone https://github.com/HKUDS/silver-research-bot.git
cd silver-research-bot
pip install -e .
```

**Install with [uv](https://github.com/astral-sh/uv)** (stable release, fast)

```bash
uv tool install silver-research-bot-ai
```

**Install from PyPI** (stable release)

```bash
pip install silver-research-bot-ai
```

### Update to latest version

**PyPI / pip**

```bash
pip install -U silver-research-bot-ai
silver-research-bot --version
```

**uv**

```bash
uv tool upgrade silver-research-bot-ai
silver-research-bot --version
```

**Using WhatsApp?** Rebuild the local bridge after upgrading:

```bash
rm -rf ~/.silver-research-bot/bridge
silver-research-bot channels login whatsapp
```

## Quick Start

> [!TIP]
> Set your API key in `~/.silver-research-bot/config.json`.
> Get API keys: [OpenRouter](https://openrouter.ai/keys) (Global)
>
> For other LLM providers, please see [`configuration.md`](./configuration.md).
>
> For web search capability setup, please see the web-search section in [`configuration.md`](./configuration.md#web-search).

**1. Initialize**

```bash
silver-research-bot onboard
```

Use `silver-research-bot onboard --wizard` if you want the interactive setup wizard.

**2. Configure** (`~/.silver-research-bot/config.json`)

Configure these **two parts** in your config (other options have defaults).

*Set your API key* (e.g. OpenRouter, recommended for global users):
```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  }
}
```

*Set your model* (optionally pin a provider â€?defaults to auto-detection):
```json
{
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-5",
      "provider": "openrouter"
    }
  }
}
```

**3. Chat**

```bash
silver-research-bot agent
```

That's it! You have a working AI agent in 2 minutes.
