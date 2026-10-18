# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report them privately from the repository's **Security** tab:
**Report a vulnerability** (GitHub private vulnerability reporting).
The report is only visible to you and the maintainer.

Please include what you found, how to reproduce it and the impact you
expect. You should get an answer within a week.

## Supported versions

Only the latest release is supported.

## Scope

seedbox reads the library read-only, talks to qBittorrent and Prowlarr
through their read-only API endpoints, and serves a static dashboard.
Relevant reports include, for example: credential leaks (logs, output
files), script injection in the dashboard, writing outside the output
directory, or the HTTP server exposing files beyond the output directory.
