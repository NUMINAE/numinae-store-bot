# Security policy

## Supported versions

Only the latest release is supported. Fixes are made on the main branch and released
as a new version; older versions do not receive patches.

## Reporting a vulnerability

Please report suspected vulnerabilities **privately** through GitHub's private
vulnerability reporting: open the repository's **Security** tab and choose
**Report a vulnerability**. Please do not open a public issue or pull request for a
security problem.

Useful details: what you found, the steps or input that trigger it, the version or
commit, and what an attacker could achieve. Please do not include real bot tokens,
customer data or licence keys in a report.

## What is in scope

- Authorization and ownership checks (one customer reaching another's orders, library
  items or files, or a non-owner reaching an admin action).
- Payment integrity: recording a payment that was not made, delivering without
  payment, selling one licence key twice, a refund path that does not match the
  payment.
- Injection and escaping: SQL injection, or message content that breaks out of the
  Telegram HTML formatting.
- Leaks of the bot token, licence keys, delivery content or personal data through
  logs, error output, backups or the Docker image.
- Denial of service that one user can cause against other users.

## What is not in scope

- **Demo mode** (`PAYMENT_MODE=mock`). It grants content without payment by design and
  must never run with real content. Reports that mock mode gives away content are
  expected behaviour.
- Telegram itself: the Bot API, the Telegram apps, Telegram Stars, and their
  availability or rate limits.
- Weaknesses that need access to the server, the database file, the `.env` file or the
  bot token, which are already full compromise.
- Vulnerabilities in third-party dependencies that are not exploitable through this
  project (these are tracked by the dependency audit in CI).

## What to expect

This is a small, maintained-as-time-allows project. I will aim to acknowledge a report
within about a week and to tell you what I decide to do about it. I cannot promise
fixed timelines, a bug bounty or formal advisories for every report.
