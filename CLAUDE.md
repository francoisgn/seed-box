# seedbox: notes for agents

- Read [docs/internals.md](docs/internals.md) first: data flow, every
  mechanism and where it lives, runbooks (add a tracker, release). Do not
  explore the code to rediscover what it already says.
- Update docs/internals.md in the same commit as any change to a mechanism,
  a module's role, a config key or a runbook.
- Public repository: no real tracker names, release names or titles (see
  CONTRIBUTING.md). Tracker-specific settings live in the private
  `seedbox.toml`, never here.
- Toolbox first: for any media, file or workstation job, look in
  `toolbox/` and use its scripts before writing ad-hoc code. If a script
  almost does the job, extend it (portable, documented, no private names)
  rather than redeveloping it in the session. New recurring need = new
  toolbox script + README/internals entry.
