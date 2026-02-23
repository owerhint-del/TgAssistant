# Contributing to TgAssistant

Thanks for your interest in contributing! This guide will help you get started.

## Reporting Bugs

- Open a [GitHub issue](../../issues) with a clear title and description.
- Include steps to reproduce, expected behavior, and actual behavior.
- Add relevant logs, screenshots, or error messages if possible.

## Suggesting Features

- Open a GitHub issue with the `enhancement` label.
- Describe the use case and why the feature would be useful.

## Submitting Changes

1. **Fork** the repository.
2. **Create a branch** from `main`:
   ```bash
   git checkout -b my-feature
   ```
3. Make your changes and commit with clear messages.
4. **Push** your branch and open a **Pull Request** against `main`.
5. Describe what your PR does and link any related issues.

## Development Setup

```bash
# Clone your fork
git clone https://github.com/<your-username>/TgAssistant.git
cd TgAssistant

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run tests
pytest
```

## Code Style

- Write clean, readable Python.
- Keep it simple -- prefer clarity over cleverness.
- Follow [PEP 8](https://peps.python.org/pep-0008/) conventions.
- Add comments where the intent isn't obvious.

## Pull Request Guidelines

- Keep PRs focused -- one change per PR when possible.
- Make sure all tests pass before submitting.
- Update documentation if your change affects it.

## Code of Conduct

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).
