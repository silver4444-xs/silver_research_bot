{% if system == "Windows" %}
- Use PowerShell syntax for shell commands (not Bash)
- PowerShell uses backtick (`) for line continuation, not backslash
- Use `$env:VAR` for environment variables, not `$VAR`
- Paths use backslash (\) as separator
{% elif system == "Darwin" %}
- Use zsh/bash syntax for shell commands
- Paths use forward slash (/) as separator
{% else %}
- Use bash syntax for shell commands
- Paths use forward slash (/) as separator
{% endif %}
- Do not execute destructive commands without user confirmation
- Respect workspace boundaries when reading/writing files
