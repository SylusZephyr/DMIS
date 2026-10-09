"""The production pre-flight check (scripts/deploy.py): refuses unsafe settings, accepts a generated env file."""

import subprocess

import pytest

import scripts.deploy as deploy

GOOD = {"DMIS_DOMAIN": "dmis.acme-dental.com", "POSTGRES_PASSWORD": "Xq7vT2mLp9RsW4kZ8nBd",
        "NEO4J_PASSWORD": "Hc3fJ8uYe1VaQ6tMw5Gr", "DIP_CORS_ORIGINS": "https://dmis.acme-dental.com",
        "ANTHROPIC_API_KEY": "k", "DIP_SMTP_HOST": "smtp.acme-dental.com", "DIP_SMTP_USER": "u",
        "DIP_SMTP_PASSWORD": "p", "DIP_SMTP_FROM": "dmis@acme-dental.com"}


def test_good_settings_pass_without_warnings():
    r = deploy.check_env(GOOD)
    assert r.ok and r.warnings == []


@pytest.mark.parametrize("change,needle", [
    ({"DMIS_DOMAIN": ""}, "DMIS_DOMAIN is not set"),
    ({"DMIS_DOMAIN": "localhost"}, "set your real domain"),
    ({"DMIS_DOMAIN": "dmis.example.com"}, "set your real domain"),
    ({"DMIS_DOMAIN": "https://dmis.acme-dental.com/"}, "is not a domain name"),
    ({"POSTGRES_PASSWORD": "change-me"}, "POSTGRES_PASSWORD is a default or weak password"),
    ({"NEO4J_PASSWORD": "change-me-too"}, "NEO4J_PASSWORD is a default or weak password"),
    ({"POSTGRES_PASSWORD": "short1"}, "default or weak"),
    ({"NEO4J_PASSWORD": "aaaaaaaaaaaaaaaaaaaaaa"}, "default or weak"),
    ({"POSTGRES_PASSWORD": "Xq7vT2mLp9Rs$W4kZ8nBd"}, "break the compose file"),
    ({"NEO4J_PASSWORD": GOOD["POSTGRES_PASSWORD"]}, "are the same"),
    ({"POSTGRES_PASSWORD": ""}, "POSTGRES_PASSWORD is not set"),
    ({"DIP_AUTH": "off"}, "DIP_AUTH is off"),
    ({"DIP_CORS_ORIGINS": "*"}, "contains '*'"),
    ({"DMIS_WEB": "frontend-v9"}, "DMIS_WEB"),
])
def test_unsafe_settings_are_refused(change, needle):
    r = deploy.check_env({**GOOD, **change})
    assert not r.ok and any(needle in e for e in r.errors), r.errors


def test_localhost_only_with_the_explicit_flag_and_warnings_do_not_block():
    env = {**GOOD, "DMIS_DOMAIN": "localhost", "DIP_CORS_ORIGINS": "https://localhost"}
    assert not deploy.check_env(env).ok and deploy.check_env(env, allow_localhost=True).ok
    bare = {k: GOOD[k] for k in ("DMIS_DOMAIN", "POSTGRES_PASSWORD", "NEO4J_PASSWORD")}
    r = deploy.check_env({**bare, "DIP_SMTP_HOST": "smtp.x.com"})
    assert r.ok and any("AI provider key" in w for w in r.warnings) and any("e-mail will fail" in w for w in r.warnings)


def test_the_shipped_example_is_refused_and_a_generated_env_passes():
    example = deploy.EXAMPLE.read_text(encoding="utf-8")
    assert not deploy.check_env(deploy.parse_env(example)).ok
    text = deploy.render_env("dmis.acme-dental.com", example)
    env = deploy.parse_env(text)
    assert deploy.check_env(env).ok
    assert env["DIP_CORS_ORIGINS"] == "https://dmis.acme-dental.com" and env["POSTGRES_PASSWORD"] != env["NEO4J_PASSWORD"]
    assert "change-me" not in text and "Never commit" in text
    assert set(deploy.parse_env(example)) <= set(env)          # every setting of the example is still there
    assert deploy.render_env("a.acme.com", example) != deploy.render_env("a.acme.com", example)   # fresh secrets each time


def test_parse_env_handles_comments_and_quotes():
    env = deploy.parse_env("# c\nA=1   # note\nB='two words'\n\nC=\"x#y\"\nnot a line\n")
    assert env == {"A": "1", "B": "two words", "C": "x#y"}


def test_preflight_reports_a_missing_file_and_a_missing_compose_plugin(tmp_path, monkeypatch):
    r = deploy.preflight(tmp_path / "prod.env")
    assert not r.ok and "does not exist" in r.errors[0]
    f = tmp_path / "prod.env"
    f.write_text("\n".join(f"{k}={v}" for k, v in GOOD.items()), encoding="utf-8")
    monkeypatch.setattr(deploy.shutil, "which", lambda name: "/usr/bin/" + name)

    def no_compose(cmd):
        return subprocess.CompletedProcess(cmd, 1 if cmd[:2] == ["docker", "compose"] else 0, "", "")
    r = deploy.preflight(f, run=no_compose)
    assert r.errors == ["the docker compose plugin is missing (docker compose version failed)."]
    r = deploy.preflight(f, run=lambda cmd: subprocess.CompletedProcess(cmd, 0, "", ""))
    assert r.ok


def test_the_production_env_file_is_git_ignored():
    out = subprocess.run(["git", "check-ignore", "-q", "infra/prod.env"], cwd=deploy.ROOT)
    assert out.returncode == 0


def test_wait_for_api_polls_until_healthy():
    calls = []

    def run(cmd):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0 if len(calls) >= 3 else 1, "", "")
    assert deploy.wait_for_api(run, timeout_s=60, sleep=lambda s: None) and len(calls) == 3
    assert "exec" in calls[0] and "api" in calls[0]
