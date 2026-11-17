# sys-inspector

Modular diagnostics / health-check framework (SSH + MySQL/MariaDB), configured via YAML and `.env`.

## Structure
```
main.py                 CLI (argparse)
core/engine.py          orchestration: .env + YAML -> connections -> checks
core/config.py          YAML loading and ${VAR} / ${VAR:-default} interpolation
core/checks.py          check types (extensible registry)
core/ssh_client.py      Paramiko wrapper (keys, known_hosts, RejectPolicy)
core/ssh_tunnel.py      SSH port-forwarding to the database
core/db_client.py       PyMySQL wrapper (read-only session)
config/projects/*.yaml  one file per project
```

## Setup
```bash
git clone git@github.com:damworks/sys-inspector.git
cd sys-inspector

cp .env.example .env                                            # fill in the values (never commit it)
cp config/projects/my_project.yaml config/projects/your_project.yaml  # edit: name, paths, patterns, commands

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py --project your_project --env production
```
`config/projects/*.yaml` (except `my_project.yaml`) and `.env` are gitignored: every clone needs its own copies
with real values, they are never pushed to the repo.

To use the plain `python main.py ...` form, activate the virtualenv first in that shell
(`source .venv/bin/activate`, then `python main.py ...`; `deactivate` when done).

Options: `--only ssh_disk_usage` (check type or name, repeatable), `--format json`, `--config-dir`, `--env-file`.
Exit codes: `0` all OK/WARN, `1` at least one FAIL, `2` configuration error (e.g. missing variable).

## Scheduling with cron
Cron does not run an interactive shell, so it has no `PATH`/venv activation. Always use absolute paths
(matching where you actually cloned the repo on that machine), redirect output to a log file, and guard
against overlapping runs with `flock`.

```bash
#!/bin/bash
# run_cron.sh
set -euo pipefail
cd /opt/sys-inspector        # adjust to your clone path

LOGFILE="/var/log/sys-inspector/$(date +%F).log"
mkdir -p "$(dirname "$LOGFILE")"

exec flock -n /tmp/sys-inspector.lock \
  .venv/bin/python main.py \
    --project your_project --env production \
    --env-file /opt/sys-inspector/.env \
    >> "$LOGFILE" 2>&1
```
```
# crontab -e
0 7 * * * /opt/sys-inspector/run_cron.sh
```
`.env` and `config/projects/your_project.yaml` must already exist on that machine (Setup above), and `run_cron.sh`
needs to be executable (`chmod +x run_cron.sh`).

## Docker
```bash
docker compose build
docker compose run --rm inspector --project my_project --env staging
```
`.env` is injected as environment variables; `config/` and `~/.ssh` are mounted read-only (`~/.ssh` at the same
absolute path as on the host, so `*_SSH_KEY_PATH` and `SSH_KNOWN_HOSTS` in `.env` must be absolute host paths,
e.g. `/home/you/.ssh/id_ed25519`; `~` is not expanded to your home inside the container).
Variables already present in the environment (CI, `docker run -e`) take precedence over `.env`.

## Available checks
| type | main parameters |
|---|---|
| `ssh_disk_usage` | `paths` (relative to `ssh.workdir`), `top` → `du -h --max-depth=1 <path> \| sort -hr` |
| `console_command` | `command`, `expect_exit_code`, `fail_if_contains`, `warn_if_contains` |
| `db_table_analysis` | `pattern` (LIKE), `must_exist`, `exact_count`, `warn_total_mb`, `fail_total_mb` |

New check: subclass `Check` in `core/checks.py` and decorate it with `@register`.

## SSH tunnel to the DB
With `database.tunnel: true` (in the YAML, typically `${STAGING_DB_TUNNEL:-true}`) the database is reached through the
environment's SSH connection (Paramiko port-forwarding on an ephemeral local port of 127.0.0.1, no extra dependencies).
`database.host`/`port` are the ones **as seen from the SSH server** (e.g. `127.0.0.1` if MySQL runs on that server, or the DB's internal host).
With `tunnel: false` (default) the connection is direct. Requires the environment's `ssh` section.

## Security
- No secrets in YAML files: only `${VAR}` references; missing variables cause an explicit error.
- SSH host key is verified (`known_hosts`); use `strict_host_key: false` for testing only.
- Paths and commands are quoted with `shlex` (no shell injection from config).
- DB: `READ ONLY` session, parameterized queries; use a DB user with read-only privileges.
- InnoDB `TABLE_ROWS` is an estimate (shown as `~` in the output); use `exact_count: true` for the real count.
