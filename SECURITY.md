# Security Policy

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report it privately to: **fsalazar@forgesynapse.com**

Include as much detail as you can: the affected component, steps to
reproduce, and the potential impact. This helps us triage and fix it faster.

### What to expect

- **All reports are received and reviewed.** Critical and high-severity
  issues are prioritized and worked on during weekends, when the maintainer
  has dedicated time for security work.
- **Resolution:** every reported issue is addressed and documented by the
  next scheduled release (see "Supported Versions" below for the release
  cadence).
- **Coordinated disclosure:** we ask that you keep the report private for
  **90 days** from your initial report, or until a fix is released,
  whichever comes first, before disclosing it publicly. We're happy to
  credit you in the release notes if you'd like.

## Supported Versions

ClawLite is currently in its first released version. There is no long-term
support for older versions — security fixes go into the next scheduled
release, which ships every **2 to 4 weeks**. Always run the latest version
from `main` (or the latest tagged release, once releases are tagged) to
have the current fixes.

## Scope

**In scope** — please report these:
- Anything that would let someone access, control, or impersonate another
  user's ClawLite instance (e.g. a bypass of the Telegram owner-only access
  control).
- Any way to escape or break out of the Docker sandbox used to execute
  generated code, beyond what the sandbox is designed to allow.
- Any way to read, exfiltrate, or tamper with credentials stored in the
  encrypted vault (API keys, OAuth tokens) without the corresponding
  system-level access that would already be required.
- Any bypass of the internal authorization/governance layer (the
  `Mandate`/`ActionGuard` system) that lets an action run without going
  through its intended policy check.
- Prompt-injection techniques that reliably defeat the project's existing
  injection defenses to make the agent take an unintended action (not just
  produce an odd response).

**Out of scope** — please don't report these here:
- Vulnerabilities in third-party dependencies themselves — please report
  those to the dependency's own maintainers. (We'd still appreciate a
  heads-up so we can track it, but the fix has to come from upstream.)
- Anything that requires the attacker to already have local access to the
  machine running ClawLite. ClawLite is local-first by design — the
  machine it runs on is the trust boundary, not a remote service.
- Issues caused entirely by running an outdated version after a fix has
  already shipped.
- The local LLM producing an incorrect, biased, or hallucinated response.
  That's a model-quality issue, not a security vulnerability — please file
  it as a regular bug report instead.

## Questions

ForgeSynapse LTD — fsalazar@forgesynapse.com
