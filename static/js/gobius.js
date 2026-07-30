/**
 * YDNU-02 Web Console — Gobius C BLE Tab
 *
 * ─── BLE PROTOCOL REFERENCE ────────────────────────────────────────────────
 * Source: "GOBIUS C Bluetooth Protocol & Functional Description"
 *         Issue 3, 2023-08-08, Anders Remar, Gobius Sensor Technology AB
 *
 * All multi-byte values are Big-Endian (MSB first) per spec §8.2.4.
 *
 * GATT Characteristics:
 *
 *   0xFFE8  R          Status (20 bytes) — §Table 26
 *     [0]     ST_ST   State (5=Active, 0=Start-Up, 2=Uninit, 6=Error)
 *     [1]     ST_SB   Status bits
 *     [2:6]   ST_T    Uptime [s] uint32 BE
 *     [6]     ST_ER1  General error code
 *     [7]     ST_ER2  Hardware error code
 *     [8]     ST_T    Processor temperature °C (int8 signed)
 *     [9:11]  ST_V    Supply voltage [mV] uint16 BE
 *     [11:17] ST_ID   BLE MAC address (6 bytes)
 *     [17]    ST_ER3  Extended HW error
 *     [18]    ST_ERR  Radar comm error counter
 *     [19]    ST_RNG  Current measurement range (0=Zero,1=Near,2=Mid,3=Far)
 *
 *   0xFFE9  R+Notify   Measurement (20 bytes) — §Table 27
 *     [0]     M_ST    State
 *     [1]     M_SB    Status bits
 *     [2]     M_VD    Level validity (0=invalid, 1=valid)
 *     [3:5]   M_FL    Fill level ‰ [0-1000] uint16 BE  → divide by 10 for %
 *     [5]     M_INC   Sensor inclination [0-90°]
 *     [6:8]   M_DIST  Distance sensor→fluid surface [mm] uint16 BE
 *     [8:10]  M_SZR   Envelope size Zero Range
 *     [10:12] M_SNR   Envelope size Near Range
 *     [12:14] M_SMR   Envelope size Mid Range
 *     [14:16] M_SFR   Envelope size Far Range
 *
 *   0xFFF3  R          N2K Status (20 bytes) — N2K firmware extension
 *     [0]     n2k_state  (0=off, 2=active)
 *     [1]     n2k_src    NMEA 2000 source address
 *
 *   0xFFE6  R/W        User Config (20 bytes) — §Table 23
 *     [0:2]   UC_DE    Distance for tank EMPTY [mm] uint16 BE  clamp [20-2000]
 *     [2:4]   UC_DF    Distance for tank FULL [mm]  uint16 BE  clamp [20-2000]
 *     [4]     UC_LPN   LP filter size (0=disable, range 0-100)
 *     [5]     UC_LPK   LP filter threshold [1-100] %
 *     [6]     UC_BITS  Config bits (§Table 17)
 *     [7]     UC_O1T   Output 1 threshold %
 *     [8]     UC_O1H   Output 1 hysteresis %
 *     [9]     UC_O2T   Output 2 threshold %
 *     [10]    UC_O2H   Output 2 hysteresis %
 *     [18]    UC_AOF   Advertise-off time [10-255 s]
 *   Write: read → patch bytes → write_char → verify with read_char
 *
 *   0xFFF2  R/W        N2K Config (20 bytes) — N2K extension
 *     [0]     enabled     0x00=off / 0x01=on
 *     [1]     instance    fluid instance (nibble &0x0F, range 0-15)
 *     [2]     fluid_type  NMEA fluid type code (0=Fuel … 6=Gasoline)
 *     [9]     volume_l    Tank volume [L] uint8  clamp [1-255]
 *   Write: read → patch bytes → write_char → verify with read_char
 *
 *   0xFFE7  W           Command (3 bytes) — §Table 18
 *     [0]     cmd_code   ASCII character
 *     [1:3]   param      uint16 BE (optional, 0x0000 if not used)
 *     Commands:
 *       'i' (0x69)  initialize  — FACTORY RESET ⚠️ all settings erased
 *       'c' (0x63)  calibrate   — calibrate radar
 *       'a' (0x61)  stop        — stop measuring
 *       'b' (0x62)  start       — start measuring
 *       'n' (0x6E)  adv_normal  — normal BLE advertising mode
 *       'o' (0x6F)  adv_off     — turn off BLE advertising
 *       'w' (0x77)  write_info  — MUST be sent after writing 0xFFEB/0xFFEC
 *       's' (0x73)  secure      — enable security
 *       'u' (0x75)  unsecure    — disable security
 *
 *   0xFFEB  R/W   Info 1 (20 bytes, UTF-8, right-padded with spaces)
 *   0xFFEC  R/W   Info 2 (20 bytes, UTF-8, right-padded with spaces)
 *   Info write sequence (MUST follow this order):
 *     1. write_char(0xFFEB, info1_20_bytes)
 *     2. write_char(0xFFEC, info2_20_bytes)
 *     3. write_char(0xFFE7, [ord('w'), 0, 0])   ← write_info commit
 *
 * BLE and NMEA are INDEPENDENT channels. Do NOT mix writes.
 * ───────────────────────────────────────────────────────────────────────────
 *
 * Change detection:
 *   After loadGobius() a _gobiusSensorSnapshot is saved.
 *   All config inputs have oninput → onGobiusInputChange() which compares
 *   current form values to snapshot. Save button is enabled only if changed.
 *
 * Auto-load:
 *   refreshGobiusLive() detects first BLE connect (disconnected→connected)
 *   and fires loadGobius() automatically — no manual Refresh needed.
 */

Object.assign(App, {

    _gobiusLoading:        false,
    _gobiusLiveInterval:   null,
    _gobiusConnectedPrev:  false,   // tracks prev connection state for auto-load
    _gobiusSensorSnapshot: null,    // config values as last read from sensor (for confirm dialog params)
    _gobiusConfigLoaded:   false,   // true once form fields have been populated

    // ── Auto-polling: live telemetry from /api/gobius/live ──

    startGobiusPolling() {
        this.refreshGobiusLive();
        this.startPolling('_gobiusLiveInterval', this.refreshGobiusLive, 5000);
    },

    stopGobiusPolling() {
        this.stopPolling('_gobiusLiveInterval');
    },

    async refreshGobiusLive() {
        try {
            const data = await this.api('/api/gobius/live');

            const statusEl = document.getElementById('gobius-ble-status');
            if (statusEl) {
                if (data.connected) {
                    const age = data.age_sec != null ? data.age_sec.toFixed(0) + 's ago' : '';
                    statusEl.innerHTML = '<span style="color:#4f8">✅ Connected</span> — ' + data.address +
                        (age ? ' <span style="opacity:.5;font-size:.85em">(' + age + ')</span>' : '');
                } else {
                    statusEl.innerHTML = '<span style="color:#f44">❌ Not connected</span>' +
                        (data.error ? ' — ' + data.error : '');
                    this._gobiusConnectedPrev = false;
                    return;
                }
            }

            // Auto-load config on first BLE connect (not requiring manual Refresh)
            if (!this._gobiusConnectedPrev) {
                this._gobiusConnectedPrev = true;
                if (!this._gobiusConfigLoaded) {
                    this.loadGobius();  // fire-and-forget
                }
            }

            const d  = data.device      || {};
            const s  = data.status      || {};
            const m  = data.measurement || {};
            const ns = data.n2k_status  || {};
            const us = data.unified_sensor || {};

            this.setFields({
                'gob-serial':      d.serial,
                'gob-fw':          d.firmware,
                'gob-state':       s.state_str,
                'gob-status-bits': s.status_bits_str,
                'gob-range':       s.current_range,
                'gob-error-code':  s.error_code,
                'gob-temp':        s.temp_c    != null ? s.temp_c    + ' °C' : null,
                'gob-voltage':     s.voltage_v != null ? s.voltage_v + ' V'  : null,
                'gob-mac':         s.mac || data.address,
                'gob-measuring':   s.measuring,
                'gob-fill-level':  m.fill_pct      != null ? m.fill_pct      + ' %'  : null,
                'gob-distance':    m.distance_mm   != null ? m.distance_mm   + ' mm' : null,
                'gob-inclination': m.inclination_deg != null ? m.inclination_deg + '°' : null,
                'gob-n2k-state':   ns.n2k_state,
                'gob-n2k-src':     ns.n2k_src,
                'gob-nmea-fill':   us.fill_level_pct != null ? us.fill_level_pct + ' %' : null,
                'gob-nmea-cap':    us.capacity_l     != null ? us.capacity_l     + ' L' : null,
                'gob-nmea-calc':   us.calculated_l   != null ? us.calculated_l   + ' L' : null,
            });
        } catch(e) { /* non-critical — poller not ready yet */ }
    },

    // ── Manual Refresh: full BLE re-read, populates config form ──

    async loadGobius(btnEl) {
        if (this._gobiusLoading) return;
        this._gobiusLoading = true;

        const btn   = btnEl || document.getElementById('btn-gobius-refresh');
        const panel = btn ? btn.closest('.panel') : null;
        const origText = btn ? btn.textContent : '';
        if (panel) panel.classList.add('busy');
        if (btn) { btn.disabled = true; btn.textContent = 'Loading...'; }

        try {
            const data = await this.api('/api/gobius/status');
            if (!data || !data.connected) {
                document.getElementById('gobius-ble-status').innerHTML =
                    '<span style="color:#f44">❌ Not connected</span>' +
                    (data && data.error ? ' — ' + data.error : '');
                return;
            }

            document.getElementById('gobius-ble-status').innerHTML =
                '<span style="color:#4f8">✅ Connected</span> — ' + data.address;

            const d  = data.device      || {};
            const s  = data.status      || {};
            const m  = data.measurement || {};
            const ns = data.n2k_status  || {};
            const us = data.unified_sensor || {};
            const n  = data.n2k_config  || {};
            const u  = data.user_config || {};

            this.setFields({
                'gob-serial':      d.serial,
                'gob-fw':          d.firmware,
                'gob-state':       s.state_str,
                'gob-status-bits': s.status_bits_str,
                'gob-range':       s.current_range,
                'gob-error-code':  s.error_code,
                'gob-temp':        s.temp_c    != null ? s.temp_c    + ' °C' : null,
                'gob-voltage':     s.voltage_v != null ? s.voltage_v + ' V'  : null,
                'gob-mac':         s.mac || data.address,
                'gob-measuring':   s.measuring,
                'gob-fill-level':  m.fill_pct      != null ? m.fill_pct      + ' %'  : null,
                'gob-distance':    m.distance_mm   != null ? m.distance_mm   + ' mm' : null,
                'gob-inclination': m.inclination_deg != null ? m.inclination_deg + '°' : null,
                'gob-n2k-state':   ns.n2k_state,
                'gob-n2k-src':     ns.n2k_src,
                'gob-nmea-fill':   us.fill_level_pct != null ? us.fill_level_pct + ' %' : null,
                'gob-nmea-cap':    us.capacity_l     != null ? us.capacity_l     + ' L' : null,
                'gob-nmea-calc':   us.calculated_l   != null ? us.calculated_l   + ' L' : null,
            });

            // Build snapshot BEFORE populating form — used for change detection
            const snapshot = {
                n2kEnabled:  !!n.n2k_enabled,
                fluidType:    n.fluid_type    ?? 0,
                volumeL:      n.volume_l      ?? 10,
                n2kInstance:  n.fluid_instance ?? 0,
                distEmpty:    u.distance_empty_mm ?? 300,
                distFull:     u.distance_full_mm  ?? 50,
                lpN:          u.lp_filter_n ?? 3,
                lpK:          u.lp_filter_k ?? 10,
                info1:        (d.info1 || '').trim(),
                info2:        (d.info2 || '').trim(),
            };
            this._gobiusSensorSnapshot = snapshot;

            // Populate form (manual Refresh always overwrites user edits)
            this._gobiusSetFormValues(snapshot);
            this._gobiusConfigLoaded = true;

            // Update badge and guard
            this._gobiusUpdateN2kBadge(snapshot.n2kEnabled);

            // Nothing changed — disable Save button
            this._gobiusUpdateSaveButton(false);

        } catch (e) {
            document.getElementById('gobius-ble-status').innerHTML =
                '<span style="color:#f44">❌ Error: ' + e.message + '</span>';
        } finally {
            this._gobiusLoading = false;
            if (panel) panel.classList.remove('busy');
            if (btn) { btn.disabled = false; btn.textContent = origText; }
        }
    },

    // ── Form helpers ──

    _gobiusSetFormValues(snap) {
        const set = (id, val) => {
            const el = document.getElementById(id);
            if (!el) return;
            if (el.type === 'checkbox') {
                el.checked = !!val;
                // Store initial state as data attribute for change detection
                el.dataset.initial = el.checked ? 'true' : 'false';
            } else {
                el.value = val;
                // Store initial state as data attribute for change detection
                el.dataset.initial = el.value;
            }
        };
        set('gob-n2k-enabled',  snap.n2kEnabled);
        set('gob-fluid-type',   snap.fluidType);
        set('gob-volume-l',     snap.volumeL);
        set('gob-n2k-instance', snap.n2kInstance);
        set('gob-dist-empty',   snap.distEmpty);
        set('gob-dist-full',    snap.distFull);
        set('gob-lp-n',         snap.lpN);
        set('gob-lp-k',         snap.lpK);
        set('gob-info1',        snap.info1);
        set('gob-info2',        snap.info2);
    },

    /** Ids of all config inputs that participate in change detection. */
    _GOBIUS_CONFIG_IDS: [
        'gob-n2k-enabled', 'gob-fluid-type', 'gob-volume-l', 'gob-n2k-instance',
        'gob-dist-empty', 'gob-dist-full', 'gob-lp-n', 'gob-lp-k',
        'gob-info1', 'gob-info2',
    ],

    _gobiusReadFormValues() {
        const g = (id) => document.getElementById(id);
        return {
            n2kEnabled:  g('gob-n2k-enabled')?.checked ?? false,
            fluidType:   parseInt(g('gob-fluid-type')?.value)   || 0,
            volumeL:     parseInt(g('gob-volume-l')?.value)     || 10,
            n2kInstance: parseInt(g('gob-n2k-instance')?.value) || 0,
            distEmpty:   parseInt(g('gob-dist-empty')?.value)   || 300,
            distFull:    parseInt(g('gob-dist-full')?.value)    || 50,
            lpN:         parseInt(g('gob-lp-n')?.value)         ?? 3,
            lpK:         parseInt(g('gob-lp-k')?.value)         ?? 10,
            info1:       (g('gob-info1')?.value || '').trim(),
            info2:       (g('gob-info2')?.value || '').trim(),
        };
    },

    _gobiusUpdateN2kBadge(enabled) {
        const badge = document.getElementById('gob-n2k-badge');
        if (badge) {
            badge.textContent = enabled ? 'N2K: ON' : 'N2K: OFF';
            badge.className   = 'badge ' + (enabled ? 'badge-on' : 'badge-off');
        }
        // Safety guard: N2K sub-fields inactive when N2K is OFF
        ['gob-n2k-instance', 'gob-fluid-type', 'gob-volume-l'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.disabled = !enabled;
        });
    },

    _gobiusUpdateSaveButton(hasChanges) {
        const btn = document.getElementById('btn-save-all');
        if (!btn) return;
        btn.disabled = !hasChanges;
        btn.classList.toggle('btn-has-changes', hasChanges);
    },

    /** Initialize dataset.initial for all config elements if not already set. */
    initGobiusForm() {
        this._GOBIUS_CONFIG_IDS.forEach(id => {
            const el = document.getElementById(id);
            if (el && el.dataset.initial === undefined) {
                el.dataset.initial = el.type === 'checkbox' ? (el.checked ? 'true' : 'false') : String(el.value);
            }
        });
    },

    /**
     * Called by oninput/onchange on every config field.
     * Change detection is DOM-based: compares el.value with el.dataset.initial.
     */
    onGobiusInputChange() {
        this.initGobiusForm();
        const hasChanges = this._GOBIUS_CONFIG_IDS.some(id => {
            const el = document.getElementById(id);
            if (!el) return false;
            const init = el.dataset.initial;
            if (el.type === 'checkbox') {
                return (el.checked ? 'true' : 'false') !== init;
            }
            return String(el.value) !== String(init);
        });
        this._gobiusUpdateSaveButton(hasChanges);
        // Update N2K badge live as user toggles the switch
        const n2kEl = document.getElementById('gob-n2k-enabled');
        if (n2kEl) this._gobiusUpdateN2kBadge(n2kEl.checked);
    },

    // ── Confirm Dialog ──────────────────────────────────────────────────────

    /**
     * Show BLE confirm dialog before writing to sensor.
     *
     * @param {object} opts
     *   opts.title  {string}                    — operation title
     *   opts.params {Array<{label,value,danger}>} — parameters table rows
     *   opts.danger {boolean}                   — true → red header + ⚠️
     * @returns {Promise<boolean>}  true if user clicked OK
     */
    gobiusConfirm(opts) {
        return new Promise((resolve) => {
            const modal    = document.getElementById('ble-confirm-modal');
            const header   = document.getElementById('ble-confirm-header');
            const titleEl  = document.getElementById('ble-confirm-title');
            const iconEl   = document.getElementById('ble-confirm-icon');
            const paramsEl = document.getElementById('ble-confirm-params');
            const okBtn    = document.getElementById('ble-confirm-ok');
            const cancelBtn = document.getElementById('ble-confirm-cancel');

            titleEl.textContent = opts.title;
            iconEl.textContent  = opts.danger ? '⚠️' : '📡';
            header.className    = 'ble-confirm-header' + (opts.danger ? ' danger' : '');

            // Build params table
            paramsEl.innerHTML = '';
            (opts.params || []).forEach(p => {
                const tr  = document.createElement('tr');
                const tdL = document.createElement('td');
                const tdV = document.createElement('td');
                tdL.textContent = p.label;
                tdV.textContent = p.value;
                if (p.danger) tdV.className = 'confirm-value-danger';
                tr.appendChild(tdL);
                tr.appendChild(tdV);
                paramsEl.appendChild(tr);
            });

            // If no params, show a generic "Proceed?" row
            if (!opts.params || opts.params.length === 0) {
                const tr  = document.createElement('tr');
                const td  = document.createElement('td');
                td.colSpan = 2;
                td.textContent = 'Send command to sensor?';
                td.style.textAlign = 'center';
                td.style.color = '#aaa';
                tr.appendChild(td);
                paramsEl.appendChild(tr);
            }

            modal.style.display = 'flex';

            const cleanup = () => { modal.style.display = 'none'; };

            okBtn.onclick    = () => { cleanup(); resolve(true); };
            cancelBtn.onclick = () => { cleanup(); resolve(false); };
            modal.onclick    = (e) => { if (e.target === modal) { cleanup(); resolve(false); } };
        });
    },

    // ── Config writes (BLE only) ─────────────────────────────────────────────

    async saveAllConfig(btnEl) {
        if (!this._gobiusSensorSnapshot) return;

        const current  = this._gobiusReadFormValues();
        const snapshot = this._gobiusSensorSnapshot;

        // Fluid type names (NMEA 2000 standard + Gobius extension)
        const FLUID_NAMES = ['Fuel', 'Fresh Water', 'Gray Water', 'Live Well', 'Oil', 'Black Water', 'Gasoline'];

        // Build change list for dialog
        const params = [];
        if (current.n2kEnabled  !== snapshot.n2kEnabled)
            params.push({ label: 'N2K Output',      value: current.n2kEnabled ? 'ON' : '⛔ OFF',
                          danger: !current.n2kEnabled });
        if (current.fluidType   !== snapshot.fluidType)
            params.push({ label: 'Fluid Type',      value: FLUID_NAMES[current.fluidType] ?? current.fluidType });
        if (current.volumeL     !== snapshot.volumeL)
            params.push({ label: 'Volume',           value: current.volumeL + ' L' });
        if (current.n2kInstance !== snapshot.n2kInstance)
            params.push({ label: 'N2K Instance',    value: current.n2kInstance });
        if (current.distEmpty   !== snapshot.distEmpty)
            params.push({ label: 'Distance Empty',  value: current.distEmpty + ' mm' });
        if (current.distFull    !== snapshot.distFull)
            params.push({ label: 'Distance Full',   value: current.distFull + ' mm' });
        if (current.lpN         !== snapshot.lpN)
            params.push({ label: 'LP Filter N',     value: current.lpN });
        if (current.lpK         !== snapshot.lpK)
            params.push({ label: 'LP Filter K',     value: current.lpK + ' %' });
        if (current.info1       !== snapshot.info1)
            params.push({ label: 'Name',             value: current.info1 || '(empty)' });
        if (current.info2       !== snapshot.info2)
            params.push({ label: 'Comment',          value: current.info2 || '(empty)' });

        // Disabling N2K output is a dangerous operation
        const isDanger = !current.n2kEnabled && snapshot.n2kEnabled;

        const confirmed = await this.gobiusConfirm({
            title:  'Save Configuration to Sensor (BLE)',
            params,
            danger: isDanger,
        });
        if (!confirmed) return;

        const btn = btnEl || document.getElementById('btn-save-all');
        await this.withButton(btn, 'Saving config', async () => {

            // 1. BLE write 0xFFF2: N2K Config (enabled, fluid_type, instance, volume)
            const r1 = await this.api('/api/gobius/n2k', 'POST', {
                enabled:        current.n2kEnabled,
                fluid_instance: current.n2kInstance,
                fluid_type:     current.fluidType,
                volume_l:       current.volumeL,
            });

            // 2. BLE write 0xFFE6: User Config (geometry + LP filters)
            const r2 = await this.api('/api/gobius/user_config', 'POST', {
                distance_empty_mm: current.distEmpty,
                distance_full_mm:  current.distFull,
                lp_filter_n:       current.lpN,
                lp_filter_k:       current.lpK,
            });

            // 3. BLE write 0xFFEB/0xFFEC: Info labels (only if changed)
            //    MUST send write_info command (0xFFE7 'w') after info writes per spec
            if (current.info1 !== snapshot.info1 || current.info2 !== snapshot.info2) {
                await this.api('/api/gobius/info', 'POST', { info1: current.info1, info2: current.info2 });
                await this.api('/api/gobius/command', 'POST', { command: 'write_info' });
            }

            // Re-read from sensor → refresh snapshot → dataset.initial
            await this.loadGobius();

            const ok = r1.status === 'ok' && r2.status === 'ok';
            return { status: ok ? 'ok' : 'error', message: ok ? 'Config saved to sensor' : 'Some writes failed' };
        });

        // withButton's finally block restores btn.disabled = false.
        // Call onGobiusInputChange() now to re-evaluate changes and disable the Save button!
        this.onGobiusInputChange();
    },

    // ── Commands ─────────────────────────────────────────────────────────────

    // Metadata for confirm dialog per command (matches 0xFFE7 spec table)
    _gobiusCmdMeta: {
        calibrate:  { title: 'Calibrate Sensor',           danger: false, params: [] },
        start:      { title: 'Start Measuring',             danger: false, params: [] },
        stop:       { title: 'Stop Measuring',              danger: false, params: [] },
        adv_normal: { title: 'Set Advertising: Normal',     danger: false, params: [] },
        adv_off:    { title: 'Turn Off BLE Advertising',    danger: true,
                      params: [
                          { label: '⚠️ BLE connection', value: 'Will be LOST immediately', danger: true },
                          { label: 'To re-enable',      value: 'Power cycle the sensor' },
                          { label: 'Reconnect window',  value: 'Connect within 10 seconds after power-on' },
                      ] },
        initialize: { title: 'Factory Reset (Initialize)',  danger: true,
                      params: [{ label: '⚠️ Warning', value: 'ALL sensor settings will be erased', danger: true }] },
    },

    async gobiusCmd(cmd, btnEl) {
        const meta = this._gobiusCmdMeta[cmd] || { title: cmd, danger: false, params: [] };

        const confirmed = await this.gobiusConfirm({
            title:  meta.title,
            params: meta.params,
            danger: meta.danger,
        });
        if (!confirmed) return;

        await this.withButton(btnEl, meta.title, async () => {
            return await this.api('/api/gobius/command', 'POST', { command: cmd });
        });
    },
});
