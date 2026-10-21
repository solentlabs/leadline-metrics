# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in leadline, please report it
privately using GitHub's security advisory feature:

**[Report a vulnerability](https://github.com/solentlabs/leadline-metrics/security/advisories/new)**

Please do not report vulnerabilities in public issues.

Please include:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Response Timeline

- **Acknowledgment:** Within 48 hours
- **Initial assessment:** Within 7 days
- **Fix timeline:** Depends on severity, typically within 30 days

## Supported Versions

Security fixes go into the latest release only.

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |
| older   | :x:                |

## Disclosure Policy

- We will acknowledge your report and keep you updated on progress
- Once a fix is released, we will publicly disclose the vulnerability (with
  credit to you, if desired)
- We ask that you do not disclose the vulnerability publicly until we have had
  a chance to fix it

## Scope

This policy covers:

- Credentials (API tokens) written to a log, a data file or a report
- Visitor data stored that should not be (leadline keeps counts, never
  visitor IP addresses)
- Code injection or a path escape through a response from a service or
  through the config file
- A release file that carries more than the package

## Out of Scope

- Vulnerabilities in the services leadline reads (report to those services)
- Issues requiring physical access
- Social engineering

## Security Notes

- **Credentials:** API tokens come only from environment variables. They are
  never read from the config file, and never written to the data files, the
  log or the reports. Keep the scheduler's environment private.
- **Read-only:** sources only read from the services they name.
- **Visitor data:** leadline keeps counts, never visitor IP addresses.
- **No dependencies:** the runtime uses the Python standard library only, so
  there are no third-party packages to patch.
- **Your data:** the data files and reports are plain text on your own
  machine. Keep them where you would keep any other analytics export.

## Recognition

We appreciate responsible disclosure and will credit reporters in release
notes (unless you prefer anonymity).
