# Contributing to ClawLite

Thank you for your interest in contributing to ClawLite. This document
explains how to propose changes and what happens after you do.

## Before you contribute

By submitting any Contribution (code, documentation, tests, or anything
else) to this repository, you agree to the terms of the
[Contributor License Agreement](CLA.md) (`CLA.md`). You retain the
copyright in your Contribution — the CLA grants ForgeSynapse LTD a
license to use it, not a transfer of ownership. See `CLA.md` and
`LICENSING_GUIDE.md` for the full terms.

- **Individual contributors:** submitting a pull request is treated as
  acceptance of the CLA. No separate signature is required.
- **Corporate contributors:** please send a signed copy of `CLA.md` to
  fsalazar@forgesynapse.com before your contribution can be merged.

## How to contribute

1. Fork the repository and create a branch from `main`.
2. Make your change. Keep pull requests focused — one change per PR is
   easier to review than several unrelated changes bundled together.
3. If your change affects behavior, include a test that demonstrates it
   works (and, where relevant, a regression test for the bug it fixes).
4. Open a pull request describing what changed and why.

## What to expect during review

Every change to ClawLite goes through technical review before merging —
not just a style check, but a review that expects real evidence
(a working example, a test run, a before/after comparison) for the
change being proposed. This is deliberate: it is the same standard the
maintainers hold their own changes to. Reviews may ask for that evidence
if a pull request doesn't already include it — this is a normal part of
the process, not a sign that something is wrong with your contribution.

## Code standards

- Match the existing style and structure of the file you're editing.
- Comments should explain *why*, not *what* — the code itself should
  make the "what" clear.
- Don't introduce new dependencies without discussing it in the PR
  description first (dependency licenses need to stay compatible with
  Apache 2.0 — see `NOTICE` for the current list).
- Don't add speculative abstractions or unused configuration options for
  hypothetical future needs.

## Reporting bugs and requesting features

Open an issue describing the problem or request. For bugs, include
steps to reproduce and what you expected to happen instead.

## Reporting security vulnerabilities

Please do **not** open a public issue for security vulnerabilities. See
`SECURITY.md` for how to report them responsibly.

## Questions

ForgeSynapse LTD — fsalazar@forgesynapse.com
