# Claude Notes

See AGENTS.md for project rules.

## Running Tests

This project uses `unittest`, NOT pytest. pytest is not installed.

```
uv run python -m unittest discover -s tests -q
```

Never call `uv run pytest` or `pytest` — it will fail.
