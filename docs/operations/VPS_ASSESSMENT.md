# Existing VPS suitability assessment

Status: **BLOCKED on owner-supplied evidence.** The agent environment has no access to the VPS. Nothing about the host has
been inspected, no host fact is recorded in this repository, and no suitability verdict exists yet.

Policy for this phase: use the **existing VPS only**; do not provision a server, buy an upgrade, change DNS or public
firewall rules, or touch unrelated workloads. If the host turns out to be unsuitable, the answer is a remediation
recommendation, not a purchase.

## 1. How to produce the evidence (owner action)

On the VPS, from a checkout of this repository (or after copying just the one script):

```bash
bash infrastructure/scripts/vps-assess.sh > vps-assessment.txt
# optional, more complete (firewall rules, LUKS targets, SSH policy keywords):
sudo bash infrastructure/scripts/vps-assess.sh > vps-assessment.txt
```

What the script does and does not do:

- **Read-only.** It installs nothing, starts or stops nothing, opens no network connection, writes nothing to disk, and runs
  no `docker run/exec/stop/rm`. `tests/unit/test_vps_assess.py` fails the build if a mutating command or a file redirection is
  added.
- **Never reads secrets.** It does not read `.env`, `secrets/`, key files, SSH material, shell history or process
  environments, and it does not run `docker inspect` or `docker logs` (which print container environment variables). The only
  contact with `.env` and `secrets/memory-keyring.json` is a metadata `stat` (mode and owner) used to confirm permissions.
- **Redacted.** IPv4, IPv6, MAC addresses and the host name are replaced by `<ipv4>`, `<ipv6>`, `<mac>`, `<host>` before the
  text reaches stdout. Container *names* are not printed, only image, status and published ports.

**Review `vps-assessment.txt` before sharing it**, then paste it into the conversation. It is also worth adding, in words,
the facts a guest VM cannot see:

1. Does the provider encrypt the volume at rest (provider console setting, or LUKS you set up)? Which?
2. Is there a provider-level (cloud) firewall in front of the VM? Which inbound ports does it allow?
3. What existing workloads share this host, and are they sensitive to CPU/RAM/disk pressure?
4. Is there an existing reverse proxy that owns ports 80/443?
5. Where could off-host backups go (object storage bucket, second machine, other provider) at no extra cost?

## 2. Suitability criteria

These are guidance thresholds for a **single-user, non-production staging** deployment (PostgreSQL + pgvector, API,
LiteLLM, Caddy). They are applied by the assessor to the pasted evidence; a miss is "unsuitable until remediated", not
automatic rejection.

| # | Criterion | Suitable | Marginal | Unsuitable |
|---|---|---|---|---|
| 1 | Available RAM after existing workloads | >= 3 GiB | 2 - 3 GiB | < 2 GiB (and no swap) |
| 2 | CPU cores | >= 2 | 1 (shared with workloads) | 1 with sustained load > 0.7 |
| 3 | Free disk on the Docker data root | >= 20 GiB | 10 - 20 GiB | < 10 GiB |
| 4 | Free disk for a local backup copy | >= 2x database size | = 1x | none |
| 5 | Docker engine / Compose | >= 24 / v2 | older v2 | Compose v1 or no Docker |
| 6 | Ports 5432, 4000, 8000 not bound on any public interface | yes | - | any is |
| 7 | Ports 80/443 | free, or an existing proxy that can be reused | contested | owned by a workload that cannot share |
| 8 | Inbound firewall default | deny (host or provider) | partial | allow-all and no provider firewall |
| 9 | Disk encryption (G1) | provider or LUKS, confirmed | unknown | confirmed absent |
| 10 | Time sync | synchronised | - | not synchronised (breaks JWT `exp` and TLS) |
| 11 | Automatic security updates | active | manual cadence | none and no patching routine |
| 12 | Coexistence | no resource contention expected | needs limits | would disrupt an existing workload |

Coexistence rules if an existing proxy or database already runs on the host: Abu AI's containers join their own Docker
networks only; the Abu AI PostgreSQL is a separate container and never reuses an existing database server; Compose
resource limits (`mem_limit`, `cpus`) are set from the staging overlay so a staging problem cannot starve other workloads.
No existing container, volume or network is modified.

## 3. Verdict (to be completed by the assessor)

| Item | Status |
|---|---|
| Evidence supplied | **BLOCKED** (awaiting `vps-assessment.txt`) |
| Criteria 1-12 | **BLOCKED** |
| G1 input | **BLOCKED** (host evidence plus provider confirmation required) |
| Verdict | **NOT ASSESSED** |

When the evidence arrives, this table is replaced by the per-criterion results (still redacted) and one of: *suitable*,
*suitable with limits*, *unsuitable (remediation list)*.

## 4. What stays blocked until then

Staging deployment on the VPS, the on-host restore rehearsal, the on-host monitoring checks, and every host-level claim
for gate G1. The repository-side work in the rest of Phase 1 (configuration, scripts, tests, CI, documentation) does not
depend on this assessment and proceeds.
