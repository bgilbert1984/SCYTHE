/**
 * Coordinate Validation and Error Prevention Module for RF SCYTHE
 * 
 * This module provides utility functions to safely handle coordinates
 * in the RF SCYTHE visualization system, preventing common Cesium errors.
 */

// Create RF_SCYTHE namespace if it doesn't exist
window.RF_SCYTHE = window.RF_SCYTHE || {};

/**
 * Sanitizes latitude and longitude coordinates to ensure they are valid
 * @param {number} latitude - The latitude value to sanitize
 * @param {number} longitude - The longitude value to sanitize
 * @param {number} altitude - The altitude value to sanitize (optional)
 * @returns {Object} - Object containing sanitized latitude, longitude, and altitude
 */
RF_SCYTHE.sanitizeCoordinates = function(latitude, longitude, altitude = 0) {
    // Check if inputs are numbers and not NaN
    if (typeof latitude !== 'number' || isNaN(latitude)) {
        console.warn('Invalid latitude provided:', latitude);
        latitude = 0; // Default to 0
    }
    
    if (typeof longitude !== 'number' || isNaN(longitude)) {
        console.warn('Invalid longitude provided:', longitude);
        longitude = 0; // Default to 0
    }
    
    if (typeof altitude !== 'number' || isNaN(altitude)) {
        console.warn('Invalid altitude provided:', altitude);
        altitude = 0; // Default to 0
    }
    
    // Constrain latitude to valid range (-90 to 90)
    latitude = Math.max(-90, Math.min(90, latitude));
    
    // Constrain longitude to valid range (-180 to 180)
    longitude = Math.max(-180, Math.min(180, longitude));
    
    return { latitude, longitude, altitude };
};

/**
 * Creates a safe Cartesian3 point from longitude and latitude
 * @param {number} longitude - The longitude value
 * @param {number} latitude - The latitude value
 * @param {number} height - The height/altitude value (optional)
 * @returns {Cesium.Cartesian3|null} - Cesium Cartesian3 position or null if invalid
 */
RF_SCYTHE.createSafeCartesian = function(longitude, latitude, height = 0) {
    try {
        // Sanitize the coordinates first
        const sanitized = RF_SCYTHE.sanitizeCoordinates(latitude, longitude, height);
        
        // Create the Cartesian3 position
        return Cesium.Cartesian3.fromDegrees(
            sanitized.longitude,
            sanitized.latitude,
            sanitized.altitude
        );
    } catch (error) {
        console.error('Error creating Cartesian3 position:', error);
        return null;
    }
};

/**
 * Creates a safe ellipse entity with error handling
 * @param {Cesium.Viewer} viewer - The Cesium viewer instance
 * @param {Cesium.Cartesian3} position - Center position of the ellipse
 * @param {number} semiMajorAxis - Semi-major axis length in meters
 * @param {number} semiMinorAxis - Semi-minor axis length in meters
 * @param {Cesium.Color} color - Fill color for the ellipse
 * @param {Object} options - Additional ellipse options
 * @returns {Cesium.Entity|null} - The created ellipse entity or null if failed
 */
RF_SCYTHE.createSafeEllipse = function(viewer, position, semiMajorAxis, semiMinorAxis, color, options = {}) {
    if (!viewer) {
        console.warn('Invalid viewer for ellipse');
        return null;
    }
    
    // Check if position is valid
    if (!position || !Cesium.defined(position)) {
        console.warn('Invalid position for ellipse');
        return null;
    }
    
    // Additional check to handle the "Cannot read properties of undefined (reading 'longitude')" error
    try {
        // This will throw an error if the position is invalid
        Cesium.Cartographic.fromCartesian(position);
    } catch (error) {
        console.warn('Invalid Cartesian position for ellipse:', error.message);
        return null;
    }
    
    // Ensure the axes are positive numbers
    semiMajorAxis = Math.abs(Number(semiMajorAxis) || 1000);
    semiMinorAxis = Math.abs(Number(semiMinorAxis) || 1000);
    
    // Ensure color is valid
    if (!color || !Cesium.defined(color)) {
        color = Cesium.Color.RED.withAlpha(0.5);
    }
    
    try {
        // FALLBACK METHOD: Use a sphere instead of an ellipse to avoid Rectangle issues
        // This works around the "Cannot assign to read only property 'north'" error
        
        // Create a simple sphere entity centered at the position
        // Scale to approximate the ellipse size
        const radius = Math.max(semiMajorAxis, semiMinorAxis) * 0.75;
        
        console.log('Creating sphere fallback instead of ellipse');
        return viewer.entities.add({
            position: position,
            name: 'Ellipse Fallback (Sphere)',
            ellipsoid: {
                radii: new Cesium.Cartesian3(radius, radius, radius * 0.2),
                material: color,
                outline: options.hasOwnProperty('outline') ? options.outline : true,
                outlineColor: options.outlineColor || Cesium.Color.WHITE,
                outlineWidth: options.outlineWidth || 2.0
            }
        });
    } catch (error) {
        console.error('Error creating safe ellipse fallback:', error);
        
        // Fallback to point if sphere fails
        try {
            console.log('Creating point fallback');
            return viewer.entities.add({
                position: position,
                point: {
                    pixelSize: 15,
                    color: color,
                    outlineColor: Cesium.Color.WHITE,
                    outlineWidth: 2
                }
            });
        } catch (fallbackError) {
            console.error('Fallback point creation also failed:', fallbackError);
            
            // Last resort: just a billboard icon
            try {
                console.log('Creating billboard as last resort');
                return viewer.entities.add({
                    position: position,
                    billboard: {
                        image: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUAAAAFCAYAAACNbyblAAAAHElEQVQI12P4//8/w38GIAXDIBKE0DHxgljNBAAO9TXL0Y4OHwAAAABJRU5ErkJggg==', // Red dot
                        scale: 2.0
                    }
                });
            } catch (e) {
                console.error('All fallbacks failed:', e);
                return null;
            }
        }
    }
};

/**
 * Adds a visualization for an FCC violation at the specified coordinates
 * @param {Cesium.Viewer} viewer - The Cesium viewer instance
 * @param {number} latitude - Latitude of the violation
 * @param {number} longitude - Longitude of the violation
 * @param {number} frequency - Frequency of the violation in MHz
 * @param {string} description - Description of the violation
 * @returns {Cesium.Entity|null} - The created entity or null if failed
 */
RF_SCYTHE.addViolation = function(viewer, latitude, longitude, frequency, description) {
    try {
        // Sanitize the coordinates
        const sanitized = RF_SCYTHE.sanitizeCoordinates(latitude, longitude);
        
        // Create position with a small height to ensure visibility
        const position = Cesium.Cartesian3.fromDegrees(
            sanitized.longitude, 
            sanitized.latitude, 
            10
        );
        
        // Create the violation entity
        return viewer.entities.add({
            position: position,
            name: `FCC Violation: ${frequency} MHz`,
            description: description,
            billboard: {
                image: 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0OCIgaGVpZ2h0PSI0OCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiNmZjAwMDAiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMTAgMTRsMi4yIDIuMkwxNCAxNCIvPjxwYXRoIGQ9Ik0yMSAxMnYyYTkgOSAwIDEgMS05LTkiLz48L3N2Zz4=',
                scale: 0.5,
                color: Cesium.Color.RED,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM
            },
            label: {
                text: `${frequency} MHz`,
                font: '14px sans-serif',
                fillColor: Cesium.Color.WHITE,
                outlineColor: Cesium.Color.BLACK,
                outlineWidth: 2,
                style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                verticalOrigin: Cesium.VerticalOrigin.TOP,
                pixelOffset: new Cesium.Cartesian2(0, -20)
            }
        });
    } catch (error) {
        console.error('Error adding violation:', error);
        return null;
    }
};

/**
 * Adds an uncertainty circle around a given position with error handling
 * @param {Cesium.Viewer} viewer - The Cesium viewer instance
 * @param {number} latitude - The latitude value
 * @param {number} longitude - The longitude value
 * @param {number} radius - The radius of the uncertainty circle in meters
 * @param {boolean} isPulsing - Whether the circle should pulse
 * @returns {Cesium.Entity|null} - The created circle entity or null if failed
 */
RF_SCYTHE.addUncertaintyCircle = function(viewer, latitude, longitude, radius, isPulsing = false) {
    try {
        // Sanitize coordinates
        const sanitized = RF_SCYTHE.sanitizeCoordinates(latitude, longitude);
        
        // Create a safe position
        const position = Cesium.Cartesian3.fromDegrees(
            sanitized.longitude,
            sanitized.latitude
        );
        
        // If we couldn't create a valid position, return null
        if (!position) {
            console.warn('Could not create valid position for uncertainty circle');
            return null;
        }
        
        // Create material based on whether it should pulse
        let material;
        if (isPulsing) {
            material = new Cesium.MaterialProperty({
                fabric: {
                    type: 'EllipsoidPulse',
                    uniforms: {
                        color: Cesium.Color.RED.withAlpha(0.3),
                        pulseColor: Cesium.Color.RED.withAlpha(0.7),
                        speed: 0.5
                    }
                }
            });
        } else {
            material = Cesium.Color.RED.withAlpha(0.3);
        }
        
        // Use our safe ellipse implementation (which uses a polygon) instead of direct ellipse creation
        return RF_SCYTHE.createSafeEllipse(viewer, position, radius, radius, material, {
            height: 0,
            outline: true,
            outlineColor: Cesium.Color.RED.withAlpha(0.7),
            outlineWidth: 2.0,
            rotation: 0.001 // Small non-zero rotation to avoid Cesium bugs
        });
    } catch (error) {
        console.error('Error adding uncertainty circle:', error);
        return null;
    }
};

// Export functions for testing
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        sanitizeCoordinates: RF_SCYTHE.sanitizeCoordinates,
        createSafeCartesian: RF_SCYTHE.createSafeCartesian,
        createSafeEllipse: RF_SCYTHE.createSafeEllipse,
        addViolation: RF_SCYTHE.addViolation,
        addUncertaintyCircle: RF_SCYTHE.addUncertaintyCircle
    };
}