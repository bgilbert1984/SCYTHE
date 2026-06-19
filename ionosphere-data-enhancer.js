/**
 * Enhanced Ionosphere Data Fetcher for RF SCYTHE
 * 
 * This script patches the existing fetchIonosphereData function to use the safe ionosphere
 * visualization as a fallback when Rectangle.north errors occur
 */

// Create RF_SCYTHE namespace if it doesn't exist
window.RF_SCYTHE = window.RF_SCYTHE || {};

/**
 * Enhanced fetchIonosphereData wrapper
 * This function will wrap the original fetchIonosphereData to provide error handling
 * and fallback to the safe ionosphere visualization
 */
RF_SCYTHE.enhanceFetchIonosphereData = function() {
    // Only attempt to enhance if the original function exists
    if (typeof window.fetchIonosphereData !== 'function') {
        console.warn('fetchIonosphereData function not found, cannot enhance');
        return false;
    }
    
    console.log('Enhancing fetchIonosphereData with safe ionosphere fallback');
    
    // Store the original function
    const originalFetchIonosphereData = window.fetchIonosphereData;
    
    // Replace with enhanced version
    window.fetchIonosphereData = function() {
        try {
            // Call the original function
            const result = originalFetchIonosphereData.apply(this, arguments);
            
            // Monitor for potential Rectangle.north errors by setting up an error handler
            // that will trigger after a short delay if we encounter issues with the ionosphere data
            setTimeout(() => {
                // Check if we have any Rectangle.north errors in the error console
                const hasRectangleErrors = document.querySelector('.error') && 
                    document.querySelector('.error').textContent.includes('Rectangle') &&
                    document.querySelector('.error').textContent.includes('north');
                
                if (hasRectangleErrors) {
                    console.warn('Rectangle.north errors detected after ionosphere initialization');
                    
                    // Try to fix with our safe implementation
                    if (typeof RF_SCYTHE.initSafeIonosphereVisualization === 'function' && 
                        typeof window.viewer !== 'undefined') {
                        
                        console.log('Switching to safe ionosphere visualization due to Rectangle.north errors');
                        
                        // Remove problematic entities
                        if (typeof window.removeIonosphereLayers === 'function') {
                            window.removeIonosphereLayers();
                        } else {
                            // Basic removal if the function doesn't exist
                            const entitiesToRemove = [];
                            window.viewer.entities.values.forEach(entity => {
                                if (entity.name && entity.name.includes('Ionosphere')) {
                                    entitiesToRemove.push(entity);
                                }
                            });
                            
                            entitiesToRemove.forEach(entity => {
                                window.viewer.entities.remove(entity);
                            });
                        }
                        
                        // Create safe ionosphere
                        window.safeIonosphere = RF_SCYTHE.initSafeIonosphereVisualization(window.viewer, {
                            useSafeEllipses: true,
                            enablePoles: false,
                            avoidProblematicLatitudes: true,
                            debug: false,
                            labelLayers: true
                        });
                        
                        if (typeof window.addConsoleMessage === 'function') {
                            window.addConsoleMessage("Using enhanced ionosphere visualization to avoid rendering errors", "response");
                        }
                    }
                }
            }, 2000);
            
            return result;
        } catch (error) {
            console.error('Error in fetchIonosphereData:', error);
            
            // Fall back to safe implementation
            if (typeof RF_SCYTHE.initSafeIonosphereVisualization === 'function' && 
                typeof window.viewer !== 'undefined') {
                
                console.log('Falling back to safe ionosphere visualization after error');
                
                // Remove any existing ionosphere entities
                if (typeof window.removeIonosphereLayers === 'function') {
                    window.removeIonosphereLayers();
                } else {
                    // Basic removal if the function doesn't exist
                    const entitiesToRemove = [];
                    window.viewer.entities.values.forEach(entity => {
                        if (entity.name && entity.name.includes('Ionosphere')) {
                            entitiesToRemove.push(entity);
                        }
                    });
                    
                    entitiesToRemove.forEach(entity => {
                        window.viewer.entities.remove(entity);
                    });
                }
                
                // Create safe ionosphere
                window.safeIonosphere = RF_SCYTHE.initSafeIonosphereVisualization(window.viewer, {
                    useSafeEllipses: true,
                    enablePoles: false,
                    avoidProblematicLatitudes: true,
                    debug: false,
                    labelLayers: true
                });
                
                if (typeof window.addConsoleMessage === 'function') {
                    window.addConsoleMessage("Using enhanced ionosphere visualization", "response");
                }
            }
            
            return null;
        }
    };
    
    console.log('fetchIonosphereData successfully enhanced');
    return true;
};

// Automatically apply the enhancement if the document is loaded
if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(RF_SCYTHE.enhanceFetchIonosphereData, 1000);
} else {
    document.addEventListener('DOMContentLoaded', function() {
        setTimeout(RF_SCYTHE.enhanceFetchIonosphereData, 1000);
    });
}
