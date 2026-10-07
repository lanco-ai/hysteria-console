# TUIC authenticated payload accounting: operator runbook

Status: on 2026-10-07 the CI-built artifact (run 37001954390) passed the real
runtime gate with the managed client below: TCP/UDP over native and QUIC relay,
IPv4/IPv6, exact per-user counters and explicit unknown-UUID rejection.
This runbook itself grants no production authorization. The session has separate
authorization for publication and activation after the required checks and
independent review. The historical first runtime attempt was blocked because
official sing-box1.14.2 assets omit `with_v2ray_api`. A trusted custom artifact is
now available; its real runtime gate must pass with the managed client below.

## What changes after a separately approved activation

A dedicated sing-box1.14.2 process replaces the old TUIC executable behind the
same `tuic-server.service`, public UDP9443, UUID/password and subscription URI.
An independent `state/tuic_user_mode.json` records explicit active/legacy mode.
Missing or unrecognized runtime config cannot silently switch an activated node
back to aggregate metering. A missing/corrupt mode or active checkpoint fails
TUIC closed. Named authenticated users expose cumulative upload/downlink counters through a
loopback-only V2Ray gRPC API on127.0.0.1:10086, distinct from Xray10085. Existing
The managed generic gRPC client queries these without reset. Upload is rx;
download is tx. Raw payload
bytes enter existing daily/cycle usage, quota decisions and display2.28. Shanghai
buckets and user expiry/disable/quota logic remain authoritative. The first
sample begins from zero in the **new runtime generation**, never from old nft
counters. Historical18.43GiB is not attributable to users and stays excluded.

Port nft counters remain wire diagnostics. In migrated mode cost calibration
and protocol/hourly payload graphs use user bytes and do not add nft totals.
Graphs are auxiliary: a collector crash after canonical commit can leave an
hourly/protocol gap; quota daily credit and source watermark recover together.
Normal restart starts a new generation from zero. An abrupt TUIC crash loses
unpolled bytes held only in its memory; this is not crash-perfect billing.
A sample crossing midnight credits the increment to the collection's Shanghai
day; cumulative runtime counters do not expose packet timestamps.

Quota ledgers and source checkpoint are advanced with a durable intent journal
under `usage.lock`. Before another tick consumes Hysteria/Xray counters, or any
panel reset/refund/delete mutates usage, pending intent is replayed. Each target
must still equal its before/after image; conflicts fail closed. Manual zero or
refund never resets `tuic_user_state.json`. Each generation also binds user names to fingerprints of their canonical UUID
and password; named config tuples are validated before credit. During a pending
restart, old counters drain but credit only still-matching canonical identities.
Deleting and recreating the same username never assigns old-generation bytes
to its new credentials. A new generation waits for the reload worker's ACK;
config or ACK changes during observation defer binding to the next sample. A
managed restart's temporary API failure also defers instead of permanently
inhibiting routine admin changes. All unresolved reloads share one cumulative
120-second grace, stored in the source checkpoint under `usage.lock`. The first
pending observation or changing-config sample starts it; refreshed tokens,
config churn, new invocations and successful old-generation drainage do not
renew it. Query time counts, and the limiter checks expiry before and after
observation. The first limiter tick at/after expiry stops only TUIC and persists
the accounting-fault inhibition. The timer's scheduling interval therefore
bounds when the stop is applied; this is not a separate real-time watchdog.

The wait clock survives collector process restarts. Within one boot it uses the
larger of UTC and monotonic elapsed time; after reboot it uses persisted UTC.
Backwards clocks, nonfinite values or malformed wait state fail TUIC closed.
Only a stable sample with the reload ACK complete and trustworthy identities
clears the wait, in the same recoverable transaction as canonical bytes and
source counters. Unexplained failures remain fail-closed.
Ordinary same-owner token/UUID rotation still restarts normally, but its changed
credential fingerprint is conservatively treated as a different identity: an
unpolled old-credential tail during that window is excluded rather than
misattributed. This is an additional documented sampling boundary, alongside
runtime crashes. Deleted identities never recreate ledger rows.

A TUIC API, generation, source checkpoint or monotonicity failure stops TUIC and
creates `/root/hysteria/tuic.json.accounting-failed`; all later named config syncs
omit credentials and the reload worker refuses to restart until operator recovery.
Other protocols continue accounting. A shared ledger/journal fault takes the
existing global static-auth fail-closed path. After migration `sync_all` without
a canonical access plan conservatively omits metered users; normal mutation and
limiter paths supply the quota/expiry plan through `sync_user_plan`.

## Trusted artifact and real fixture gate

The prepared `.github/workflows/tuic-runtime.yml` retains `workflow_dispatch`
and also builds on an authorized push to **only** `codex/tuic-traffic-accounting`
when that push changes the workflow itself or `scripts/tuic/build-runtime.sh`.
This restricted push trigger permits the first reviewed feature-branch publication
to build without merging unvalidated accounting code onto the default branch.
It has read-only repository permissions, pinned action commits, no deployment
credentials, and no deployment step. Artifact upload is not runtime approval.

Manual dispatch still requires the workflow definition on the default branch;
see the [official manual workflow documentation](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow?tool=webui).
Use the restricted authorized push path above, or separately approve a build-only
workflow/recipe merge and dispatch, or use an authorized trusted external builder.
For every path record the run URL and exact source commit, download the artifact,
verify provenance/checksums, and run `runtime-gate.py` on isolated ports with the
prepared stats Python environment before considering production activation.

Use a separate trusted builder with Go1.26.8 (the toolchain in the inspected
official1.14.2 artifact), at least4GiB free disk and2GiB available memory:

```sh
bash scripts/tuic/build-runtime.sh /absolute/new/tuic-build
python3 scripts/tuic/runtime-gate.py \
  --binary /absolute/new/tuic-build/sing-box \
  --sha256 TRUSTED_BUILDER_BINARY_SHA256 \
  --stats-python /tmp/tuic-stats-stage-TASK/venv/bin/python
```

The recipe pins the upstream source archive:
`https://github.com/SagerNet/sing-box/archive/refs/tags/v1.14.2.tar.gz`,
SHA256`67dd8f8c37ecaaadcfcafad1f0827eed4b034c963b86fd3aa5c0d7a36876845d`.
It builds only with `with_quic,with_v2ray_api`, CGOdisabled, readonly module graph,
checksum verification, one build worker and a memory soft limit. Keep
`SHA256SUMS`, `version.txt`, `go-build-info.txt`, source archive and fixture JSON
with the artifact. Builder provenance must be trusted independently of a supplied
hash; a hash supplied by an unknown binary provider is not sufficient.

The fixture checks real config parsing, two authorized identities, rejected
third identity, TCP and both native/QUIC UDP relays, IPv4/IPv6 connections, h3/TLS,
exact upload/download payload counters and non-reset managed-client interoperability.
It uses temporary self-signed certs, synthetic credentials and ephemeral ports;
no production API is read or reset. A failure is a release blocker, not a skip.
The official binary SHA256`fc9c6e6ab345f045b16a0ed10d1ff28d68e8e56e7749fca30738d1406e98d7b8`
fails the build-tags gate and must not be installed for accounting.

At the initial artifact stage, local build was not attempted on the production host: only roughly150MiB root
and336MiB shared-memory storage were free, available RAM about252MiB with swap
already in use. At that stage SDK+source+dependency+compiler working space could
not retain a credible reserve and no reachable trusted builder was configured.
The later trusted artifact has SHA256
`0e4ffde4260808498722e2a4f040f15ba8763bae7b577821ea524fb4f67977c7`,
version1.14.2, `with_quic,with_v2ray_api`, Go1.26.8 and CGOdisabled. Keep the
source archive and artifact evidence; availability alone is not a runtime PASS.

## Managed stats client and dedicated dependency

The pinned source rewrites the generated service descriptor at initialization.
The actual RPC is
`/v2ray.core.app.stats.command.StatsService/QueryStats`; the Xray CLI namespace is
incompatible. The helper sends only repeated `patterns` field3 with `user>>>`.
Deprecated singular field1 is ignored by this server. Reset field2 and regexp
field4 are omitted and default to false; there is no reset option in the helper.
The strict wire decoder accepts omitted zero values and rejects negative int64,
overflowed/truncated fields, duplicate scalars and invalid UTF8. Existing
accounting validation then rejects unknown or duplicate authenticated identities.

The limiter runs `/usr/bin/python3`, separately from the web environment. Its
observation launches the fixed managed command:

```sh
/root/hysteria/.venv-tuic-stats/bin/python -s -E \
  /root/hysteria/tuic_stats_client.py --endpoint 127.0.0.1:10086
```

The helper imports grpcio lazily, accepts only a dedicated literal loopback
endpoint, disables gRPC HTTP proxies, uses a3-second RPC deadline and a2MiB
receive/output cap. The parent command has a5-second deadline and a joint2MiB
stdout/stderr cap; generation checks still bracket the entire observation.
Legacy mode does not launch this helper or require grpcio.

`requirements-tuic-stats.txt` pins grpcio1.74.0 and the reviewed CPython3.12 Linux
amd64 wheel hash. No mandatory dependencies are installed. Prepare a new
private task directory with the trusted external pip, using no pip in the venv:

```sh
python3 -I scripts/tuic/stage-stats-deps.py \
  --output /tmp/tuic-stats-stage-TASK \
  --python /usr/bin/python3 --pip-python /usr/bin/python3
```

The helper permits only a new `/tmp` or `/dev/shm/tuic-stats-stage-*` directory,
uses `venv --without-pip`, hash checks, binary wheels, `--no-deps` and
`--no-compile`, and verifies the6,180,664-byte wheel and15,251,027-byte unpacked
size. Its task-owned staging cap is24MiB; disk staging keeps256MiB free. It
prepares a venv and never installs deployed files. A reviewed maintenance release
must place that venv at `/root/hysteria/.venv-tuic-stats` and copy the helper;
the general legacy installer retains its migrated-mode guard.

## Offline candidate and preflight

Use secure copies of the current configs, users, daily ledger and metadata.
Prepare writes only a new output directory, never production:

```sh
python3 scripts/tuic/prepare-migration.py \
  --binary /absolute/verified/sing-box --sha256 TRUSTED_BUILDER_BINARY_SHA256 \
  --legacy /secure/snapshot/tuic.json --users /secure/snapshot/users.json \
  --daily /secure/snapshot/usage_daily.json --meta /secure/snapshot/subscription_meta.json \
  --output /secure/new-candidate --accept-runtime-defaults
```

The explicit `--accept-runtime-defaults` acknowledges that the old TUIC tuning
keys `task_negotiation_timeout`, `max_idle_time`, `max_external_packet_size`,
`send_window`, `receive_window`, `gc_interval`, `gc_lifetime` use sing-box defaults
instead of claiming an equivalent mapping. Only original project default values
are accepted; custom values fail preflight and need a reviewed mapping. No
acknowledgment means these keys are rejected. BBR, disabled0RTT, h3, TLS paths,
listener and auth timeout map explicitly; disabled IPv6 UDP relay maps to a UDP
IPv6 reject rule. `dual_stack=false` is rejected rather than silently changed.
Validate the generated config with the actual cert paths available. Output
includes `tuic.json`, `tuic-server.override.conf`, and a nonsecret report.

Before requesting production approval, independently review this source diff,
run the full quality gate and obtain a successful real runtime fixture report.
Record the binary hash, candidate diff, active-user counts, observed quotas,
public UDP9443/firewall state, loopback10086 availability and tested rollback.
The existing general `deploy.sh` now refuses a migrated config before mutable
work because it still manages the legacy1.0.0 binary/unit. Use a separately
reviewed metered maintenance release rather than bypassing its guard.

## Activation procedure (requires separate authorization)

1. Acquire the normal deployment maintenance lock; stop the traffic limiter
   timer and wait for its service to finish. Stop `tuic-server.service`. Keep the
   panel in maintenance so its config writers cannot race the migration. Expect
   a brief TUIC reconnect; do not restart Grok containers.
2. Under the canonical `usage.lock`, recover any pending accounting journal,
   validate the canonical users/usage/daily/meta, then snapshot the old TUIC
   executable, `tuic.json`, unit and drop-ins, nft diagnostic state and accounting
   files into a mode0700 backup directory. Record checksums and exact UTC/Shanghai
   activation timestamps. Do not reset or attribute historical nft totals.
3. Install verified source modules including `tuic_user_meter.py` and
   `tuic_stats_client.py`, plus the prepared dedicated stats venv at
   `/root/hysteria/.venv-tuic-stats`; install the
   tested binary at `/usr/local/lib/hy2/sing-box-tuic-1.14.2` mode0755 and candidate
   TUIC config mode0600. Install the prepared systemd drop-in for the existing
   service. Preserve all other service restrictions. Under `usage.lock`, run
   `tuic_user_meter.initialize(Path('/root/hysteria/state'), activated_at=...)`.
   Initialization durably records active mode before creating the fresh checkpoint.
   It deliberately refuses existing checkpoint/pending files. On reactivation,
   archive the old source checkpoint only after recovery and a stopped runtime;
   never overwrite it silently. Do not change existing user ledger totals.
4. Validate the installed config again, daemon-reload and start the existing
   TUIC unit. Verify service invocation identity, loopback-only API ownership,
   IPv4/IPv6 UDP9443 and TLS/h3. Use the managed helper above for the live
   non-reset observation; preserve its fixed request and endpoint restrictions.
5. Run a controlled authorized test user, verify its raw byte delta and2.28 display,
   disabled/expired rejection, quota cutoff and that a second sample does not
   duplicate credit. Resume the limiter timer and panel. Save the activation
   report and leave historical counters archived.

## Rollback and fault recovery

For rollback stop the timer and TUIC, enter maintenance and acquire `usage.lock`.
Recover any pending transaction first. Preserve all credited daily/cycle bytes;
**do not restore old ledger snapshots**, which would erase postactivation usage.
Archive the source checkpoint, pending state (after recovery), fault marker and
new config/artifact evidence. After restoring the old runtime config while the
service remains stopped, call `tuic_user_meter.deactivate_locked(daily_path,
usage_path)` under the canonical lock. It recovers pending intent and writes an
explicit legacy mode; do not merely delete the active-mode file or config. Restore the old TUIC binary/config/unit/drop-ins
from the matching backup, daemon-reload and start the old service. Confirm
subscriptions and expiry/disable/quota generated plan, then resume the timer.
Old mode returns to protocol aggregate diagnostics; no per-user TUIC accounting
claim is valid until another approved metered activation. The retained checkpoint
is inert only under the explicit legacy mode signal. The legacy installer also
checks this signal and refuses active mode even if `tuic.json` is missing. Re-run source validation before reactivation.

For a source-only fault, fix the cause with TUIC stopped. Inspect the checkpoint,
API binding/runtime provenance and pending journal before clearing the fault
marker. Keep quota ledgers and the watermark. Under maintenance/locks with TUIC stopped, clear the marker, regenerate named
config from the canonical access plan and verify the config before restarting. A new invocation will charge from zero;
record any known unpolled tail loss. Never bypass a malformed canonical journal
by deleting it or zeroing the daily ledger.

### Runtime fixture rejection evidence

The isolated runtime gate now probes unauthorized TCP and UDP in both native and
QUIC relay modes. Each probe uses a fresh unknown UUID and requires a new server
log entry identifying that exact UUID as `authentication: unknown user`. This
specific error is defined by the pinned [sing-quic TUIC service source](https://github.com/SagerNet/sing-quic/blob/6a3a24d65b99/tuic/service.go),
used by sing-box 1.14.2. A timeout, SOCKS error, empty stats response, or unrelated
or historical rejection log cannot establish credential rejection.

Loopback targets record every accepted TCP connection, received byte, and UDP
datagram before replying. Any negative-probe ingress fails the gate, even if its
reply is lost. Each negative probe is bracketed by authorized traffic with exact
payload, target-ingress and user-counter checks; observer threads, fixture
processes and the stats API must remain healthy. After explicit rejection the
invalid client is terminated, followed by a bounded quiet observation and the
authorized post-control. Rejection polling is bounded to three seconds after the
bounded transport attempt; unexpected or unconfirmed evidence returns
`BLOCKED/FAIL`. This validates the observed fixture interval, not arbitrary future
traffic. Unit tests using socketpair/loopback are regression evidence only; the
supported trusted binary must still pass the real runtime gate before activation.
