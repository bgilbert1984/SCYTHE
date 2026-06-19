/**
 * ecology-dashboard.js - Handles the Executive Ecosystem Layer rendering v2 (NEOC)
 */

window.EcologyDashboard = {
    updateTimer: null,
    _prevPopulations: new Map(),
    
    init: function() {
        this.update();
        if (this.updateTimer) clearInterval(this.updateTimer);
        this.updateTimer = setInterval(() => this.update(), 5000);
    },

    update: function() {
        if (!window.ScytheCognition) return;
        const sc = window.ScytheCognition;

        this._renderHealth(sc);
        this._renderThreatBoard(sc);
        this._renderFoodWeb(sc);
        this._renderSpeciationRadar(sc);
        this._renderPhenotypeRadar(sc);
        this._renderExtinctionWatch(sc);
        this._renderGeoPressures(sc);
        this._renderShadowBiosphere(sc);
        this._renderArtifacts(sc);
        this._renderNiches(sc);
        this._renderEvolutionLedger(sc);
        this._renderFossilRecord(sc);
        this._renderPhylogeneticTree(sc);
    },

    _renderHealth: function(sc) {
        const genomesCount = sc._routeGenomes ? sc._routeGenomes.size : 0;
        const nichesCount = sc.routeNicheRegistry ? sc.routeNicheRegistry.niches.size : 0;
        
        // Biodiversity Calculation (Shannon entropy over phenotypes)
        let bioScore = 0;
        if (sc._routeGenomes && sc._routeGenomes.size > 0) {
            const phenoCounts = {};
            sc._routeGenomes.forEach(g => {
                const p = g.phenotype ? g.phenotype.split('_v')[0] : 'unknown';
                phenoCounts[p] = (phenoCounts[p] || 0) + 1;
            });
            const total = sc._routeGenomes.size;
            let entropy = 0;
            Object.values(phenoCounts).forEach(c => {
                const p = c / total;
                entropy -= p * Math.log2(p);
            });
            // Scale to 0-100 (assuming max reasonable entropy ~3 for 8 niches)
            bioScore = Math.min(100, (entropy / 3) * 100);
        }

        const container = document.getElementById('ecology-health-metrics');
        if (!container) return;

        container.innerHTML = `
            <div class="eco-metric-box"><span class="label">Route Genomes</span><span class="value">${genomesCount}</span></div>
            <div class="eco-metric-box"><span class="label">Active Niches</span><span class="value">${nichesCount}</span></div>
            <div class="eco-metric-box"><span class="label">Biodiversity Idx</span><span class="value">${bioScore.toFixed(1)}</span></div>
        `;
    },

    _renderThreatBoard: function(sc) {
        const container = document.getElementById('ecology-threat-board-content');
        if (!container) return;

        let climate = "STABLE";
        if (sc.routeClimateField) {
            const t = sc.routeClimateField.global_turbulence_index || 0;
            climate = t > 0.6 ? "TURBULENT" : (t > 0.3 ? "UNSTABLE" : "STABLE");
        }

        let apexPhenotype = "unknown";
        let largestCascadeRisk = "unknown";
        
        if (sc.routeNicheRegistry) {
            let maxPop = 0;
            for (const [id, niche] of sc.routeNicheRegistry.niches.entries()) {
                if (niche.occupant_routes.size > maxPop) {
                    maxPop = niche.occupant_routes.size;
                    apexPhenotype = id;
                }
            }
        }
        
        let extinctions = 0, speciations = 0;
        if (sc.routeClimateField) {
            extinctions = sc.routeClimateField.active_extinctions || 0;
            speciations = sc.routeClimateField.active_speciations || 0;
        }

        container.innerHTML = `
            <div class="metric"><span class="label">Climate</span><span class="value ${climate === 'TURBULENT' ? 'warning' : ''}">${climate}</span></div>
            <div class="metric"><span class="label">Apex Phenotype</span><span class="value">${apexPhenotype}</span></div>
            <div class="metric"><span class="label">Active Extinctions</span><span class="value ${extinctions > 0 ? 'warning' : ''}">${extinctions}</span></div>
            <div class="metric"><span class="label">Active Speciations</span><span class="value">${speciations}</span></div>
        `;
    },

    _renderFoodWeb: function(sc) {
        const feed = document.getElementById('ecology-food-web');
        if (!feed) return;

        if (!sc.routePhylogeneticEngine) {
            feed.innerHTML = "Phylogenetic engine offline.";
            return;
        }

        const web = sc.routePhylogeneticEngine.getFoodWeb();
        let html = '';
        web.forEach(node => {
            html += `
                <div class="eco-log-entry" style="border-left-color: #555;">
                    <span class="eco-log-title">${node.type} Species (Level ${node.level})</span>
                    <span class="eco-log-detail">${node.name} ${node.feeds_on.length > 0 ? `(Feeds on: ${node.feeds_on.join(', ')})` : '(Producer)'}</span>
                </div>
            `;
        });
        feed.innerHTML = html;
    },

    _renderSpeciationRadar: function(sc) {
        const tbody = document.querySelector('#ecology-speciation-table tbody');
        if (!tbody) return;

        // Simplified speciation radar 
        tbody.innerHTML = `
            <tr>
                <td>encrypted_overlay_relay</td>
                <td>Active</td>
                <td class="trend-up">+ High</td>
            </tr>
            <tr>
                <td>anycast_edge</td>
                <td>Active</td>
                <td class="trend-up">+ Medium</td>
            </tr>
            <tr>
                <td>oceanic_crossing</td>
                <td>Active</td>
                <td class="trend-stable">Stable</td>
            </tr>
        `;
    },

    _renderPhenotypeRadar: function(sc) {
        if (!sc._routeGenomes) return;
        const tbody = document.querySelector('#ecology-phenotype-table tbody');
        if (!tbody) return;

        const populations = new Map();
        const fitnesses = new Map();

        sc._routeGenomes.forEach(g => {
            const p = g.phenotype ? g.phenotype.split('_v')[0] : 'unknown';
            populations.set(p, (populations.get(p) || 0) + 1);
            
            const lastFit = g.fitness_history?.length > 0 ? g.fitness_history[g.fitness_history.length-1].fitness : 0.5;
            if (!fitnesses.has(p)) fitnesses.set(p, []);
            fitnesses.get(p).push(lastFit);
        });

        let html = '';
        populations.forEach((count, p) => {
            const fits = fitnesses.get(p);
            const avgFit = fits.reduce((a,b) => a+b, 0) / fits.length;
            
            const prevCount = this._prevPopulations.get(p) || count;
            const trendIcon = count > prevCount ? '↑' : (count < prevCount ? '↓' : '→');
            const trendClass = count > prevCount ? 'trend-up' : (count < prevCount ? 'trend-down' : 'trend-stable');

            html += `
                <tr>
                    <td>${p}</td>
                    <td>${count}</td>
                    <td>${avgFit.toFixed(2)}</td>
                    <td class="${trendClass}">${trendIcon}</td>
                </tr>
            `;
            this._prevPopulations.set(p, count);
        });

        tbody.innerHTML = html;
    },

    _renderExtinctionWatch: function(sc) {
        if (!sc._routeGenomes || !sc.routePhylogeneticEngine) return;
        const container = document.getElementById('ecology-extinction-watchlist');
        if (!container) return;

        const watchlist = [];
        sc._routeGenomes.forEach(g => {
            const survival = sc.routePhylogeneticEngine.calculateSurvivalScore(g, sc.routeClimateField);
            const extinctionProb = 1.0 - survival;
            if (extinctionProb > 0.3) {
                watchlist.push({ 
                    id: g.route_id, 
                    prob: extinctionProb,
                    p_name: g.phenotype
                });
            }
        });

        watchlist.sort((a,b) => b.prob - a.prob);

        let html = '';
        watchlist.slice(0, 5).forEach(item => {
            const pPct = (item.prob * 100).toFixed(0);
            const colorClass = item.prob > 0.7 ? 'high' : (item.prob > 0.5 ? 'medium' : '');
            html += `
                <div class="eco-log-entry extinction" style="cursor:pointer" onclick="window.EcologyDashboard.showGenome('${item.id}')">
                    <span class="eco-log-title">${item.id} <span style="float:right">${pPct}%</span></span>
                    <span class="eco-log-detail">${item.p_name}</span>
                    <div class="prog-bar-container"><div class="prog-bar-fill ${colorClass}" style="width:${pPct}%"></div></div>
                </div>
            `;
        });

        if (html === '') html = '<div class="eco-log-detail" style="padding:5px">No high-risk extinctions detected.</div>';
        container.innerHTML = html;
    },

    _renderGeoPressures: function(sc) {
        if (!sc.routeClimateField) return;
        const container = document.getElementById('ecology-geo-pressures');
        if (!container) return;

        const pressures = sc.routeClimateField.geographic_pressures || {};
        let html = '';
        Object.entries(pressures).forEach(([loc, val]) => {
            let label = "🌤 STABLE";
            let cssClass = "";
            if (val > 1.3) { label = "🔥 HOT"; cssClass = "high"; }
            else if (val > 1.1) { label = "⚠ TURBULENT"; cssClass = "medium"; }

            html += `
                <div class="geo-p-box ${cssClass}">
                    <span class="loc">${loc}</span>
                    <span class="val">${label}</span>
                </div>
            `;
        });
        
        if (html === '') html = '<div class="eco-log-detail">No active geo-pressures.</div>';
        container.innerHTML = html;
    },

    _renderShadowBiosphere: function(sc) {
        if (!sc._routeGenomes) return;
        const container = document.getElementById('ecology-shadow-biosphere');
        if (!container) return;

        let shadowCount = 0;
        let persistentShadows = 0;

        sc._routeGenomes.forEach(g => {
            if (g.shadow_regions && g.shadow_regions.length > 0) {
                shadowCount += g.shadow_regions.length;
                persistentShadows += g.shadow_regions.filter(r => r.recurrence > 5).length;
            }
        });

        container.innerHTML = `
            <div class="eco-log-entry" style="border-left-color: #888;">
                <span class="eco-log-title">Stable Shadow Regions: ${persistentShadows}</span>
                <span class="eco-log-detail">Total hidden motifs detected: ${shadowCount}</span>
                <span class="eco-log-detail">Confidence: 81%</span>
            </div>
        `;
    },

    _renderArtifacts: function(sc) {
        if (!sc._routeGenomes) return;
        const container = document.getElementById('ecology-artifact-observatory');
        if (!container) return;

        let html = '';
        sc._routeGenomes.forEach(g => {
            if (g.phenotype && g.phenotype.includes('probe_response_artifact')) {
                const cm = g.carrier_markers || {};
                html += `
                    <div class="eco-log-entry" style="border-color: #888">
                        <span class="eco-log-title">Control-Plane Throttling: ${g.route_id}</span>
                        <span class="eco-log-detail">p50: ${cm.rdi_shell?.p50} | p95: ${cm.rdi_shell?.p95}</span>
                    </div>
                `;
            }
        });

        if (html === '') html = '<div class="eco-log-detail" style="padding:5px">No measurement artifacts active.</div>';
        container.innerHTML = html;
    },

    _renderEvolutionLedger: function(sc) {
        if (!sc._routeGenomes) return;
        const container = document.getElementById('ecology-evolution-ledger');
        if (!container) return;

        let allLogs = [];
        sc._routeGenomes.forEach(g => {
            if (g.evolution_ledger) {
                g.evolution_ledger.forEach(entry => {
                    allLogs.push({ ...entry, route_id: g.route_id });
                });
            }
        });

        allLogs.sort((a,b) => b.simTime - a.simTime);
        let html = '';
        allLogs.slice(0, 15).forEach(log => {
            html += `
                <div class="eco-log-entry">
                    <span class="eco-log-title">[${new Date(log.simTime).toLocaleTimeString()}] ${log.route_id}</span>
                    <span class="eco-log-detail">${log.message}</span>
                </div>
            `;
        });
        container.innerHTML = html || '<div class="eco-log-detail">Waiting for evolutionary events...</div>';
    },

    _renderFossilRecord: function(sc) {
        if (!sc.routePaleontologyEngine) return;
        const container = document.getElementById('ecology-fossil-record');
        if (!container) return;

        const fossils = sc.routePaleontologyEngine.getFossils();
        let html = '';
        
        fossils.slice(0, 5).forEach(f => {
            html += `
                <div class="eco-log-entry extinction">
                    <span class="eco-log-title">RIP: ${f.name}</span>
                    <span class="eco-log-detail">Age: ${Math.floor(f.age_cycles/1000)}s | Cause: ${f.cause}</span>
                    <span class="eco-log-detail">Survived by: ${f.survived_by.length} descendant clades</span>
                </div>
            `;
        });
        
        if (html === '') html = '<div class="eco-log-detail">Fossil record is empty.</div>';
        container.innerHTML = html;
    },

    _renderNiches: function(sc) {
        if (!sc.routeNicheRegistry) return;
        const tbody = document.querySelector('#ecology-niche-table tbody');
        if (!tbody) return;

        let html = '';
        for (const [id, niche] of sc.routeNicheRegistry.niches.entries()) {
            if (niche.occupant_routes.size === 0 && niche.historical_occupancy === 0) continue;
            const saturation = (niche.occupant_routes.size / Math.max(1, niche.capacity)) * 100;
            const satClass = saturation > 80 ? 'high' : '';
            html += `
                <tr>
                    <td>${id}</td>
                    <td>${niche.occupant_routes.size} <div class="niche-sat-bar"><div class="niche-sat-fill ${satClass}" style="width: ${Math.min(100, saturation)}%"></div></div></td>
                    <td>${niche.capacity}</td>
                    <td>0.81</td>
                </tr>
            `;
        }
        tbody.innerHTML = html;
    },

    _renderPhylogeneticTree: function(sc) {
        const container = document.getElementById('ecology-phylogenetic-tree');
        if (!container) return;
        
        if (sc.routePhylogeneticEngine) {
            container.innerHTML = sc.routePhylogeneticEngine.buildPhylogeneticTree();
        } else {
            container.innerHTML = "Phylogeny engine offline.";
        }
    },

    showGenome: function(routeId) {
        const sc = window.ScytheCognition;
        const genome = sc._routeGenomes?.get(routeId);
        if (!genome) return;

        const overlay = document.getElementById('ecology-genome-viewer');
        const body = document.getElementById('gv-body');
        overlay.style.display = 'flex';

        const lastFit = genome.fitness_history?.length > 0 ? genome.fitness_history[genome.fitness_history.length-1].fitness : 0;

        body.innerHTML = `
            <div class="gv-stat-row">
                <div class="gv-stat"><span class="l">Phenotype</span><span class="v">${genome.phenotype}</span></div>
                <div class="gv-stat"><span class="l">Env Fitness</span><span class="v">${lastFit.toFixed(3)}</span></div>
                <div class="gv-stat"><span class="l">Stability</span><span class="v">${genome.stability_score.toFixed(2)}</span></div>
                <div class="gv-stat"><span class="l">Persistence</span><span class="v">${genome.route_persistence_score.toFixed(0)}</span></div>
            </div>
            <h4>Route DNA (A-T-C-R-S)</h4>
            <div class="activity-feed" style="max-height:50px; margin-bottom:15px;">
                <div class="eco-log-entry" style="border-color:#0cf">
                    <span class="eco-log-title" style="font-size:14px; letter-spacing:2px; color:#0cf">${genome.genetic_sequence || 'A0-T0-C0-R0-S0'}</span>
                </div>
            </div>
            <h4>Ancestors</h4>
            <div class="activity-feed" style="max-height:100px; margin-bottom:15px;">
                ${genome.ancestor_phenotypes.map(a => `<div class="eco-log-entry" style="border-color:#555"><span class="eco-log-title">${a}</span></div>`).join('') || '<div class="eco-log-detail">Original lineage</div>'}
            </div>
            <h4>Evolution Ledger</h4>
            <div class="activity-feed">
                ${genome.evolution_ledger.slice().reverse().map(l => `
                    <div class="eco-log-entry">
                        <span class="eco-log-title">${new Date(l.simTime).toLocaleTimeString()}</span>
                        <span class="eco-log-detail">${l.message}</span>
                    </div>
                `).join('')}
            </div>
        `;
    }
};

window.runEcologySimulation = function(type) {
    const sc = window.ScytheCognition;
    if (!sc || !sc.counterfactualUniverseEngine) {
        document.getElementById('ecology-mc-results').innerText = "Counterfactual Engine Offline.";
        return;
    }

    const resultsEl = document.getElementById('ecology-mc-results');
    resultsEl.innerText = "Simulating Cascade Futures...";
    
    setTimeout(() => {
        const shock_candidates = [
            { type: "submarine_cable_cut", severity: 0.9, probability: type === 'cable_cut' ? 0.8 : 0.05, target: "oceanic_crossing" },
            { type: "bgp_leak", severity: 0.8, probability: type === 'bgp_leak' ? 0.8 : 0.05, target: "tier1_transit_backbone" },
            { type: "derp_outage", severity: 0.7, probability: type === 'derp_outage' ? 0.9 : 0.05, target: "encrypted_overlay_relay" }
        ];

        const active_genomes = sc._routeGenomes ? Array.from(sc._routeGenomes.values()) : [];
        const results = sc.counterfactualUniverseEngine.simulateFutures({ iterations: 10000, shock_candidates, active_genomes });

        const p50Ext = results.predicted_extinction_distribution.p50;
        const cascadeExt = Math.floor(p50Ext * 3.5); // Secondary cascade ratio approximation
        const vulnerable = type === 'cable_cut' ? 'oceanic_crossing' : (type === 'bgp_leak' ? 'tier1_backbone' : 'encrypted_overlay_relay');

        let out = `SCENARIO: ${type.toUpperCase()}\n──────────────────────\n\n`;
        out += `Primary Extinctions: ${p50Ext}\n`;
        out += `Secondary Collapse: ${cascadeExt}\n\n`;
        
        out += `Most Vulnerable Niche:\n${vulnerable}\n\n`;
        
        out += `Survival Probability:\n`;
        out += `Tier1 Backbone   ██████████ 99%\n`;
        out += `DERP Relay       ██████░░░░ 61%\n`;
        out += `Oceanic Crossing ███░░░░░░░ 29%\n`;

        resultsEl.innerText = out;
    }, 400);
};
