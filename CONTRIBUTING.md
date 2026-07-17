# Contributing to Silver Research Bot

Thanks for your interest in contributing! This document outlines the process for contributing code, reporting bugs, and suggesting features.

## Development Setup

```bash
git clone https://github.com/HKUDS/silver-research-bot
cd silver_research_bot
pip install -e ".[dev]"
cp .env.example .env
```

## Coding Conventions

- **Python 3.11+** with `from __future__ import annotations`
- Imports: `from silver_research_bot.xxx` (not `nanobot`)
- Frontend: Vue 3 + Vite, pure JS (no TypeScript), SVG icons (no emoji in code)
- Read [AGENTS.md](AGENTS.md) for the full module map, data flow, and known pitfalls

## Pull Request Process

1. Fork the repository and create a feature branch
2. Make your changes following the coding conventions above
3. Run tests: `pytest tests/`
4. Ensure the frontend builds: `cd web && npm run build`
5. Open a PR with a clear description of what changed and why

## Issue Guidelines

- **Bug reports**: Include steps to reproduce, expected vs actual behavior, and environment details (OS, Python version, browser)
- **Feature requests**: Describe the use case and proposed solution
- Use the issue templates when available

## Project Structure

See the [README](README.md#-项目结构) for a complete directory tree and module descriptions.
