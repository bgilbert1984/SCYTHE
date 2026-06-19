/**
 * Cesium Minimal Globe Configuration
 *
 * This file provides a minimal configuration for Cesium that works without
 * requiring a valid Cesium Ion API key.
 */

// Configuration function to create a minimal Cesium viewer without Ion dependency
function createMinimalCesiumViewer(containerId, options = {}) {
    // Use Open Street Map as a free imagery provider
    const osmProvider = new Cesium.OpenStreetMapImageryProvider({
        url: 'https://a.tile.openstreetmap.org/'
    });

    // Default options that don't require Cesium Ion
    const defaultOptions = {
        imageryProvider: osmProvider,
        baseLayerPicker: false,
        geocoder: false,
        homeButton: true,
        sceneModePicker: true,
        navigationHelpButton: false,
        animation: false,
        timeline: false,
        fullscreenButton: true,
        terrainProvider: new Cesium.EllipsoidTerrainProvider(),
        infoBox: false,
        selectionIndicator: false,
        shadows: true,
        shouldAnimate: true
    };

    // Merge default options with provided options
    const mergedOptions = {...defaultOptions, ...options};

    // Create and return the viewer
    return new Cesium.Viewer(containerId, mergedOptions);
}

// Custom function to create a simple star skybox
function createSimpleStarSkybox(viewer) {
    // Create a simple dark skybox for night sky appearance
    viewer.scene.skyBox = undefined;
    viewer.scene.backgroundColor = new Cesium.Color(0.0, 0.0, 0.0, 1.0);
    viewer.scene.sun = undefined;
    viewer.scene.moon = undefined;

    // Turn off atmosphere and ground atmosphere
    viewer.scene.skyAtmosphere.show = false;
    if (viewer.scene.globe) {
        viewer.scene.globe.showGroundAtmosphere = false;
    }

    console.log("Simple star skybox created (using dark background)");
    return true;
}
