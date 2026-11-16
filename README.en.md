# midas-civil-nx-api

[中文](README.md) | [English](README.en.md)

A Codex skill for MIDAS Civil NX 2024, with guidance for parametric modeling and analysis verification, a Python standard-library API client, and an offline parameter update planner. Verify fields and analysis behavior against the target installation.

## Capabilities and scope

- Connect through environment variables, read model databases through generic REST endpoints, and let task scripts explicitly request model changes or analysis.
- Generate `set` / `scale` updates to existing numeric fields in a fixed topology from a frozen baseline, avoiding cumulative scaling.
- Parse results by their actual table headers, and check request success, analysis completion, and result freshness separately.
- Redact configured credentials and connection addresses when echoed, and block redirects that could forward authentication headers.

The tools do not include an optimizer and do not start analysis automatically. The offline planner does not create meshes, materials, or sections. The skill entry point and detailed technical references are currently in Chinese; this README provides an English overview.

## Files

- [SKILL.md](SKILL.md): skill entry point and operational boundaries.
- [agents/openai.yaml](agents/openai.yaml): skill display metadata.
- [references/](references/): API contracts, parametric workflows, and troubleshooting.
- [scripts/midas_api.py](scripts/midas_api.py): API client and offline update planner.
- [tests/test_midas_api.py](tests/test_midas_api.py): offline behavior tests without a MIDAS connection.

## Installation and requirements

Copy this directory into your Codex skills directory, keeping the folder name `midas-civil-nx-api`. You can also run the tools directly from the repository root. Python 3.9 or newer is required; no third-party runtime dependencies are needed.

In Codex, invoke `$midas-civil-nx-api` and describe the target model, software version, units, and requested task. Online operations also require a reachable MIDAS Civil NX installation. Confirm its connection status in the software's API Settings first.

## Offline usage and verification

Run these commands from the repository root. They do not connect to the software:

```powershell
python -X utf8 -B scripts/midas_api.py --help
python -X utf8 -B -m unittest discover -s tests -v
```

Prepare `baseline.json`, `parameters.json`, and `values.json` following the [parametric workflow](references/parametric-workflow.md), then generate updates offline. These are user-supplied input files and are not included in the repository.

```powershell
python -X utf8 -B scripts/midas_api.py plan --baseline baseline.json --spec parameters.json --values values.json --output updates.json
```

The output file must not already exist; the tool does not overwrite existing output. Offline tests cover retry boundaries, analysis completion checks, redaction, non-accumulating parameter updates, and result table parsing.

## Online read-only checks

Before using the client, `probe`, or `get`, configure the following environment variables locally. This repository supplies no real values and does not accept credentials as command-line arguments.

| Environment variable | Purpose |
|---|---|
| `MIDAS_BASE_URL` | The connection address supplied by the target software, including its product root path |
| `MIDAS_API_KEY` | The credential supplied by the target installation, sent in the `MAPI-Key` header |

Once configured, run a read-only check:

```powershell
python -X utf8 -B scripts/midas_api.py probe
python -X utf8 -B scripts/midas_api.py get UNIT --output unit.json
```

Task scripts must explicitly request model changes and analysis. Only read-only GET requests may be retried automatically within a finite limit. After a mutation or analysis timeout, inspect the actual state first. See [API contracts](references/api-contracts.md) and [troubleshooting](references/troubleshooting.md).

