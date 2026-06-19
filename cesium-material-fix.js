/**
 * Cesium material fix for Plasma Sheath visualization
 * This file patches the Cesium material system to handle the specific error:
 * TypeError: t.getType is not a function
 */

(function() {
    // Save original getValue method from MaterialProperty prototype
    const originalGetValue = Cesium.MaterialProperty.prototype.getValue;

    // Patch the getValue method to handle missing getType functions
    Cesium.MaterialProperty.prototype.getValue = function(time, result) {
        try {
            // Try original method first
            return originalGetValue.call(this, time, result);
        } catch (e) {
            // If we get an error about getType, provide a safe fallback
            if (e && e.message && e.message.includes('getType is not a function')) {
                console.warn('Fixing material property error');

                // Create a default material result if one wasn't provided
                result = result || {};

                // Set default values for a Color material
                result.color = Cesium.Color.WHITE;
                result.type = 'Color';

                return result;
            }

            // Re-throw other errors
            throw e;
        }
    };

    // Add a safety check for DynamicEllipsoidGeometryUpdater
    const originalDynamicEllipsoidUpdate = Cesium.DynamicEllipsoidGeometryUpdater.prototype.update;

    Cesium.DynamicEllipsoidGeometryUpdater.prototype.update = function(time) {
        try {
            // Try original method
            return originalDynamicEllipsoidUpdate.call(this, time);
        } catch (e) {
            // Handle material property errors
            if (e && e.message && e.message.includes('getType is not a function')) {
                console.warn('Fixing ellipsoid updater error');

                // Force update the material if possible
                if (this._materialProperty) {
                    try {
                        // Try to get a valid material
                        this._materialProperty = new Cesium.ColorMaterialProperty(Cesium.Color.ORANGE.withAlpha(0.5));
                    } catch (e2) {
                        console.error('Failed to fix material:', e2);
                    }
                }

                // Prevent crash by returning without further processing
                return;
            }

            // Re-throw other errors
            throw e;
        }
    };

    console.log('Installed Cesium material error handlers');
})();
