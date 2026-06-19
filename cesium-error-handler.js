// Global error handler for Cesium operations
// This will catch unhandled errors in Cesium that might otherwise crash the application

window.addEventListener('load', function() {
    // Wait for Cesium to be initialized
    setTimeout(function installErrorHandler() {
        if (typeof Cesium === 'undefined') {
            setTimeout(installErrorHandler, 100);
            return;
        }
        
        // Install global error handler for Cesium operations
        console.log('Installing global error handler for Cesium operations');
        
        // Set up a global error handler
        window.addEventListener('error', function(event) {
            // Check if this is a Cesium-related error
            if (event.error && event.error.stack && 
                (event.error.stack.includes('Cesium') || 
                 event.error.stack.includes('geodeticSurfaceNormal') || 
                 event.error.message.includes('Cannot read properties of undefined'))) {
                
                console.error('Caught Cesium error:', event.error);
                
                // Prevent the error from crashing the application
                event.preventDefault();
                
                // Log specific details if this is the geodeticSurfaceNormal error
                if (event.error.stack.includes('geodeticSurfaceNormal') || 
                    event.error.message.includes('Cannot read properties of undefined')) {
                    console.warn('Detected the geodeticSurfaceNormal issue. This error has been caught and will not crash the application.');
                    
                    // Add a UI notification if needed
                    if (window.RF_SCYTHE && typeof window.RF_SCYTHE.showNotification === 'function') {
                        window.RF_SCYTHE.showNotification('Cesium rendering issue detected. Some visualizations may not display correctly.', 'warning');
                    }
                }
                
                // Handle the specific 'longitude undefined' error from Rectangle.fromCartesianArray
                if (event.error.message.includes("Cannot read properties of undefined (reading 'longitude')") &&
                    event.error.stack.includes('Rectangle.fromCartesianArray')) {
                    console.warn('Detected Cesium coordinate conversion error with undefined longitude. ' +
                                'This is likely due to invalid coordinates being passed to a Cesium function.');
                    
                    // Show a more user-friendly message in the UI if possible
                    const messageContainer = document.getElementById('cesium-error-message');
                    if (messageContainer) {
                        messageContainer.innerHTML = 'Invalid coordinates detected. Check the data source.';
                        messageContainer.style.display = 'block';
                        
                        // Hide the message after 5 seconds
                        setTimeout(() => {
                            messageContainer.style.display = 'none';
                        }, 5000);
                    }
                }
                
                // Handle the 'Cannot assign to read only property' Rectangle error
                if (event.error.message.includes("Cannot assign to read only property 'north'") ||
                    event.error.message.includes("Cannot assign to read only property")) {
                    console.warn('Detected Cesium Rectangle property error. This is likely due to attempting to modify a frozen Rectangle object.');
                    console.log('Error details:', event.error);
                    console.log('Error stack:', event.error.stack);
                    
                    // Try to recover by reusing our safe ellipse creation method
                    try {
                        // Find any EllipseGeometry entities and recreate them using polygons
                        if (window.RF_SCYTHE && typeof window.RF_SCYTHE.createSafeEllipse === 'function' &&
                            window.Cesium && window.Cesium.viewer) {
                            console.log('Attempting to recreate problematic entities with safe ellipses');
                            window.setTimeout(function() {
                                // This runs after the current execution stack to avoid recursion
                                var entities = window.Cesium.viewer.entities.values;
                                for (var i = 0; i < entities.length; i++) {
                                    if (entities[i].ellipse) {
                                        console.log('Converting entity to safe ellipse:', entities[i].id);
                                        // Convert to safe ellipse (polygon-based implementation)
                                        window.RF_SCYTHE.createSafeEllipse(
                                            window.Cesium.viewer,
                                            entities[i].position._value,
                                            entities[i].ellipse.semiMajorAxis._value,
                                            entities[i].ellipse.semiMinorAxis._value,
                                            entities[i].ellipse.material.color._value
                                        );
                                        // Remove the problematic entity
                                        window.Cesium.viewer.entities.remove(entities[i]);
                                    }
                                }
                            }, 0);
                        }
                    } catch (recoveryError) {
                        console.error('Failed to recover from Rectangle property error:', recoveryError);
                    }
                    
                    // Add a UI notification if needed
                    if (window.RF_SCYTHE && typeof window.RF_SCYTHE.showNotification === 'function') {
                        window.RF_SCYTHE.showNotification('Cesium Rectangle error detected. Some visualizations may not display correctly.', 'warning');
                    }
                }
                
                return true;
            }
        });
        
        // Install a specific patch for Cesium.EllipseGeometry and Cesium.EllipseOutlineGeometry
        // These are the classes that commonly have issues with geodeticSurfaceNormal
        if (Cesium.EllipseGeometry && typeof Cesium.EllipseGeometry._computeEllipsePositions === 'function') {
            const originalComputeEllipsePositions = Cesium.EllipseGeometry._computeEllipsePositions;
            
            Cesium.EllipseGeometry._computeEllipsePositions = function(...args) {
                try {
                    return originalComputeEllipsePositions.apply(this, args);
                } catch (error) {
                    console.error('Error in EllipseGeometry._computeEllipsePositions:', error);
                    
                    // Return a minimal valid result to prevent crashing
                    return {
                        positions: new Float64Array(0),
                        numPts: 0
                    };
                }
            };
        }
        
        // Patch Rectangle to handle the 'Cannot assign to read only property' error
        if (Cesium.Rectangle) {
            // Patch the Rectangle.clone method to ensure we're working with a non-frozen object
            const originalRectangleClone = Cesium.Rectangle.clone;
            
            Cesium.Rectangle.clone = function(rectangle, result) {
                try {
                    return originalRectangleClone.call(this, rectangle, result);
                } catch (error) {
                    console.error('Error in Rectangle.clone:', error);
                    
                    // Create a new rectangle with default values
                    if (!result) {
                        result = new Cesium.Rectangle();
                    }
                    
                    if (rectangle) {
                        try {
                            result.west = rectangle.west || -Math.PI;
                            result.south = rectangle.south || -Math.PI/2;
                            result.east = rectangle.east || Math.PI;
                            result.north = rectangle.north || Math.PI/2;
                        } catch (e) {
                            // If we can't copy properties, use defaults
                            result.west = -Math.PI;
                            result.south = -Math.PI/2;
                            result.east = Math.PI;
                            result.north = Math.PI/2;
                        }
                    }
                    
                    return result;
                }
            };
        }
        
        console.log('Global error handler for Cesium operations installed successfully');
    }, 500);
});