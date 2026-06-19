/**
 * Handles coordinate-related errors by cleaning up problematic entities
 * @param {Cesium.Viewer} viewer - The Cesium viewer instance
 * @param {Error} error - The error that occurred
 */
RF_SCYTHE.handleCoordinateError = function(viewer, error) {
    if (!viewer || !viewer.entities) return;
    
    console.warn("Handling coordinate error:", error.message);
    
    // Look for problematic ellipse entities that might cause the error
    const problemEntities = [];
    viewer.entities.values.forEach(entity => {
        // Check for ellipse entities
        if (entity.ellipse) {
            try {
                // Try to get the position
                const position = entity.position.getValue(Cesium.JulianDate.now());
                if (!position) {
                    problemEntities.push(entity);
                    return;
                }
                
                // Try to convert to cartographic
                try {
                    Cesium.Cartographic.fromCartesian(position);
                } catch (e) {
                    // If this fails, the entity has invalid coordinates
                    problemEntities.push(entity);
                }
            } catch (e) {
                // If any error occurs while checking, mark as problematic
                problemEntities.push(entity);
            }
        }
    });
    
    // Remove the problematic entities
    console.log(`Found ${problemEntities.length} problematic entities with invalid coordinates`);
    problemEntities.forEach(entity => {
        try {
            viewer.entities.remove(entity);
        } catch (e) {
            console.error("Error removing problematic entity:", e);
        }
    });
    
    // Add a console message
    if (typeof addConsoleMessage === 'function') {
        addConsoleMessage(`Fixed rendering error by removing ${problemEntities.length} invalid entities`, 'response');
    }
    
    // Show a notification if available
    if (typeof showNotification === 'function') {
        showNotification(
            'Rendering Error Fixed',
            `Removed ${problemEntities.length} entities with invalid coordinates to restore normal operation.`,
            'info'
        );
    }
};
