#!/usr/bin/env python3
"""Production deploy in one command, with a pre-flight check that refuses unsafe settings.

    python3 scripts/deploy.py up --domain dmis.example.com --admin-email you@example.com
        writes infra/prod.env with generated secrets when it does not exist yet, checks it, builds and starts the
        production stack (infra/docker-compose.prod.yml), waits for the API, creates the first admin user and
        prints the address and the admin's token (shown once).
    python3 scripts/deploy.py init --domain dmis.example.com     only write infra/prod.env
    python3 scripts/deploy.py check                              only run the pre-flight check (exit 1 on a problem)

The check refuses: a missing env file, a domain left at localhost or an example value, default or weak database
passwords, the same password for both databases, a wildcard CORS origin, sign-in turned off, an env file tracked
by git, and a server without docker compose. ``--allow-localhost`` accepts DMIS_DOMAIN=localhost for a trial run
on one machine (Caddy then uses its local certificate authority). Warnings (no AI key, incomplete SMTP) are
printed but do not stop the deploy. Standard library only, so it runs on a fresh server with python3.
"""

from __future__ import annotations

import argparse
import re
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "infra" / "prod.env"
EXAMPLE = ROOT / "infra" / "prod.env.example"
COMPOSE = ROOT / "infra" / "docker-compose.prod.yml"
MIN_PASSWORD = 16
WEAK = re.compile(r"change|example|password|secret|admin|dmis|postgres|neo4j|^(.)\1*$", re.I)
EXAMPLE_DOMAIN = re.compile(r"(^|\.)example\.(com|org|net)$|^localhost$|^127\.|^0\.0\.0\.0$", re.I)
DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$|^localhost$", re.I)

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_env(text: str) -> dict[str, str]:
    """KEY=value lines; blank lines and # comments ignored, an inline ' #' comment stripped, quotes removed."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def check_env(env: dict[str, str], allow_localhost: bool = False) -> Report:
    r = Report()
    domain = env.get("DMIS_DOMAIN", "").strip()
    if not domain:
        r.errors.append("DMIS_DOMAIN is not set: put the server's domain name (e.g. dmis.yourcompany.com).")
    elif not DOMAIN.match(domain):
        r.errors.append(f"DMIS_DOMAIN '{domain}' is not a domain name (no scheme, path or port).")
    elif EXAMPLE_DOMAIN.search(domain) and not (allow_localhost and domain.lower() == "localhost"):
        r.errors.append(f"DMIS_DOMAIN is '{domain}': set your real domain (or pass --allow-localhost for a one-machine trial).")
    pw = {k: env.get(k, "") for k in ("POSTGRES_PASSWORD", "NEO4J_PASSWORD")}
    for k, v in pw.items():
        if not v:
            r.errors.append(f"{k} is not set.")
        elif len(v) < MIN_PASSWORD or WEAK.search(v):
            r.errors.append(f"{k} is a default or weak password: use at least {MIN_PASSWORD} random characters "
                            "(`python3 scripts/deploy.py init` generates them).")
        elif re.search(r"[\s$\"'`\\]", v):
            r.errors.append(f"{k} contains spaces, quotes, '$' or a backslash, which break the compose file: use letters, digits, - and _.")
    if pw["POSTGRES_PASSWORD"] and pw["POSTGRES_PASSWORD"] == pw["NEO4J_PASSWORD"]:
        r.errors.append("POSTGRES_PASSWORD and NEO4J_PASSWORD are the same: use different passwords.")
    if env.get("DIP_AUTH", "on").strip().lower() in ("off", "0", "false", "no"):
        r.errors.append("DIP_AUTH is off: production must require sign-in (remove the line).")
    cors = [o.strip() for o in env.get("DIP_CORS_ORIGINS", "").split(",") if o.strip()]
    if "*" in cors:
        r.errors.append("DIP_CORS_ORIGINS contains '*': list only https://<your domain>.")
    elif domain and cors and f"https://{domain}" not in cors:
        r.warnings.append(f"DIP_CORS_ORIGINS does not include https://{domain}; the web app is same-origin, so this only matters "
                          "for other sites calling the API.")
    web = env.get("DMIS_WEB", "frontend-v2")
    if web not in ("frontend", "frontend-v2"):
        r.errors.append(f"DMIS_WEB is '{web}': use frontend-v2 or frontend.")
    if not (env.get("ANTHROPIC_API_KEY") or env.get("GEMINI_API_KEY")):
        r.warnings.append("No AI provider key (ANTHROPIC_API_KEY or GEMINI_API_KEY): every number still works; answer "
                          "phrasing, machine translation and the LLM attribute tier stay off.")
    if env.get("DIP_SMTP_HOST") and not (env.get("DIP_SMTP_USER") and env.get("DIP_SMTP_PASSWORD") and env.get("DIP_SMTP_FROM")):
        r.warnings.append("DIP_SMTP_HOST is set but DIP_SMTP_USER, DIP_SMTP_PASSWORD or DIP_SMTP_FROM is empty: e-mail will fail.")
    if not env.get("DIP_SMTP_HOST"):
        r.warnings.append("No SMTP server: alerts and daily summaries are shown in the app but not e-mailed.")
    return r


def check_host(run: Runner = _run, env_file: Path = ENV_FILE) -> Report:
    r = Report()
    if shutil.which("docker") is None:
        r.errors.append("docker is not installed (https://docs.docker.com/engine/install/).")
    else:
        v = run(["docker", "compose", "version"])
        if v.returncode != 0:
            r.errors.append("the docker compose plugin is missing (docker compose version failed).")
    if shutil.which("git") and (ROOT / ".git").exists() and env_file.resolve().is_relative_to(ROOT):
        rel = env_file.resolve().relative_to(ROOT)
        t = run(["git", "ls-files", "--error-unmatch", str(rel)])
        if t.returncode == 0:
            r.errors.append(f"{rel} is tracked by git: it holds secrets; `git rm --cached` it.")
    return r


def preflight(env_file: Path = ENV_FILE, allow_localhost: bool = False, run: Runner = _run) -> Report:
    if not env_file.exists():
        return Report(errors=[f"{env_file.relative_to(ROOT) if env_file.is_relative_to(ROOT) else env_file} does not exist: "
                              "run `python3 scripts/deploy.py init --domain <your domain>`."])
    r = check_env(parse_env(env_file.read_text(encoding="utf-8")), allow_localhost)
    h = check_host(run, env_file)
    return Report(r.errors + h.errors, r.warnings + h.warnings)


def render_env(domain: str, example: str, token: Callable[[int], str] = secrets.token_urlsafe) -> str:
    """The example env file with the domain, CORS origin and freshly generated database passwords filled in."""
    def pw() -> str:
        while True:            # token_urlsafe may start with '-'; keep values plain and strong
            v = token(24)
            if v[0].isalnum() and not WEAK.search(v):
                return v
    values = {"DMIS_DOMAIN": domain, "POSTGRES_PASSWORD": pw(), "NEO4J_PASSWORD": pw(), "DIP_CORS_ORIGINS": f"https://{domain}"}
    out = []
    for line in example.splitlines():
        k = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if k in values:
            comment = re.search(r"\s+#.*$", line.split("=", 1)[1])
            line = f"{k}={values.pop(k)}" + (f"  {comment.group(0).strip()}" if comment else "")
        out.append(line)
    out += [f"{k}={v}" for k, v in values.items()]
    return "\n".join(out).replace("# Copy to infra/prod.env (never commit it) and change every value.",
                                  "# Generated by scripts/deploy.py init. Never commit this file.") + "\n"


def cmd_init(domain: str, force: bool = False) -> int:
    if ENV_FILE.exists() and not force:
        print(f"{ENV_FILE.relative_to(ROOT)} already exists; leaving it unchanged (--force overwrites it).")
        return 0
    if not DOMAIN.match(domain):
        print(f"'{domain}' is not a domain name.", file=sys.stderr)
        return 2
    ENV_FILE.write_text(render_env(domain, EXAMPLE.read_text(encoding="utf-8")), encoding="utf-8")
    ENV_FILE.chmod(0o600)
    print(f"wrote {ENV_FILE.relative_to(ROOT)} (database passwords generated; add AI and SMTP settings there if you use them)")
    return 0


def show(r: Report) -> None:
    for w in r.warnings:
        print(f"  warning: {w}")
    for e in r.errors:
        print(f"  REFUSED: {e}", file=sys.stderr)
    print("pre-flight check: " + ("passed" if r.ok else f"{len(r.errors)} problem(s); nothing was started"))


def compose(*args: str) -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE), "--env-file", str(ENV_FILE), *args]


def wait_for_api(run: Runner = _run, timeout_s: int = 300, sleep: Callable[[float], None] = time.sleep) -> bool:
    probe = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v2/health', timeout=5)"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if run(compose("exec", "-T", "api", "python", "-c", probe)).returncode == 0:
            return True
        sleep(5)
    return False


def cmd_up(a: argparse.Namespace, run: Runner = _run) -> int:
    if not ENV_FILE.exists():
        if not a.domain:
            print("infra/prod.env does not exist: pass --domain <your domain> to generate it.", file=sys.stderr)
            return 2
        if cmd_init(a.domain):
            return 2
    r = preflight(ENV_FILE, a.allow_localhost, run)
    show(r)
    if not r.ok:
        return 1
    print("building and starting the stack (the first build takes several minutes) ...")
    if subprocess.run(compose("up", "-d", "--build"), cwd=ROOT).returncode != 0:
        print("docker compose up failed; see the output above.", file=sys.stderr)
        return 1
    if not wait_for_api(run):
        print("the API did not become healthy in 5 minutes: `docker compose -f infra/docker-compose.prod.yml logs api`.",
              file=sys.stderr)
        return 1
    env = parse_env(ENV_FILE.read_text(encoding="utf-8"))
    print(f"DMIS is running at https://{env['DMIS_DOMAIN']}")
    if a.admin_email:
        u = run(compose("exec", "-T", "api", "python", "scripts/dmis.py", "create-user", a.admin_email, a.admin_name,
                        "--role", "admin"))
        print(u.stdout.strip() or u.stderr.strip())
        if u.returncode != 0:
            return 1
        print("paste the token into the sign-in dialog; it is not shown again.")
    print("backups: docker compose -f infra/docker-compose.prod.yml exec api python scripts/dmis.py backup  (see docs/DEPLOYMENT_GUIDE.md)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="write infra/prod.env with generated secrets")
    i.add_argument("--domain", required=True)
    i.add_argument("--force", action="store_true")
    c = sub.add_parser("check", help="pre-flight check only")
    c.add_argument("--allow-localhost", action="store_true")
    u = sub.add_parser("up", help="check, build, start, create the first admin")
    u.add_argument("--domain")
    u.add_argument("--admin-email")
    u.add_argument("--admin-name", default="Admin")
    u.add_argument("--allow-localhost", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "init":
        return cmd_init(a.domain, a.force)
    if a.cmd == "check":
        r = preflight(ENV_FILE, a.allow_localhost)
        show(r)
        return 0 if r.ok else 1
    return cmd_up(a)


if __name__ == "__main__":
    sys.exit(main())
