/**
 * URH Integration Module for RF SCYTHE
 * 
 * This module integrates Universal Radio Hacker functionality
 * into the RF SCYTHE command operations center, providing signal
 * analysis, protocol decoding, and device fuzzing capabilities.
 */

(function() {
    // Namespace for URH integration
    window.RF_SCYTHE = window.RF_SCYTHE || {};
    window.RF_SCYTHE.URH = window.RF_SCYTHE.URH || {};

    // URH connection state
    let urhConnected = false;
    let urhDevice = null;
    let capturedSignals = [];
    let analyzedProtocols = [];
    let fuzzingResults = [];

    /**
     * Initialize the URH integration
     */
    function initializeURH() {
        // Set up event listeners for URH interface
        const connectButton = document.getElementById('urh-connect-btn');
        if (connectButton) {
            connectButton.addEventListener('click', toggleURHConnection);
        }

        // Add event listeners for URH action buttons
        const analyzeBtn = document.getElementById('urh-analyze-btn');
        const decodeBtn = document.getElementById('urh-decode-btn');
        const sniffBtn = document.getElementById('urh-sniff-btn');
        const fuzzBtn = document.getElementById('urh-fuzz-btn');

        if (analyzeBtn) analyzeBtn.addEventListener('click', analyzeSignal);
        if (decodeBtn) decodeBtn.addEventListener('click', decodeProtocol);
        if (sniffBtn) sniffBtn.addEventListener('click', toggleSniffing);
        if (fuzzBtn) fuzzBtn.addEventListener('click', startFuzzing);
    }

    /**
     * Toggle URH connection state
     */
    function toggleURHConnection() {
        const statusIndicator = document.getElementById('urh-status-indicator');
        const statusText = document.getElementById('urh-status-text');
        const connectBtn = document.getElementById('urh-connect-btn');
        const actionButtons = document.querySelectorAll('.urh-actions button');
        
        if (!urhConnected) {
            // Attempt to connect to URH
            const deviceType = document.getElementById('urh-signal-source').value;
            
            // In the actual implementation, this would be a real connection attempt
            // For this demo, we'll just simulate it
            setTimeout(() => {
                urhConnected = true;
                urhDevice = deviceType;
                
                if (statusIndicator) statusIndicator.className = 'status-indicator status-active';
                if (statusText) statusText.textContent = 'CONNECTED';
                if (connectBtn) connectBtn.textContent = 'Disconnect';
                
                // Enable action buttons
                actionButtons.forEach(btn => {
                    btn.disabled = false;
                });
                
                // Log connection success
                console.log(`Connected to URH with device: ${deviceType}`);
                window.addConsoleMessage(`Connected to Universal Radio Hacker using ${deviceType}`, 'response');
                window.showNotification('URH Connected', `Successfully connected to Universal Radio Hacker with ${deviceType} device.`, 'info');
            }, 1500);
        } else {
            // Disconnect from URH
            urhConnected = false;
            urhDevice = null;
            
            if (statusIndicator) statusIndicator.className = 'status-indicator status-inactive';
            if (statusText) statusText.textContent = 'DISCONNECTED';
            if (connectBtn) connectBtn.textContent = 'Connect';
            
            // Disable action buttons
            actionButtons.forEach(btn => {
                btn.disabled = true;
            });
            
            window.addConsoleMessage('Disconnected from Universal Radio Hacker', 'response');
        }
    }

    /**
     * Analyze a signal using URH
     */
    function analyzeSignal() {
        if (!urhConnected) return;

        const frequency = document.getElementById('urh-frequency').value;
        const modulation = document.getElementById('urh-modulation').value;
        
        window.addConsoleMessage(`Analyzing signal at ${frequency} using ${modulation} demodulation...`, 'command');
        window.showNotification('Signal Analysis', `Analyzing signal at ${frequency} with ${modulation} demodulation.`, 'info');
        
        // Simulate signal capture and analysis
        setTimeout(() => {
            const signalId = `signal_${Date.now()}`;
            const signalStrength = -Math.floor(Math.random() * 30 + 40); // -40 to -70 dBm
            const bandwidth = Math.floor(Math.random() * 200 + 50); // 50-250 kHz
            
            // Add to captured signals
            capturedSignals.push({
                id: signalId,
                frequency: frequency,
                modulation: modulation,
                strength: signalStrength,
                bandwidth: bandwidth,
                samples: Math.floor(Math.random() * 1000000 + 100000),
                timestamp: new Date().toISOString()
            });
            
            // Update the signal list
            updateSignalList();
            
            window.addConsoleMessage(`Signal analysis complete. Signal captured at ${frequency}`, 'response');
        }, 3000);
    }

    /**
     * Decode a protocol from a signal
     */
    function decodeProtocol() {
        if (!urhConnected || capturedSignals.length === 0) return;
        
        window.addConsoleMessage('Decoding protocol from captured signal...', 'command');
        window.showNotification('Protocol Decoding', 'Attempting to decode protocol from selected signal.', 'info');
        
        // Simulate protocol decoding
        setTimeout(() => {
            const protocolId = `protocol_${Date.now()}`;
            const signal = capturedSignals[capturedSignals.length - 1];
            const bits = Math.floor(Math.random() * 100 + 20);
            
            // Create message patterns
            const messages = [];
            for (let i = 0; i < Math.floor(Math.random() * 5 + 2); i++) {
                let message = '';
                for (let j = 0; j < bits; j++) {
                    message += Math.random() > 0.5 ? '1' : '0';
                }
                messages.push(message);
            }
            
            // Add to analyzed protocols
            analyzedProtocols.push({
                id: protocolId,
                name: `Protocol from ${signal.frequency}`,
                bits: bits,
                messages: messages,
                timestamp: new Date().toISOString(),
                sourceSignal: signal.id
            });
            
            // Update the protocol list
            updateProtocolList();
            
            window.addConsoleMessage(`Protocol decoded: ${bits} bit messages found.`, 'response');
        }, 2500);
    }

    /**
     * Toggle sniffing mode
     */
    let sniffingActive = false;
    function toggleSniffing() {
        if (!urhConnected) return;
        
        const sniffBtn = document.getElementById('urh-sniff-btn');
        
        if (!sniffingActive) {
            const frequency = document.getElementById('urh-frequency').value;
            sniffingActive = true;
            
            if (sniffBtn) sniffBtn.textContent = 'Stop Sniffing';
            
            window.addConsoleMessage(`Started sniffing at ${frequency}...`, 'command');
            window.showNotification('Sniffing Active', `Continuously monitoring for signals at ${frequency}.`, 'info');
            
            // Simulate signal detection at random intervals
            sniffingInterval = setInterval(() => {
                if (Math.random() > 0.7) {
                    const signalId = `signal_${Date.now()}`;
                    const signalStrength = -Math.floor(Math.random() * 30 + 40);
                    const bandwidth = Math.floor(Math.random() * 200 + 50);
                    const exactFreq = parseFloat(frequency.replace(/[^\d.]/g, '')) + 
                                    (Math.random() * 0.2 - 0.1).toFixed(3);
                    
                    // Add to captured signals
                    capturedSignals.push({
                        id: signalId,
                        frequency: `${exactFreq}M`,
                        modulation: document.getElementById('urh-modulation').value,
                        strength: signalStrength,
                        bandwidth: bandwidth,
                        samples: Math.floor(Math.random() * 500000 + 50000),
                        timestamp: new Date().toISOString()
                    });
                    
                    // Update the signal list
                    updateSignalList();
                    
                    window.addConsoleMessage(`URH detected signal at ${exactFreq}MHz with ${signalStrength}dBm`, 'alert');
                }
            }, 5000);
        } else {
            sniffingActive = false;
            if (sniffBtn) sniffBtn.textContent = 'Start Sniffing';
            
            clearInterval(sniffingInterval);
            window.addConsoleMessage('Stopped sniffing', 'response');
        }
    }

    /**
     * Start fuzzing a protocol
     */
    function startFuzzing() {
        if (!urhConnected || analyzedProtocols.length === 0) return;
        
        const protocol = analyzedProtocols[analyzedProtocols.length - 1];
        
        window.addConsoleMessage(`Starting protocol fuzzing of ${protocol.name}...`, 'command');
        window.showNotification('Fuzzing Started', `Creating variations of protocol patterns and testing device responses.`, 'warning');
        
        // Simulate fuzzing results over time
        let progress = 0;
        const totalIterations = 100;
        
        // Create placeholder in fuzzing results panel
        const fuzzingResultsContainer = document.getElementById('urh-fuzzing-results');
        if (fuzzingResultsContainer) {
            fuzzingResultsContainer.innerHTML = `
                <div class="urh-progress-container">
                    <div class="urh-progress-label">Fuzzing Progress: 0%</div>
                    <div class="urh-progress-bar">
                        <div class="urh-progress-fill" style="width: 0%"></div>
                    </div>
                </div>
                <div class="urh-fuzzing-stats">Iterations: 0/${totalIterations} | Found: 0 responses</div>
            `;
        }
        
        const fuzzingInterval = setInterval(() => {
            progress += 5;
            
            // Update progress bar
            const progressFill = document.querySelector('.urh-progress-fill');
            const progressLabel = document.querySelector('.urh-progress-label');
            if (progressFill) progressFill.style.width = `${progress}%`;
            if (progressLabel) progressLabel.textContent = `Fuzzing Progress: ${progress}%`;
            
            // Update stats
            const currentIterations = Math.floor((progress / 100) * totalIterations);
            const foundResponses = Math.floor(currentIterations * 0.15); // About 15% success rate
            
            const stats = document.querySelector('.urh-fuzzing-stats');
            if (stats) stats.textContent = `Iterations: ${currentIterations}/${totalIterations} | Found: ${foundResponses} responses`;
            
            // Occasionally find an interesting response
            if (progress % 20 === 0) {
                const responseId = `response_${Date.now()}`;
                const variant = Math.floor(Math.random() * protocol.messages.length);
                const message = protocol.messages[variant];
                
                // Modify a random bit to create a variant
                const variantMessage = message.split('');
                const bitToChange = Math.floor(Math.random() * message.length);
                variantMessage[bitToChange] = (variantMessage[bitToChange] === '0') ? '1' : '0';
                
                // Add to fuzzing results
                fuzzingResults.push({
                    id: responseId,
                    message: variantMessage.join(''),
                    originMessage: message,
                    modifiedBit: bitToChange,
                    response: `Device ${Math.random() > 0.5 ? 'unlocked' : 'reset'} on iteration ${currentIterations}`,
                    timestamp: new Date().toISOString()
                });
                
                window.addConsoleMessage(`URH fuzzing found interesting response from device!`, 'alert');
            }
            
            // When complete, update the full results
            if (progress >= 100) {
                clearInterval(fuzzingInterval);
                updateFuzzingResults();
                window.addConsoleMessage(`URH fuzzing completed with ${foundResponses} device responses identified.`, 'response');
                window.showNotification('Fuzzing Complete', `Completed ${totalIterations} iterations with ${foundResponses} device responses.`, 'info');
            }
        }, 500);
    }

    /**
     * Update the signal list display
     */
    function updateSignalList() {
        const signalList = document.getElementById('urh-signal-list');
        if (!signalList) return;
        
        if (capturedSignals.length === 0) {
            signalList.innerHTML = '<div class="list-placeholder">No signals captured</div>';
            return;
        }
        
        signalList.innerHTML = '';
        capturedSignals.forEach(signal => {
            const signalEl = document.createElement('div');
            signalEl.className = 'urh-list-item';
            signalEl.dataset.id = signal.id;
            
            signalEl.innerHTML = `
                <div class="urh-list-item-header">
                    <span class="urh-list-item-title">${signal.frequency}</span>
                    <span class="urh-list-item-badge">${signal.modulation}</span>
                </div>
                <div class="urh-list-item-detail">
                    <span>Strength: ${signal.strength} dBm</span>
                    <span>BW: ${signal.bandwidth} kHz</span>
                </div>
            `;
            
            signalEl.addEventListener('click', () => {
                // Remove selected class from all signals
                document.querySelectorAll('.urh-list-item').forEach(el => {
                    el.classList.remove('selected');
                });
                
                // Add selected class to this signal
                signalEl.classList.add('selected');
                
                // Update signal details
                updateSignalDetails(signal);
            });
            
            signalList.appendChild(signalEl);
        });
        
        // Select the latest signal
        const latestSignal = signalList.lastChild;
        if (latestSignal) {
            latestSignal.classList.add('selected');
            updateSignalDetails(capturedSignals[capturedSignals.length - 1]);
        }
    }

    /**
     * Update signal details panel
     */
    function updateSignalDetails(signal) {
        const detailsPanel = document.getElementById('urh-signal-details');
        if (!detailsPanel) return;
        
        detailsPanel.innerHTML = `
            <div class="urh-detail-item">
                <span class="urh-detail-label">Frequency:</span>
                <span class="urh-detail-value">${signal.frequency}</span>
            </div>
            <div class="urh-detail-item">
                <span class="urh-detail-label">Modulation:</span>
                <span class="urh-detail-value">${signal.modulation}</span>
            </div>
            <div class="urh-detail-item">
                <span class="urh-detail-label">Signal Strength:</span>
                <span class="urh-detail-value">${signal.strength} dBm</span>
            </div>
            <div class="urh-detail-item">
                <span class="urh-detail-label">Bandwidth:</span>
                <span class="urh-detail-value">${signal.bandwidth} kHz</span>
            </div>
            <div class="urh-detail-item">
                <span class="urh-detail-label">Samples:</span>
                <span class="urh-detail-value">${signal.samples.toLocaleString()}</span>
            </div>
            <div class="urh-detail-item">
                <span class="urh-detail-label">Timestamp:</span>
                <span class="urh-detail-value">${new Date(signal.timestamp).toLocaleString()}</span>
            </div>
            <div class="urh-detail-actions">
                <button class="action-button">View Signal</button>
                <button class="action-button">Decode</button>
                <button class="action-button">Export</button>
            </div>
        `;
    }

    /**
     * Update the protocol list display
     */
    function updateProtocolList() {
        const protocolList = document.getElementById('urh-protocol-list');
        if (!protocolList) return;
        
        if (analyzedProtocols.length === 0) {
            protocolList.innerHTML = '<div class="list-placeholder">No protocols analyzed</div>';
            return;
        }
        
        protocolList.innerHTML = '';
        analyzedProtocols.forEach(protocol => {
            const protocolEl = document.createElement('div');
            protocolEl.className = 'urh-list-item';
            protocolEl.dataset.id = protocol.id;
            
            protocolEl.innerHTML = `
                <div class="urh-list-item-header">
                    <span class="urh-list-item-title">${protocol.name}</span>
                    <span class="urh-list-item-badge">${protocol.bits} bits</span>
                </div>
                <div class="urh-list-item-detail">
                    <span>Messages: ${protocol.messages.length}</span>
                </div>
            `;
            
            protocolEl.addEventListener('click', () => {
                // Remove selected class from all protocols
                document.querySelectorAll('.urh-list-item').forEach(el => {
                    el.classList.remove('selected');
                });
                
                // Add selected class to this protocol
                protocolEl.classList.add('selected');
                
                // Update protocol details
                updateProtocolDetails(protocol);
            });
            
            protocolList.appendChild(protocolEl);
        });
        
        // Select the latest protocol
        const latestProtocol = protocolList.lastChild;
        if (latestProtocol) {
            latestProtocol.classList.add('selected');
            updateProtocolDetails(analyzedProtocols[analyzedProtocols.length - 1]);
        }
    }

    /**
     * Update protocol details panel
     */
    function updateProtocolDetails(protocol) {
        const detailsPanel = document.getElementById('urh-protocol-details');
        if (!detailsPanel) return;
        
        let messagesHtml = '';
        protocol.messages.forEach((message, idx) => {
            messagesHtml += `
                <div class="urh-message">
                    <div class="urh-message-header">Message ${idx + 1}</div>
                    <div class="urh-message-bits">${message}</div>
                </div>
            `;
        });
        
        detailsPanel.innerHTML = `
            <div class="urh-detail-item">
                <span class="urh-detail-label">Protocol:</span>
                <span class="urh-detail-value">${protocol.name}</span>
            </div>
            <div class="urh-detail-item">
                <span class="urh-detail-label">Message Length:</span>
                <span class="urh-detail-value">${protocol.bits} bits</span>
            </div>
            <div class="urh-detail-item">
                <span class="urh-detail-label">Message Count:</span>
                <span class="urh-detail-value">${protocol.messages.length}</span>
            </div>
            <div class="urh-messages-container">
                ${messagesHtml}
            </div>
            <div class="urh-detail-actions">
                <button class="action-button">Edit Protocol</button>
                <button class="action-button">Generate C Code</button>
                <button class="action-button">Start Fuzzing</button>
            </div>
        `;
    }

    /**
     * Update fuzzing results panel
     */
    function updateFuzzingResults() {
        const fuzzingResultsContainer = document.getElementById('urh-fuzzing-results');
        if (!fuzzingResultsContainer || fuzzingResults.length === 0) return;
        
        let resultsHtml = '';
        fuzzingResults.forEach(result => {
            const messageSegments = [];
            
            for (let i = 0; i < result.message.length; i++) {
                if (i === result.modifiedBit) {
                    messageSegments.push(`<span class="urh-modified-bit">${result.message[i]}</span>`);
                } else {
                    messageSegments.push(result.message[i]);
                }
            }
            
            resultsHtml += `
                <div class="urh-fuzzing-result">
                    <div class="urh-fuzzing-result-header">
                        <span>Variant ${fuzzingResults.indexOf(result) + 1}</span>
                        <span class="urh-fuzzing-timestamp">${new Date(result.timestamp).toLocaleTimeString()}</span>
                    </div>
                    <div class="urh-fuzzing-message">${messageSegments.join('')}</div>
                    <div class="urh-fuzzing-response">${result.response}</div>
                </div>
            `;
        });
        
        fuzzingResultsContainer.innerHTML = `
            <div class="urh-fuzzing-summary">
                <div>Total Iterations: 100</div>
                <div>Successful Responses: ${fuzzingResults.length}</div>
            </div>
            <div class="urh-fuzzing-results-list">
                ${resultsHtml}
            </div>
            <div class="urh-detail-actions">
                <button class="action-button">Export Results</button>
                <button class="action-button">Generate Attack</button>
            </div>
        `;
    }

    // Public API
    RF_SCYTHE.URH = {
        initialize: initializeURH,
        connect: toggleURHConnection,
        analyzeSignal: analyzeSignal,
        decodeProtocol: decodeProtocol,
        startSniffing: toggleSniffing,
        startFuzzing: startFuzzing
    };

    // Initialize when DOM is loaded
    document.addEventListener('DOMContentLoaded', initializeURH);
})();
