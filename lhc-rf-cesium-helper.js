/**
 * LHC RF Cesium Helper
 * This file provides helper functions for initializing Cesium in the LHC RF visualization
 */

// Create global namespace
if (typeof RF_SCYTHE === 'undefined') {
    window.RF_SCYTHE = {};
}

RF_SCYTHE.CesiumHelper = {
    // Initialize a minimal Cesium viewer
    initializeViewer: function(containerId) {
        console.log('Initializing Cesium viewer');
        Cesium.Ion.defaultAccessToken = window.CESIUM_ION_TOKEN || window.SCYTHE_CESIUM_ION_TOKEN || '';

        try {
            const viewer = new Cesium.Viewer(containerId, {
                baseLayerPicker: false,
                geocoder: false,
                homeButton: false,
                sceneModePicker: false,
                navigationHelpButton: false,
                animation: false,
                timeline: false,
                fullscreenButton: false
            });

            // Add terrain if available
            if (Cesium.createWorldTerrain) {
                viewer.terrainProvider = Cesium.createWorldTerrain();
            } else if (Cesium.Terrain && Cesium.Terrain.fromWorldTerrain) {
                viewer.terrainProvider = Cesium.Terrain.fromWorldTerrain();
            } else {
                console.warn('World terrain not available in this version of Cesium');
            }

            return viewer;
        } catch (error) {
            console.error('Failed to initialize Cesium viewer:', error);
            return null;
        }
    },

    // Fly to CERN location
    flyToCERN: function(viewer) {
        viewer.camera.flyTo({
            destination: Cesium.Cartesian3.fromDegrees(6.0411, 46.2336, 10000),
            orientation: {
                heading: Cesium.Math.toRadians(0),
                pitch: Cesium.Math.toRadians(-50),
                roll: 0.0
            }
        });
    }
};

// LHC simulation placeholder
RF_SCYTHE.LHCSimulation = {
    init: function(viewer) {
        console.log('Initializing LHC RF simulation');
        this.viewer = viewer;
        this.active = false;

        // Create entity for LHC
        this.lhcEntity = viewer.entities.add({
            name: 'Large Hadron Collider',
            position: Cesium.Cartesian3.fromDegrees(6.0411, 46.2336, 0),
            ellipse: {
                semiMinorAxis: 3000.0,
                semiMajorAxis: 3000.0,
                material: new Cesium.ColorMaterialProperty(
                    Cesium.Color.BLUE.withAlpha(0.3)
                ),
                outline: true,
                outlineColor: Cesium.Color.BLUE
            }
        });
    },

    startSimulation: function() {
        console.log('Starting LHC RF simulation');
        this.active = true;
    },

    resetSimulation: function() {
        console.log('Resetting LHC RF simulation');
        this.active = false;
    },

    triggerCollision: function() {
        console.log('Triggered LHC collision event');

        // Create a brief flash at the LHC location
        const flashEntity = this.viewer.entities.add({
            position: Cesium.Cartesian3.fromDegrees(6.0411, 46.2336, 0),
            ellipse: {
                semiMinorAxis: 3000.0,
                semiMajorAxis: 3000.0,
                material: new Cesium.ColorMaterialProperty(
                    Cesium.Color.YELLOW.withAlpha(0.7)
                )
            },
            lifetime: 1.0 // seconds
        });

        // Remove flash after animation
        setTimeout(() => {
            if (this.viewer.entities.contains(flashEntity)) {
                this.viewer.entities.remove(flashEntity);
            }
        }, 1000);
    }
};
