# HACS & NMEA 2000 Integration Setup Guide

This guide explains how to set up **HACS** (Home Assistant Community Store) and add our custom
**NMEA 2000** integration (`github.com/dnevera/ha-nmea2000`) through the HA UI — both on **Prod**
(vessel Pi5, `bumblebee.local`) and on **Stage** (`local-ha` Docker container).

> On Stage, `stage_provisioner.py` already installs the real HACS integration and a vendored copy
> of the NMEA 2000 integration automatically (see `INSTALLATION.md`). This document covers the
> **manual UI steps** that cannot be automated headlessly (HACS activation, adding a custom
> repository), which are identical on Stage and Prod.

---

## 1. Installing HACS

### On Prod (fresh HA install)
HACS is **not** part of core Home Assistant and must be installed once:
```bash
wget -O - https://get.hacs.xyz | bash -
```
Then restart Home Assistant (`ha core restart` or restart the container/service).

### On Stage (`local-ha`)
Already installed automatically for you — `stage_provisioner.py`'s `deploy_hacs_integration()`
downloads the same official release used above and installs it into
`/config/custom_components/hacs/` as part of `./run_stage.sh` / `./deploy.sh --stage`. No manual
step is needed to *install* the files; you still need to *activate* it (next section).

### Activating HACS (manual step, required on both Stage and Prod)
This is a GitHub device-flow login and cannot be automated headlessly:
1. Open Home Assistant in the browser (`http://localhost:8123/` on Stage, or the vessel address on Prod).
2. Go to **Settings -> Devices & Services -> Add Integration**, search for **HACS**, and add it.
3. HACS will show a code and a link to `github.com/login/device`. Open that link, sign in with a
   GitHub account, and enter the code.
4. Accept the requested permissions. HACS will finish setup and a new **HACS** item appears in the
   left sidebar.

---

## 2. Adding the `dnevera/ha-nmea2000` Custom Repository

Our own fork (branch `bumblebee-custom`) is not in the default HACS store, so it must be added as a
**custom repository**:

1. In the sidebar, open **HACS -> Integrations**.
2. Click the three-dot menu (⋮) in the top-right corner -> **Custom repositories**.
3. In the dialog, enter:
   - **Repository**: `https://github.com/dnevera/ha-nmea2000`
   - **Category**: `Integration`
4. Click **Add**, then close the dialog.
5. Back in **HACS -> Integrations**, click **+ Explore & Download Repositories**, search for
   **NMEA 2000 (Bumblebee Custom)**, and select it.
6. Click **Download**, choose the latest version/branch (`bumblebee-custom`), and confirm.
7. Restart Home Assistant when prompted (**Settings -> System -> Restart**, or on Stage:
   `docker restart local-ha`).

> On Stage this step is optional — `deploy_nmea2000_integration()` already installs the same fork's
> files directly into `/config/custom_components/nmea2000/` without going through HACS. Use this
> section only if you want the integration to appear/update through the HACS UI itself, or on Prod
> where there is no vendored copy-install fallback.

---

## 3. Configuring the NMEA 2000 Integration

Once the integration is installed (either via HACS above or automatically on Stage):

1. Go to **Settings -> Devices & Services -> Add Integration**.
2. Search for **NMEA 2000** and select it.
3. Fill in the connection settings:
   - **Gateway type**: `text` (matches the YDNU-02 TCP gateway / mock emulator output format).
   - **Host**: `127.0.0.1` on Stage (container runs with `network_mode: host`, reaching
     `mock_nmea_emulator.py` on port `4001` directly); the gateway's LAN IP on Prod.
   - **Port**: `4001` on Stage; the real gateway port on Prod (see `deploy.conf`).
4. Click **Submit**. New N2K sensor entities (COG/SOG, wind, STW, depth, GPS, heading) should appear
   within a few seconds, once HA finishes installing the integration's pip dependency
   (our patched `dnevera/nmea2000` git fork — see `requirements-ha.txt` section 0).

> On Stage, `stage_provisioner.py provision` already creates this config entry automatically
> (`provision_nmea2000_config_entry()`), pointed at `127.0.0.1:4001` — this step is only needed if
> the entry was removed manually or you're configuring a fresh Prod instance.

---

## 4. Installing Frontend Custom Cards via HACS

The dashboard uses several custom Lovelace cards (`card-mod`, `compass-card`, `apexcharts-card`,
`windrose-card`, `plotly-graph-card`, `config-template-card`, `windy-boat-card`). On Prod, install
them through HACS:

1. In the sidebar, open **HACS -> Frontend**.
2. Click **+ Explore & Download Repositories**, and install each of the following, in this order
   (`card-mod` is a soft dependency of some others):
   - `card-mod`
   - `apexcharts-card`
   - `compass-card`
   - `windrose-card`
   - `plotly-graph-card`
   - `config-template-card`
3. `windy-boat-card` is a custom element specific to this project — it is **not** in the HACS store;
   it is built from `src/` by `build.py` and deployed to `/config/www/` directly by `deploy.sh` /
   `stage_provisioner.py` on both Stage and Prod.
4. After installing, do a hard browser refresh (Ctrl/Cmd+Shift+R) or clear the frontend cache so HA
   picks up the newly registered resources.

> On Stage, all of the above (including `windy-boat-card`) are deployed automatically by
> `deploy_card_bundles()` without needing HACS — see `INSTALLATION.md`.

---

## Troubleshooting

| Problem | Cause | Solution |
| :--- | :--- | :--- |
| HACS device-flow code expires before you finish logging in | GitHub device codes are time-limited (~15 min) | Restart the HACS setup flow (**Settings -> Devices & Services -> HACS -> Configure**) to get a fresh code. |
| `dnevera/ha-nmea2000` doesn't show up under **Explore & Download Repositories** | Custom repository not added, or added under the wrong category | Re-check **HACS -> Integrations -> ⋮ -> Custom repositories** — the **Category** must be `Integration`, not `Plugin`/`Theme`. |
| NMEA 2000 integration installs but entities never appear | Wrong host/port in the config entry, or gateway/emulator not running | Confirm `mock_nmea_emulator.py --port 4001` (Stage) or the real gateway (Prod) is running and reachable, then remove and re-add the integration with the correct **Host**/**Port**. |
| Custom cards show "Custom element doesn't exist" after HACS install | Browser cached the old frontend bundle without the new resource | Hard-refresh the browser (Ctrl/Cmd+Shift+R) or clear cache; confirm the resource appears under **Settings -> Dashboards -> Resources**. |
