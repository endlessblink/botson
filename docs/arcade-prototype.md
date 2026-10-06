# Separate opt-in arcade prototype

`services/arcade` is an independent FastAPI service using dependencies already
present in Botson. Botson only supplies requested `/arcade` entry and a private
Mini App button; it does not run the game, broadcast invitations, publish scores,
or start another service. Public configuration is disabled with no URL/topics.

The first game is a progressive four-pad memory sequence. Everyone gets the
same daily challenge. Server state validates each round, order, timing, session
ownership, expiry, attempt limit and replay. The API never accepts a score from
the browser. Equal verified scores have equal rank; network speed is not used
to rank players. This is server validation, not proof against automated players
who can read a visible sequence. No payments, rewards or client score authority.

The leaderboard is shared by eligible members of the existing configured group.
It exposes only generated aliases, best verified scores and rank. The SQLite
file contains token-derived pseudonymous keys, best scores and daily attempt
counts; no group message text, Telegram name/username/photo or raw user ID.
Transient admission sessions hold the authenticated ID in memory to recheck
membership. Sessions/runs are invalid after process restart. Best scores survive;
rotating the existing bot token changes pseudonymous keys, so migration/reset
must be considered separately if that happens.

Production admission validates Telegram-signed Mini App initData and freshness
with the existing Botson token, then calls getChatMember for the existing group.
Membership is rechecked during play. No new persistent credential is created.
There is no public anonymous-play mode. The service requires explicitly enabled
configuration, the existing bot/group environment and an HTTPS origin.
Telegram references: [Mini App validation](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app),
[membership lookup](https://core.telegram.org/bots/api#getchatmember).

## Local reversible preview

From the candidate checkout:

```sh
PYTHONPATH=. /path/to/existing/botson/.venv/bin/python -m services.arcade --demo
```

This binds only `127.0.0.1:8082`, accepts only loopback clients/host/origin, uses
an ephemeral demo identity and temporary SQLite directory, and needs no token,
cloud/model call or new credential. Demo scores are separate and removed when
the preview exits. Opening the page does not start a run or publish a score;
the player explicitly clicks the consent/start button.

## Deployment boundary

No service, proxy, firewall, domain, BotFather setting, credential or audience
was changed. The existing denied Botson publication/deployment remains blocked.
Before a future approved production release, confirm an existing HTTPS origin
and authorized hosting route without adding costs or weakening access. Then
test an opt-in launch, real membership/refusal and shared scoring. Do not claim
production UI success without the authenticated visual check required by AGENTS.
Disabling the configured entry prevents new game invitations; disabling/stopping
this separate service ends play without restarting Botson or touching weekly state.
