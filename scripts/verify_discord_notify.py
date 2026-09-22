"""Headless verify: the notify task's discord configuration gate.

Run under a project hython (``TH_PROJECT_PATH`` at any project), e.g. via
TumbleTrove Desktop's run_hython:

    hython scripts/verify_discord_notify.py

Nothing is posted to Discord and nothing is submitted to Deadline: the
`discord` library is stubbed, so reaching `client.run` is observed rather
than performed.

Background: a project created from `scripts/project_template` carries an
EMPTY `config:/discord` block -- `token: ""`, no users, no channels. Before
1.52.2 `get_token()` tested `is None`, so that empty string read as a set
token and sailed past its own guard; the channel lookup then failed the
notify job, and since notify is the last job in every family, every render
and playblast batch on such a project read as failed.

Checks:

 1. `get_token()` reads a blank token as None, so the template's `""` is
    "not configured" rather than a token that cannot log in.
 2. `is_configured()` agrees with what the project's config actually holds
    (token AND at least one channel).
 3. On a project with no discord at all, `_post` skips and returns 0 -- a
    successful render does not go red over a message nobody configured.
 4. On a configured project, an unknown channel still returns 1, and names
    the channels that do exist so the farm log carries the fix.
 5. On a configured project, a known channel gets past the gate and reaches
    the discord client (the stub records the attempt).

Checks 3 and 5 are mutually exclusive for any one project, so each is
skipped (not failed) when the project under test is not of that kind. Run it
once against a configured project and once against an unconfigured one for
full coverage; `scripts/audit_discord_config.py` lists which is which.
"""

from __future__ import annotations

import sys
import types


def _stub_discord() -> dict:
    """Replace the `discord` library with a recorder. Returns the record."""
    record = {'ran_with_token': None}

    class _Intents:
        @staticmethod
        def default():
            return object()

    class _Client:
        def __init__(self, **kwargs):
            pass

        def event(self, fn):
            return fn

        def run(self, token):
            record['ran_with_token'] = token

    stub = types.ModuleType('discord')
    stub.Intents = _Intents
    stub.Client = _Client
    stub.File = object
    sys.modules['discord'] = stub
    return record


def main() -> int:
    record = _stub_discord()

    from tumblepipe.config import discord as discord_config
    from tumblepipe.farm.tasks.notify import notify

    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(bool(ok))
        line = f"{'PASS' if ok else 'FAIL'}: {name}"
        if detail and not ok:
            line += f" — {detail}"
        print(line)

    async def _callback(channel, user, fallback_name):
        raise AssertionError('callback reached without a real client')

    token = discord_config.get_token()
    channels = discord_config.list_channels()
    configured = discord_config.is_configured()
    print(f"project discord: token={'set' if token else 'unset'} "
          f"channels={sorted(channels)}")

    # 1. A blank token is not a token.
    check(
        "get_token() never returns a blank string",
        token is None or len(token.strip()) != 0,
        repr(token),
    )

    # 2. is_configured() matches the parts it is made of.
    check(
        "is_configured() == (token and at least one channel)",
        configured == (token is not None and len(channels) != 0),
        f"is_configured={configured}",
    )

    # 3. Unconfigured project: skip, exit 0.
    if not configured:
        record['ran_with_token'] = None
        code = notify._post('nobody', 'renders', _callback)
        check("unconfigured project: _post skips and returns 0", code == 0, str(code))
        check(
            "unconfigured project: no discord client was run",
            record['ran_with_token'] is None,
        )
        check("SKIPPED (configured-project checks need a configured project)", True)
        print("ALL PASS" if all(results) else "FAILURES")
        return 0 if all(results) else 1

    # 4. Configured project, unknown channel: fail, and name the known ones.
    unknown = 'no-such-channel-' + 'x' * 8
    record['ran_with_token'] = None
    code = notify._post('nobody', unknown, _callback)
    check("configured project: an unknown channel returns 1", code == 1, str(code))
    check(
        "configured project: an unknown channel runs no client",
        record['ran_with_token'] is None,
    )

    # 5. Configured project, known channel: past the gate, into the client.
    known = sorted(channels)[0]
    record['ran_with_token'] = None
    notify._post('nobody', known, _callback)
    check(
        f"configured project: channel '{known}' reaches the discord client",
        record['ran_with_token'] == token,
    )

    print("ALL PASS" if all(results) else "FAILURES")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
