/**
 * Helper utilities for Cesium material properties
 */

window.RF_SCYTHE = window.RF_SCYTHE || {};

/**
 * Create a safe material property from a callback function that returns material or color
 * This fixes the "t.getType is not a function" error by ensuring the callback always returns a proper material
 *
 * @param {Function} callback - A function that returns a material or color
 * @param {Boolean} isConstant - Whether the property is constant
 * @returns {Cesium.MaterialProperty} A material property
 */
RF_SCYTHE.createSafeMaterialProperty = function(callback, isConstant = false) {
    return new Cesium.CallbackProperty(function(time, result) {
        // Get the material from the callback
        const value = callback(time, result);

        // If it's already a material property, return it
        if (value && typeof value.getType === 'function') {
            return value;
        }

        // If it's a Color, wrap it in a ColorMaterialProperty
        if (value instanceof Cesium.Color) {
            return new Cesium.ColorMaterialProperty(value);
        }

        // Default to transparent white if all else fails
        return new Cesium.ColorMaterialProperty(Cesium.Color.WHITE.withAlpha(0.0));
    }, isConstant);
};

/**
 * Creates a plasma material for hypersonic effects
 *
 * @param {Object} options - Configuration options
 * @returns {Cesium.MaterialProperty} A material property for plasma effects
 */
RF_SCYTHE.createPlasmaMaterial = function(options) {
    const defaultOptions = {
        color: Cesium.Color.ORANGE,
        glowIntensity: 0.5,
        pulseRate: 1.0,
        alpha: 0.7
    };

    const settings = {...defaultOptions, ...options};

    // Create a glowing material using rimLighting
    return new Cesium.MaterialProperty({
        getType: function() {
            return 'RimLighting';
        },
        getValue: function(time, result) {
            result = result || {};
            result.color = settings.color;
            result.rimColor = Cesium.Color.WHITE;
            result.rimPower = 0.8;
            if (settings.pulseRate > 0) {
                // Add pulsing effect
                const seconds = Cesium.JulianDate.secondsOfDay(time);
                const pulseFactor = 0.3 * Math.sin(seconds * settings.pulseRate) + 0.7;
                result.rimColor = Cesium.Color.fromAlpha(result.rimColor, pulseFactor);
            }
            return result;
        },
        equals: function(other) {
            return settings.color.equals(other.color) &&
                   settings.glowIntensity === other.glowIntensity &&
                   settings.pulseRate === other.pulseRate;
        }
    });
};

console.log("Cesium material helpers loaded for RF SCYTHE");
