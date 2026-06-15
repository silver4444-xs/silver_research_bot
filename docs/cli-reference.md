# CLI Reference

| Command | Description |
|---------|-------------|
| `silver-research-bot onboard` | Initialize config & workspace at `~/.silver-research-bot/` |
| `silver-research-bot onboard --wizard` | Launch the interactive onboarding wizard |
| `silver-research-bot onboard -c <config> -w <workspace>` | Initialize or refresh a specific instance config and workspace |
| `silver-research-bot agent -m "..."` | Chat with the agent |
| `silver-research-bot agent -w <workspace>` | Chat against a specific workspace |
| `silver-research-bot agent -w <workspace> -c <config>` | Chat against a specific workspace/config |
| `silver-research-bot agent` | Interactive chat mode |
| `silver-research-bot agent --no-markdown` | Show plain-text replies |
| `silver-research-bot agent --logs` | Show runtime logs during chat |
| `silver-research-bot serve` | Start the OpenAI-compatible API |
| `silver-research-bot gateway` | Start the gateway |
| `silver-research-bot research run --topic "..."` | Run a full autonomous research session |
| `silver-research-bot research run --topic "..." --dry-run` | Create the research workspace without CPU execution |
| `silver-research-bot research resume -w <workspace>` | Resume a research session |
| `silver-research-bot research batch --topic "A" --topic "B"` | Run multiple research topics in batch |
| `silver-research-bot research export -w <workspace> -o <dir>` | Export paper, analysis, runs, and audit artifacts |
| `silver-research-bot status` | Show status |
| `silver-research-bot provider login openai-codex` | OAuth login for providers |
| `silver-research-bot channels login <channel>` | Authenticate a channel interactively |
| `silver-research-bot channels status` | Show channel status |

Interactive mode exits: `exit`, `quit`, `/exit`, `/quit`, `:q`, or `Ctrl+D`.
