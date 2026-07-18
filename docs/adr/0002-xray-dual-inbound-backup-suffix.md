# Per-user xray clients use a reserved backup-inbound identity suffix

xray runs two VLESS Reality inbounds on the same server — port **443** and port **8443** — so that clients have a fallback path when the primary port is blocked by a network operator. Each user must exist as a `clients[]` entry in *both* inbounds, so the same `username` is registered twice in xray configuration: once as `username` (port 443) and once as `username@hy2-backup.invalid` (port 8443). When traffic is collected via `xray statsquery`, the reserved suffix is stripped before aggregation, so the two ports contribute to a single per-user usage figure that is invisible to the rest of the system.

The alternative — issuing each user two distinct usernames or two distinct UUIDs — was rejected because it would have leaked the dual-port topology into the user panel, the subscription YAML, and the alert payloads, all of which want a single canonical identity per user. The reserved suffix keeps the duplication contained inside `xray_config` and traffic aggregation. It includes `@`, which the username validator does not permit, so a creatable username such as `alice-backup` can never collide with the generated backup identity.

**Status:** accepted. Maintenance rule: any code that mutates xray clients must
update both inbound ports through `xray_config`. The periodic access
reconciliation treats `users.json` as the sole authorization source and
replaces both VLESS client lists with its exact active-user plan. Bootstrap,
legacy, duplicate, or otherwise unmanaged clients are removed; the repository
template therefore starts with empty client lists. Forgetting a port leaves the
user reachable on one inbound and rejected on the other, with no obvious error.
