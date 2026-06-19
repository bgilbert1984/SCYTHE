// Cesium Helper Functions for RF SCYTHE
// This file contains utility functions for working with Cesium in the RF SCYTHE visualization system

// Check if a position is valid for Cesium operations
function isValidCesiumPosition(position) {
    if (!position) return false;
    
    // Check for NaN or infinite values
    if (position.x === undefined || position.y === undefined || position.z === undefined) return false;
    if (!isFinite(position.x) || !isFinite(position.y) || !isFinite(position.z)) return false;
    
    // Additional checks for Cesium-specific issues
    // Avoid positions exactly at poles which can cause geodeticSurfaceNormal issues
    const cartographic = Cesium.Cartographic.fromCartesian(position);
    if (!cartographic) return false;
    
    const lat = Cesium.Math.toDegrees(cartographic.latitude);
    const lon = Cesium.Math.toDegrees(cartographic.longitude);
    
    // Check for valid ranges and avoid exact poles
    if (lat < -89.999 || lat > 89.999 || lon < -180 || lon > 180) return false;
    
    return true;
}

// Safely convert from degrees to Cartesian3
function safeCartesian3FromDegrees(lon, lat, height = 0) {
    try {
        // Validate inputs
        if (typeof lon !== 'number' || typeof lat !== 'number' || !isFinite(lon) || !isFinite(lat)) {
            console.warn('Invalid lon/lat for Cartesian3 conversion', { lon, lat });
            return null;
        }
        
        // Ensure lat/lon are within valid ranges
        const safeLat = Math.max(-89.999, Math.min(89.999, lat)); // Avoid exact poles
        const safeLon = ((lon + 180) % 360) - 180; // Normalize to -180 to 180
        
        // Convert to Cartesian3
        return Cesium.Cartesian3.fromDegrees(safeLon, safeLat, height);
    } catch (error) {
        console.error('Error creating Cartesian3 from degrees:', error);
        return null;
    }
}

// Safely create an uncertainty circle that won't fail with geodetic surface normal errors
function safeAddUncertaintyCircle(viewer, lat, lon, radius, isViolation = false, altitude = 0) {
    // Use the safer addUncertaintyCircle implementation
    return window.RF_SCYTHE.addUncertaintyCircle(viewer, lat, lon, radius, isViolation, altitude);
}

// Safely create and handle geodeticSurfaceNormal
function safeGeodeticSurfaceNormal(position) {
    try {
        // Defensive check for valid position
        if (!position || !isFinite(position.x) || !isFinite(position.y) || !isFinite(position.z)) {
            console.warn('Invalid position for geodeticSurfaceNormal', position);
            return null;
        }
        
        // Check for near-zero positions which can cause issues
        const magnitude = Math.sqrt(position.x * position.x + position.y * position.y + position.z * position.z);
        if (magnitude < 1) {
            console.warn('Position magnitude too small for geodeticSurfaceNormal', position);
            return null;
        }
        
        // Try to get the normal, with additional error handling
        return Cesium.Ellipsoid.WGS84.geodeticSurfaceNormal(position);
    } catch (error) {
        console.error('Error in geodeticSurfaceNormal calculation:', error);
        // Return a default up vector as fallback
        return new Cesium.Cartesian3(0, 0, 1);
    }
}

// Wait for both the RF_SCYTHE namespace and Cesium to be initialized
(function() {
    function initializeHelpers() {
        if (typeof window.RF_SCYTHE === 'undefined' || typeof Cesium === 'undefined') {
            console.log('Waiting for RF_SCYTHE and Cesium to be available...');
            setTimeout(initializeHelpers, 100);
            return;
        }
        
        console.log('Initializing Cesium helper functions...');
        
        // Add our helper functions to the RF_SCYTHE namespace
        Object.assign(window.RF_SCYTHE, {
            isValidCesiumPosition,
            safeCartesian3FromDegrees,
            safeGeodeticSurfaceNormal
        });
        
        console.log('Helper functions initialized and added to RF_SCYTHE namespace.');
    }
    
    // Start initialization when the script loads
    initializeHelpers();
})();
