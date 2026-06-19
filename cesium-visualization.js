// RF SCYTHE Cesium visualization

// Set up the Cesium viewer
function initializeCesiumViewer() {
    // Apply Rectangle north property fix before creating the viewer
    if (typeof RF_SCYTHE !== 'undefined') {
        if (typeof RF_SCYTHE.enhancedRectangleNorthFix === 'function') {
            console.log('Applying enhanced Rectangle north fix before viewer creation');
            RF_SCYTHE.enhancedRectangleNorthFix();
        }
        else if (typeof RF_SCYTHE.patchComputeRectangle === 'function') {
            console.log('Applying standard Rectangle north fix before viewer creation');
            RF_SCYTHE.patchComputeRectangle();
        }
    }

    // Store global reference for the error handler
    var viewer = new Cesium.Viewer('cesiumContainer', {
        terrainProvider: Cesium.createWorldTerrain(),
        timeline: false,
        animation: false,
        baseLayerPicker: true,
        geocoder: true,
        sceneModePicker: true,
        navigationHelpButton: false,
        homeButton: true,
        scene3DOnly: false,
        infoBox: true
    });
    
    // Store the viewer in a global reference for error handling
    window.Cesium = window.Cesium || {};
    window.Cesium.viewer = viewer;
    
    // Apply Rectangle north property fix again after creating the viewer
    if (typeof RF_SCYTHE !== 'undefined') {
        if (typeof RF_SCYTHE.enhancedRectangleNorthFix === 'function') {
            console.log('Applying enhanced Rectangle north fix after viewer creation');
            RF_SCYTHE.enhancedRectangleNorthFix();
        }
        else if (typeof RF_SCYTHE.patchComputeRectangle === 'function') {
            console.log('Applying standard Rectangle north fix after viewer creation');
            RF_SCYTHE.patchComputeRectangle();
        }
    }
    
    // Set up error recovery - when a rendering error occurs, try to continue
    viewer.scene.renderError.addEventListener(function(error) {
        console.error('Rendering error occurred:', error);
        
        if (window.RF_SCYTHE && typeof window.RF_SCYTHE.showNotification === 'function') {
            window.RF_SCYTHE.showNotification('Rendering error detected. Attempting to recover...', 'error');
        }
        
        // Enable debugging visualization for problematic geometries
        viewer.scene.debugShowBoundingVolume = true;
        
        // Try to render next frame despite the error
        return true;
    });
    
    return viewer;
}

// Add RF signal entity to the map
function addRFSignal(viewer, lat, lon, freq, power, modulation, altitude = 0) {
    // Input validation to prevent errors
    if (!viewer || typeof lat !== 'number' || typeof lon !== 'number' || isNaN(lat) || isNaN(lon)) {
        console.warn("Invalid parameters for RF signal marker", { lat, lon, freq });
        return null;
    }
    
    // Validate coordinates are within reasonable ranges
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) {
        console.warn("Invalid lat/lon values for RF signal marker", { lat, lon });
        return null;
    }
    
    try {
        const signalColor = getModulationColor(modulation);
        
        // Add a small altitude offset to ensure signals aren't obscured by terrain
        const elevationOffset = altitude || 50; // Default 50 meters above terrain if no altitude provided
        
        // Create position in a safe way
        let cartesian;
        try {
            cartesian = Cesium.Cartesian3.fromDegrees(lon, lat, elevationOffset);
            
            // Ensure the cartesian position is valid
            if (!cartesian || isNaN(cartesian.x) || isNaN(cartesian.y) || isNaN(cartesian.z)) {
                console.warn("Invalid cartesian position for RF signal", { lat, lon, cartesian });
                return null;
            }
        } catch (posErr) {
            console.error("Error creating cartesian position for RF signal:", posErr);
            return null;
        }
        
        return viewer.entities.add({
            position: cartesian,
            point: {
                pixelSize: 10 + (power * 10),
                color: signalColor,
                outlineColor: Cesium.Color.WHITE,
                outlineWidth: 2,
                heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND // Make signals follow terrain
            },
            label: {
                text: `${freq.toFixed(3)} MHz (${modulation})`,
                font: '12px sans-serif',
                fillColor: Cesium.Color.WHITE,
                outlineColor: Cesium.Color.BLACK,
                outlineWidth: 2,
                style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
                pixelOffset: new Cesium.Cartesian2(0, -15),
                heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND // Make labels follow terrain
            }
        });
    } catch (e) {
        console.error("Error creating RF signal entity:", e);
        return null;
    }
}

// Add FCC violation entity to the map
function addViolation(viewer, lat, lon, freq, description, altitude = 0) {
    // Input validation to prevent errors
    if (!viewer || typeof lat !== 'number' || typeof lon !== 'number' || isNaN(lat) || isNaN(lon)) {
        console.warn("Invalid parameters for violation marker", { lat, lon, freq });
        return null;
    }
    
    // Validate coordinates are within reasonable ranges
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) {
        console.warn("Invalid lat/lon values for violation marker", { lat, lon });
        return null;
    }
    
    try {
        // Add a small altitude offset to ensure violations aren't obscured by terrain
        const elevationOffset = altitude || 100; // Default 100 meters above terrain if no altitude provided
        
        // Create position in a safe way
        let cartesian;
        try {
            cartesian = Cesium.Cartesian3.fromDegrees(lon, lat, elevationOffset);
            
            // Ensure the cartesian position is valid
            if (!cartesian || isNaN(cartesian.x) || isNaN(cartesian.y) || isNaN(cartesian.z)) {
                console.warn("Invalid cartesian position for violation marker", { lat, lon, cartesian });
                return null;
            }
        } catch (posErr) {
            console.error("Error creating cartesian position for violation:", posErr);
            return null;
        }
        
        return viewer.entities.add({
            position: cartesian,
            point: {
                pixelSize: 15,
                color: Cesium.Color.RED,
                outlineColor: Cesium.Color.WHITE,
                outlineWidth: 2,
                heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND
            },
            label: {
                text: `VIOLATION: ${freq.toFixed(3)} MHz\n${description}`,
                font: '14px sans-serif',
                fillColor: Cesium.Color.RED,
                outlineColor: Cesium.Color.BLACK,
                outlineWidth: 2,
                style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
                pixelOffset: new Cesium.Cartesian2(0, -15),
                heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND
            }
        });
    } catch (e) {
        console.error("Error creating violation marker:", e);
        return null;
    }
}

// Get color based on modulation type
function getModulationColor(modulation) {
    const colors = {
        'AM': Cesium.Color.AQUA,
        'FM': Cesium.Color.LIMEGREEN,
        'SSB': Cesium.Color.ORANGE,
        'CW': Cesium.Color.YELLOW,
        'PSK': Cesium.Color.MAGENTA,
        'FSK': Cesium.Color.CYAN,
        'UNKNOWN': Cesium.Color.GRAY
    };
    
    return colors[modulation] || colors['UNKNOWN'];
}

// Add uncertainty circle for signal location
function addUncertaintyCircle(viewer, lat, lon, radius, isViolation = false, altitude = 0) {
    // Enhanced input validation using our coordinate validation utilities
    if (!viewer || !radius || radius <= 0) {
        console.warn("Invalid parameters for uncertainty circle", { lat, lon, radius });
        return null;
    }
    
    try {
        // Use our coordinate sanitization utilities
        const sanitized = window.RF_SCYTHE.sanitizeCoordinates(lat, lon, altitude);
        const safeColor = isViolation ? Cesium.Color.RED.withAlpha(0.3) : Cesium.Color.CYAN.withAlpha(0.3);
        const outlineColor = isViolation ? Cesium.Color.RED.withAlpha(0.7) : Cesium.Color.CYAN.withAlpha(0.7);
        
        // Create a safe cartesian position
        let cartesian;
        try {
            cartesian = Cesium.Cartesian3.fromDegrees(
                sanitized.longitude,
                sanitized.latitude,
                sanitized.altitude
            );
            
            // Final validation of the cartesian
            if (!cartesian || isNaN(cartesian.x) || isNaN(cartesian.y) || isNaN(cartesian.z)) {
                console.warn("Invalid cartesian position for uncertainty circle", { lat, lon, sanitized });
                return null;
            }
        } catch (posErr) {
            console.error("Error creating cartesian position:", posErr);
            return null;
        }
        
        // Check if we should use a fallback visualization
        if (window.RF_SCYTHE_USE_FALLBACKS || 
            (Cesium.RF_SCYTHE_CONFIG && Cesium.RF_SCYTHE_CONFIG.USE_ELLIPSE_FALLBACK)) {
            console.log("Using fallback visualization for uncertainty circle");
            
            // Create a simple sphere at the location instead of an ellipse
            return viewer.entities.add({
                position: cartesian,
                name: 'Uncertainty Circle',
                ellipsoid: {
                    radii: new Cesium.Cartesian3(radius, radius, radius * 0.2),
                    material: safeColor,
                    outline: true, 
                    outlineColor: outlineColor,
                    outlineWidth: 2
                }
            });
        } else {
            // Use the safe ellipse creation method with non-zero rotation
            // to avoid geodeticSurfaceNormal issues
            return window.RF_SCYTHE.createSafeEllipse(viewer, cartesian, radius, radius, safeColor, {
                outlineColor: outlineColor,
                outlineWidth: 2,
                height: sanitized.altitude,
                rotation: 0.01 // Small non-zero rotation to avoid problems at poles
            });
        }
    } catch (e) {
        console.error("Error creating uncertainty circle:", e);
        
        // Last resort fallback - create a point
        try {
            const cartesian = Cesium.Cartesian3.fromDegrees(
                parseFloat(lon), 
                parseFloat(lat), 
                parseFloat(altitude)
            );
            
            return viewer.entities.add({
                position: cartesian,
                point: {
                    pixelSize: 15,
                    color: isViolation ? Cesium.Color.RED : Cesium.Color.CYAN,
                    outlineColor: Cesium.Color.WHITE,
                    outlineWidth: 2
                }
            });
        } catch (fallbackError) {
            console.error("Fallback point creation also failed:", fallbackError);
            return null;
        }
    }
}

// Initialize default UI event handlers
function setupEventListeners() {
    // Any UI elements that exist in the DOM can have event listeners attached here
    const toggleButtons = document.querySelectorAll('[id^="toggle"]');
    toggleButtons.forEach(button => {
        if (button) {
            button.addEventListener('change', (e) => {
                console.log(`Toggle ${button.id}: ${e.target.checked}`);
                // Additional toggle logic can be added here
            });
        }
    });
}

// Add ionospheric layers to the map
function addIonosphericLayers(viewer, ionosphereData) {
    const existingLayers = viewer.entities.values.filter(entity => entity.name && entity.name.includes('Ionosphere'));
    existingLayers.forEach(layer => viewer.entities.remove(layer));
    
    // Define layer colors with different transparency
    const layerColors = {
        D: Cesium.Color.fromCssColorString('#FF5500').withAlpha(0.15), // Orange
        E: Cesium.Color.fromCssColorString('#FFAA00').withAlpha(0.15), // Yellow-Orange
        F1: Cesium.Color.fromCssColorString('#AA00FF').withAlpha(0.15), // Purple
        F2: Cesium.Color.fromCssColorString('#0088FF').withAlpha(0.15)  // Blue
    };
    
    // Create layers with appropriate heights
    Object.entries(ionosphereData.layers).forEach(([layerName, layer]) => {
        if (!layer.active) return;
        
        const minAltitude = layer.minHeight * 1000; // Convert km to meters
        const maxAltitude = layer.maxHeight * 1000;
        const midAltitude = (minAltitude + maxAltitude) / 2;
        
        // Create ellipsoid for the layer
        viewer.entities.add({
            name: `Ionosphere ${layerName}`,
            position: Cesium.Cartesian3.fromDegrees(0, 0, 0), // Center of Earth
            ellipsoid: {
                radii: new Cesium.Cartesian3(
                    Cesium.Ellipsoid.WGS84.radii.x + midAltitude,
                    Cesium.Ellipsoid.WGS84.radii.y + midAltitude,
                    Cesium.Ellipsoid.WGS84.radii.z + midAltitude
                ),
                innerRadii: new Cesium.Cartesian3(
                    Cesium.Ellipsoid.WGS84.radii.x + minAltitude,
                    Cesium.Ellipsoid.WGS84.radii.y + minAltitude,
                    Cesium.Ellipsoid.WGS84.radii.z + minAltitude
                ),
                minimumClock: Cesium.Math.toRadians(-90),
                maximumClock: Cesium.Math.toRadians(90),
                minimumCone: Cesium.Math.toRadians(45),
                maximumCone: Cesium.Math.toRadians(135),
                outline: true,
                outlineColor: layerColors[layerName].brighten(0.6, new Cesium.Color()),
                outlineWidth: 1.0,
                material: layerColors[layerName],
                slicePartitions: 24,
                stackPartitions: 24
            }
        });
    });
    
    // Add label with solar activity data
    viewer.entities.add({
        name: 'Ionosphere Solar Data',
        position: Cesium.Cartesian3.fromDegrees(-160, 45, 500000),
        label: {
            text: `Ionosphere Status\nSolar Flux: ${ionosphereData.solarActivity.solarFlux.toFixed(1)} SFU\nKp Index: ${ionosphereData.solarActivity.kpIndex.toFixed(1)}\nUpdated: ${new Date(ionosphereData.lastUpdate).toLocaleTimeString()}`,
            font: '16px sans-serif',
            fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            horizontalOrigin: Cesium.HorizontalOrigin.LEFT,
            verticalOrigin: Cesium.VerticalOrigin.TOP,
            pixelOffset: new Cesium.Cartesian2(5, 5),
            showBackground: true,
            backgroundColor: new Cesium.Color(0, 0, 0, 0.7)
        }
    });
    
    return {
        layers: Object.keys(ionosphereData.layers).filter(name => ionosphereData.layers[name].active),
        solarActivity: ionosphereData.solarActivity
    };
}

// Visualize a signal propagation path through the ionosphere
function visualizeSignalPath(viewer, pathData) {
    const positions = pathData.path.map(point => 
        Cesium.Cartesian3.fromDegrees(point.lon, point.lat, point.alt || 0)
    );
    
    // Determine color based on path type and layer
    let pathColor;
    if (pathData.type === 'direct') {
        pathColor = Cesium.Color.LIMEGREEN;
    } else if (pathData.type === 'ionospheric') {
        switch(pathData.layer) {
            case 'D': pathColor = Cesium.Color.ORANGE; break;
            case 'E': pathColor = Cesium.Color.YELLOW; break;
            case 'F1': pathColor = Cesium.Color.MAGENTA; break;
            case 'F2': pathColor = Cesium.Color.CYAN; break;
            default: pathColor = Cesium.Color.WHITE;
        }
    } else {
        pathColor = Cesium.Color.WHITE;
    }
    
    // Create path entity
    const path = viewer.entities.add({
        name: `Signal Path (${pathData.type})`,
        polyline: {
            positions: positions,
            width: 2,
            material: new Cesium.PolylineGlowMaterialProperty({
                glowPower: 0.2,
                color: pathColor
            }),
            clampToGround: false
        }
    });
    
    // Add points at each reflection point
    pathData.path.forEach((point, index) => {
        if (index === 0 || index === pathData.path.length - 1) return; // Skip source and destination
        
        viewer.entities.add({
            position: Cesium.Cartesian3.fromDegrees(point.lon, point.lat, point.alt || 0),
            point: {
                pixelSize: 10,
                color: pathColor,
                outlineColor: Cesium.Color.WHITE,
                outlineWidth: 1
            }
        });
    });
    
    return path;
}

// Add a 3D model to the scene
function add3DModel(viewer, modelUrl, lat, lon, alt = 0, scale = 1.0, heading = 0, pitch = 0, roll = 0) {
    // Create entity for the 3D model
    const modelEntity = viewer.entities.add({
        name: 'RF SCYTHE 3D Model',
        position: Cesium.Cartesian3.fromDegrees(lon, lat, alt),
        orientation: Cesium.Transforms.headingPitchRollQuaternion(
            Cesium.Cartesian3.fromDegrees(lon, lat, alt),
            new Cesium.HeadingPitchRoll(
                Cesium.Math.toRadians(heading),
                Cesium.Math.toRadians(pitch),
                Cesium.Math.toRadians(roll)
            )
        ),
        model: {
            uri: modelUrl,
            scale: scale,
            minimumPixelSize: 128,
            maximumScale: 20000,
            shadows: Cesium.ShadowMode.ENABLED,
            heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND
        }
    });
    
    console.log(`Added 3D model from ${modelUrl} at position: ${lat}, ${lon}, ${alt}`);
    return modelEntity;
}

// Export functions for use in other scripts
window.RF_SCYTHE = {
    initializeCesiumViewer,
    addRFSignal,
    addViolation,
    addUncertaintyCircle,
    getModulationColor,
    addIonosphericLayers,
    visualizeSignalPath,
    add3DModel
};
