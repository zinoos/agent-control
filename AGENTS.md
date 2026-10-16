# Notes for AI agents

## Before every push: run the tests

GitHub Actions doesn't run the tests for this repo, so they have to pass locally before anything is pushed.

1. Get the latest `master` and merge it into your branch first, so the tests run against what will be merged:
   ```sh
   git fetch origin && git merge origin/master
   ```
2. Run the whole suite (from the repo root):
   ```sh
   dbus-run-session -- python3 -m unittest discover -s tests -t . -v
   ```
3. Push only if it ends with `OK`. If a test fails, fix the code (or the test, if the behavior change is
   intended) and run everything again. Never push failing tests, and never skip or delete a test to get green.

The tests use their own HOME, settings and tmux server and a fake `claude`, so they never touch real agents.
The GUI tests (`tests/test_app.py`) need a display and briefly open a window; without one they are skipped,
so say so in your report when that happened.

## New code needs tests

Changes to behavior come with a test in `tests/`, including edge cases (missing or broken files, wrong
values, empty input). Use `unittest` only: no extra dependencies.
