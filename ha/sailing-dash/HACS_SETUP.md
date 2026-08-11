# HACS & NMEA 2000 Integration Setup Guide

This guide explains how to set up **HACS** (Home Assistant Community Store) and add our custom
**NMEA 2000** integration (`github.com/dnevera/ha-nmea2000`, pinned to the tag
`ydnu-02-usb-tcp-gw`) through the HA UI — both on **Prod** (the vessel server) and on **Stage**
(any test container; by default the bundled `local-ha`).

> Nothing is vendored in this repo. On Stage, `stage_provisioner.py` installs the real HACS release
> and the integration **downloaded from the pinned tag** into `build/deps/` (see `deps.yaml` and
> `INSTALLATION.md`). This document covers the **manual UI steps** that cannot be automated
> headlessly (HACS activation, adding a custom repository), which are identical on Stage and Prod.

**Order matters:** do everything in this document *before* `./deploy.sh --<target> --install`.
Auto-discovery reads an entity registry that only exists once the integration is configured and the
bus has produced entities; `--install` runs a preflight check and refuses to proceed otherwise.

`./install_wizard.sh --target <profile>` walks this document for you and blocks on two gates:

* **GATE A** — HACS: files delivered *and* activated, verified with
  `python3 helpers/stage_provisioner.py check-hacs --target <profile>`;
* **GATE B** — NMEA 2000 integration, config entry on the tcp-gw and raw entities, verified with
  `./deploy.sh --target <profile> --preflight`.

Both gates behave the same on Stage and on Prod — nothing here is "advisory on stage".

---

## 1. Installing HACS

The **files** are delivered by our scripts on every profile — Stage and Prod alike, so the two
environments cannot drift apart. The release is pinned in `deps.yaml`, fetched by
`helpers/fetch_deps.py` into `build/deps/hacs/` and installed into
`/config/custom_components/hacs/` by:

* Stage: `stage_provisioner.py`'s `deploy_hacs_integration()` (part of `./run_stage.sh`);
* Prod: `./deploy.sh --prod --bootstrap` (`scp` + `docker cp`).

No manual step is needed to *install* the files; you still need to *activate* HACS (next section).

> `wget -O - https://get.hacs.xyz | bash -` remains an alternative way to deliver the same files on
> a vessel server. It is not a required step of this procedure.

### Activating HACS (the only manual part — required on both Stage and Prod)
This is a GitHub device-flow login and cannot be automated headlessly:
1. Open Home Assistant in the browser (`http://localhost:8123/` on Stage, or the vessel address on Prod).
2. Go to **Settings -> Devices & Services -> Add Integration**, search for **HACS**, and add it.
3. HACS will show a code and a link to `github.com/login/device`. Open that link, sign in with a
   GitHub account, and enter the code.
4. Accept the requested permissions. HACS will finish setup and a new **HACS** item appears in the
   left sidebar.

Verify it — the wizard's GATE A runs exactly this:

```bash
python3 helpers/stage_provisioner.py check-hacs --target <profile>
```

It distinguishes *not delivered* (`custom_components/hacs/manifest.json` missing — our automation
failed, re-run `fetch_deps.py` / `--bootstrap`) from *not activated* (no config entry for domain
`hacs` — the UI steps above are still pending). Exit code 0 only when both are true.

---

## 2. Adding the `dnevera/ha-nmea2000` Custom Repository

Our own fork is not in the default HACS store, so it must be added as a **custom repository**. Always
pick the **tag `ydnu-02-usb-tcp-gw`**, never a branch: a branch is a moving pointer and two installs
on different days would give different code.

1. In the sidebar, open **HACS -> Integrations**.
2. Click the three-dot menu (⋮) in the top-right corner -> **Custom repositories**.
3. In the dialog, enter:
   - **Repository**: `https://github.com/dnevera/ha-nmea2000`
   - **Category**: `Integration`
4. Click **Add**, then close the dialog.
5. Back in **HACS -> Integrations**, click **+ Explore & Download Repositories**, search for
   **NMEA 2000 (Bumblebee Custom)**, and select it.
6. Click **Download**, choose the **tag `ydnu-02-usb-tcp-gw`**, and confirm.
7. Restart Home Assistant when prompted (**Settings -> System -> Restart**, or
   `docker restart <container>`).

> This step is optional if you used `./deploy.sh --<target> --bootstrap` (Prod) or `./run_stage.sh`
> (Stage): both deliver the very same tag's files into `/config/custom_components/nmea2000/`
> straight from `build/deps/` — HACS is only a downloader, so the runtime result is identical. Use
> the UI route when you want the integration to be updatable through HACS itself.

---

## 3. Configuring the NMEA 2000 Integration

Once the integration is installed (either via HACS above or automatically on Stage):

1. Go to **Settings -> Devices & Services -> Add Integration**.
2. Search for **NMEA 2000** and select it.
3. Fill in the connection settings:
   - **Gateway type**: `text` (matches the YDNU-02 TCP gateway / mock emulator output format).
   - **Host**: the profile's `<P>_GW_HOST` — `127.0.0.1` on a local Stage (the container runs with
     `network_mode: host` and reaches `mock_nmea_emulator.py` directly); the gateway's LAN IP on Prod.
   - **Port**: the profile's `<P>_GW_DATA_PORT` from `ha/sailing-dash/.env` (`4001` by default).
4. Click **Submit**. New N2K sensor entities (COG/SOG, wind, STW, depth, GPS, heading) should appear
   within a few seconds, once HA finishes installing the integration's pip dependency — the
   `nmea2000` library from our fork's tag `dnevera/nmea2000@cpu-overload-fix` (no patches; see
   `requirements-ha.txt` section 0). Verify it with `./deploy.sh --check-ha` from the repo root.
5. Run `./deploy.sh --prod` (or `./deploy.sh --stage`), which automatically executes `map_nmea_sensors.py`
   to discover these new raw PGN entities and bind them to the canonical alias template sensors
   (`sensor.boat_stw`, `sensor.boat_depth`, `sensor.boat_wind_speed`, etc.).

> On Stage, `stage_provisioner.py provision` already creates this config entry automatically
> (`provision_nmea2000_config_entry()`), pointed at the profile's `GW_HOST:GW_DATA_PORT` — this step
> is only needed if the entry was removed manually or you're configuring a fresh Prod instance.
> The provisioned entry is written as a **v2** entry carrying the legacy `mode: TCP` key, so the
> integration's migration cannot log `Unknown mode 'None' during migration` any more.

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
   `stage_provisioner.py` on both Stage and Prod. Every other card is downloaded by `fetch_deps.py`
   from the version pinned in `deps.yaml`.
4. After installing, do a hard browser refresh (Ctrl/Cmd+Shift+R) or clear the frontend cache so HA
   picks up the newly registered resources.

> On Stage, all of the above (including `windy-boat-card`) are deployed automatically by
> `deploy_card_bundles()` from `build/deps/cards/` without needing HACS; on Prod,
> `./deploy.sh --prod --bootstrap` does the same. Install through the HACS UI when you want HACS to
> keep the cards updated. See `INSTALLATION.md` for the full order of operations.

---

## Troubleshooting

| Problem | Cause | Solution |
| :--- | :--- | :--- |
| HACS device-flow code expires before you finish logging in | GitHub device codes are time-limited (~15 min) | Restart the HACS setup flow (**Settings -> Devices & Services -> HACS -> Configure**) to get a fresh code. |
| `dnevera/ha-nmea2000` doesn't show up under **Explore & Download Repositories** | Custom repository not added, or added under the wrong category | Re-check **HACS -> Integrations -> ⋮ -> Custom repositories** — the **Category** must be `Integration`, not `Plugin`/`Theme`. |
| NMEA 2000 integration installs but entities never appear | Wrong host/port in the config entry, or gateway/emulator not running | Confirm `mock_nmea_emulator.py --port 4001` (Stage) or the real gateway (Prod) is running and reachable, then remove and re-add the integration with the correct **Host**/**Port**. |
| Custom cards show "Custom element doesn't exist" after HACS install | Browser cached the old frontend bundle without the new resource | Hard-refresh the browser (Ctrl/Cmd+Shift+R) or clear cache; confirm the resource appears under **Settings -> Dashboards -> Resources**. |
