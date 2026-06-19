/**
 * Ellipse Error Fix Utility for RF SCYTHE
 * 
 * This module provides utility functions to prevent and fix ellipse rendering errors in Cesium
 */

// Create RF_SCYTHE namespace if it doesn't exist
window.RF_SCYTHE = window.RF_SCYTHE || {};

/**
 * Validates and returns a safe ellipse geometry for Cesium
 * This prevents the "Cannot read properties of undefined (reading 'longitude')" error
 * 
 * @param {Cesium.Cartesian3} center - The center position of the ellipse
 * @param {number} semiMajorAxis - The semi-major axis in meters
 * @param {number} semiMinorAxis - The semi-minor axis in meters
 * @param {Cesium.Ellipsoid} [ellipsoid=Cesium.Ellipsoid.WGS84] - The ellipsoid to use
 * @returns {Cesium.EllipseGeometry|null} - A safe ellipse geometry or null if invalid
 */
RF_SCYTHE.createSafeEllipseGeometry = function(center, semiMajorAxis, semiMinorAxis, ellipsoid) {
    try {
        // Input validation
        if (!center || !Cesium.defined(center)) {
            console.warn('Invalid center position for ellipse geometry');
            return null;
        }

        // Verify that position is a valid Cartesian3
        if (!Cesium.defined(center.x) || !Cesium.defined(center.y) || !Cesium.defined(center.z)) {
            console.warn('Center position is missing x, y, or z component');
            return null;
        }

        // Ensure the values are finite
        if (!isFinite(center.x) || !isFinite(center.y) || !isFinite(center.z)) {
            console.warn('Center position contains non-finite values');
            return null;
        }

        // Verify the position is not at the core of the earth or in space
        try {
            const cartographic = Cesium.Cartographic.fromCartesian(center, ellipsoid);
            if (!cartographic || !Cesium.defined(cartographic)) {
                console.warn('Failed to convert center to cartographic');
                return null;
            }

            // Check if longitude and latitude are valid
            if (!Cesium.defined(cartographic.longitude) || !Cesium.defined(cartographic.latitude)) {
                console.warn('Invalid longitude or latitude in cartographic');
                return null;
            }
        } catch (e) {
            console.warn('Error converting center to cartographic', e);
            return null;
        }

        // Ensure semi-axes are positive numbers
        semiMajorAxis = Math.abs(Number(semiMajorAxis) || 1000);
        semiMinorAxis = Math.abs(Number(semiMinorAxis) || 1000);

        // Create ellipse with a small non-zero rotation to avoid Cesium bug
        const rotation = 0.001; // Small rotation to avoid the zero-rotation bug

        // Create the ellipse geometry safely
        return new Cesium.EllipseGeometry({
            center: center,
            semiMajorAxis: semiMajorAxis,
            semiMinorAxis: semiMinorAxis,
            rotation: rotation,
            ellipsoid: ellipsoid || Cesium.Ellipsoid.WGS84
        });
    } catch (error) {
        console.error('Error creating safe ellipse geometry:', error);
        return null;
    }
};

/**
 * Helper function to patch Cesium's ellipse creation to be more robust
 * Call this function once during initialization to make all ellipses safer
 */
RF_SCYTHE.patchCesiumEllipses = function() {
    try {
        // Store the original EllipseGeometry constructor
        const originalEllipseGeometry = Cesium.EllipseGeometry;
        
        // Override with our safer version
        Cesium.EllipseGeometry = function(options) {
            try {
                // Validate center position
                if (!options.center || 
                    !Cesium.defined(options.center.x) || 
                    !Cesium.defined(options.center.y) || 
                    !Cesium.defined(options.center.z) ||
                    !isFinite(options.center.x) || 
                    !isFinite(options.center.y) || 
                    !isFinite(options.center.z)) {
                    
                    console.warn('Invalid center for EllipseGeometry, using default position');
                    options.center = Cesium.Cartesian3.fromDegrees(0, 0, 0);
                }
                
                // Ensure semi-axes are positive and non-zero
                options.semiMajorAxis = Math.max(1, Math.abs(Number(options.semiMajorAxis) || 1000));
                options.semiMinorAxis = Math.max(1, Math.abs(Number(options.semiMinorAxis) || 1000));
                
                // Ensure rotation is non-zero to avoid Cesium bug
                if (options.rotation === 0 || options.rotation === undefined) {
                    options.rotation = 0.001; // Small non-zero value
                }
                
                // Call the original constructor
                return originalEllipseGeometry.call(this, options);
            } catch (error) {
                console.error('Error in patched EllipseGeometry constructor:', error);
                // Return a minimal working ellipse at 0,0
                return originalEllipseGeometry.call(this, {
                    center: Cesium.Cartesian3.fromDegrees(0, 0, 0),
                    semiMajorAxis: 1000,
                    semiMinorAxis: 1000,
                    rotation: 0.001
                });
            }
        };
        
        // Copy prototype properties
        Cesium.EllipseGeometry.prototype = originalEllipseGeometry.prototype;
        Cesium.EllipseGeometry.packedLength = originalEllipseGeometry.packedLength;
        Cesium.EllipseGeometry.createGeometry = originalEllipseGeometry.createGeometry;
        
        console.log('Successfully patched Cesium EllipseGeometry for safer operation');
    } catch (error) {
        console.error('Failed to patch Cesium EllipseGeometry:', error);
    }
};

/**
 * Patches the Cesium Rectangle.fromCartesianArray function to handle the specific
 * "Cannot read properties of undefined (reading 'longitude')" error
 */
RF_SCYTHE.patchRectangleFromCartesianArray = function() {
    try {
        // Store the original function
        const originalFromCartesianArray = Cesium.Rectangle.fromCartesianArray;
        
        // Override with our safer version
        Cesium.Rectangle.fromCartesianArray = function(cartesians, ellipsoid, result) {
            try {
                // Validate the input array
                if (!cartesians || !Array.isArray(cartesians) || cartesians.length === 0) {
                    console.warn('Invalid cartesians array provided to Rectangle.fromCartesianArray');
                    // Return a default rectangle around the entire globe
                    return Cesium.Rectangle.MAX_VALUE;
                }
                
                // Filter out invalid cartesians (null, undefined, or with missing coordinates)
                const validCartesians = cartesians.filter(cartesian => 
                    cartesian && 
                    Cesium.defined(cartesian) && 
                    Cesium.defined(cartesian.x) && 
                    Cesium.defined(cartesian.y) && 
                    Cesium.defined(cartesian.z) &&
                    isFinite(cartesian.x) && 
                    isFinite(cartesian.y) && 
                    isFinite(cartesian.z)
                );
                
                // If there are no valid cartesians, return a default rectangle
                if (validCartesians.length === 0) {
                    console.warn('No valid cartesians in array provided to Rectangle.fromCartesianArray');
                    return Cesium.Rectangle.MAX_VALUE;
                }
                
                // Call the original function with validated input
                return originalFromCartesianArray.call(this, validCartesians, ellipsoid, result);
            } catch (error) {
                console.error('Error in patched Rectangle.fromCartesianArray:', error);
                // Return a default rectangle
                return Cesium.Rectangle.MAX_VALUE;
            }
        };
        
        console.log('Successfully patched Cesium Rectangle.fromCartesianArray for safer operation');
    } catch (error) {
        console.error('Failed to patch Cesium Rectangle.fromCartesianArray:', error);
    }
};

// Initialize the patch if we're in a browser environment
if (typeof window !== 'undefined' && window.Cesium) {
    // We'll wait for Cesium to be fully loaded
    setTimeout(function() {
        if (window.Cesium && window.Cesium.Rectangle && window.RF_SCYTHE && typeof RF_SCYTHE.patchCesiumEllipses === 'function') {
            console.log('Applying cesium ellipse patches...');
            RF_SCYTHE.patchCesiumEllipses();
            if (typeof RF_SCYTHE.patchRectangleFromCartesianArray === 'function') {
                RF_SCYTHE.patchRectangleFromCartesianArray();
            }
            console.log('Cesium ellipse patches applied successfully');
        } else {
            console.warn('Cesium or RF_SCYTHE not ready for patching, skipping ellipse patches');
        }
    }, 1000);
}
