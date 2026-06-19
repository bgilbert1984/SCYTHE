// filepath: /home/gorelock/gemma/NerfEngine/ionosphere-visualization.js
/**
 * RF SCYTHE Ionospheric Propagation Visualization
 * 
 * This module handles the visualization of ionospheric layers and signal propagation
 * with the ionosphere_modeling.js backend.
 */

// API endpoint for ionospheric data
const IONOSPHERE_API_ENDPOINT = '/api/ionosphere/layers';
const PROPAGATION_API_ENDPOINT = '/api/propagation/simulate';

// Initialize ionosphere visualization
async function initializeIonosphereVisualization() {
    try {
        await fetchIonosphereData();
        updateIonosphereStatus(true);
        addConsoleMessage('Ionospheric propagation model initialized successfully', 'response');
    } catch (error) {
        console.error('Failed to initialize ionosphere visualization:', error);
        updateIonosphereStatus(false);
        addConsoleMessage('Failed to initialize ionospheric model. Check connection.', 'error');
    }
}

// Fetch ionosphere data from the backend
async function fetchIonosphereData() {
    try {
        // First try the API endpoint
        try {
            const response = await fetch(IONOSPHERE_API_ENDPOINT);
            if (response.ok) {
                const data = await response.json();
                ionosphereData = data;
                
                // If the ionosphere toggle is checked, update the visualization
                if (document.getElementById('toggleIonosphere').checked) {
                    addIonosphericLayers(viewer, data);
                }
                
                updateIonosphereStatus(true);
                addConsoleMessage('Ionospheric data updated from server', 'response');
                return data;
            }
        } catch (apiError) {
            console.warn('API endpoint not available, using fallback data:', apiError);
        }
        
        // Fallback to default data if API is not available
        ionosphereData = getDefaultIonosphereData();
        if (document.getElementById('toggleIonosphere').checked) {
            addIonosphericLayers(viewer, ionosphereData);
        }
        
        updateIonosphereStatus(true, 'Fallback');
        addConsoleMessage('Using fallback ionospheric data', 'warning');
        return ionosphereData;
    } catch (error) {
        console.error('Error fetching ionosphere data:', error);
        updateIonosphereStatus(false);
        addConsoleMessage('Failed to get ionospheric data', 'error');
        throw error;
    }
}

// Update the ionosphere status indicator
function updateIonosphereStatus(isActive, statusText = null) {
    // Find the correct span by checking text content
    const statusIndicators = document.querySelectorAll('.status-indicator span');
    let ionosphereSpan = null;
    for (const span of statusIndicators) {
        if (span.textContent.includes('IONOSPHERE MODEL')) {
            ionosphereSpan = span;
            break;
        }
    }

    if (!ionosphereSpan) {
        console.error("Could not find Ionosphere status indicator span.");
        // Optionally create it if needed, similar to how strf-bridge does
        return; 
    }

    const ionosphereStatus = ionosphereSpan.parentNode; // Get the parent .status-indicator div
    
    if (isActive) {
        ionosphereStatus.className = 'status-indicator status-active';
        ionosphereSpan.textContent = statusText ? `IONOSPHERE MODEL (${statusText})` : 'IONOSPHERE MODEL';
    } else {
        ionosphereStatus.className = 'status-indicator status-inactive';
        ionosphereSpan.textContent = 'IONOSPHERE MODEL OFFLINE';
    }
}

// Generate default ionosphere data for fallback
function getDefaultIonosphereData() {
    return {
        layers: {
            D: { minHeight: 60, maxHeight: 90, active: true, absorption: 0.5, reflection: 0.2 },
            E: { minHeight: 90, maxHeight: 150, active: true, absorption: 0.3, reflection: 0.5 },
            F1: { minHeight: 150, maxHeight: 210, active: true, absorption: 0.1, reflection: 0.7 },
            F2: { minHeight: 210, maxHeight: 400, active: true, absorption: 0.05, reflection: 0.8 }
        },
        solarActivity: {
            solarFlux: 120,
            kpIndex: 3,
            xRayFlux: 2.5e-7
        },
        lastUpdate: Date.now()
    };
}

// Generate example signal paths for demonstration
async function generateExampleSignalPaths() {
    // Clear existing paths
    signalPaths.forEach(path => viewer.entities.remove(path));
    signalPaths = [];
    
    try {
        // Try to fetch real propagation data from the API
        const response = await fetch(PROPAGATION_API_ENDPOINT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                signal: {
                    frequency: 14.2, // MHz (HF band)
                    power: 0.8,
                    location: { lat: 37.7749, lon: -122.4194, alt: 10 } // San Francisco
                },
                receivers: [
                    { id: 'rx1', location: { lat: 40.7128, lon: -74.0060, alt: 5 }, sensitivity: 0.001 }, // New York
                    { id: 'rx2', location: { lat: 51.5074, lon: -0.1278, alt: 10 }, sensitivity: 0.001 }, // London
                    { id: 'rx3', location: { lat: 35.6762, lon: 139.6503, alt: 8 }, sensitivity: 0.001 } // Tokyo
                ]
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            if (data.success && data.results) {
                processAndVisualizePaths(data.results);
                addConsoleMessage('Signal propagation paths calculated', 'response');
                return;
            }
        }
    } catch (apiError) {
        console.warn('API endpoint not available for propagation, using example paths:', apiError);
    }
    
    // Fallback to example paths
    createExamplePaths();
    addConsoleMessage('Using example signal propagation paths', 'warning');
}

// Process propagation results and visualize paths
function processAndVisualizePaths(results) {
    results.receiverData.forEach(receiver => {
        if (receiver.bestPath) {
            const path = visualizeSignalPath(viewer, receiver.bestPath);
            signalPaths.push(path);
            
            // Add notification for significant paths
            if (receiver.detectable && receiver.bestPath.type === 'ionospheric') {
                showNotification(
                    'Ionospheric Propagation Detected',
                    `Signal propagating via ${receiver.bestPath.layer} layer to receiver ${receiver.id}. 
                    Distance: ${Math.round(receiver.bestPath.distance)} km, 
                    Delay: ${receiver.bestPath.delay.toFixed(2)} ms`,
                    'info'
                );
            }
        }
    });
}

// Create example signal paths for demonstration
function createExamplePaths() {
    // Example source location (San Francisco)
    const sourceLocation = { lat: 37.7749, lon: -122.4194, alt: 10 };
    
    // Example receiver locations
    const receiverLocations = [
        { id: 'rx1', lat: 40.7128, lon: -74.0060, alt: 5 }, // New York
        { id: 'rx2', lat: 51.5074, lon: -0.1278, alt: 10 }, // London
        { id: 'rx3', lat: 35.6762, lon: 139.6503, alt: 8 }  // Tokyo
    ];
    
    // Create paths for each receiver
    receiverLocations.forEach((receiver, index) => {
        // Create a direct path to New York
        if (index === 0) {
            const directPath = {
                type: 'direct',
                distance: 4130,
                signalStrength: 0.3,
                delay: 13.8,
                path: [
                    sourceLocation,
                    receiver
                ],
                complete: true,
                blocked: false
            };
            
            const path = visualizeSignalPath(viewer, directPath);
            signalPaths.push(path);
        }
        // Create a single-hop F2 path to London
        else if (index === 1) {
            // Calculate intermediate points for visualization
            const midLat = (sourceLocation.lat + receiver.lat) / 2;
            const midLon = (sourceLocation.lon + receiver.lon) / 2;
            
            const upPoint = {
                lat: sourceLocation.lat + (midLat - sourceLocation.lat) * 0.3,
                lon: sourceLocation.lon + (midLon - sourceLocation.lon) * 0.3,
                alt: 350000 // F2 layer height
            };
            
            const reflectionPoint = {
                lat: midLat,
                lon: midLon,
                alt: 350000 // F2 layer height
            };
            
            const downPoint = {
                lat: midLat + (receiver.lat - midLat) * 0.7,
                lon: midLon + (receiver.lon - midLon) * 0.7,
                alt: 350000 // F2 layer height
            };
            
            const ionoPath = {
                type: 'ionospheric',
                layer: 'F2',
                distance: 8800,
                signalStrength: 0.4,
                delay: 29.4,
                takeoffAngle: 15,
                criticalFrequency: 9.8,
                muf: 28.5,
                path: [
                    sourceLocation,
                    upPoint,
                    reflectionPoint,
                    downPoint,
                    receiver
                ],
                complete: true,
                hops: 1
            };
            
            const path = visualizeSignalPath(viewer, ionoPath);
            signalPaths.push(path);
        }
        // Create a multi-hop path to Tokyo
        else if (index === 2) {
            // Calculate intermediate points
            const hop1Ground = {
                lat: sourceLocation.lat + (receiver.lat - sourceLocation.lat) * 0.33,
                lon: sourceLocation.lon + (receiver.lon - sourceLocation.lon) * 0.33,
                alt: 0
            };
            
            const hop1Iono = {
                lat: sourceLocation.lat + (hop1Ground.lat - sourceLocation.lat) * 0.5,
                lon: sourceLocation.lon + (hop1Ground.lon - sourceLocation.lon) * 0.5,
                alt: 350000 // F2 layer height
            };
            
            const hop2Iono = {
                lat: hop1Ground.lat + (receiver.lat - hop1Ground.lat) * 0.5,
                lon: hop1Ground.lon + (receiver.lon - hop1Ground.lon) * 0.5,
                alt: 350000 // F2 layer height
            };
            
            const multiHopPath = {
                type: 'ionospheric',
                layer: 'F2',
                distance: 9800,
                signalStrength: 0.1,
                delay: 32.7,
                path: [
                    sourceLocation,
                    hop1Iono,
                    hop1Ground,
                    hop2Iono,
                    receiver
                ],
                complete: true,
                hops: 2
            };
            
            const path = visualizeSignalPath(viewer, multiHopPath);
            signalPaths.push(path);
        }
    });
    
    // Show notification for demo
    showNotification(
        'Ionospheric Paths Visualization',
        'Showing example signal propagation through ionospheric layers. These demonstrate direct path, single-hop F2, and multi-hop F2 propagation.',
        'info'
    );
}

// Update the map legend to include ionosphere items
function updateMapLegend() {
    const mapLegend = document.getElementById('map-legend');
    if (!mapLegend) return;
    
    // Check if ionosphere legend items already exist
    if (mapLegend.querySelector('.legend-item[data-type="ionosphere"]')) return;
    
    // Add ionosphere legend items
    const legendItems = [
        { name: 'D Layer', color: '#FF5500' },
        { name: 'E Layer', color: '#FFAA00' },
        { name: 'F1 Layer', color: '#AA00FF' },
        { name: 'F2 Layer', color: '#0088FF' }
    ];
    
    legendItems.forEach(item => {
        const legendItem = document.createElement('div');
        legendItem.className = 'legend-item';
        legendItem.setAttribute('data-type', 'ionosphere');
        legendItem.innerHTML = `
            <div class="legend-color" style="background-color: ${item.color}; opacity: 0.4;"></div>
            <span>${item.name}</span>
        `;
        mapLegend.appendChild(legendItem);
    });
    
    // Add signal path legend items
    const pathLegendItem = document.createElement('div');
    pathLegendItem.className = 'legend-item';
    pathLegendItem.setAttribute('data-type', 'ionosphere');
    pathLegendItem.innerHTML = `
        <div class="legend-color" style="background: linear-gradient(90deg, #00FFFF, #FF00FF);"></div>
        <span>Signal Path</span>
    `;
    mapLegend.appendChild(pathLegendItem);
}

// Initialize legend when document is loaded
document.addEventListener('DOMContentLoaded', updateMapLegend);
