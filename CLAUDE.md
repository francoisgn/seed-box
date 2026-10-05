# seedbox: notes for agents

- Read [docs/internals.md](docs/internals.md) first: data flow, every
  mechanism and where it lives, runbooks (add a tracker, release). Do not
  explore the code to rediscover what it already says.
- Update docs/internals.md in the same commit as any change to a mechanism,
  a module's role, a config key or a runbook.
- Public repository: no real tracker names, release names or titles (see
  CONTRIBUTING.md). Tracker-specific settings live in the private
  `seedbox.toml`, never here.
