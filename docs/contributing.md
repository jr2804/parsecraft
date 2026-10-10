---
title: Contributing
---

## Development setup

```bash
# Clone the repository
git clone https://github.com/jr2804/parsecraft.git
cd parsecraft

# Install dependencies
uv sync --dev

# Install mise tasks
mise install
```

## Running tests

```bash
mise test
```

## Code quality

```bash
mise lint         # ruff check
mise typecheck    # ty check
mise spell        # codespell
mise format       # ruff format + isort + pyreorder
mise format-check # verify the tree is canonical (what CI runs)
```

## Pull requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request
