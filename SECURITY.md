# Security and data handling

This repository distributes fictional support cases and a curated recorded-evidence bundle. No provider credentials, local ledgers, personal profiles, or original generator configuration are required to use the public demo.

Keep `.env`, API keys, local SQLite files, raw captures, and recordings out of Git. The ignore rules cover the standard paths; review every staged change before publication. Never add a provider key to the public frontend or GitHub Pages workflow. A secret committed even once must be revoked; deleting it from the latest version is insufficient.

The live app is intended for a trusted workstation, binds to loopback, and is not a multi-user service. Anyone able to use the local app can inspect its saved workload data and initiate configured provider calls. Follow [the enterprise guide](docs/ENTERPRISE.md) before adapting it for shared or sensitive workloads.

For a vulnerability report, use the repository's private vulnerability reporting feature if available. Do not put secrets, customer data, or sensitive exploit details in a public issue.
