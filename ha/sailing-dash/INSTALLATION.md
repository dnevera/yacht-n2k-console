# Installation — Sailing Dashboard

Installing this from scratch is **not** "one script does everything". There is a
hard synchronisation point in the middle: auto-discovery maps canonical sensor
aliases (`sensor.boat_stw`, …) by reading an entity registry that only exists
**after** you have created the NMEA 2000 config entry by hand and the bus has
carried real traffic.

So the procedure is always three phases:

```
[AUTO stage 1]  container + HACS files + integration + cards
      ↓
[GATE A]        activate HACS in the UI (device-flow)  → verified by check-hacs
      ↓
[GATE B]        NMEA 2000 config entry (host:4001, type text) + traffic on the bus
                → verified by deploy.sh --preflight
      ↓
[AUTO stage 2]  ./deploy.sh --target <profile> --install  (discovery + sensors + dashboard)
```

`--install` refuses to run before the gates are green (see
[Preflight gate](#preflight-gate)) instead of deploying a dashboard bound to
nothing.

### The guided way: `./install_wizard.sh`

One command walks the whole thing for any profile and **stops** at each gate:

```bash
cd ha/sailing-dash
./install_wizard.sh                          # profile "stage"
./install_wizard.sh --target prod            # any profile from .env
./install_wizard.sh --target prod --reinstall
./install_wizard.sh --list                   # the 8 steps
./install_wizard.sh --from 5                 # resume at a gate
```

| Step | What it does |
|---|---|
| 1 | profile (`.env`) + prerequisites (python, docker / ssh) |
| 2 | wipe (only with `--reinstall`, and only a local stage target) |
| 3 | `build.py` + `fetch_deps.py` |
| 4 | container up, HACS files + pinned integration + cards delivered, HA provisioned (no deploy yet) |
| 5 | **GATE A** — you activate HACS in the UI, then `stage_provisioner.py check-hacs` must pass |
| 6 | **GATE B** — you finish the NMEA 2000 setup, then `deploy.sh --preflight` must pass |
| 7 | deploy: auto-discovery, sensors, cards, dashboard |
| 8 | verify (`inspect`, HTTP 200 on the dashboard) |

A gate prints its checklist, waits for Enter, then runs its check. If the check
fails it says why and waits again — it never continues on a warning, and it
behaves identically for stage and for a vessel server.

---

## 0. Concepts you need before starting

### Configuring targets — this subproject is self-contained

Everything about environments lives in `ha/sailing-dash/.env`, created from
[`.env.template`](.env.template):

```bash
cd ha/sailing-dash
cp .env.template .env      # then edit
```

The repo root `.env` / `deploy.conf` belong to the **ydnu-02 manager** and are
never read from here — that is why a production host configured there can no
longer leak into a stage deploy.

Targets are **named profiles**, not a fixed stage/prod pair. List them in
`HA_PROFILES` and describe each one with variables prefixed by the profile name
uppercased, `-` becoming `_` (`stage-pi5` → `STAGE_PI5_*`). Two Pi5 boxes side by
side:

```env
HA_PROFILES="stage stage-pi5 prod"

STAGE_PI5_TRANSPORT="ssh-docker"
STAGE_PI5_SSH_HOST="pi@stage-pi5.local"
STAGE_PI5_CONTAINER="homeassistant"
STAGE_PI5_HA_URL="http://stage-pi5.local:8123"
STAGE_PI5_GW_HOST="192.168.1.51"
STAGE_PI5_GW_DATA_PORT="4001"

PROD_TRANSPORT="ssh-docker"
PROD_SSH_HOST="pi@vessel-pi5.local"
PROD_CONTAINER="homeassistant"
PROD_HA_URL="http://vessel-pi5.local:8123"
PROD_GW_HOST="192.168.1.50"
PROD_GW_DATA_PORT="4001"
```

| Field | Meaning |
|---|---|
| `<P>_TRANSPORT` | `local-docker` (docker on this machine) or `ssh-docker` (ssh + docker) |
| `<P>_SSH_HOST` | ssh destination, ignored for `local-docker` |
| `<P>_CONTAINER` | Home Assistant container name on that machine |
| `<P>_CONFIG_DIR` | HA config directory inside the container |
| `<P>_HA_URL` / `<P>_HA_TOKEN` | readiness checks and REST auto-discovery |
| `<P>_GW_HOST` / `<P>_GW_DATA_PORT` | the YDNU-02 tcp-gw **that instance** connects to (the nmea2000 config entry) |

Pick a profile with `--target`; `--stage` / `--prod` are aliases of
`--target stage` / `--target prod`:

```bash
./deploy.sh --target stage-pi5 --install
PROD_CONTAINER=ha-test ./deploy.sh --prod      # env still overrides .env
```

A profile whose name starts with `stage` is a *verification* environment — only
there are provisioning shortcuts (onboarding bypass, `test`/`test`, mock
emulator) allowed. The gates themselves are **not** relaxed on stage: HACS
activation and the NMEA 2000 readiness check are the same everywhere, so stage
really rehearses the vessel procedure. Everything else is treated as a real
vessel target.

### Nothing external lives in this repo

There is no `vendor/` directory. Every external artifact is declared once in
[`deps.yaml`](deps.yaml) and downloaded by `fetch_deps.py` into `build/deps/` —
an ordinary build artifact directory (gitignored, wiped together with `build/`):

```
build/deps/cards/*.js                            the 6 third-party Lovelace cards
build/deps/hacs/custom_components/hacs/          HACS itself
build/deps/nmea2000/custom_components/nmea2000/  our ha-nmea2000 fork, by tag
```

Everything is pinned to a **tag**, never a branch, so two installs on different
days produce identical code. If GitHub is unreachable the deploy stops with the
list of missing artifacts — it never silently falls back to a stale local copy.

```bash
python3 helpers/fetch_deps.py                 # fetch what's missing
python3 helpers/fetch_deps.py --force         # re-download everything
python3 helpers/fetch_deps.py --update-hashes # record sha256 after a version bump
```

### The nmea2000 library comes from our fork, by tag

`nmea2000 @ git+https://github.com/dnevera/nmea2000.git@cpu-overload-fix`

The same tag applies in all three places: the repo root `requirements.txt` (our
venv/Docker), the `manifest.json` of the `ha-nmea2000` fork (inside the HA
container), and `deps.yaml` (the single source of truth). There are **no patch
scripts** — both fixes are inside the tag. Verify with
`./deploy.sh --check-ha` from the repo root.

---

## 1. Stage — AUTO stage 1

```bash
cd ha/sailing-dash
./run_stage.sh                 # demo mode: local NMEA PGN emulator on the profile's port
./run_stage.sh --clean-install # force clean re-provisioning
./run_stage.sh --live --gw-host <gateway-host>   # against a real gateway
./run_stage.sh --target stage-pi5                # a stage container on another Pi5
```

This single entry point starts the container, runs `build.py` and
`fetch_deps.py` exactly once, provisions HA (onboarding bypass, `test`/`test`
user, HACS, the NMEA 2000 integration from the pinned tag, a config entry on the
profile's `GW_HOST:GW_DATA_PORT` with `gateway_type: text`), deploys cards,
sensors and the dashboard, and then watches `src/` for changes.

`./build_docker.sh` is a thin wrapper around the same entry point, kept only for
muscle memory (`--no-cache` forces a docker image rebuild first).

### Manual steps (Stage)

| # | Step | Why it is manual |
|---|---|---|
| 1 | Log into `http://localhost:8123` with `test` / `test` and confirm onboarding really was bypassed | verification, not automatable meaningfully |
| 2 | Restart HA after custom components were delivered | HA does not pick up `custom_components/` at runtime |
| 3 | Settings → Devices & services → Add integration → **HACS**, then authorize at `github.com/login/device` | **CANNOT be automated** — needs a live GitHub account |

HACS **files** are installed by the scripts (from the pin in `deps.yaml`),
identically on Stage and Prod — only the activation above is manual. Check it
with:

```bash
python3 helpers/stage_provisioner.py check-hacs --target stage
```

It reports the two states separately: *not delivered* (our bug — re-run
`fetch_deps.py` / bootstrap) versus *not activated* (your turn, in the UI).

---

## 2. Prod — AUTO stage 1

Configure the `prod` profile once in `.env` (`PROD_SSH_HOST`, `PROD_CONTAINER`,
`PROD_HA_URL`, `PROD_GW_HOST`), then:

```bash
cd ha/sailing-dash
./deploy.sh --prod --bootstrap      # delivers cards + the pinned integration
```

`--bootstrap` checks SSH/Docker/container, delivers `build/deps/` artifacts —
**HACS included**, so Prod and Stage get the exact same files (`scp` +
`docker cp`) — and restarts HA. It is idempotent: re-running it does not
duplicate anything. If you prefer letting HACS own the integration/cards
updates, install them through its UI as well; preflight accepts either outcome.

### Manual steps (Prod)

| # | Step | Why it is manual |
|---|---|---|
| 1 | Install Docker and run the `homeassistant` container on the vessel server | outside this pipeline |
| 2 | Complete the real onboarding: owner account, home name, coordinates, units, timezone | a real installation — `test`/`test` is not acceptable |
| 3 | Add HACS under Settings → Devices & Services → Add integration (its files are already delivered by `--bootstrap`; `wget -O - https://get.hacs.xyz \| bash -` is only an alternative way to deliver the same files) | UI only |
| 4 | Activate HACS through the GitHub device-flow (`github.com/login/device`) | **CANNOT be automated** |
| 5 | Add the custom repository `dnevera/ha-nmea2000`, tag `ydnu-02-usb-tcp-gw`, and install it | HACS UI only (skip if you used `--bootstrap`) |
| 6 | Install the 6 frontend cards through the HACS UI | HACS UI only (skip if you used `--bootstrap`) |
| 7 | Create the NMEA 2000 config entry: **Host** = gateway IP, **Port** = `4001`, **Gateway type** = `text` | config-flow, UI only |
| 8 | Restart HA and wait for raw `nmea2000` entities to appear | needs real traffic on the bus (two-phase announce, SA 64/200) |

Only after step 8 does auto-discovery have anything to map.

---

## 3. AUTO stage 2 — discovery, sensors, dashboard

```bash
./deploy.sh --prod  --install    # or --stage
./deploy.sh --prod  --update     # incremental, same pipeline
```

What runs, in order: `build.py` → `fetch_deps.py` → preflight → Lovelace
resources merge → `map_nmea_sensors.py` (auto-discovery) → rebuild →
`configuration.yaml` merge → dashboard upload → restart.

Partial modes: `--resources-only`, `--sensors-only`, `--dashboard-only`.

### Preflight gate

```bash
./deploy.sh --prod --preflight    # read-only, changes nothing
```

Checks that the container is running, HACS is **delivered and activated**
(delegated to `stage_provisioner.py check-hacs`, so "the files are there" is
never mistaken for "it works"), the NMEA 2000 integration is installed, a config
entry exists, and raw `nmea2000` entities are present in `core.entity_registry`.
On failure it stops and prints the exact remaining manual steps. It is enforced
automatically for `--prod --install` / `--update`; `--skip-preflight` overrides it
at your own risk. `install_wizard.sh` runs the same check as GATE B for every
profile, stage included.

### Rollback

```bash
./deploy.sh --prod --rollback
```

Restores the newest timestamped backups of `configuration.yaml` and
`.storage/lovelace_resources` taken by previous deploys, restarts HA, and prunes
all but the 5 most recent backups.

---

## 4. Verification

```bash
python3 env_profile.py --list                 # which profiles are declared
python3 env_profile.py prod                   # what the profile resolves to
python3 stage_provisioner.py inspect          # what the target actually has
./deploy.sh --prod --preflight                # readiness of a real target
../../deploy.sh --check-ha                    # is the installed library our fork?
```

Dashboard URL: `http://<host>:8123/dashboard-sailing/`.

---

## 5. Examples

### 5.1 Stage (`local-ha`) from scratch, with a full wipe

Everything below runs from `ha/sailing-dash`.

**Step 0 — the profile (once).**

```bash
cd ha/sailing-dash
cp .env.template .env          # only if .env does not exist yet
```

For stage there is usually nothing to edit: `STAGE_TRANSPORT=local-docker`,
`STAGE_CONTAINER=local-ha`, `STAGE_GW_HOST=127.0.0.1`,
`STAGE_GW_DATA_PORT=4001`. Check that the profile is picked up:

```bash
python3 helpers/env_profile.py --list
python3 helpers/env_profile.py stage
```

**Step 1 — the wipe (this is what makes the install "clean").**

```bash
docker compose -f local-ha/docker-compose.yml down -v
rm -rf local-ha/config          # the whole HA config incl. .storage
rm -rf build                    # build artifacts incl. build/deps
```

`local-ha/config` and `build/` are gitignored — nothing of yours lives there.

**Step 2 — the clean install.**

Guided (recommended — stops at the two gates):

```bash
./install_wizard.sh --target stage --reinstall
```

Or the unguided one-shot, which provisions *and* deploys without stopping:

```bash
./run_stage.sh --clean-install
```

What happens automatically, in order:

1. `docker compose up` of the `local-ha` container (`network_mode: host`);
2. `build.py` + `fetch_deps.py` — exactly once per run; dependencies are fetched
   by tag into `build/deps/`;
3. the mock NMEA emulator starts on `127.0.0.1:4001` (demo mode is the default);
4. `stage_provisioner.py provision --clean-install`: onboarding bypass,
   `test`/`test` user, HACS, the `nmea2000` integration from tag
   `ydnu-02-usb-tcp-gw`, and a config entry on `GW_HOST:GW_DATA_PORT` with
   `gateway_type: text`;
5. `deploy.sh --target stage --install`: card bundles into `/config/www/`, the
   `lovelace_resources` merge, `map_nmea_sensors.py` auto-discovery, sensors into
   `configuration.yaml`, the dashboard into
   `.storage/lovelace.dashboard_sailing`, HA restart;
6. a watcher stays on `src/` — edits are rebuilt and redeployed automatically.

Need the HA image rebuilt too: `./build_docker.sh --no-cache` (a thin wrapper
around the same entry point).

**Step 3 — check.**

```bash
python3 helpers/stage_provisioner.py inspect --target stage
python3 helpers/stage_provisioner.py check-hacs --target stage
open http://localhost:8123/dashboard-sailing/
```

Log in with `test` / `test`. The dashboard must answer HTTP 200 and show values
coming from the emulator.

**Manual steps on stage.** Confirm in the UI that onboarding really was
bypassed, then restart HA and add the **HACS** integration, authorizing at
`github.com/login/device` — this **cannot be automated**. The HACS *files* are
already in place (delivered from the pin), so this is activation only; the wizard
blocks at GATE A until `check-hacs` confirms it. The order of manual steps is
identical to Prod on purpose.

### 5.2 Partial commands

```bash
./deploy.sh --target stage --resources-only    # cards + Lovelace resources only
./deploy.sh --target stage --sensors-only      # sensors only (with auto-discovery)
./deploy.sh --target stage --dashboard-only    # dashboard only
SKIP_RESTART=1 ./deploy.sh --target stage ...  # no HA restart
./run_stage.sh --live --gw-host <host>         # a real gateway, not the emulator
```

### 5.3 A second Pi5 as a remote stage box

```bash
# .env
HA_PROFILES="stage stage-pi5 prod"
STAGE_PI5_TRANSPORT=ssh-docker
STAGE_PI5_SSH_HOST=pi@stage-pi5.local
STAGE_PI5_CONTAINER=homeassistant
STAGE_PI5_HA_URL=http://stage-pi5.local:8123
STAGE_PI5_GW_HOST=192.168.1.50
```

```bash
./deploy.sh --target stage-pi5 --preflight
./deploy.sh --target stage-pi5 --install
```

### 5.4 Prod, end to end

Guided:

```bash
./install_wizard.sh --target prod
```

The same thing by hand:

```bash
./deploy.sh --prod --bootstrap     # AUTO stage 1 (HACS files + integration + cards)
#  … GATE A: add HACS in the UI, authorize at github.com/login/device …
python3 helpers/stage_provisioner.py check-hacs --target prod
#  … GATE B: config entry on <gw>:4001 (type text), restart HA, wait for raw entities …
./deploy.sh --prod --preflight     # read-only readiness gate
./deploy.sh --prod --install       # AUTO stage 2
./deploy.sh --prod --rollback      # if something went wrong
```

---

See [HACS_SETUP.md](HACS_SETUP.md) for the HACS and NMEA 2000 UI details,
[README.md](README.md) for the architecture, and [TEST.md](TEST.md) for the
verification procedures.
