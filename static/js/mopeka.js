/**
 * YDNU-02 Web Console — Mopeka BLE Tab
 *
 * Dynamically renders one card per registered Mopeka sensor.
 * Each card shows live BLE data + editable config.
 * Uses shared helpers from core.js: renderInfoTable, escAttr, startPolling/stopPolling, bleRemoveSensor.
 */
Object.assign(App, {
    _mopekaTimer: null,

    async loadMopekaSensors() {
        try {
            const data = await this.api('/api/mopeka/sensors');
            const sensors = data.sensors || [];
            const noEl = document.getElementById('mopeka-no-sensors');
            const container = document.getElementById('mopeka-sensors-container');
            if (!container) return;

            if (!sensors.length) {
                noEl.style.display = '';
                noEl.textContent = '⚠️ No Mopeka sensors registered. Press ➕ Add Sensor to scan.';
                container.innerHTML = '';
                return;
            }
            noEl.style.display = 'none';

            sensors.forEach(s => {
                const safeId = s.mac_address.replace(/:/g, '');
                let wrap = document.getElementById('mop-card-' + safeId);
                if (!wrap) {
                    wrap = document.createElement('div');
                    wrap.id = 'mop-card-' + safeId;
                    wrap.className = 'mopeka-sensor-wrap';
                    container.appendChild(wrap);
                }
                this._renderMopekaSensorCard(wrap, s);
            });

            const activeMacs = new Set(sensors.map(s => 'mop-card-' + s.mac_address.replace(/:/g, '')));
            container.querySelectorAll('.mopeka-sensor-wrap').forEach(el => {
                if (!activeMacs.has(el.id)) el.remove();
            });
        } catch (e) {
            console.error('loadMopekaSensors:', e);
        }
    },

    _renderMopekaSensorCard(wrap, s) {
        const mac = s.mac_address;
        const stars = '★'.repeat(s.quality_stars || 0) + '☆'.repeat(3 - (s.quality_stars || 0));
        const age = s.age_sec != null ? s.age_sec.toFixed(0) + 's ago' : '--';
        const online = s.source !== 'OFFLINE';
        const badgeCls = online ? 'badge-on' : 'badge-off';

        // Preserve user-edited config inputs across re-renders
        const nameInput = wrap.querySelector('.mop-cfg-name');
        const isFirstRender = !nameInput;
        const prevName = nameInput ? nameInput.value : (s.name || '');
        const prevDepth = wrap.querySelector('.mop-cfg-depth')?.value ?? (s.tank_depth_mm || '');
        const prevCap = wrap.querySelector('.mop-cfg-cap')?.value ?? (s.capacity_l || '');
        const prevFluid = wrap.querySelector('.mop-cfg-fluid')?.value ?? (s.fluid_type || 'Fresh Water');
        const curName = isFirstRender ? (s.name || '') : prevName;
        const curDepth = isFirstRender ? (s.tank_depth_mm || '') : prevDepth;
        const curCap = isFirstRender ? (s.capacity_l || '') : prevCap;
        const curFluid = isFirstRender ? (s.fluid_type || 'Fresh Water') : prevFluid;

        const fluidOptions = ['Fresh Water', 'Fuel', 'Waste Water', 'Black Water', 'Oil', 'LPG'];
        const fluidSelect = fluidOptions.map(f =>
            `<option value="${f}" ${curFluid === f ? 'selected' : ''}>${f}</option>`
        ).join('');

        wrap.innerHTML = `
            <div class="grid-2">
                <div class="card">
                    <div class="card-head">
                        <h2>💧 ${s.name || 'Mopeka'}</h2>
                        <div>
                            <span class="badge ${badgeCls}">${age}</span>
                            <button class="btn sm" onclick="App.bleRemoveSensor('${mac}')" title="Remove">🗑</button>
                        </div>
                    </div>
                    ${this.renderInfoTable([
                        ['Sensor ID', mac],
                        ['Sensor type', s.sensor_type || '--'],
                        ['Fill level', s.fill_level_pct != null ? s.fill_level_pct + '%' : '--'],
                        ['Volume', s.calculated_l != null ? s.calculated_l + ' L' : '--'],
                        ['Distance (air gap)', s.distance_mm != null ? s.distance_mm.toFixed(1) + ' mm' : '--'],
                        ['Temperature', s.temp_c != null ? s.temp_c + '°C' : '--'],
                        ['Supply voltage', s.voltage_v != null ? s.voltage_v + ' V' : '--'],
                        ['Battery', s.battery_pct != null ? s.battery_pct.toFixed(0) + '%' : '--'],
                        ['Signal quality', stars + ' (' + (s.quality_label || '--') + ')'],
                        ['RSSI', s.rssi != null ? s.rssi + ' dBm' : '--'],
                    ])}
                </div>
                <div class="card">
                    <div class="card-head"><h2>⚙️ Tank Configuration</h2></div>
                    <table class="info-table">
                        <tr><td>Name</td><td><input type="text" class="input mop-cfg-name" data-mac="${mac}" value="${this.escAttr(curName)}" maxlength="30"></td></tr>
                        <tr><td>Tank Depth (mm)</td><td><input type="number" class="input mop-cfg-depth" data-mac="${mac}" value="${curDepth}" min="10" max="5000"></td></tr>
                        <tr><td>Capacity (L)</td><td><input type="number" class="input mop-cfg-cap" data-mac="${mac}" value="${curCap}" min="1" max="10000"></td></tr>
                        <tr><td>Fluid Type</td><td><select class="input mop-cfg-fluid" data-mac="${mac}">${fluidSelect}</select></td></tr>
                    </table>
                    <div class="spacer"></div>
                    <button class="btn accent" onclick="App.saveMopekaConfigFor('${mac}', this)" style="width:100%">💾 Save Configuration</button>
                </div>
            </div>`;
    },

    async saveMopekaConfigFor(mac, btnEl) {
        const wrap = document.getElementById('mop-card-' + mac.replace(/:/g, ''));
        if (!wrap) return;
        await this.withButton(btnEl, 'Saving', async () => {
            const body = {
                name: wrap.querySelector('.mop-cfg-name').value,
                tank_depth_mm: parseFloat(wrap.querySelector('.mop-cfg-depth').value) || 0,
                capacity_l: parseFloat(wrap.querySelector('.mop-cfg-cap').value) || 0,
                fluid_type: wrap.querySelector('.mop-cfg-fluid').value,
            };
            await this.api('/api/mopeka/config/' + mac, 'POST', body);
            return { message: 'Saved' };
        });
    },

    startMopekaPolling() {
        this.startPolling('_mopekaTimer', this.loadMopekaSensors, 5000);
    },

    stopMopekaPolling() {
        this.stopPolling('_mopekaTimer');
    }
});
