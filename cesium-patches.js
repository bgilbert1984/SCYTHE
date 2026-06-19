// Cesium Patch Script
// This script patches specific Cesium functions to make them more resilient
// against common errors like "Cannot read properties of undefined (reading 'x')"

(function() {
    // Wait for Cesium to be available
    function patchCesium() {
        if (typeof Cesium === 'undefined') {
            console.log('Waiting for Cesium to load before applying patches...');
            setTimeout(patchCesium, 100);
            return;
        }
        
        console.log('Applying Cesium patches for RF SCYTHE...');
        
        // First register a global error handler for Cesium rendering errors
        window.addEventListener('error', function(event) {
            if (event.error && event.error.message && 
                (event.error.message.includes('Cesium') || 
                 event.error.message.includes('Cannot assign to read only property'))) {
                console.warn('Global error handler caught Cesium error:', event.error.message);
                
                // Since we have a fallback visualization approach, we can prevent this error from crashing
                event.preventDefault();
                
                return true;
            }
        }, true);
        
        // Specifically target the geodeticSurfaceNormal calculation in Ellipsoid.js
        // This is where the "Cannot read properties of undefined (reading 'x')" error occurs
        
        // Save the original function
        const originalGeodeticSurfaceNormal = Cesium.Ellipsoid.WGS84.geodeticSurfaceNormal;
        
        // Disable problematic ellipses by providing a global config flag
        // This is used by other parts of the code to know if they should
        // use ellipses or fall back to other geometry types
        Cesium.RF_SCYTHE_CONFIG = {
            USE_ELLIPSES: false,
            USE_ELLIPSE_FALLBACK: true,
            DISABLE_GROUND_PRIMITIVES: true
        };
        
        // Force Rectangle.fromCartesianArray to use a new non-frozen Rectangle
        if (Cesium.Rectangle && Cesium.Rectangle.fromCartesianArray) {
            const originalFromCartesianArray = Cesium.Rectangle.fromCartesianArray;
            
            Cesium.Rectangle.fromCartesianArray = function(cartesians, ellipsoid, result) {
                // Always create a new result
                const safeResult = new Cesium.Rectangle();
                
                try {
                    // Call the original function with our safe result
                    return originalFromCartesianArray.call(this, cartesians, ellipsoid || Cesium.Ellipsoid.WGS84, safeResult);
                } catch (error) {
                    console.warn('Error in Rectangle.fromCartesianArray:', error);
                    // Return a default rectangle
                    safeResult.west = -Math.PI;
                    safeResult.south = -Math.PI/2;
                    safeResult.east = Math.PI;
                    safeResult.north = Math.PI/2;
                    return safeResult;
                }
            };
            
            console.log('Successfully patched Rectangle.fromCartesianArray');
        }
        
        // Since we can't easily patch the internal _computeRectangle function, override specific computed rectangles
        // Monkey patch computeRectangle internal function if we can find it in Cesium's code
        if (Cesium.EllipseGeometry) {
            // Override Cesium.EllipseGeometry constructor to catch errors
            const originalEllipseGeometry = Cesium.EllipseGeometry;
            
            Cesium.EllipseGeometry = function(options) {
                try {
                    return new originalEllipseGeometry(options);
                } catch (error) {
                    console.warn('Error in EllipseGeometry constructor:', error);
                    // Create a minimal valid ellipse at 0,0
                    return new originalEllipseGeometry({
                        center: Cesium.Cartesian3.fromDegrees(0, 0),
                        semiMajorAxis: 1000,
                        semiMinorAxis: 1000,
                        rotation: 0.001 // Small non-zero value to avoid bugs
                    });
                }
            };
            
            // Copy prototype and static properties
            Cesium.EllipseGeometry.prototype = originalEllipseGeometry.prototype;
            Cesium.EllipseGeometry.createGeometry = originalEllipseGeometry.createGeometry;
            Cesium.EllipseGeometry.packedLength = originalEllipseGeometry.packedLength;
            Cesium.EllipseGeometry.pack = originalEllipseGeometry.pack;
            Cesium.EllipseGeometry.unpack = originalEllipseGeometry.unpack;
            
            console.log('Successfully patched EllipseGeometry constructor');
        }
        
        // Replace geodeticSurfaceNormal with our more robust version
        Cesium.Ellipsoid.WGS84.geodeticSurfaceNormal = function(cartesian, result) {
            // Thorough validation of input
            if (!cartesian) {
                console.warn('Null cartesian passed to geodeticSurfaceNormal');
                
                // Return a safe default (up vector)
                if (!result) {
                    return new Cesium.Cartesian3(0, 0, 1);
                }
                
                result.x = 0;
                result.y = 0;
                result.z = 1;
                return result;
            }
            
            // Check if cartesian has x, y, z properties
            if (cartesian.x === undefined || cartesian.y === undefined || cartesian.z === undefined) {
                console.warn('Cartesian missing x, y, z properties in geodeticSurfaceNormal');
                
                // Return a safe default (up vector)
                if (!result) {
                    return new Cesium.Cartesian3(0, 0, 1);
                }
                
                result.x = 0;
                result.y = 0;
                result.z = 1;
                return result;
            }
            
            // Ensure values are finite
            if (!isFinite(cartesian.x) || !isFinite(cartesian.y) || !isFinite(cartesian.z)) {
                console.warn('Cartesian has non-finite values in geodeticSurfaceNormal');
                
                // Return a safe default (up vector)
                if (!result) {
                    return new Cesium.Cartesian3(0, 0, 1);
                }
                
                result.x = 0;
                result.y = 0;
                result.z = 1;
                return result;
            }
            
            // Check if the cartesian is at the zero point (or very close)
            const epsilon = 1e-10;
            if (Math.abs(cartesian.x) < epsilon && 
                Math.abs(cartesian.y) < epsilon && 
                Math.abs(cartesian.z) < epsilon) {
                console.warn('Cartesian position too close to origin in geodeticSurfaceNormal');
                
                // Return a safe default (up vector)
                if (!result) {
                    return new Cesium.Cartesian3(0, 0, 1);
                }
                
                result.x = 0;
                result.y = 0;
                result.z = 1;
                return result;
            }
            
            try {
                // Call the original implementation with validated input
                return originalGeodeticSurfaceNormal.call(Cesium.Ellipsoid.WGS84, cartesian, result);
            } catch (error) {
                console.error('Error in original geodeticSurfaceNormal function:', error);
                
                // Return a safe default (up vector)
                if (!result) {
                    return new Cesium.Cartesian3(0, 0, 1);
                }
                
                result.x = 0;
                result.y = 0;
                result.z = 1;
                return result;
            }
        };

        // Additional safety patches for Ellipsoid.js
        // Also patch the scaleToGeodeticSurface method that can cause similar issues
        if (Cesium.Ellipsoid.prototype.scaleToGeodeticSurface) {
            const originalScaleToGeodeticSurface = Cesium.Ellipsoid.prototype.scaleToGeodeticSurface;
            
            Cesium.Ellipsoid.prototype.scaleToGeodeticSurface = function(cartesian, result) {
                // Validate input similar to geodeticSurfaceNormal
                if (!cartesian || 
                    cartesian.x === undefined || 
                    cartesian.y === undefined || 
                    cartesian.z === undefined || 
                    !isFinite(cartesian.x) || 
                    !isFinite(cartesian.y) || 
                    !isFinite(cartesian.z)) {
                    console.warn('Invalid cartesian passed to scaleToGeodeticSurface');
                    return undefined;
                }
                
                try {
                    // Call the original implementation with validated input
                    return originalScaleToGeodeticSurface.call(this, cartesian, result);
                } catch (error) {
                    console.error('Error in original scaleToGeodeticSurface function:', error);
                    return undefined;
                }
            };
        }
        
        console.log('Cesium patches applied successfully.');
    }
    
    // Start the patching process
    patchCesium();
})();