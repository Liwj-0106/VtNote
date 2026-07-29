# VtNote live POC

The live POC is a release-qualification activity, not a normal automated test. It can upload
authorized media to Tencent Cloud ASR, send transcript text to Aliyun Bailian, and incur charges.
VtNote therefore refuses to initialize a live run unless every gate below is present.

## Required inputs

- A JSON manifest conforming to `tools/poc_manifest.schema.json`.
- 30–50 samples the owner has a lawful right to process.
- At least eight Bilibili, eight YouTube, and eight local-media samples.
- At least eight Mandarin, eight English, and eight mixed-language samples.
- Coverage for all declared duration, subtitle, audio-condition, and processing-route categories.
- Current Tencent ASR upload consent and Aliyun Bailian text consent tied to exact configuration
  revisions. Bailian is restricted to the official Beijing workspace.
- An explicit CNY maximum approved by the owner.
- An empty or matching dedicated output directory on the D drive.

Do not include API secrets in the manifest. Connections are referenced by UUID and revision only;
credentials stay in Windows Credential Manager.

## Offline validation

This command reads the manifest and performs no network request:

```text
D:\ProgramData\Anaconda3\envs\vtnote\python.exe tools\run_live_poc.py ^
  --manifest D:\path\to\manifest.json --validate-only
```

## Live gate

Only after the corpus, account configuration, expected billing, and legal processing rights have
been reviewed:

```text
D:\ProgramData\Anaconda3\envs\vtnote\python.exe tools\run_live_poc.py ^
  --manifest D:\path\to\manifest.json ^
  --output D:\Workspace\Codex\cache\VtNote-poc-YYYYMMDD ^
  --allow-network --allow-billing
```

The two switches are deliberately independent. Omitting either refuses the run. Initialization
creates a resumable `state.json`; it does not itself submit provider work. Run each reviewed stage
through the VtNote production adapter and record every success, failure, cancellation, and unknown
submission in the journal. A completed or possibly submitted paid sample/stage pair cannot be
recorded a second time, protecting resume from accidental duplicate charges.

Raw numeric measurements are preserved for later aggregation. Credential-shaped fields, source
paths, URLs, prompts, tokens, and raw provider responses are rejected or redacted from the result
journal. Failures remain in the evidence set and must not be discarded.

## Release boundary

The harness and its offline tests can be completed without provider credentials. A claim that V1
is live-release-qualified requires the user-authorized run, real provider billing evidence, manual
ASR references and translation/note review, and a signed summary under `docs/poc/`.

Until then the accurate status is:

> Implementation complete; live release qualification pending.
