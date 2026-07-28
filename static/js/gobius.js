/**
 * YDNU-02 Web Console — Gobius C BLE Tab
 *
 * Auto-refreshes ALL Sensor Information fields every 5s from /api/gobius/live.
 * Manual Refresh re-reads configs (geometry, N2K) via /api/gobius/status.
 * Config writes go through the same BLE poller connection.
 */
Object.assign(App, {

    _gobiusLoading: false,
    _gobiusLiveInterval: null,

    // ── Auto-polling: ALL info fields from /api/gobius/live ──

    startGobiusPolling() {
        this.refreshGobiusLive();  // immediate first update
        this.startPolling('_gobiusLiveInterval', this.refreshGobiusLive, 5000);
    },

    stopGobiusPolling() {
        this.stopPolling('_gobiusLiveInterval');
    },

    async refreshGobiusLive() {
        try {
            const data = await this.api('/api/gobius/live');

            // Connection status
            const statusEl = document.getElementById('gobius-ble-status');
            if (statusEl) {
                if (data.connected) {
                    const age = data.age_sec != null ? data.age_sec.toFixed(0) + 's ago' : '';
                    statusEl.innerHTML = '<span style="color:#4f8">✅ Connected</span> — ' + data.address +
                        (age ? ' <span style="opacity:.5;font-size:.85em">(' + age + ')</span>' : '');
                } else {
                    statusEl.innerHTML = '<span style="color:#f44">❌ Not connected</span>' +
                        (data.error ? ' — ' + data.error : '');
                    return;
                }
            }

            const d = data.device || {};
            const s = data.status || {};
            const m = data.measurement || {};
            const ns = data.n2k_status || {};
            const us = data.unified_sensor || {};

            // Update ALL Sensor Information fields (read-only)
            this.setFields({
                'gob-serial':      d.serial,
                'gob-fw':          d.firmware,
                'gob-state':       s.state_str,
                'gob-status-bits': s.status_bits_str,
                'gob-range':       s.current_range,
                'gob-error-code':  s.error_code,
                'gob-temp':        s.temp_c != null ? s.temp_c + ' °C' : null,
                'gob-voltage':     s.voltage_v != null ? s.voltage_v + ' V' : null,
                'gob-mac':         s.mac || data.address,
                'gob-measuring':   s.measuring,
                'gob-fill-level':  m.fill_pct != null ? m.fill_pct + ' %' : null,
                'gob-distance':    m.distance_mm != null ? m.distance_mm + ' mm' : null,
                'gob-inclination': m.inclination_deg != null ? m.inclination_deg + '°' : null,
                'gob-n2k-state':   ns.n2k_state,
                'gob-n2k-src':     ns.n2k_src,
                'gob-nmea-fill':   us.fill_level_pct != null ? us.fill_level_pct + ' %' : null,
                'gob-nmea-cap':    us.capacity_l != null ? us.capacity_l + ' L' : null,
                'gob-nmea-calc':   us.calculated_l != null ? us.calculated_l + ' L' : null,
            });
        } catch(e) { /* non-critical — poller not ready yet */ }
    },

    // ── Manual Refresh: full re-read including configs ──

    async loadGobius(btnEl) {
        if (this._gobiusLoading) return;
        this._gobiusLoading = true;

        const btn = btnEl || document.getElementById('btn-gobius-refresh');
        const panel = btn ? btn.closest('.panel') : null;
        if (panel) panel.classList.add('busy');
        const origText = btn ? btn.textContent : '';
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

            const d = data.device || {};
            const s = data.status || {};
            const m = data.measurement || {};
            const ns = data.n2k_status || {};
            const us = data.unified_sensor || {};

            // ALL info fields
            this.setFields({
                'gob-serial':      d.serial,
                'gob-fw':          d.firmware,
                'gob-state':       s.state_str,
                'gob-status-bits': s.status_bits_str,
                'gob-range':       s.current_range,
                'gob-error-code':  s.error_code,
                'gob-temp':        s.temp_c != null ? s.temp_c + ' °C' : null,
                'gob-voltage':     s.voltage_v != null ? s.voltage_v + ' V' : null,
                'gob-mac':         s.mac || data.address,
                'gob-measuring':   s.measuring,
                'gob-fill-level':  m.fill_pct != null ? m.fill_pct + ' %' : null,
                'gob-distance':    m.distance_mm != null ? m.distance_mm + ' mm' : null,
                'gob-inclination': m.inclination_deg != null ? m.inclination_deg + '°' : null,
                'gob-n2k-state':   ns.n2k_state,
                'gob-n2k-src':     ns.n2k_src,
                'gob-nmea-fill':   us.fill_level_pct != null ? us.fill_level_pct + ' %' : null,
                'gob-nmea-cap':    us.capacity_l != null ? us.capacity_l + ' L' : null,
                'gob-nmea-calc':   us.calculated_l != null ? us.calculated_l + ' L' : null,
            });

            // Config inputs (only on manual Refresh)
            const n = data.n2k_config || {};
            const u = data.user_config || {};
            this.loadInputs({
                'gob-n2k-enabled':  n.n2k_enabled,
                'gob-fluid-type':   n.fluid_type || 0,
                'gob-volume-l':     n.volume_l || 0,
                'gob-n2k-instance': n.fluid_instance || 0,
                'gob-dist-empty':   u.distance_empty_mm || 300,
                'gob-dist-full':    u.distance_full_mm || 50,
                'gob-lp-n':         u.lp_filter_n ?? 3,
                'gob-lp-k':         u.lp_filter_k ?? 10,
                'gob-info1':        d.info1,
                'gob-info2':        d.info2,
            }, '_gobiusConfigLoaded');

            // N2K badge & Safety Guard
            const badge = document.getElementById('gob-n2k-badge');
            const n2kEnabled = n.n2k_enabled || false;
            if (badge) {
                badge.textContent = n2kEnabled ? 'N2K: ON' : 'N2K: OFF';
                badge.className = 'badge ' + (n2kEnabled ? 'badge-on' : 'badge-off');
            }

            // Safety Guard: Disable N2K configuration inputs when N2K is OFF
            ['gob-n2k-instance', 'gob-fluid-type', 'gob-volume-l'].forEach(id => {
                const input = document.getElementById(id);
                if (input) input.disabled = !n2kEnabled;
            });

        } catch (e) {
            document.getElementById('gobius-ble-status').innerHTML =
                '<span style="color:#f44">❌ Error: ' + e.message + '</span>';
        } finally {
            this._gobiusLoading = false;
            if (panel) panel.classList.remove('busy');
            if (btn) { btn.disabled = false; btn.textContent = origText; }
        }
    },

    // ── Config writes ──

    async saveAllConfig(btnEl) {
        const btn = btnEl || document.getElementById('btn-save-all');
        await this.withButton(btn, 'Saving config', async () => {
            const n2kEnabled = document.getElementById('gob-n2k-enabled').checked;

            // 1. BLE Physical write: N2K enable toggle
            const r1 = await this.api('/api/gobius/n2k', 'POST', { enabled: n2kEnabled });

            // 2. BLE Physical write: Geometry (distance empty/full, filters)
            const uc = {
                distance_empty_mm: parseInt(document.getElementById('gob-dist-empty').value) || 300,
                distance_full_mm: parseInt(document.getElementById('gob-dist-full').value) || 50,
                lp_filter_n: parseInt(document.getElementById('gob-lp-n').value),
                lp_filter_k: parseInt(document.getElementById('gob-lp-k').value),
            };
            const r2 = await this.api('/api/gobius/user_config', 'POST', uc);

            // 3. NMEA 2000 PGN 126208 write (only if N2K is enabled)
            let r3 = { status: 'ok' };
            if (n2kEnabled) {
                const n2kCmd = {
                    instance: parseInt(document.getElementById('gob-n2k-instance').value) || 0,
                    fluid_type_code: parseInt(document.getElementById('gob-fluid-type').value) || 0,
                    capacity_l: parseInt(document.getElementById('gob-volume-l').value) || 10,
                };
                r3 = await this.api('/api/gobius/n2k_command', 'POST', n2kCmd);
            }

            const info1 = document.getElementById('gob-info1').value.trim();
            const info2 = document.getElementById('gob-info2').value.trim();
            if (info1 || info2) {
                await this.api('/api/gobius/info', 'POST', { info1, info2 });
                await this.api('/api/gobius/command', 'POST', { command: 'write_info' });
            }

            // Refresh to show updated config
            await this.loadGobius();

            const ok = r1.status === 'ok' && r2.status === 'ok' && r3.status === 'ok';
            return { status: ok ? 'ok' : 'error', message: ok ? 'All config saved' : 'Some writes failed' };
        });
    },

    async gobiusCmd(cmd, btnEl) {
        await this.withButton(btnEl, cmd, async () => {
            const r = await this.api('/api/gobius/command', 'POST', { command: cmd });
            return r;
        });
    },
});

