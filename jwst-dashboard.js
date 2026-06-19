/**
 * RF SCYTHE - JWST Dashboard
 * Interactive dashboard for JWST data integration with RF analysis
 */

// Import Three.js modules
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// Global variables
let spectralPlot = null;
let correlationPlot = null;
let jwstScene, jwstCamera, jwstRenderer, jwstControls;
let jwstModel;
let animationPaused = false;

// Initialize when document is ready
document.addEventListener('DOMContentLoaded', function() {
    // Initialize the dashboard
    initializeDashboard();

    // Update system time
    setInterval(updateSystemTime, 1000);

    // Set up event listeners
    setupEventListeners();
});

/**
 * Initialize the dashboard components
 */
function initializeDashboard() {
    // Initialize plots
    initializeSpectralPlot();
    initializeCorrelationPlot();

    // Initialize 3D model viewer
    initializeJWSTModel();

    // Initialize WebSocket connection if available
    initializeWebSocketConnection();

    // Populate file list
    populateFileList();

    // Update status indicators
    updateStatusIndicators();
}

/**
 * Initialize the spectral data plot
 */
function initializeSpectralPlot() {
    const spectralData = JWST_Integration.getSpectralData();

    const trace = {
        x: spectralData.wavelengths,
        y: spectralData.intensities,
        type: 'scatter',
        mode: 'lines',
        name: 'Spectral Intensity',
        line: {
            color: '#00a8ff',
            width: 2
        }
    };

    const layout = {
        title: 'JWST NIRISS Spectral Data',
        font: {
            family: 'Titillium Web, sans-serif',
            color: '#e6e6e6'
        },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        xaxis: {
            title: 'Wavelength (μm)',
            color: '#e6e6e6',
            gridcolor: 'rgba(255,255,255,0.1)'
        },
        yaxis: {
            title: 'Normalized Intensity',
            color: '#e6e6e6',
            gridcolor: 'rgba(255,255,255,0.1)'
        },
        margin: { l: 50, r: 20, b: 40, t: 30 },
        showlegend: false,
        hovermode: 'closest'
    };

    const config = {
        responsive: true,
        displayModeBar: false
    };

    spectralPlot = Plotly.newPlot('spectral-plot', [trace], layout, config);
}

/**
 * Initialize the RF correlation plot
 */
function initializeCorrelationPlot() {
    const spectralData = JWST_Integration.getSpectralData();

    const trace1 = {
        x: spectralData.wavelengths,
        y: spectralData.intensities,
        type: 'scatter',
        mode: 'lines',
        name: 'Spectral Intensity',
        line: { color: '#00a8ff' },
        yaxis: 'y'
    };

    const trace2 = {
        x: spectralData.wavelengths,
        y: spectralData.rfCorrelations,
        type: 'scatter',
        mode: 'lines',
        name: 'RF Correlation',
        line: { color: '#ff5500' },
        yaxis: 'y2'
    };

    const layout = {
        title: 'Spectral-RF Correlation Analysis',
        font: {
            family: 'Titillium Web, sans-serif',
            color: '#e6e6e6'
        },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        xaxis: {
            title: 'Wavelength (μm)',
            color: '#e6e6e6',
            gridcolor: 'rgba(255,255,255,0.1)'
        },
        yaxis: {
            title: 'Intensity',
            color: '#00a8ff',
            gridcolor: 'rgba(255,255,255,0.1)'
        },
        yaxis2: {
            title: 'RF Correlation',
            color: '#ff5500',
            overlaying: 'y',
            side: 'right',
            gridcolor: 'rgba(255,255,255,0)'
        },
        margin: { l: 50, r: 50, b: 40, t: 30 },
        legend: {
            x: 0,
            y: 1,
            font: { color: '#e6e6e6' },
            bgcolor: 'rgba(0,0,0,0.3)'
        },
        hovermode: 'closest'
    };

    const config = {
        responsive: true,
        displayModeBar: false
    };

    correlationPlot = Plotly.newPlot('rf-correlation-plot', [trace1, trace2], layout, config);
}

/**
 * Initialize the 3D JWST model
 */
function initializeJWSTModel() {
    // Create scene
    jwstScene = new THREE.Scene();
    jwstScene.background = new THREE.Color(0x05070a);

    // Create camera
    jwstCamera = new THREE.PerspectiveCamera(60, 1, 0.1, 1000);
    jwstCamera.position.set(0, 30, 50);
    jwstCamera.lookAt(0, 0, 0);

    // Create renderer
    jwstRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    const container = document.getElementById('jwst-model-container');
    jwstRenderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(jwstRenderer.domElement);

    // Add lights
    const ambientLight = new THREE.AmbientLight(0x222233);
    jwstScene.add(ambientLight);

    const directionalLight = new THREE.DirectionalLight(0xffd700, 0.8);
    directionalLight.position.set(1, 1, 1);
    jwstScene.add(directionalLight);

    // Add orbit controls
    jwstControls = new OrbitControls(jwstCamera, jwstRenderer.domElement);
    jwstControls.enableDamping = true;
    jwstControls.dampingFactor = 0.05;
    jwstControls.rotateSpeed = 0.5;

    // Create and add JWST model
    createJWSTModel();

    // Start animation loop
    animateJWSTModel();

    // Handle window resize
    window.addEventListener('resize', () => {
        if (container) {
            jwstRenderer.setSize(container.clientWidth, container.clientHeight);
            jwstCamera.aspect = container.clientWidth / container.clientHeight;
            jwstCamera.updateProjectionMatrix();
        }
    });
}

/**
 * Create the JWST model
 */
function createJWSTModel() {
    // Create a group to hold all JWST components
    jwstModel = new THREE.Group();

    // Main bus - hexagonal primary mirror
    const mainMirrorGeometry = new THREE.CylinderGeometry(15, 15, 1, 6);
    const mainMirrorMaterial = new THREE.MeshPhongMaterial({
        color: 0xffd700,
        emissive: 0x665500,
        shininess: 100,
        specular: 0xffffaa,
        side: THREE.DoubleSide
    });
    const mainMirror = new THREE.Mesh(mainMirrorGeometry, mainMirrorMaterial);
    mainMirror.rotation.x = Math.PI / 2;
    jwstModel.add(mainMirror);

    // Add 18 individual mirror segments
    for (let i = 0; i < 18; i++) {
        const segmentGeometry = new THREE.CylinderGeometry(2.5, 2.5, 0.5, 6);
        const segment = new THREE.Mesh(segmentGeometry, mainMirrorMaterial.clone());

        // Arrange in honeycomb pattern
        const radius = 8;
        const angle = (i % 6) * (Math.PI / 3);
        const ring = Math.floor(i / 6);
        const x = radius * Math.cos(angle) * (ring === 0 ? 0.5 : 1);
        const z = radius * Math.sin(angle) * (ring === 0 ? 0.5 : 1);
        const y = 0.5; // Slightly above the main mirror

        segment.position.set(x, y, z);
        segment.rotation.x = Math.PI / 2;
        jwstModel.add(segment);
    }

    // Secondary mirror support
    const supportGeometry = new THREE.CylinderGeometry(0.5, 0.5, 20, 8);
    const supportMaterial = new THREE.MeshPhongMaterial({
        color: 0xaaaaaa,
        emissive: 0x222222,
        shininess: 80,
    });
    const support = new THREE.Mesh(supportGeometry, supportMaterial);
    support.position.set(0, 0, 10);
    support.rotation.x = Math.PI / 2;
    jwstModel.add(support);

    // Secondary mirror
    const secondaryMirrorGeometry = new THREE.CircleGeometry(2.5, 16);
    const secondaryMirror = new THREE.Mesh(secondaryMirrorGeometry, mainMirrorMaterial);
    secondaryMirror.position.set(0, 0, 20);
    secondaryMirror.rotation.x = -Math.PI / 2;
    jwstModel.add(secondaryMirror);

    // Spacecraft bus
    const busGeometry = new THREE.BoxGeometry(8, 5, 8);
    const busMaterial = new THREE.MeshPhongMaterial({
        color: 0x333333,
        emissive: 0x111111,
        shininess: 30,
    });
    const bus = new THREE.Mesh(busGeometry, busMaterial);
    bus.position.set(0, -5, 0);
    jwstModel.add(bus);

    // Solar shield (5-layer sunshield)
    const shieldGeometry = new THREE.PlaneGeometry(40, 20);
    const shieldMaterial = new THREE.MeshPhongMaterial({
        color: 0xaabbff,
        emissive: 0x112244,
        transparent: true,
        opacity: 0.7,
        side: THREE.DoubleSide
    });

    // Create 5 layers with slight offset
    for (let i = 0; i < 5; i++) {
        const shield = new THREE.Mesh(shieldGeometry, shieldMaterial.clone());
        shield.position.set(0, -15 - i * 0.25, 0);
        shield.rotation.x = Math.PI / 2;
        jwstModel.add(shield);
    }

    // Add the model to the scene
    jwstScene.add(jwstModel);
}

/**
 * Animate the JWST model
 */
function animateJWSTModel() {
    requestAnimationFrame(animateJWSTModel);

    // Update controls
    jwstControls.update();

    // Rotate model if animation is not paused
    if (!animationPaused) {
        jwstModel.rotation.y += 0.003;
    }

    // Render the scene
    jwstRenderer.render(jwstScene, jwstCamera);
}

/**
 * Initialize WebSocket connection to the JWST data server
 */
function initializeWebSocketConnection() {
    try {
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProtocol}//${window.location.host}/jwst-ws`;

        const socket = new WebSocket(wsUrl);

        socket.onopen = function() {
            console.log('WebSocket connection established');
            updateConnectionStatus(true);
        };

        socket.onmessage = function(event) {
            const data = JSON.parse(event.data);
            handleWebSocketMessage(data);
        };

        socket.onclose = function() {
            console.log('WebSocket connection closed');
            updateConnectionStatus(false);

            // Try to reconnect after 5 seconds
            setTimeout(initializeWebSocketConnection, 5000);
        };

        socket.onerror = function(error) {
            console.error('WebSocket error:', error);
            updateConnectionStatus(false);
        };
    } catch (error) {
        console.error('Failed to establish WebSocket connection:', error);
        updateConnectionStatus(false);
    }
}

/**
 * Handle incoming WebSocket messages
 */
function handleWebSocketMessage(data) {
    switch (data.type) {
        case 'spectral_data':
            updateSpectralPlot(data.data);
            break;
        case 'correlation_data':
            updateCorrelationPlot(data.data);
            break;
        case 'status_update':
            updateDashboardStatus(data.status);
            break;
        case 'file_list':
            updateFileList(data.files);
            break;
    }
}

/**
 * Update connection status indicators
 */
function updateConnectionStatus(connected) {
    const statusIndicator = document.querySelector('.status-item:nth-child(3) .status-indicator');
    const statusValue = document.querySelector('.status-item:nth-child(3) .value');

    if (connected) {
        statusIndicator.className = 'status-indicator status-green';
        statusValue.textContent = 'Connected';
    } else {
        statusIndicator.className = 'status-indicator status-red';
        statusValue.textContent = 'Disconnected';
    }
}

/**
 * Update dashboard status based on server data
 */
function updateDashboardStatus(status) {
    if (!status) return;

    // Update server status
    if (status.server) {
        document.getElementById('data-server-status').textContent = status.server;
    }

    // Update files downloaded
    if (status.filesDownloaded && status.totalFiles) {
        document.getElementById('files-downloaded').textContent =
            `${status.filesDownloaded}/${status.totalFiles}`;
    }

    // Update processing status
    if (status.processing) {
        document.getElementById('processing-status').textContent = status.processing;
    }

    // Update RF correlation status
    if (status.rfCorrelation) {
        document.getElementById('rf-correlation-status').textContent = status.rfCorrelation;
    }
}

/**
 * Update the spectral plot with new data
 */
function updateSpectralPlot(data) {
    if (!data || !data.wavelengths || !data.intensities) return;

    Plotly.update('spectral-plot', {
        x: [data.wavelengths],
        y: [data.intensities]
    }, {}, [0]);
}

/**
 * Update the correlation plot with new data
 */
function updateCorrelationPlot(data) {
    if (!data) return;

    Plotly.update('rf-correlation-plot', {
        x: [data.wavelengths, data.wavelengths],
        y: [data.intensities, data.rfCorrelations]
    }, {}, [0, 1]);

    // Update correlation statistics
    if (data.correlationCoefficient) {
        document.getElementById('correlation-coefficient').textContent =
            data.correlationCoefficient.toFixed(2);
    }

    if (data.ionosphericImpact) {
        document.getElementById('ionospheric-impact').textContent =
            data.ionosphericImpact;
    }

    if (data.signalToNoise) {
        document.getElementById('signal-to-noise').textContent =
            `${data.signalToNoise.toFixed(1)} dB`;
    }
}

/**
 * Populate file list with available JWST data files
 */
function populateFileList() {
    // This would normally fetch from the server,
    // but for now we'll use the static content already in the HTML

    // Add click handlers to download buttons
    const downloadButtons = document.querySelectorAll('.download-btn');
    downloadButtons.forEach(button => {
        button.addEventListener('click', function() {
            const fileName = this.closest('tr').cells[0].textContent;
            downloadFile(fileName);
        });
    });
}

/**
 * Update file list with new data
 */
function updateFileList(files) {
    if (!files || !Array.isArray(files)) return;

    const tableBody = document.getElementById('file-list-body');
    tableBody.innerHTML = '';

    files.forEach(file => {
        const row = document.createElement('tr');

        const nameCell = document.createElement('td');
        nameCell.textContent = file.name;
        row.appendChild(nameCell);

        const instrumentCell = document.createElement('td');
        instrumentCell.textContent = file.instrument;
        row.appendChild(instrumentCell);

        const sizeCell = document.createElement('td');
        sizeCell.textContent = formatFileSize(file.size);
        row.appendChild(sizeCell);

        const actionCell = document.createElement('td');
        const downloadBtn = document.createElement('button');
        downloadBtn.className = 'download-btn';
        downloadBtn.textContent = 'Download';
        downloadBtn.addEventListener('click', () => downloadFile(file.name));
        actionCell.appendChild(downloadBtn);
        row.appendChild(actionCell);

        tableBody.appendChild(row);
    });
}

/**
 * Format file size in human-readable format
 */
function formatFileSize(bytes) {
    if (!bytes) return '0 B';

    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;

    while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
    }

    return `${bytes.toFixed(1)} ${units[i]}`;
}

/**
 * Simulate downloading a file
 */
function downloadFile(fileName) {
    console.log(`Simulating download of: ${fileName}`);

    // In a real implementation, this would trigger a file download
    // For now, just show an alert
    alert(`Download started for: ${fileName}`);
}

/**
 * Update status indicators with current data
 */
function updateStatusIndicators() {
    // This would normally fetch real-time status from the server
    // For now, we'll use simulated data

    document.getElementById('data-server-status').textContent = 'Connected';
    document.getElementById('files-downloaded').textContent = '42/57';
    document.getElementById('processing-status').textContent = 'Active';
    document.getElementById('rf-correlation-status').textContent = 'Running';

    document.getElementById('correlation-coefficient').textContent = '0.72';
    document.getElementById('ionospheric-impact').textContent = 'Medium';
    document.getElementById('signal-to-noise').textContent = '18.3 dB';
}

/**
 * Set up event listeners for UI interactions
 */
function setupEventListeners() {
    // Tab buttons
    document.querySelectorAll('.tab-btn').forEach(button => {
        button.addEventListener('click', function() {
            // Remove active class from all tabs
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.remove('active');
            });

            // Add active class to clicked tab
            this.classList.add('active');

            // Update plot title based on selected instrument
            const instrument = this.dataset.tab;
            updatePlotTitle(instrument);
        });
    });

    // Wavelength range selector
    document.getElementById('wavelength-range').addEventListener('change', function() {
        updateWavelengthRange(this.value);
    });

    // Data source selector
    document.getElementById('data-source').addEventListener('change', function() {
        updateDataSource(this.value);
    });

    // Toggle animation button
    document.getElementById('toggle-animation').addEventListener('click', function() {
        animationPaused = !animationPaused;
        this.textContent = animationPaused ? 'Resume Animation' : 'Pause Animation';
    });

    // Reset view button
    document.getElementById('reset-view').addEventListener('click', resetModelView);

    // Refresh data button
    document.getElementById('refresh-data').addEventListener('click', refreshData);

    // Download FITS button
    document.getElementById('download-fits').addEventListener('click', downloadAllFits);

    // Pagination buttons
    document.querySelector('.prev-page').addEventListener('click', () => changePage('prev'));
    document.querySelector('.next-page').addEventListener('click', () => changePage('next'));

    // Apply settings button
    document.getElementById('apply-settings').addEventListener('click', applySettings);
}

/**
 * Update plot title based on selected instrument
 */
function updatePlotTitle(instrument) {
    let title = 'JWST ';

    switch (instrument) {
        case 'niriss':
            title += 'NIRISS Spectral Data';
            break;
        case 'nirspec':
            title += 'NIRSpec Spectral Data';
            break;
        case 'miri':
            title += 'MIRI Spectral Data';
            break;
        default:
            title += 'Spectral Data';
    }

    Plotly.update('spectral-plot', {}, {title: title}, []);
}

/**
 * Update wavelength range on the plots
 */
function updateWavelengthRange(range) {
    let xRange = [];

    switch (range) {
        case 'near-ir':
            xRange = [0.6, 5];
            break;
        case 'mid-ir':
            xRange = [5, 28];
            break;
        case 'far-ir':
            xRange = [28, 1000];
            break;
        default:
            // Full range - let Plotly auto-scale
            xRange = null;
    }

    if (xRange) {
        Plotly.update('spectral-plot', {}, {
            xaxis: {range: xRange}
        }, []);

        Plotly.update('rf-correlation-plot', {}, {
            xaxis: {range: xRange}
        }, []);
    } else {
        // Reset to auto-scale
        Plotly.update('spectral-plot', {}, {
            xaxis: {autorange: true}
        }, []);

        Plotly.update('rf-correlation-plot', {}, {
            xaxis: {autorange: true}
        }, []);
    }
}

/**
 * Update data source for the plots
 */
function updateDataSource(source) {
    console.log(`Switching to data source: ${source}`);

    // Simulate fetching new data
    setTimeout(() => {
        const newData = JWST_Integration.getSpectralData();

        // Add some variance based on the selected source
        for (let i = 0; i < newData.intensities.length; i++) {
            newData.intensities[i] *= 0.7 + Math.random() * 0.6;
            newData.rfCorrelations[i] *= 0.7 + Math.random() * 0.6;
        }

        // Update plots with new data
        updateSpectralPlot(newData);
        updateCorrelationPlot(newData);
    }, 500);
}

/**
 * Reset the JWST model view
 */
function resetModelView() {
    jwstCamera.position.set(0, 30, 50);
    jwstCamera.lookAt(0, 0, 0);
    jwstControls.reset();
}

/**
 * Refresh all data from the server
 */
function refreshData() {
    console.log('Refreshing data from server');

    // Simulate progress with the reload button
    const refreshBtn = document.getElementById('refresh-data');
    const originalText = refreshBtn.textContent;
    refreshBtn.textContent = 'Refreshing...';
    refreshBtn.disabled = true;

    // Simulate data refresh
    setTimeout(() => {
        // Reset button
        refreshBtn.textContent = originalText;
        refreshBtn.disabled = false;

        // Update all components with new data
        initializeSpectralPlot();
        initializeCorrelationPlot();
        updateStatusIndicators();

        // Show success message
        alert('Data refreshed successfully');
    }, 1500);
}

/**
 * Download all FITS files
 */
function downloadAllFits() {
    console.log('Downloading all FITS files');
    alert('Bulk download started for all FITS files. Check your downloads folder.');
}

/**
 * Change the file list page
 */
function changePage(direction) {
    const pageIndicator = document.querySelector('.page-indicator');
    const [currentPage, totalPages] = pageIndicator.textContent
        .replace('Page ', '')
        .split(' of ')
        .map(Number);

    let newPage = currentPage;
    if (direction === 'next' && currentPage < totalPages) {
        newPage++;
    } else if (direction === 'prev' && currentPage > 1) {
        newPage--;
    }

    if (newPage !== currentPage) {
        pageIndicator.textContent = `Page ${newPage} of ${totalPages}`;
        // Load new page data (simulation)
        populateFileList();
    }
}

/**
 * Apply RF analysis settings
 */
function applySettings() {
    const freqMin = document.getElementById('freq-min').value;
    const freqMax = document.getElementById('freq-max').value;
    const correlationMethod = document.getElementById('correlation-method').value;
    const ionosphereModel = document.getElementById('ionosphere-model').value;
    const enableRealtime = document.getElementById('enable-realtime').checked;
    const includeWeather = document.getElementById('include-weather').checked;
    const advancedMode = document.getElementById('advanced-mode').checked;

    console.log('Applying settings:', {
        frequencyRange: [freqMin, freqMax],
        correlationMethod,
        ionosphereModel,
        enableRealtime,
        includeWeather,
        advancedMode
    });

    alert('Settings applied successfully');
}

/**
 * Update system time display
 */
function updateSystemTime() {
    const now = new Date();
    const timeString = now.toISOString().replace('T', ' ').substr(0, 19);
    document.getElementById('system-time').textContent = timeString;
}
