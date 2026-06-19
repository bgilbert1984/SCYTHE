/**
 * Cesium Error Debugger for RF SCYTHE
 * 
 * This module provides functions to debug and diagnose Cesium rendering errors
 */

// Create RF_SCYTHE namespace if it doesn't exist
window.RF_SCYTHE = window.RF_SCYTHE || {};

/**
 * Check for entities with invalid positions that might cause rendering errors
 * @param {Cesium.Viewer} viewer - The Cesium viewer to check
 * @returns {Array} Array of entities with invalid positions
 */
RF_SCYTHE.findEntitiesWithInvalidPositions = function(viewer) {
    if (!viewer || !viewer.entities) {
        console.warn('Invalid viewer provided');
        return [];
    }
    
    const invalidEntities = [];
    
    try {
        viewer.entities.values.forEach(entity => {
            try {
                if (!entity.position) {
                    // Skip entities without positions
                    return;
                }
                
                // Try to get current position
                const position = entity.position.getValue(Cesium.JulianDate.now());
                
                // Check if position is valid
                if (!position || 
                    !Cesium.defined(position) || 
                    !Cesium.defined(position.x) || 
                    !Cesium.defined(position.y) || 
                    !Cesium.defined(position.z) ||
                    !isFinite(position.x) || 
                    !isFinite(position.y) || 
                    !isFinite(position.z)) {
                    
                    invalidEntities.push({
                        entity: entity,
                        reason: 'Invalid or undefined position'
                    });
                    return;
                }
                
                // Try to convert to cartographic to check longitude/latitude
                try {
                    const cartographic = Cesium.Cartographic.fromCartesian(position);
                    if (!cartographic || 
                        !Cesium.defined(cartographic.longitude) || 
                        !Cesium.defined(cartographic.latitude)) {
                        
                        invalidEntities.push({
                            entity: entity,
                            reason: 'Invalid cartographic coordinates'
                        });
                    }
                } catch (error) {
                    invalidEntities.push({
                        entity: entity,
                        reason: 'Error converting to cartographic: ' + error.message
                    });
                }
            } catch (error) {
                invalidEntities.push({
                    entity: entity,
                    reason: 'Error checking position: ' + error.message
                });
            }
        });
    } catch (error) {
        console.error('Error finding entities with invalid positions:', error);
    }
    
    return invalidEntities;
};

/**
 * Check for geometry instances that might cause rendering errors
 * @param {Cesium.Viewer} viewer - The Cesium viewer to check
 */
RF_SCYTHE.debugCesiumGeometries = function(viewer) {
    if (!viewer || !viewer.scene) {
        console.warn('Invalid viewer provided');
        return;
    }
    
    const primitives = viewer.scene.primitives;
    let issues = 0;
    
    try {
        for (let i = 0; i < primitives.length; i++) {
            const primitive = primitives.get(i);
            
            // Skip non-ground primitives or undefined primitives
            if (!primitive || !primitive._groundPrimitives) {
                continue;
            }
            
            if (primitive._ellipseGeometries) {
                primitive._ellipseGeometries.forEach((geometry, index) => {
                    try {
                        if (!geometry.center || 
                            !Cesium.defined(geometry.center.x) || 
                            !isFinite(geometry.center.x)) {
                            
                            console.warn(`Invalid ellipse geometry at index ${index}`);
                            issues++;
                        }
                    } catch (error) {
                        console.warn(`Error checking ellipse geometry at index ${index}:`, error);
                        issues++;
                    }
                });
            }
        }
    } catch (error) {
        console.error('Error debugging Cesium geometries:', error);
    }
    
    return issues;
};

/**
 * Fix or remove entities with invalid positions to prevent rendering errors
 * @param {Cesium.Viewer} viewer - The Cesium viewer to fix
 * @returns {number} Number of entities fixed or removed
 */
RF_SCYTHE.fixEntitiesWithInvalidPositions = function(viewer) {
    if (!viewer || !viewer.entities) {
        console.warn('Invalid viewer provided');
        return 0;
    }
    
    const invalidEntities = RF_SCYTHE.findEntitiesWithInvalidPositions(viewer);
    let fixed = 0;
    
    invalidEntities.forEach(item => {
        try {
            // Try to fix the entity by setting a valid position
            if (item.entity.ellipse) {
                // For ellipses, we'll use a safe position
                const safePosition = Cesium.Cartesian3.fromDegrees(0, 0, 0);
                item.entity.position = safePosition;
                
                // Add a non-zero rotation to avoid bugs
                if (item.entity.ellipse.rotation) {
                    item.entity.ellipse.rotation = 0.001;
                }
                
                fixed++;
                
                // Add a console message about the fix
                if (typeof addConsoleMessage === 'function') {
                    addConsoleMessage(`Fixed entity with invalid position: ${item.entity.id || 'Unknown'}`, 'response');
                }
            } else {
                // For other entities, it's safer to remove them
                viewer.entities.remove(item.entity);
                fixed++;
                
                // Add a console message about the removal
                if (typeof addConsoleMessage === 'function') {
                    addConsoleMessage(`Removed entity with invalid position: ${item.entity.id || 'Unknown'}`, 'alert');
                }
            }
        } catch (error) {
            console.error('Error fixing entity:', error);
            
            // If fixing failed, remove the entity
            try {
                viewer.entities.remove(item.entity);
                fixed++;
            } catch (removeError) {
                console.error('Error removing entity:', removeError);
            }
        }
    });
    
    return fixed;
};

/**
 * Wire the Fix Rendering Errors action into the Settings panel.
 * Replaces the old floating button with a button inside the
 * Performance & Error Recovery settings group.
 * @param {Cesium.Viewer} viewer - The Cesium viewer to debug
 */
RF_SCYTHE.addDebugButton = function(viewer) {
    const runFix = () => {
        const fixed  = RF_SCYTHE.fixEntitiesWithInvalidPositions(viewer);
        const issues = RF_SCYTHE.debugCesiumGeometries(viewer);
        if (typeof showNotification === 'function') {
            showNotification(
                'Rendering Error Fix',
                `Fixed ${fixed} entities with invalid positions. Found ${issues} geometry issues.`,
                'info'
            );
        }
        if (typeof addConsoleMessage === 'function') {
            addConsoleMessage(`Fixed ${fixed} entities with invalid positions`, 'response');
        }
    };

    // Inject into Settings → Performance & Error Recovery group
    const injectIntoSettings = () => {
        // Avoid double-injection
        if (document.getElementById('btnFixRenderingErrors')) return;

        // Find the Performance & Error Recovery settings group by its heading
        const headings = document.querySelectorAll('#settings-panel .settings-group h4');
        let targetGroup = null;
        headings.forEach(h => {
            if (h.textContent.includes('Performance')) targetGroup = h.parentElement;
        });
        if (!targetGroup) return; // settings panel not ready yet

        const item = document.createElement('div');
        item.className = 'settings-item';
        item.style.marginTop = '8px';

        const btn = document.createElement('button');
        btn.id = 'btnFixRenderingErrors';
        btn.textContent = 'Fix Rendering Errors';
        btn.className = 'action-button';
        btn.style.backgroundColor = '#c93840';
        btn.style.color = 'white';
        btn.style.border = 'none';
        btn.style.borderRadius = '3px';
        btn.style.cursor = 'pointer';
        btn.style.padding = '5px 12px';
        btn.addEventListener('click', runFix);

        const desc = document.createElement('div');
        desc.className = 'setting-description';
        desc.textContent = 'Repair entities with invalid positions and geometry issues';

        item.appendChild(btn);
        item.appendChild(desc);
        targetGroup.appendChild(item);
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', injectIntoSettings);
    } else {
        injectIntoSettings();
    }

    // Expose programmatic trigger
    window.fixRenderingErrors = runFix;
};

// Initialize debug button if we're in a browser environment with a console
if (typeof window !== 'undefined' && typeof console !== 'undefined') {
    // We'll wait for Cesium to be fully loaded
    window.addEventListener('load', function() {
        // Wait a bit for the viewer to be initialized
        setTimeout(function() {
            if (window.viewer) {
                RF_SCYTHE.addDebugButton(window.viewer);
            }
        }, 2000);
    });
}
