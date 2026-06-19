// This file provides mock API responses for RF SCYTHE system
// It intercepts fetch requests to specific endpoints and returns predefined responses
// NOTE: /api/rf-hypergraph/* are served by the live server and NOT mocked here.

document.addEventListener('DOMContentLoaded', function() {
    console.log('[Mock API] Initializing RF SCYTHE API mock server');
    
    // Define mock API endpoints — only endpoints with no real server counterpart
    const mockApis = [
        { 
            url: '/api/ionosphere/layers', 
            response: {
                status: 'success',
                data: {
                    layers: {
                        D: { active: true, minHeight: 60, maxHeight: 90 },
                        E: { active: true, minHeight: 90, maxHeight: 150 },
                        F1: { active: true, minHeight: 150, maxHeight: 250 },
                        F2: { active: true, minHeight: 250, maxHeight: 500 }
                    },
                    solarActivity: {
                        solarFlux: 120.5,
                        kpIndex: 3.2
                    },
                    lastUpdate: new Date().getTime()
                }
            }
        },
        {
            url: '/api/strf/satellites',
            response: {
                status: 'success',
                data: {
                    satellites: [
                        { id: 'SAT-001', name: 'RF Monitor 1', lat: 37.7749, lon: -122.4194, alt: 500000, type: 'LEO', status: 'active' },
                        { id: 'SAT-002', name: 'RF Monitor 2', lat: 40.7128, lon: -74.0060, alt: 520000, type: 'LEO', status: 'active' },
                        { id: 'SAT-003', name: 'SIGINT Alpha', lat: 34.0522, lon: -118.2437, alt: 480000, type: 'LEO', status: 'standby' }
                    ]
                }
            }
        },
        {
            url: '/api/classify-signal',
            response: {
                success: true,
                classification: {
                    modulation: 'FSK',
                    confidence: 0.92,
                    source_types: ['Wireless IoT Device', 'Smart Home System', 'Industrial Control']
                }
            }
        }
        // NOTE: /api/rf-hypergraph/* endpoints are served by the live rf_scythe_api_server.py
        // and intentionally NOT mocked here so real hypergraph data flows through.
    ];
    
    // Store the original fetch function
    const originalFetch = window.fetch;
    
    // Override fetch to intercept requests to our mock endpoints
    window.fetch = function(url, options) {
        // Normalize the URL to extract just the path
        let urlPath = url;
        if (typeof url === 'string') {
            try {
                // Handle both relative and absolute URLs
                if (url.startsWith('http://') || url.startsWith('https://')) {
                    urlPath = new URL(url).pathname + new URL(url).search;
                }
            } catch (e) {
                // If URL parsing fails, use the original
                urlPath = url;
            }
        }
        
        // Check if the request URL matches any of our mock APIs
        for (const mockApi of mockApis) {
            if (typeof urlPath === 'string' && urlPath.includes(mockApi.url)) {
                console.log(`[Mock API] Intercepted request to ${urlPath}`);
                
                // Handle dynamic responses
                const response = mockApi.dynamic ? mockApi.getResponse() : mockApi.response;
                
                // Return a Promise that resolves with a mock Response object
                return Promise.resolve({
                    ok: true,
                    status: 200,
                    json: () => Promise.resolve(response),
                    text: () => Promise.resolve(JSON.stringify(response))
                });
            }
        }
        
        // For any other requests, pass through to the original fetch
        console.log(`[Mock API] Passing through request to ${url}`);
        return originalFetch(url, options);
    };
    
    // Add RF_SCYTHE.generateNetworkCaptureReport function
    window.RF_SCYTHE = window.RF_SCYTHE || {};
    window.RF_SCYTHE.generateNetworkCaptureReport = function(options) {
        return Promise.resolve({
            timestamp: options.timestamp || new Date().toISOString(),
            summary: {
                total_packets: Math.floor(Math.random() * 10000) + 1000,
                unique_sources: Math.floor(Math.random() * 50) + 10,
                unique_destinations: Math.floor(Math.random() * 100) + 20,
                protocols_detected: ['TCP', 'UDP', 'ICMP', 'DNS', 'HTTP', 'TLS'],
                anomalies_detected: Math.floor(Math.random() * 5)
            },
            traffic_analysis: {
                top_talkers: [
                    { ip: '192.168.1.100', packets: 2500, bytes: 1500000 },
                    { ip: '10.0.0.50', packets: 1800, bytes: 900000 },
                    { ip: '172.16.0.25', packets: 1200, bytes: 600000 }
                ],
                protocol_distribution: {
                    'TCP': 65,
                    'UDP': 25,
                    'ICMP': 5,
                    'Other': 5
                }
            },
            security_events: [
                { type: 'Port Scan Detected', severity: 'medium', source: '192.168.1.200', timestamp: new Date().toISOString() },
                { type: 'Unusual Traffic Pattern', severity: 'low', source: '10.0.0.75', timestamp: new Date().toISOString() }
            ],
            rf_correlation: {
                signals_detected: Math.floor(Math.random() * 10) + 5,
                frequency_range: '2.4GHz - 5.8GHz',
                interference_level: 'Low'
            }
        });
    };
    
    console.log('[Mock API] RF SCYTHE API mock server ready');
});
