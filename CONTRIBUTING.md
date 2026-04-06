# Contributing

Thanks for helping with Lumin.

## Local setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Or use:

```bash
./setup.sh
```

## Run tests

```bash
python3 -m compileall main.py
python3 -m compileall engine/ proxy/
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Submitting a PR

- Keep PRs focused.
- Follow existing code patterns and naming.
- Add or update tests when behavior changes.
- Include a short note on what you changed and how you verified it.

## What kinds of PRs are welcome

- bug fixes
- performance improvements
- provider compatibility fixes
- dashboard simplification
- better docs and setup flows
- tests for existing behavior

## Code style

- Follow the existing patterns in the repo.
- Prefer small, readable functions.
- Be honest in docs and UI about what is fully built vs partial.

## What to work on

- Look at open issues first.
- Good first contributions are docs, tests, small bug fixes, and provider polish.
