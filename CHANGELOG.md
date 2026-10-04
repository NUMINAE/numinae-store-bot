# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

First release: a Telegram bot storefront for digital goods, paid with Telegram Stars,
built as a portfolio project.

### Added

- Customer flow: catalogue browsing, a cart, checkout and a library of purchases from
  which delivered items can be sent again.
- Digital goods of three kinds: text, files (Telegram file ids) and licence keys, with
  key reservation while an order is awaiting payment.
- Payments through Telegram Stars (XTR invoices, pre-checkout validation, recorded
  payments) and a demo mode that runs the same internal completion path without payment.
- Automatic handling of duplicate and late payments: a payment for an expired,
  cancelled or already-paid order is refunded.
- Delivery of paid orders with retries, long content split across messages, and a
  receipt. A recurring sweep expires unpaid orders and retries undelivered ones.
- Owner tools under `/admin`: catalogue management, file and licence-key uploads, orders
  with cancel, retry, resend and refund actions, statistics, and settings.
- Refunds that call Telegram first and update the database second, revoking the sold
  licence keys of the refunded order.
- A shared design language for every screen (header, breadcrumbs, steps, navigation
  row, one icon table) and a Telegram profile (command menus, descriptions).
- Per-user concurrent update processing with a per-user cap, so a slow action by one
  user does not delay anyone else's payment.
- Operations: a database backup command, `--check-config` and `--healthcheck` flags with
  a sweep heartbeat, a Dockerfile and a Docker Compose file.
- Continuous integration for linting, type checking, tests with coverage thresholds,
  a dependency audit and packaging checks.

### Security

- Ownership checks on orders and library items return identical responses for foreign
  and missing ids; admin routes are owner-only.
- Telegram HTML escaping at render time; SQL is parameterised and every dynamic SQL
  fragment is a fixed constant or whitelisted.
- Bot-token redaction in logs and in uncaught tracebacks; no message text, key values or
  update objects in logs.
- Atomic payment completion with a unique charge-id index; a licence key can belong to
  only one order.
- Required, explicit `PAYMENT_MODE` (no default), and a startup warning in demo mode.
- Owner-only permissions on the data directory and database files on POSIX.

### Known limitations

See the "Known limitations" section of the README.
