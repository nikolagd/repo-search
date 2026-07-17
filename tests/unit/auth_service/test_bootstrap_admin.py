from __future__ import annotations

import pytest

from microservices.auth_service import bootstrap_admin
from microservices.auth_service.auth import AdminAlreadyExistsError


def install_prompts(
    monkeypatch: pytest.MonkeyPatch,
    *,
    username: str = " FirstAdmin ",
    password: str = "sentinel-bootstrap-password",
    confirmation: str | None = None,
) -> None:
    responses = iter([password, password if confirmation is None else confirmation])
    monkeypatch.setattr("builtins.input", lambda _prompt: username)
    monkeypatch.setattr(bootstrap_admin.getpass, "getpass", lambda _prompt: next(responses))


def test_bootstrap_command_creates_normalized_administrator_without_emitting_secrets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    password = "sentinel-bootstrap-password"
    stored_hash = "sentinel-password-hash"
    captured: dict[str, str] = {}
    install_prompts(monkeypatch, password=password)

    def fake_bootstrap(username: str, supplied_password: str) -> dict[str, object]:
        captured.update(username=username, password=supplied_password)
        return {"id": 1, "username": username, "password_hash": stored_hash}

    monkeypatch.setattr(bootstrap_admin, "bootstrap_admin_user", fake_bootstrap)

    assert bootstrap_admin.main() == 0

    output = capsys.readouterr()
    combined_output = output.out + output.err
    assert captured == {"username": "firstadmin", "password": password}
    assert output.out.strip() == "Administrator account created."
    assert password not in combined_output
    assert stored_hash not in combined_output


def test_bootstrap_command_refuses_existing_administrator_without_emitting_password(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    password = "sentinel-bootstrap-password"
    install_prompts(monkeypatch, password=password)

    def refuse_bootstrap(_username: str, _password: str) -> None:
        raise AdminAlreadyExistsError(password)

    monkeypatch.setattr(bootstrap_admin, "bootstrap_admin_user", refuse_bootstrap)

    assert bootstrap_admin.main() == 3

    output = capsys.readouterr()
    combined_output = output.out + output.err
    assert output.err.strip() == "An administrator account already exists."
    assert password not in combined_output


def test_bootstrap_command_hides_unexpected_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    password = "sentinel-bootstrap-password"
    stored_hash = "sentinel-password-hash"
    install_prompts(monkeypatch, password=password)

    def fail_bootstrap(_username: str, _password: str) -> None:
        raise RuntimeError(f"{password} {stored_hash}")

    monkeypatch.setattr(bootstrap_admin, "bootstrap_admin_user", fail_bootstrap)

    assert bootstrap_admin.main() == 1

    output = capsys.readouterr()
    combined_output = output.out + output.err
    assert output.err.strip() == "Administrator bootstrap failed."
    assert password not in combined_output
    assert stored_hash not in combined_output


def test_bootstrap_command_rejects_invalid_username_before_password_prompt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "x")
    monkeypatch.setattr(
        bootstrap_admin.getpass,
        "getpass",
        lambda _prompt: pytest.fail("Password must not be requested for an invalid username."),
    )

    assert bootstrap_admin.main() == 2
    assert capsys.readouterr().err.strip() == "Username must contain between 3 and 80 characters."


@pytest.mark.parametrize(
    ("password", "confirmation", "expected_message"),
    [
        ("short", "short", "Password must contain between 8 and 200 characters."),
        ("valid-password", "different-password", "Password confirmation does not match."),
    ],
)
def test_bootstrap_command_rejects_invalid_password_input(
    password: str,
    confirmation: str,
    expected_message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_prompts(monkeypatch, password=password, confirmation=confirmation)
    monkeypatch.setattr(
        bootstrap_admin,
        "bootstrap_admin_user",
        lambda _username, _password: pytest.fail("Invalid input must not reach the database."),
    )

    assert bootstrap_admin.main() == 2
    assert capsys.readouterr().err.strip() == expected_message
