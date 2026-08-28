# Install and Quick Start

## Install

> [!IMPORTANT]
> This README may describe features that are available first in the latest source code.
> Install from source to get the newest features and experiments. The package is not
> yet published to PyPI.

**Install from source**

```bash
git clone https://github.com/silver4444-xs/silver_research_bot.git
cd silver_research_bot
pip install -e ".[dev]"
```

### Update to latest version

```bash
git pull
pip install -e ".[dev]"
```

**Using WhatsApp?** Rebuild the local bridge after upgrading:

```bash
rm -rf ~/.silver_research_bot/bridge
silver-research-bot channels login whatsapp
```

## Quick Start

> [!TIP]
> Set your API key in `~/.silver_research_bot/config.json`.
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

**2. Configure** (`~/.silver_research_bot/config.json`)

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

*Set your model* (optionally pin a provider —defaults to auto-detection):
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
